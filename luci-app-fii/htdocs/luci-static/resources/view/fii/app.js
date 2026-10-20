'use strict';
'require view';
'require rpc';
'require ui';
'require poll';
'require fs';

// SPDX-License-Identifier: GPL-3.0-or-later
// Content version is synchronized by tools/build_ipk.py, including same-version rebuilds.
var fiiStyleHash = 'ad673c223f5c2c9a';
var fiiStylePromise = null;
function loadStyle() {
    var href = L.resource('fii/style.css') + '?v=' + fiiStyleHash;
    var previous = document.getElementById('fii-style');
    if (previous && previous.getAttribute('href') === href && previous.sheet) return Promise.resolve();
    if (fiiStylePromise) return fiiStylePromise;
    fiiStylePromise = new Promise(function(resolve, reject) {
        var link = document.createElement('link'), timer;
        function fail() {
            clearTimeout(timer); link.onload = link.onerror = null; link.remove();
            reject(new Error('FII 界面样式加载失败，请检查连接后重新载入页面。'));
        }
        link.rel = 'stylesheet'; link.href = href;
        link.onload = function() {
            clearTimeout(timer); link.onload = link.onerror = null;
            if (previous) previous.remove();
            link.id = 'fii-style'; resolve();
        };
        link.onerror = fail;
        timer = setTimeout(fail, 15000);
        document.head.appendChild(link);
    });
    return fiiStylePromise.finally(function() { fiiStylePromise = null; });
}

var methods = {};
var rpcUploadChunkSize = 16 * 1024;
var rpcDownloadChunkSize = 16 * 1024;
function transportError(err) {
    return /XHR|RPC reply|HTTP error (?:0|413|502|504)|Failed to fetch|NetworkError/i.test(err.message || '');
}
function reduceChunk(size, onProgress, download) {
    if (size <= 1024) return false;
    if (download) rpcDownloadChunkSize = Math.min(rpcDownloadChunkSize, Math.max(1024, Math.floor(size / 2)));
    else rpcUploadChunkSize = Math.min(rpcUploadChunkSize, Math.max(1024, Math.floor(size / 2)));
    if (onProgress) onProgress({ message: '正在调整传输大小并重试…' });
    return true;
}
['get', 'status', 'save', 'refresh', 'preview', 'apply', 'service', 'export', 'import',
 'import_preview', 'logs', 'job', 'nodes', 'groups', 'select', 'resources', 'resource_update', 'core_info', 'core_check', 'core_update', 'core_channel', 'network',
 'transfer_begin', 'transfer_append', 'transfer_invoke', 'transfer_read', 'transfer_release', 'transfer_status'].forEach(function(name) {
    methods[name] = rpc.declare({ object: 'luci.fii', method: name, params: ['payload'], expect: { '': {} }, nobatch: true });
});

async function rpcReply(name, payload, onProgress) {
    var body = JSON.stringify(payload || {}), reply;
    try {
        reply = await methods[name](body);
    } catch (err) {
        throw new Error('请求 ' + name + ' 失败（参数 ' + new TextEncoder().encode(body).length + ' 字节）：' + err.message);
    }
    if (!reply.ok) throw new Error(reply.error || 'FII 请求失败');
    if (reply.responseTransfer) {
        var transfer = reply.responseTransfer;
        if (!Number.isSafeInteger(transfer.size) || transfer.size <= 0 || transfer.size > 32 * 1024 * 1024) throw new Error('响应大小无效');
        var bytes = new Uint8Array(transfer.size), offset = 0;
        try {
            if (transfer.downloadPath === '/tmp/fii/transfers/' + transfer.id + '.out' && /^[a-f0-9]{32}$/.test(transfer.id)) {
                try {
                    var direct = await fs.read_direct(transfer.downloadPath, 'text');
                    if (new TextEncoder().encode(direct).length !== transfer.size) throw new Error('响应大小不符');
                    return JSON.parse(direct);
                } catch (err) {
                    // Older cgi-io/ACL installations can still use bounded ubus chunks.
                    if (onProgress) onProgress({ message: '正在分块读取结果…' });
                }
            }
            while (offset < bytes.length) {
                var part, requestedSize = rpcDownloadChunkSize;
                try {
                    part = await rpcReply('transfer_read', { id: transfer.id, offset: offset, size: requestedSize });
                } catch (err) {
                    if (transportError(err) && reduceChunk(requestedSize, onProgress, true)) continue;
                    throw err;
                }
                var data = Uint8Array.from(atob(part.result.data), function(c) { return c.charCodeAt(0); });
                if (!data.length || offset + data.length > bytes.length) throw new Error('响应分块大小无效');
                bytes.set(data, offset); offset += data.length;
                if (onProgress) onProgress({ message: '正在读取结果…', received: offset, total: bytes.length });
            }
            reply = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
        } finally {
            methods.transfer_release(JSON.stringify({ id: transfer.id })).catch(function() {});
        }
    }
    return reply;
}

async function call(name, payload, onProgress) {
    var encoded = new TextEncoder().encode(JSON.stringify(payload || {})), reply;
    if (encoded.length > 1024) {
        var upload = (await rpcReply('transfer_begin', { size: encoded.length })).result;
        try {
            if (upload.protocol !== 2) throw new Error('FII 后端与界面版本不一致，请同时升级两个安装包到同一版本，并强制刷新页面。');
            for (var offset = 0; offset < encoded.length;) {
                var size = Math.min(upload.chunkSize, rpcUploadChunkSize);
                var chunk = encoded.subarray(offset, offset + size);
                var data = btoa(String.fromCharCode.apply(null, chunk));
                try {
                    await rpcReply('transfer_append', { id: upload.id, offset: offset, data: data });
                } catch (err) {
                    if (transportError(err) && reduceChunk(size, onProgress)) continue;
                    throw err;
                }
                offset += chunk.length;
                if (onProgress) onProgress({ message: '正在传输备份…', received: offset, total: encoded.length });
            }
            reply = await rpcReply('transfer_invoke', { id: upload.id, method: name }, onProgress);
        } finally {
            await methods.transfer_release(JSON.stringify({ id: upload.id })).catch(function() {});
        }
    } else {
        reply = await rpcReply(name, payload, onProgress);
    }
    var result = reply.result;
    if (result && result.jobId) {
        var deadline = Date.now() + (name === 'core_update' || name === 'resource_update' ? 1000000 : 220000);
        while (Date.now() < deadline) {
            var polledAt = Date.now();
            var job = await call('job', { id: result.jobId, waitMs: 600 }, onProgress);
            if (onProgress && job.progress) onProgress(job.progress);
            if (job.done) {
                if (!job.ok) throw new Error(job.error);
                return job.result;
            }
            // Avoid busy polling against an older backend without waitMs.
            var interval = Number.isInteger(job.pollAfterMs) ? Math.max(100, Math.min(750, job.pollAfterMs)) : 250;
            if (Date.now() - polledAt < interval) await new Promise(function(resolve) { window.setTimeout(resolve, interval - (Date.now() - polledAt)); });
        }
        throw new Error('任务仍未完成，请重新载入并查看日志');
    }
    return result;
}

function uploadFile(file, path, onProgress, control) {
    return new Promise(function(resolve, reject) {
        var xhr = new XMLHttpRequest(), data = new FormData();
        data.append('sessionid', L.env.sessionid); data.append('filename', path);
        data.append('filemode', '0600'); data.append('filedata', file);
        xhr.open('POST', L.env.cgi_base + '/cgi-upload'); xhr.timeout = 120000;
        control.abort = function() { xhr.abort(); };
        xhr.upload.onprogress = function(event) {
            if (!control.cancelled && onProgress) onProgress({ message: '正在上传备份…', received: event.loaded, total: event.total });
        };
        xhr.onload = function() {
            control.abort = null;
            try {
                var reply = JSON.parse(xhr.responseText);
                if (xhr.status !== 200 || reply.failure || reply.size !== file.size) throw new Error('文件上传未完成');
                resolve();
            } catch (err) { reject(err); }
        };
        xhr.onerror = xhr.ontimeout = xhr.onabort = function() { control.abort = null; reject(new Error('文件上传连接中断')); };
        if (control.cancelled) reject(new Error('上传已取消'));
        else xhr.send(data);
    });
}

async function checkBackup(file, revision, onProgress, control) {
    function active() { if (control.cancelled) throw new Error('上传已取消'); }
    var upload = (await rpcReply('transfer_begin', { size: file.size, fileUpload: true })).result;
    var fallback = !upload.uploadPath;
    try {
        active();
        if (!fallback) {
            try { await uploadFile(file, upload.uploadPath, onProgress, control); }
            catch (err) {
                active();
                // An upload can finish before its HTTP acknowledgement is lost.
                // Confirm its size before deciding to send the file again.
                var status = await call('transfer_status', { id: upload.id }).catch(function() { return {}; });
                fallback = !status.complete;
            }
            active();
            if (!fallback) {
                onProgress({ message: '正在检查备份…' });
                return await call('import_preview', { upload: upload.id, stage: true, revision: revision }, onProgress);
            }
        }
    } finally { await methods.transfer_release(JSON.stringify({ id: upload.id })).catch(function() {}); }
    active(); onProgress({ message: '正在重试上传…' });
    var raw = JSON.parse(await file.text()); active();
    return await call('import_preview', { data: raw, stage: true, revision: revision }, function(progress) { active(); onProgress(progress); });
}

function notify(message, error) {
    ui.addNotification(null, E('p', {}, message), error ? 'error' : 'info');
}

function button(label, action, primary, attrs) {
    // LuCI's E() stringifies attribute values, and a present "disabled"
    // attribute disables the element even when set to "false" — only add it
    // when the button should actually be disabled.
    var opts = Object.assign({}, attrs);
    var isDisabled = !!opts.disabled;
    delete opts.disabled;
    return E('button', Object.assign({ type: 'button', 'class': 'cbi-button ' + (primary ? 'cbi-button-action important' : 'cbi-button-neutral'),
        click: async function(event) {
            var target = event.currentTarget;
            target.disabled = true;
            target.setAttribute('aria-busy', 'true');
            try { await action(); } catch (err) { notify(err.message, true); }
            finally { target.disabled = false; target.removeAttribute('aria-busy'); }
        }
    }, opts, isDisabled ? { disabled: 'disabled' } : null), label);
}

function field(label, value, options) {
    options = options || {};
    var ident = 'fii-' + Math.random().toString(36).slice(2);
    var attrs = { id: ident, 'class': options.code ? 'fii-code' : '' };
    if (options.required) attrs.required = 'required';
    var control;
    if (options.select) {
        control = E('select', attrs, options.select.map(function(pair) {
            return E('option', { value: pair[0] }, pair[1]);
        }));
        control.value = value;
    } else if (options.area) {
        control = E('textarea', Object.assign(attrs, { rows: options.rows || 8, spellcheck: 'false' }), value || '');
    } else {
        control = E('input', Object.assign(attrs, { type: options.type || 'text', value: value == null ? '' : value,
            min: options.min, max: options.max, autocomplete: 'off', spellcheck: 'false' }));
    }
    return { node: E('div', { 'class': 'fii-field' }, [E('label', { 'for': ident }, label), control,
        options.help ? E('small', {}, options.help) : '']),
        read: function() { return options.type === 'number' ? Number(control.value) : control.value; }, control: control };
}

function toggle(label, value, help) {
    var input = E('input', { type: 'checkbox' });
    input.checked = !!value;
    return { node: E('div', { 'class': 'fii-field fii-check' }, [E('label', {}, [input, E('span', {}, label)]),
        help ? E('small', {}, help) : '']), read: function() { return input.checked; }, control: input };
}

function badge(label, tone) { return E('span', { 'class': 'fii-badge ' + (tone || '') }, label); }
function actions(children) { return E('div', { 'class': 'fii-actions' }, children); }
function empty(title, body, action) {
    return E('div', { 'class': 'fii-empty' }, [E('h3', {}, title), E('p', {}, body), action || '']);
}
function bytes(value) {
    if (value == null) return '未知';
    var unit = ['B', 'KiB', 'MiB', 'GiB', 'TiB'], i = 0;
    while (value >= 1024 && i < 4) { value /= 1024; i++; }
    return value.toFixed(i ? 1 : 0) + ' ' + unit[i];
}
function date(value) { return value ? new Date(value * 1000).toLocaleString() : '尚未刷新'; }
function isExhausted(source) {
    return source.totalBytes > 0 && (source.uploadBytes != null || source.downloadBytes != null) &&
        (source.uploadBytes || 0) + (source.downloadBytes || 0) >= source.totalBytes;
}
function download(name, content, mime) {
    var url = URL.createObjectURL(new Blob([content], { type: mime || 'application/json' }));
    var a = E('a', { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(function() { URL.revokeObjectURL(url); }, 5000);
}

function effectiveOverrides(items, selected) {
    var ordered = items.filter(function(item) { return item.global; }).concat(selected.map(function(id) {
        return items.find(function(item) { return item.id === id; });
    }).filter(Boolean)), used = {};
    return ordered.filter(function(item) {
        if (used[item.id] || item.enabled === false) return false;
        used[item.id] = true; return true;
    });
}

function executionList(items) {
    return items.length ? E('ol', { 'class': 'fii-execution', 'aria-label': '覆写执行顺序' }, items.map(function(item) {
        return E('li', { 'data-id': item.id }, [E('span', {}, item.name), badge(item.global ? '全局' : '专属'), badge(item.type)]);
    })) : E('p', { 'class': 'fii-muted' }, '没有启用的覆写');
}

function choices(items, initial, overrideMode) {
    var selected = initial.filter(function(id) { return !overrideMode || !items.some(function(item) { return item.id === id && item.global; }); });
    var host = E('div', { 'class': 'fii-choices' });
    function draw() {
        var selectable = overrideMode ? items.filter(function(item) { return !item.global; }) : items;
        var ordered = selected.map(function(id) { return items.find(function(item) { return item.id === id; }); }).filter(Boolean)
            .concat(selectable.filter(function(item) { return selected.indexOf(item.id) < 0; }));
        host.replaceChildren.apply(host, ordered.map(function(item) {
            var index = selected.indexOf(item.id);
            var checkbox = E('input', { type: 'checkbox', change: function() {
                if (checkbox.checked) selected.push(item.id); else selected = selected.filter(function(id) { return id !== item.id; });
                draw();
            } });
            checkbox.checked = index >= 0;
            return E('div', { 'class': 'fii-choice' }, [E('label', {}, [checkbox,
                E('span', {}, (index >= 0 ? (index + 1) + '. ' : '') + item.name + (item.enabled === false ? ' · 已禁用，不执行' : ''))]),
                index >= 0 ? actions([
                    button('上移', function() { var tmp = selected[index - 1]; selected[index - 1] = item.id; selected[index] = tmp; draw(); }, false, { disabled: index === 0, 'aria-label': '上移 ' + item.name }),
                    button('下移', function() { var tmp = selected[index + 1]; selected[index + 1] = item.id; selected[index] = tmp; draw(); }, false, { disabled: index === selected.length - 1, 'aria-label': '下移 ' + item.name })
                ]) : '' ]);
        }));
        if (overrideMode) {
            var globals = items.filter(function(item) { return item.global; });
            var automatic = E('div', { 'class': 'fii-global-choices' }, globals.map(function(item) {
                var checked = E('input', { type: 'checkbox', disabled: 'disabled' });
                checked.checked = item.enabled !== false;
                return E('div', { 'class': 'fii-choice' }, [E('label', {}, [checked, E('span', {}, item.name)]),
                    badge(item.enabled === false ? '全局 · 已禁用' : '全局 · 自动生效', item.enabled === false ? '' : 'success')]);
            }));
            host.prepend(E('h4', {}, '全局覆写（自动生效）'), globals.length ? automatic : E('p', { 'class': 'fii-muted' }, '暂无全局覆写'),
                E('h4', {}, '专属覆写（勾选后按顺序执行）'));
            host.append(E('h4', {}, '实际执行顺序'), executionList(effectiveOverrides(items, selected)));
        }
        if (!items.length) host.appendChild(E('p', { 'class': 'fii-muted' }, '暂无可选项'));
    }
    draw();
    return { node: host, read: function() { return selected.slice(); } };
}

function selection(label, items, initial, help) {
    var selected = initial.slice(), host = E('div', { 'class': 'fii-device-options' });
    var options = items.slice();
    initial.forEach(function(id) {
        if (!options.some(function(item) { return item.id === id; })) options.push({ id: id, name: id + ' · 当前未发现' });
    });
    options.forEach(function(item) {
        var input = E('input', { type: 'checkbox', change: function() {
            selected = selected.filter(function(id) { return id !== item.id; });
            if (input.checked) selected.push(item.id);
        } });
        input.checked = selected.indexOf(item.id) >= 0;
        host.appendChild(E('label', {}, [input, E('span', {}, item.name)]));
    });
    if (!options.length) host.appendChild(E('p', { 'class': 'fii-muted' }, '暂无可选项'));
    return { node: E('fieldset', { 'class': 'fii-selection' }, [E('legend', {}, label), host, help ? E('small', {}, help) : '']),
        read: function() { return selected.slice(); } };
}

function entries(label, initial, placeholder, suggestions, formatValue) {
    var values = initial.slice(), list = E('div', { 'class': 'fii-entry-list' });
    var draft = field('添加' + label, '', {});
    draft.control.placeholder = placeholder || '';
    function key(value) {
        value = value.trim();
        return /^(?:[\da-f]{2}:){5}[\da-f]{2}$/i.test(value) ? value.toLowerCase() : value;
    }
    function unique(items) {
        var seen = new Set();
        return items.filter(function(value) {
            var id = key(value);
            if (seen.has(id)) return false;
            seen.add(id); return true;
        });
    }
    function contains(value) { return values.some(function(item) { return key(item) === key(value); }); }
    function draw() {
        list.replaceChildren.apply(list, values.map(function(value) {
            var suggestion = (suggestions || []).find(function(item) { return key(item[0]) === key(value); });
            var display = formatValue ? formatValue(value) : suggestion ? suggestion[1] : value;
            return E('div', { 'class': 'fii-entry' }, [E('span', { title: value }, display), button('移除', function() {
                values = values.filter(function(v) { return v !== value; }); draw();
            }, false, { 'aria-label': '移除 ' + value })]);
        }));
        if (pick) {
            var seen = new Set();
            var available = suggestions.filter(function(item) {
                var id = key(item[0]);
                if (contains(item[0]) || seen.has(id)) return false;
                seen.add(id); return true;
            });
            pick.control.replaceChildren.apply(pick.control, [E('option', { value: '' }, available.length ? '从列表选择…' : '所有候选项均已添加')].concat(
                available.map(function(item) { return E('option', { value: item[0] }, item[1]); })));
            pick.control.value = '';
            pick.control.disabled = !available.length;
        }
    }
    function add() {
        var value = draft.read().trim();
        if (value && !contains(value)) values.push(value);
        draft.control.value = ''; draw(); draft.control.focus();
    }
    draft.control.addEventListener('keydown', function(event) { if (event.key === 'Enter') { event.preventDefault(); add(); } });
    var pick = suggestions && suggestions.length ? field('选择' + label, '', { select: [['', '从列表选择…']].concat(suggestions) }) : null;
    if (pick) pick.control.addEventListener('change', function() {
        if (pick.read() && !contains(pick.read())) values.push(pick.read());
        pick.control.value = ''; draw();
    });
    draw();
    return { node: E('fieldset', { 'class': 'fii-selection fii-entries' }, [E('legend', {}, label), list,
        pick ? pick.node : '', E('div', { 'class': 'fii-entry-add' }, [draft.node, button('添加', add)])]),
        read: function() { return unique(values.concat(draft.read().trim() ? [draft.read().trim()] : [])); } };
}

function legacyAccess(s, scope) {
    if (s[scope + 'Rules'] != null) return s[scope + 'Rules'];
    var allow = { name: '默认规则', enabled: true, proxy: true, dns: true };
    if (scope === 'router') return (s.routerBypassUsers.length ? [{ name: '系统用户直连', enabled: true, proxy: false, dns: false, users: s.routerBypassUsers }] : []).concat([allow]);
    if (s.lanAccessMode === 'all') return [allow];
    var rule = { name: '选定设备', enabled: true, proxy: s.lanAccessMode === 'include', dns: s.lanAccessMode === 'include',
        ips: s.lanAccess.filter(function(v) { return !/^(?:[\da-f]{2}:){5}[\da-f]{2}$/i.test(v); }),
        macs: s.lanAccess.filter(function(v) { return /^(?:[\da-f]{2}:){5}[\da-f]{2}$/i.test(v); }) };
    return [rule].concat(s.lanAccessMode === 'exclude' ? [allow] : []);
}

function accessEditor(scope, initial, network) {
    var values = JSON.parse(JSON.stringify(initial)), host = E('div', { 'class': 'fii-acl-list' });
    var keys = scope === 'lan' ? ['ips', 'macs'] : ['users', 'groups', 'cgroups'];
    function edit(index) {
        var rule = index == null ? { name: '', enabled: true, proxy: true, dns: true } : values[index];
        var name = field('规则名称', rule.name || '');
        var enabled = toggle('启用规则', rule.enabled), proxy = toggle('代理流量', rule.proxy), dns = toggle('接管 DNS', rule.dns);
        var controls = {};
        if (scope === 'lan') {
            controls.ips = entries('IP / IPv6 / 网段', rule.ips || [], '192.168.1.100 或 2001:db8::/64', (network.hosts || []).map(function(h) { return [h.ip, (h.name || h.mac) + ' · ' + h.ip]; }));
            controls.macs = entries('MAC 地址', rule.macs || [], 'AA:BB:CC:DD:EE:FF', (network.hosts || []).map(function(h) { return [h.mac, (h.name || h.ip) + ' · ' + h.mac]; }));
        } else {
            controls.users = selection('系统用户', (network.users || []).map(function(u) { return { id: u.id, name: u.name + ' · UID ' + u.id }; }), rule.users || []);
            controls.groups = selection('系统用户组', (network.groups || []).map(function(u) { return { id: u.id, name: u.name + ' · GID ' + u.id }; }), rule.groups || []);
            controls.cgroups = entries('CGroup', rule.cgroups || [], 'services/dnsmasq', (network.cgroups || []).map(function(g) { return [g.id, g.name]; }));
        }
        ui.showModal(index == null ? '添加访问规则' : '编辑访问规则', [name.node, E('div', { 'class': 'fii-settings-grid' }, [enabled.node, proxy.node, dns.node]),
            E('p', { 'class': 'fii-muted' }, '条件为空匹配全部；任一条件命中即可匹配。')].concat(
            keys.map(function(k) { return controls[k].node; })).concat([actions([button('取消', function() { ui.hideModal(); }), button('确定', function() {
                var next = { name: name.read().trim(), enabled: enabled.read(), proxy: proxy.read(), dns: dns.read() };
                keys.forEach(function(k) { next[k] = controls[k].read(); });
                if (index == null) values.push(next); else values[index] = next;
                draw(); ui.hideModal();
            }, true)])]), 'fii-modal');
    }
    function draw() {
        host.replaceChildren.apply(host, values.map(function(rule, index) {
            var conditions = keys.reduce(function(all, k) {
                return all.concat((rule[k] || []).map(function(value) {
                    if (k === 'users' || k === 'groups') {
                        var item = (network[k] || []).find(function(v) { return v.id === value; });
                        return (k === 'users' ? '用户 ' : '用户组 ') + (item ? item.name : value);
                    }
                    return k === 'cgroups' ? 'CGroup ' + value : value;
                }));
            }, []);
            var enabled = toggle('启用 ' + (rule.name || '规则 ' + (index + 1)), rule.enabled);
            enabled.control.addEventListener('change', function() { rule.enabled = enabled.read(); });
            return E('article', { 'class': 'fii-acl-rule' }, [E('div', { 'class': 'fii-card-heading' }, [enabled.node,
                E('div', {}, [badge(rule.proxy ? '代理' : '直连', rule.proxy ? 'success' : ''), ' ', badge(rule.dns ? '接管 DNS' : '原始 DNS')])]),
                E('p', { 'class': 'fii-muted' }, conditions.length ? conditions.join(' / ') : (scope === 'lan' ? '所有设备' : '所有本机进程')),
                actions([button('编辑', function() { edit(index); }),
                    button('上移', function() { var v = values.splice(index, 1)[0]; values.splice(index - 1, 0, v); draw(); }, false, { disabled: index === 0, 'aria-label': '上移规则 ' + (index + 1) }),
                    button('下移', function() { var v = values.splice(index, 1)[0]; values.splice(index + 1, 0, v); draw(); }, false, { disabled: index === values.length - 1, 'aria-label': '下移规则 ' + (index + 1) }),
                    button('删除', function() { values.splice(index, 1); draw(); })])]);
        }));
        if (!values.length) host.appendChild(E('p', { 'class': 'fii-muted' }, '没有访问规则，流量保持原有路径。'));
    }
    draw();
    return { node: E('div', {}, [E('p', { 'class': 'fii-muted' }, '从上到下匹配，首条命中生效。代理与 DNS 可分别控制。'), host,
        actions([button('添加规则', function() { edit(null); })])]), read: function() { return JSON.parse(JSON.stringify(values)); } };
}

// Log parsing is deliberately independent from rendering and never interprets HTML.
function parseLogSnapshot(snapshot) {
    var records = [], occurrences = Object.create(null);
    function append(raw, preparation) {
        var clean = raw.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, ''), message = clean, time = '', tag = '', severity = '';
        var syslog = clean.match(/^(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})\s+\S+\.([a-z]+)\s+([^:]+):\s?(.*)$/i);
        if (syslog) { time = syslog[1]; severity = syslog[2]; tag = syslog[3]; message = syslog[4]; }
        var embedded = message.match(/\blevel=(?:"|')?(debug|info|warn(?:ing)?|error|fatal|panic|silent)\b/i);
        var core = !preparation && (/^(?:mihomo|core-run)(?:\[|$)/i.test(tag) || !!embedded);
        if (embedded) {
            severity = embedded[1];
            var stamp = message.match(/\btime="([^"]+)"/); if (stamp) time = stamp[1];
            var msg = message.match(/\bmsg=("(?:\\.|[^"\\])*"|.*)$/);
            if (msg) { try { message = JSON.parse(msg[1]); } catch (_) { message = msg[1]; } }
        } else if (/^\s*\{/.test(message)) {
            try {
                var json = JSON.parse(message);
                if (typeof json.level === 'string' && typeof json.msg === 'string') {
                    core = !preparation; severity = json.level; message = json.msg;
                    if (typeof json.time === 'string') time = json.time;
                }
            } catch (_) {}
        }
        var level = /^(err(?:or)?|crit|alert|emerg|fatal|panic)$/i.test(severity) ? 'error' : /^warn(?:ing)?$/i.test(severity) ? 'warning' : /^debug$/i.test(severity) ? 'debug' : 'info';
        var source = core ? 'core' : 'app', key = source + '\n' + raw;
        occurrences[key] = (occurrences[key] || 0) + 1;
        records.push({ key: key + '\n' + occurrences[key], raw: raw, time: time || (preparation ? '最近准备' : '—'),
            level: level, source: source, message: message, tag: tag || (preparation ? 'fii-prepare' : 'fii') });
    }
    // The last prepare command is a separate diagnostic snapshot, not a new event.
    (snapshot.prepareText || '').split(/\r?\n/).filter(Boolean).slice(-128).forEach(function(line) { append(line, true); });
    (snapshot.text || '').split(/\r?\n/).filter(Boolean).slice(-500).forEach(function(line) { append(line, false); });
    return records;
}

return view.extend({
    handleSaveApply: null,
    handleSave: null,
    handleReset: null,

    load: function() { return Promise.all([call('get', { compact: true }), call('status'), call('network').catch(function(err) { return { error: err.message }; }), loadStyle()]); },

    render: function(result) {
        this.data = result[0].data;
        this.status = result[1];
        this.network = result[2] || {};
        this.tab = ['overview', 'sources', 'mihomo', 'overrides', 'outputs', 'settings', 'proxies', 'logs'].indexOf(location.hash.slice(1)) >= 0 ? location.hash.slice(1) : 'overview';
        var self = this;
        this.stateNode = E('span');
        this.body = E('div', { 'class': 'fii-body' });
        this.nav = E('nav', { 'class': 'fii-tabs', 'aria-label': 'FII 管理' });
        this.root = E('div', { 'class': 'fii' }, [
            E('header', { 'class': 'fii-header' }, [E('div', {}, [E('p', { 'class': 'fii-eyebrow' }, 'MIHOMO FOR OPENWRT'),
                E('h2', {}, 'FII'), E('p', { 'class': 'fii-muted' }, '订阅、覆写、输出。让配置回归简单。')]),
                E('div', { 'class': 'fii-header-actions' }, [this.stateNode, button('重新载入', function() { return self.reload(); })])]),
            this.nav, this.body
        ]);
        // Some themes (iStoreOS Argon) hang a decorative strip (header::after)
        // below their top bar that overlays the first rows of page content.
        // Measure it once the view is mounted and pad past it; themes without
        // such a strip leave the view untouched.
        requestAnimationFrame(function() {
            if (!self.root.isConnected) return;
            var bar = document.querySelector('header.bg-primary');
            if (!bar) return;
            var deco = getComputedStyle(bar, '::after');
            if (deco.content === 'none' || deco.content === 'normal') return;
            var strip = parseFloat(deco.height) || 0;
            var overlap = bar.getBoundingClientRect().bottom + strip - self.root.getBoundingClientRect().top;
            if (overlap > 4) self.root.style.paddingTop = Math.ceil(overlap + 4) + 'px';
        });
        this.draw();
        poll.add(async function() {
            try { self.status = await call('status'); self.drawStatus(); }
            catch (err) { self.stateNode.replaceChildren(badge('连接中断', 'warning')); }
        }, 5);
        return this.root;
    },

    drawStatus: function() {
        this.stateNode.replaceChildren(badge(this.status.running ? 'Mihomo 运行中' : 'Mihomo 已停止', this.status.running ? 'success' : ''));
        var stamp = JSON.stringify(this.status) + this.data.revision;
        if (this.liveStatus && this.runtimeStamp !== stamp) {
            this.liveStatus.replaceChildren(this.runtimeCard()); this.runtimeStamp = stamp;
        }
        var groupsStamp = JSON.stringify([this.status.running, this.status.activeOutputId, this.status.appliedRevision]);
        if (this.refreshProxyGroups && this.proxyGroupsStamp !== groupsStamp) {
            this.proxyGroupsStamp = groupsStamp; this.refreshProxyGroups();
        }
        if (this.refreshResources && this.resourcesStamp !== groupsStamp) {
            this.resourcesStamp = groupsStamp; this.refreshResources();
        }
    },

    reload: async function() {
        var results = await this.load();
        this.data = results[0].data; this.status = results[1]; this.network = results[2]; this.draw();
    },

    draw: function() {
        var self = this;
        console.log('[draw]', this.tab, new Error().stack.split('\n').slice(1, 4).join(' | '));
        if (this.disposeProxyGroups) this.disposeProxyGroups();
        if (this.disposeLogs) this.disposeLogs();
        if (this.disposeResources) this.disposeResources();
        this.disposeResources = null;
        this.refreshResources = null;
        this.disposeLogs = null;
        this.disposeProxyGroups = null;
        this.liveStatus = null;
        this.runtimeStamp = null;
        this.refreshProxyGroups = null;
        this.nav.replaceChildren.apply(this.nav, [
            ['overview', '概览'], ['sources', '订阅源'], ['mihomo', 'Mihomo'], ['overrides', '覆写'], ['outputs', '输出配置'], ['settings', '路由器设置'], ['proxies', '控制面板'], ['logs', '日志']
        ].map(function(tab) {
            return button(tab[1], function() { self.tab = tab[0]; history.replaceState(null, '', '#' + tab[0]); self.draw(); }, false,
                { 'class': 'fii-tab ' + (self.tab === tab[0] ? 'active' : ''), 'aria-current': self.tab === tab[0] ? 'page' : null });
        }));
        this.body.replaceChildren(this[this.tab + 'Page']()); this.drawStatus();
    },

    save: async function(patch) {
        var result = await call('save', { patch: patch, compact: true, revision: this.data.revision });
        this.data = result.data; this.draw(); notify(result.message);
    },

    put: async function(kind, item, original) {
        var existing = this.data[kind].find(function(v) { return v.id === item.id; });
        if (!existing) return this.save({ kind: kind, item: item });
        var before = original || existing, fields = {};
        Object.keys(item).forEach(function(key) {
            if (item[key] !== before[key] && JSON.stringify(item[key]) !== JSON.stringify(before[key])) fields[key] = item[key];
        });
        await this.save({ kind: kind, id: item.id, fields: fields });
    },

    remove: function(kind, item) {
        var self = this;
        ui.showModal('删除“' + item.name + '”', [E('p', {}, '将移除这个配置项及它在输出中的关联。正在运行的配置保留到下次应用。'), actions([
            button('取消', function() { ui.hideModal(); }),
            button('删除', async function() {
                await self.save({ kind: kind, remove: item.id }); ui.hideModal();
            }, false, { 'class': 'cbi-button cbi-button-negative' })
        ])]);
    },

    runtimeCard: function() {
        var self = this, s = this.status;
        var selected = this.data.outputs.find(function(item) { return item.id === s.activeOutputId; });
        return E('section', { 'class': 'fii-card' }, [E('div', { 'class': 'fii-card-heading' }, [E('h3', {}, '服务与运行配置'), badge(s.running ? '运行中' : '已停止', s.running ? 'success' : '')]),
            E('p', { 'class': 'fii-active-name' }, s.activeName || '还没有应用配置'),
            E('p', { 'class': 'fii-muted' }, s.activeOutputId ? '配置生成于 ' + date(s.appliedAt) : '在输出页生成运行配置后，可在这里手动启动内核。'),
            s.activeOutputId && s.appliedRevision !== this.data.revision ? E('p', { 'class': 'fii-notice' }, '配置库有更新，请点击“更新运行配置”。内核停止时会保持停止。') : '',
            E('p', { 'class': 'fii-muted' }, s.mode === 'core' || s.proxyEnabled === false ? '仅运行内核' : s.mode ? [s.lanProxy !== false ? '局域网 · ' + s.interfaces.join(', ') : '', s.routerProxy ? '路由器本机' : '',
                s.protocols ? 'TCP ' + s.protocols.tcp.toUpperCase() + ' · UDP ' + s.protocols.udp.toUpperCase() : s.mode.toUpperCase()].filter(Boolean).join(' / ') : ''),
            actions([button('更新运行配置', function() { return self.updateRuntime(s.activeOutputId); }, false, { disabled: !selected || selected.enabled === false || !s.coreInstalled }),
                button(s.running ? '重启服务' : '启动服务', async function() { var r = await call('service', { action: s.running ? 'restart' : 'start' }); notify(r.message); await self.reload(); }, false, { disabled: !s.activeOutputId || !s.coreInstalled }),
                button('停止服务', async function() { var r = await call('service', { action: 'stop' }); notify(r.message); await self.reload(); }, false, { disabled: !s.running }),
                button('运行日志', function() { return self.logs(); })])
        ]);
    },

    updateRuntime: async function(id) {
        var result = await call('apply', { id: id });
        notify(result.message + (result.warnings.length ? '；' + result.warnings.join('；') : ''));
        await this.reload();
    },

    overviewPage: function() {
        var self = this;
        this.liveStatus = E('div');
        return E('div', {}, [E('div', { 'class': 'fii-stats' }, [['订阅源', this.data.sources.length], ['覆写', this.data.overrides.length], ['输出配置', this.data.outputs.length]].map(function(v) {
            return E('div', { 'class': 'fii-stat' }, [E('span', {}, v[0]), E('strong', {}, String(v[1]))]);
        })), this.liveStatus,
        E('section', { 'class': 'fii-card' }, [E('h3', {}, 'Mihomo'), E('p', { 'class': 'fii-muted' }, '内核版本：' + (this.status.coreVersion || '尚未安装')),
            actions([button('内核与端口管理', function() { self.tab = 'mihomo'; history.replaceState(null, '', '#mihomo'); self.draw(); })])])
        ]);
    },

    proxiesPage: function() {
        return E('div', { 'class': 'fii-control-panel' }, [this.proxyGroupsPanel(), this.resourcesPanel()]);
    },

    resourcesPanel: function() {
        var self = this, data = null, loading = false, stale = true, serial = 0, disposed = false;
        var task = self.resourceTask || (self.resourceTask = { pending: false, message: '', error: false });
        var info = E('p', { 'class': 'fii-muted', role: 'status', 'aria-live': 'polite' });
        var feedback = E('p', { role: 'status', 'aria-live': 'polite' });
        var geo = E('div', { 'class': 'fii-resource-grid' }), rules = E('div', { 'class': 'fii-resource-list' });
        var refresh = button('刷新资源状态', load, false);
        var updateGeo = E('button', { type: 'button', 'class': 'cbi-button cbi-button-action important',
            disabled: 'disabled', click: function() { update('geo'); } }, '更新 GEO 数据');
        var panel = E('section', { 'class': 'fii-resources', 'aria-label': '外部资源' }, [
            E('div', { 'class': 'fii-resource-heading' }, [E('h3', {}, '外部资源'), refresh]), info,
            E('div', { 'class': 'fii-resource-heading' }, [E('h4', {}, 'GEO 数据'), updateGeo]),
            E('p', { 'class': 'fii-muted' }, '由内核统一更新已启用的 GEOSITE、GEOIP / MMDB 和 ASN 数据。GEOIP 与 MMDB 按 geodata-mode 二选一，未启用的资源会跳过。文件存在不代表当前规则正在使用。'), geo,
            feedback, E('h4', {}, '规则资源 · Ruleset'),
            E('p', { 'class': 'fii-muted' }, '运行时从内核读取已加载的 rule-providers；远程资源可更新，本地文件可重新加载，内联规则通过覆写修改。'), rules
        ]);
        function render() {
            refresh.disabled = loading;
            updateGeo.disabled = loading || stale || !data || !data.running || task.pending;
            updateGeo.setAttribute('aria-busy', String(task.pending && task.key === 'geo'));
            updateGeo.textContent = task.pending && task.key === 'geo' ? '正在更新 GEO…' : '更新 GEO 数据';
            feedback.textContent = task.message;
            feedback.className = task.error ? 'fii-error' : 'fii-muted';
            if (!data) return;
            geo.replaceChildren.apply(geo, data.geo.map(function(item) {
                return E('article', { 'class': 'fii-resource-card' }, [
                    E('div', { 'class': 'fii-resource-heading' }, [E('strong', {}, item.name), badge(item.exists ? '已下载' : '未下载', item.exists ? 'success' : '')]),
                    E('p', {}, item.file),
                    E('p', { 'class': 'fii-muted' }, item.exists ? bytes(item.size) + ' · 文件修改于 ' + date(item.modifiedAt) : '内核需要时下载此数据'),
                    item.url ? E('details', { 'class': 'fii-resource-source' }, [E('summary', {}, '配置来源'), E('span', {}, item.url)]) : E('small', { 'class': 'fii-resource-source' }, '来源：内核默认源')
                ]);
            }));
            rules.replaceChildren();
            if (data.rulesError) {
                rules.appendChild(E('p', { 'class': 'fii-error', role: 'alert' }, '规则资源读取失败：' + data.rulesError));
                return;
            }
            data.rulesets.forEach(function(item) {
                var local = String(item.vehicleType).toLowerCase() === 'file';
                var control = button(task.pending && task.key === 'ruleset:' + item.name ? '正在更新…' : local ? '重新加载' : '更新资源', function() {
                    return update('ruleset', item.name);
                }, false, { disabled: loading || stale || task.pending || !item.canUpdate, 'aria-label': (local ? '重新加载规则资源 ' : '更新规则资源 ') + item.name });
                rules.appendChild(E('article', { 'class': 'fii-resource-row' }, [E('div', { 'class': 'fii-resource-details' }, [
                    E('strong', {}, item.name),
                    E('p', { 'class': 'fii-muted' }, [item.vehicleType || '未知来源', item.behavior || '未知策略', item.format || '未知格式',
                        item.ruleCount == null ? '规则数未知' : item.ruleCount + ' 条规则'].join(' · ')),
                    E('p', { 'class': 'fii-muted' }, item.updatedAt && !/^0001-/.test(item.updatedAt) ? '内核更新时间：' + new Date(item.updatedAt).toLocaleString() : '内核更新时间：未知'),
                    item.url ? E('details', { 'class': 'fii-resource-source' }, [E('summary', {}, '配置来源'), E('span', {}, item.url)]) : ''
                ]), control]));
            });
            if (!data.rulesets.length) rules.appendChild(E('p', { 'class': 'fii-muted' }, data.source === 'none' ? '请先生成运行配置。' : data.running ? '当前内核没有加载规则资源。' : '当前运行配置快照没有规则资源。'));
        }
        async function load() {
            var request = ++serial; loading = true; info.textContent = '正在读取资源…'; render();
            try {
                var result = await call('resources');
                if (disposed || request !== serial) return;
                data = result; stale = false;
                info.textContent = (data.running ? '内核运行中' : data.source === 'none' ? '尚未生成运行配置' : '配置快照 · 内核已停止，更新不可用') +
                    ' · GEOIP 模式：' + (data.geodataMode ? 'GEOIP.dat' : 'MMDB') +
                    ' · GEO 自动更新：' + (data.geoAutoUpdate ? '每 ' + data.geoUpdateInterval + ' 小时' : '关闭');
                info.className = 'fii-muted';
            } catch (err) {
                if (disposed || request !== serial) return;
                stale = true; info.textContent = '资源状态读取失败：' + err.message; info.className = 'fii-error';
            } finally {
                if (!disposed && request === serial) { loading = false; render(); }
            }
        }
        async function update(kind, name) {
            if (task.pending || stale || !data || !data.running) return;
            task.pending = true; task.key = kind + (name == null ? '' : ':' + name);
            task.error = false; task.message = '正在请求内核更新资源…'; render();
            try {
                var result = await call('resource_update', { kind: kind, name: name }, function(progress) {
                    task.message = progress.message; if (!disposed) render();
                });
                task.message = result.message;
            } catch (err) {
                task.error = true; task.message = '更新失败：' + err.message + '；请检查内核日志。' +
                    (kind === 'geo' ? 'GEO 多文件更新失败时，部分数据可能已更新。' : '');
            } finally {
                task.pending = false;
                if (self.refreshResources) await self.refreshResources();
            }
        }
        self.resourcesStamp = JSON.stringify([self.status.running, self.status.activeOutputId, self.status.appliedRevision]);
        self.refreshResources = load;
        self.disposeResources = function() { disposed = true; serial++; };
        load();
        return panel;
    },

    proxyGroupsPanel: function() {
        var self = this, data = null, loading = false, stale = false, serial = 0, cleanups = [], modalHost = null, modalName = null;
        var state = self.proxyUI || (self.proxyUI = { query:'', sort:'config', groups:Object.create(null) });
        var types = { Selector:'手动选择', URLTest:'自动测速', Fallback:'故障转移', LoadBalance:'负载均衡', Relay:'链式代理' };
        function kind(value) { return types[value] || value || '代理'; }
        function canPin(group) { return group.type === 'URLTest' || group.type === 'Fallback'; }
        function currentLabel(group) {
            if (!data.running) return '配置预览';
            if (canPin(group) && group.fixed) return group.fixed === group.now ? '已钉住 · ' + group.fixed : '钉住 ' + group.fixed + ' · 当前 ' + (group.now || '暂无选择');
            return group.now || (group.type === 'LoadBalance' ? '按策略分配' : '暂无选择');
        }
        function groupState(name) { return state.groups[name] || (state.groups[name] = { open:false, pending:false, error:'', scroll:null }); }
        function mobile() { return window.matchMedia('(max-width:700px)').matches; }
        function cleanup() { cleanups.splice(0).forEach(function(dispose) { dispose(); }); }
        function health(node) {
            var type = (node.type || '').toLowerCase();
            if (type === 'direct') return { text:'直连', tone:'neutral' };
            if (type === 'reject' || type === 'rejectdrop' || type === 'reject-drop') return { text:'拦截', tone:'neutral' };
            if (node.alive === false) return { text:'超时', tone:'error' };
            if (Number.isFinite(node.delay) && node.delay > 0) return { text:node.delay + ' ms', tone:node.delay < 150 ? 'fast' : node.delay < 500 ? 'medium' : 'slow' };
            return { text:'—', tone:'neutral' };
        }
        function memberCount(group) { return group.dynamic ? (group.all.length ? group.all.length + ' + 动态' : '动态成员') : String(group.all.length); }
        var count = E('span', { 'class':'fii-proxy-count' }, '');
        var status = E('span', { 'class':'fii-proxy-status', role:'status', 'aria-live':'polite' }, '正在读取…');
        var host = E('div', { 'class':'fii-proxy-groups' });
        var search = E('input', { type:'search', value:state.query, placeholder:'搜索代理组或节点', 'aria-label':'搜索代理组或节点', input:function() {
            state.query = search.value;
            Object.keys(state.groups).forEach(function(name) { state.groups[name].scroll = 0; });
            renderGroups();
        } });
        var sort = E('select', { 'aria-label':'节点排序', change:function() {
            state.sort = sort.value;
            Object.keys(state.groups).forEach(function(name) { state.groups[name].scroll = 0; });
            renderGroups();
        } }, [['config','配置顺序'], ['latency','延迟优先'], ['name','名称顺序']].map(function(pair) { return E('option', { value:pair[0], selected:state.sort === pair[0] ? 'selected' : null }, pair[1]); }));
        var refresh = button('刷新', loadGroups, false, { 'aria-label':'刷新代理组' });
        var collapse = button('全部收起', function() {
            if (!data) return;
            data.groups.forEach(function(group) { groupState(group.name).open = false; });
            state.query = ''; search.value = ''; renderGroups();
        }, false, { 'class':'fii-proxy-collapse', 'aria-label':'收起所有代理组' });
        var panel = E('section', { 'class':'fii-proxies-panel', 'aria-label':'代理组面板' }, [
            E('div', { 'class':'fii-proxies-heading' }, [E('div', { 'class':'fii-proxies-title' }, [E('h3', {}, '代理组'), count, status]), refresh]),
            E('div', { 'class':'fii-proxy-toolbar' }, [search, sort, collapse]), host]);

        function filteredNodes(group) {
            var query = state.query.trim().toLocaleLowerCase(), groupMatches = group.name.toLocaleLowerCase().indexOf(query) >= 0;
            var names = group.all.filter(function(name) { return groupMatches || name.toLocaleLowerCase().indexOf(query) >= 0; });
            if (state.sort === 'name') names.sort(function(a, b) { return a.localeCompare(b, 'zh-CN', { numeric:true }); });
            if (state.sort === 'latency') names.sort(function(a, b) {
                function delay(name) { var node = data.nodes[name] || {}; return node.alive !== false && node.delay > 0 ? node.delay : Infinity; }
                return delay(a) - delay(b);
            });
            return names;
        }
        function nodeList(group) {
            var gs = groupState(group.name), names = filteredNodes(group), columns = 2, start = -1, rowHeight = 64;
            var feedback = E('p', { 'class':'fii-proxy-feedback' + (gs.error ? ' fii-error' : ''), role:'status', hidden:!gs.pending && !gs.error ? 'hidden' : null }, gs.pending ? '正在更新选择…' : gs.error);
            var viewport = E('div', { 'class':'fii-proxy-viewport', 'aria-label':group.name + '的节点', role:'region', tabindex:'0' });
            var space = E('div', { 'class':'fii-proxy-space' }), grid = E('div', { 'class':'fii-proxy-nodes' });
            space.appendChild(grid); viewport.appendChild(space);
            function drawNodes(force) {
                if (!viewport.isConnected) return;
                var cols = Math.max(1, Math.floor((viewport.clientWidth + 8) / 148));
                var rows = Math.ceil(names.length / cols), height = Math.min(280, rows * rowHeight - 8);
                viewport.style.height = Math.max(0, height) + 'px'; space.style.height = Math.max(0, rows * rowHeight - 8) + 'px';
                var first = Math.max(0, Math.floor(viewport.scrollTop / rowHeight) - 2);
                if (!force && first === start && columns === cols) return;
                start = first; columns = cols;
                var end = Math.min(rows, first + Math.ceil(height / rowHeight) + 4);
                grid.style.gridTemplateColumns = 'repeat(' + cols + ', minmax(0, 1fr))'; grid.style.top = first * rowHeight + 'px';
                grid.replaceChildren.apply(grid, names.slice(first * cols, end * cols).map(function(name) {
                    var node = data.nodes[name] || {}, selected = name === group.now, pinned = canPin(group) && name === group.fixed, latency = health(node);
                    var attrs = { 'class':'fii-proxy-node' + (selected ? ' is-current' : '') + (pinned ? ' is-pinned' : ''), 'data-node':name, title:name };
                    var contents = [E('span', { 'class':'fii-proxy-node-name' }, name),
                        E('span', { 'class':'fii-proxy-node-meta' }, [E('span', { 'class':'fii-proxy-node-type' }, kind(node.type)),
                            pinned ? E('span', { 'class':'fii-proxy-selected' }, '已钉住') : selected ? E('span', { 'class':'fii-proxy-selected' }, '已选') : '',
                            E('span', { 'class':'fii-proxy-delay ' + latency.tone, title:node.testedAt ? '最近检测：' + node.testedAt : '暂无检测记录' }, latency.text)])];
                    if (!data.running || (group.type !== 'Selector' && !canPin(group))) return E('div', attrs, contents);
                    return E('button', Object.assign(attrs, { type:'button', 'aria-label':(canPin(group) ? '钉住节点 ' : '选择节点 ') + name, 'aria-pressed':selected ? 'true' : 'false', disabled:gs.pending || loading || stale ? 'disabled' : null,
                        click:function() { gs.scroll = viewport.scrollTop; return selectNode(group, name); }, keydown:function(event) {
                            var index = names.indexOf(name), next = { ArrowRight:index+1, ArrowLeft:index-1, ArrowDown:index+columns, ArrowUp:index-columns, Home:0, End:names.length-1 }[event.key];
                            if (next == null) return;
                            event.preventDefault(); next = Math.max(0, Math.min(names.length - 1, next));
                            viewport.scrollTop = Math.floor(next / columns) * rowHeight; drawNodes(true);
                            var target = Array.from(grid.querySelectorAll('button[data-node]')).find(function(element) { return element.dataset.node === names[next]; });
                            if (target) target.focus({ preventScroll:true });
                        }
                    }), contents);
                }));
            }
            var frame = requestAnimationFrame(function() {
                if (!viewport.isConnected) return;
                drawNodes(true);
                viewport.scrollTop = gs.scroll == null ? Math.floor(Math.max(0,names.indexOf(group.now)) / columns) * rowHeight : gs.scroll;
                gs.scroll = viewport.scrollTop; drawNodes(false);
            });
            var observer = new ResizeObserver(function() { drawNodes(false); }); observer.observe(viewport);
            viewport.addEventListener('scroll', function() { if (!viewport.isConnected) return; gs.scroll = viewport.scrollTop; drawNodes(false); }, { passive:true });
            cleanups.push(function() { cancelAnimationFrame(frame); observer.disconnect(); });
            var hint = !data.running ? '配置预览 · 内核停止时只读' : group.type === 'Selector' ? '点击节点切换' : canPin(group) ? group.fixed ? '已钉住 · 失效时由内核回退' : '自动选择 · 点击节点钉住' : group.type === 'Relay' ? '按顺序串联' : '按策略分配';
            return E('div', { 'class':'fii-proxy-group-body', 'data-group-body':group.name }, [feedback,
                names.length ? viewport : E('p', { 'class':'fii-proxy-empty' }, state.query && group.all.length ? '没有匹配的节点' : group.dynamic ? '动态节点将在内核运行后显示' : '此组没有配置节点'),
                E('div', { 'class':'fii-proxy-list-footer' }, [E('span', {}, hint), E('span', {}, names.length ? names.length + ' 个节点' : '')]),
                data.running && canPin(group) && group.fixed ? E('div', { 'class':'fii-proxy-pin-actions' }, [button('取消钉住', function() {
                    gs.scroll = viewport.scrollTop; return selectNode(group, null, 'unpin');
                }, false, { disabled:gs.pending || loading || stale, 'aria-label':'取消钉住 ' + group.name })]) : '']);
        }
        function closeModal() {
            var name = modalName; modalName = null; modalHost = null; ui.hideModal(); renderGroups();
            var target = Array.from(host.querySelectorAll('.fii-proxy-group-toggle')).find(function(element) { return element.closest('[data-group]').dataset.group === name; });
            if (target) target.focus({ preventScroll:true });
        }
        function showGroup(group) {
            modalName = group.name;
            groupState(group.name).scroll = null;
            modalHost = E('div', { 'class':'fii-proxy-modal-content' });
            ui.showModal(group.name, [modalHost, actions([button('关闭', closeModal)])], 'fii-modal', 'fii-proxy-modal');
            renderGroups();
        }
        async function selectNode(group, name, action) {
            var gs = groupState(group.name);
            if (gs.pending || loading || stale || !data.running || (group.type !== 'Selector' && !canPin(group))) return;
            if (action === 'unpin' ? !canPin(group) || !group.fixed : group.type === 'Selector' ? name === group.now : name === group.fixed) return;
            gs.pending = true; gs.error = ''; renderGroups();
            try { await call('select', action === 'unpin' ? { group:group.name, action:'unpin' } : { group:group.name, name:name }); await loadGroups(); }
            catch (err) { gs.error = err.message; }
            finally {
                gs.pending = false;
                if (!panel.isConnected) return;
                renderGroups();
                requestAnimationFrame(function() {
                    var scope = modalHost && modalHost.isConnected ? modalHost : host;
                    var target = Array.from(scope.querySelectorAll('button[data-node]')).find(function(element) { return element.dataset.node === name && element.closest('[data-group-body]').dataset.groupBody === group.name; });
                    if (!target && action === 'unpin') target = Array.from(scope.querySelectorAll('[data-group-body]')).find(function(element) { return element.dataset.groupBody === group.name; });
                    if (target && action === 'unpin') target = target.querySelector('.fii-proxy-viewport');
                    if (target) target.focus({ preventScroll:true });
                });
            }
        }
        function renderGroups() {
            cleanup();
            if (!data) return;
            var query = state.query.trim().toLocaleLowerCase();
            var groups = data.groups.filter(function(group) { return group.name.toLocaleLowerCase().indexOf(query) >= 0 || group.all.some(function(name) { return name.toLocaleLowerCase().indexOf(query) >= 0; }); });
            var columns = [E('div', { 'class':'fii-proxy-column' }), E('div', { 'class':'fii-proxy-column' })];
            groups.forEach(function(group, index) {
                var gs = groupState(group.name), nodeMatches = query && group.name.toLocaleLowerCase().indexOf(query) < 0;
                var open = gs.open || !!nodeMatches, body = E('div', { 'class':'fii-proxy-inline-body' });
                var current = currentLabel(group);
                var preview = E('span', { 'class':'fii-proxy-preview', 'aria-hidden':'true' }, group.all.slice(0,32).map(function(name) {
                    return E('i', { 'class':health(data.nodes[name] || {}).tone + (name === group.now ? ' selected' : '') });
                }));
                if (group.all.length > 32) preview.appendChild(E('small', {}, '+' + (group.all.length - 32)));
                var header = E('button', { type:'button', 'class':'fii-proxy-group-toggle', 'aria-label':'查看代理组 ' + group.name, 'aria-expanded':mobile() ? 'false' : String(open), click:function() {
                    if (mobile()) return showGroup(group);
                    gs.open = !open; renderGroups();
                    var target = Array.from(host.querySelectorAll('.fii-proxy-group-toggle')).find(function(element) { return element.closest('[data-group]').dataset.group === group.name; });
                    if (target) target.focus({ preventScroll:true });
                } }, [E('span', { 'class':'fii-proxy-group-line' }, [E('span', { 'class':'fii-proxy-group-name' }, group.name),
                    E('span', { 'class':'fii-proxy-kind' }, kind(group.type) + ' · ' + memberCount(group)), E('span', { 'class':'fii-proxy-chevron', 'aria-hidden':'true' })]),
                    E('span', { 'class':'fii-proxy-current', title:current }, [E('span', { 'aria-hidden':'true' }, '↳ '), current]), preview]);
                var article = E('article', { 'class':'fii-proxy-group' + (open ? ' is-open' : ''), 'data-group':group.name }, [header, body]);
                if (open && !mobile()) body.appendChild(nodeList(group));
                columns[index % 2].appendChild(article);
            });
            host.replaceChildren.apply(host, columns);
            if (!groups.length) host.appendChild(E('p', { 'class':'fii-proxy-empty' }, data.groups.length ? '没有匹配的代理组或节点' : data.source === 'none' ? '生成运行配置后，这里会展示代理组。' : '当前配置没有代理组。'));
            if (modalHost && modalHost.isConnected) {
                var group = data.groups.find(function(item) { return item.name === modalName; });
                modalHost.replaceChildren();
                if (group) modalHost.append(E('p', { 'class':'fii-proxy-modal-meta' }, kind(group.type) + ' · ' + memberCount(group) + ' · ' + currentLabel(group)), nodeList(group));
                else modalHost.appendChild(E('p', {}, '此代理组已不在当前配置中，请关闭后刷新。'));
            }
        }
        async function loadGroups() {
            var request = ++serial; loading = true; refresh.disabled = true; panel.setAttribute('aria-busy','true');
            status.textContent = '正在读取…'; status.classList.remove('fii-error'); renderGroups();
            try {
                var result = await call('groups');
                if (request !== serial || !panel.isConnected) return;
                data = result; data.nodes = data.nodes || {}; stale = false; count.textContent = data.groups.length + ' 个组';
                status.textContent = data.running ? '运行中 · 最近检测记录' : '配置预览 · 内核已停止';
            } catch (err) {
                if (request !== serial || !panel.isConnected) return;
                stale = true; status.textContent = '读取失败：' + err.message; status.classList.add('fii-error');
                if (!data) count.textContent = '暂不可用';
            } finally {
                if (request === serial) { loading = false; refresh.disabled = false; panel.removeAttribute('aria-busy'); renderGroups(); }
            }
        }
        var media = window.matchMedia('(max-width:700px)');
        media.addEventListener('change', renderGroups);
        function onEscape(event) { if (event.key === 'Escape' && modalHost && modalHost.isConnected) { event.preventDefault(); closeModal(); } }
        document.addEventListener('keydown', onEscape);
        self.disposeProxyGroups = function() { serial++; cleanup(); media.removeEventListener('change', renderGroups); document.removeEventListener('keydown', onEscape); if (modalHost && modalHost.isConnected) ui.hideModal(); };
        self.proxyGroupsStamp = JSON.stringify([self.status.running, self.status.activeOutputId, self.status.appliedRevision]);
        self.refreshProxyGroups = loadGroups;
        loadGroups();
        return panel;
    },

    corePanel: function() {
        var self = this, remote = null, info = null;
        var channel = field('内核渠道', this.status.coreSelectedChannel || 'release', {
            select: [['release', 'Release · 稳定版'], ['alpha', 'Alpha · 开发版']],
            help: '渠道选择会保存。Alpha 按构建提交和文件摘要识别更新。'
        });
        var installed = E('p', { 'class': 'fii-code fii-wrap' }, this.status.coreVersion || '尚未安装内核');
        var installedChannel = E('p', { 'class': 'fii-muted' });
        var latest = E('p', { 'class': 'fii-muted', 'aria-live': 'polite' }, '检查所选渠道后显示可下载的版本。');
        var progress = E('p', { 'class': 'fii-notice', role: 'status', hidden: !this.coreBusy }, this.coreProgress || '');
        var meter = E('progress', { max: 100, value: 0, hidden: true, 'aria-label': '内核下载进度' });
        this.coreProgressNode = progress; this.coreProgressMeter = meter;
        function setBusy(busy) {
            channel.control.disabled = busy;
            checkButton.disabled = busy || !info;
            installButton.disabled = busy || !info || !info.supported;
        }
        var checkButton = button('检查更新', async function() {
            self.coreBusy = true; setBusy(true);
            latest.textContent = '正在检查官方 ' + channel.read().toUpperCase() + ' 发布…';
            try {
                var result = await call('core_check', { channel: channel.read() });
                remote = result.remote;
                latest.textContent = '可下载版本：' + remote.version + ' · ' + bytes(remote.size) + ' · ' +
                    (result.updateAvailable ? '可安装或切换到此版本' : '当前已是此渠道最新版本，可重新安装');
            } catch (err) { latest.textContent = err.message; throw err; }
            finally { self.coreBusy = false; setBusy(false); }
        });
        var installButton = button(this.status.coreInstalled ? '下载 / 更新内核' : '下载并安装内核', async function() {
            self.coreBusy = true; self.coreProgress = '正在检查并下载内核…';
            setBusy(true); progress.hidden = false; progress.textContent = self.coreProgress;
            try {
                var result = await call('core_update', { channel: channel.read(), digest: remote ? remote.digest : null }, function(update) {
                    self.coreProgress = update.message + (update.total ? ' · ' + bytes(update.received) + ' / ' + bytes(update.total) : '');
                    if (self.coreProgressNode) { self.coreProgressNode.hidden = false; self.coreProgressNode.textContent = self.coreProgress; }
                    if (self.coreProgressMeter) {
                        self.coreProgressMeter.hidden = !update.total;
                        self.coreProgressMeter.value = update.total ? update.received * 100 / update.total : 0;
                    }
                });
                notify(result.message);
            } catch (err) { progress.textContent = err.message; throw err; }
            finally {
                self.coreBusy = false;
                await self.reload();
            }
        }, true);
        channel.control.addEventListener('change', async function() {
            channel.control.disabled = true; remote = null;
            latest.textContent = '检查所选渠道后显示可下载的版本。';
            try { await call('core_channel', { channel: channel.read() }); }
            catch (err) { notify(err.message, true); }
            finally { channel.control.disabled = false; }
        });
        setBusy(true);
        call('core_info').then(function(result) {
            info = result;
            channel.control.value = result.selectedChannel;
            installed.textContent = result.versionText || '尚未安装内核，请选择渠道后下载。';
            installedChannel.textContent = result.installed ? '当前渠道：' + (result.channel || '未知') +
                ' · 安装于 ' + date(result.updatedAt) : '应用安装包不包含内核。下载完成后即可应用输出配置。';
            if (!result.supported) latest.textContent = '当前下载器支持 Linux x86_64（Intel J4125）。';
            setBusy(!!self.coreBusy);
        }).catch(function(err) { latest.textContent = err.message; });
        return E('section', { 'class': 'fii-card' }, [E('h3', {}, 'Mihomo 内核管理'), installed, installedChannel,
            channel.node, latest, progress, meter, actions([checkButton, installButton]),
            E('p', { 'class': 'fii-muted' }, '从官方发布下载 amd64-compatible 内核，校验 SHA-256 后安装。运行中的服务会在下载和测试完成后切换，失败时恢复原内核。')]);
    },

    mihomoPage: function() {
        var self = this, s = this.data.settings;
        var ports = {};
        [['mixedPort', '本机混合代理端口'], ['redirPort', 'REDIRECT 端口'], ['tproxyPort', 'TPROXY 端口'], ['dnsPort', 'DNS 端口'], ['controllerPort', '控制器端口']].forEach(function(pair) {
            ports[pair[0]] = field(pair[1], s[pair[0]], { type: 'number', min: 1024, max: 65535, required: true });
        });
        var currentAddress = s.controllerAddress || '127.0.0.1';
        var addressOptions = currentAddress !== '127.0.0.1' && currentAddress !== '0.0.0.0'
            ? [[currentAddress, currentAddress + ' · 自定义']] : [];
        var controllerAddress = field('控制器监听地址', currentAddress, { select: addressOptions.concat([
            ['127.0.0.1', '仅本机（默认）'], ['0.0.0.0', '所有 IPv4 接口 · 局域网面板可达']]),
            help: '面板等工具通过 路由器IP:控制器端口 访问 Mihomo，需携带控制器密钥。' });
        var secret = field('控制器密钥', s.secret, { type: 'password', help: '留空表示控制器不启用认证。监听局域网时建议设置强密钥。' });
        var form = E('form', { 'class': 'fii-settings', submit: function(event) { event.preventDefault(); } });
        var canApply = !!this.status.activeOutputId && this.status.coreInstalled;
        async function saveDefaults(apply) {
            var invalid = Array.from(form.querySelectorAll('input,select')).find(function(el) { return !el.disabled && !el.checkValidity(); });
            if (invalid) { invalid.reportValidity(); return; }
            var settings = { controllerAddress: controllerAddress.read(), secret: secret.read() };
            Object.keys(ports).forEach(function(k) { settings[k] = ports[k].read(); });
            var changed = {};
            Object.keys(settings).forEach(function(key) {
                if (JSON.stringify(settings[key]) !== JSON.stringify(self.data.settings[key])) changed[key] = settings[key];
            });
            if (Object.keys(changed).length) {
                var saved = await call('save', { patch: { settings: changed }, compact: true, revision: self.data.revision });
                self.data = saved.data;
            }
            if (apply) {
                try {
                    var result = await call('apply', { id: self.status.activeOutputId });
                    notify(result.message + ((result.warnings || []).length ? '；' + result.warnings.join('；') : ''));
                } catch (err) { throw new Error('默认值已保存，更新运行配置失败：' + err.message); }
                finally { await self.reload(); }
            } else { self.draw(); notify('Mihomo 默认值已保存，应用后生效'); }
        }
        var saveActions = actions([button('保存默认值', function() { return saveDefaults(false); }),
            button('保存并更新配置', function() { return saveDefaults(true); }, true, { disabled: !canApply })]);
        saveActions.classList.add('fii-settings-save');
        form.appendChild(E('section', { 'class': 'fii-card' }, [E('h3', {}, '连接与端口（默认值）'),
            E('p', { 'class': 'fii-muted' }, '这些数值在生成运行配置时作为默认值写入；覆写中显式配置的字段优先于此处，实际生效值可在输出预览中查看。'),
            E('div', { 'class': 'fii-settings-grid fii-port-grid' }, Object.keys(ports).map(function(k) { return ports[k].node; }).concat(controllerAddress.node)),
            secret.node, saveActions, !canApply ? E('p', { 'class': 'fii-muted' }, '首次使用请先在输出页生成一份运行配置。') : '']));
        return E('div', {}, [this.corePanel(), this.mihomoBasics(), form,
            E('p', { 'class': 'fii-muted' }, 'Mihomo 的 DNS、规则、代理组和通用参数始终在覆写中配置；这里只提供内核与接入默认值。')]);
    },

    mihomoBasics: function() {
        var self = this;
        var override = (this.data.overrides || []).find(function(item) { return item.origin === 'legacy-mihomo-settings'; });
        if (!override) return '';
        var host = E('div');
        function card(content) {
            return E('section', { 'class': 'fii-card' }, [E('div', { 'class': 'fii-card-heading' }, [
                E('h3', {}, 'Mihomo 基础配置'), actions([badge('全局 · 最低优先级', 'success')])]),
                E('p', { 'class': 'fii-muted' }, '由旧版设置迁移而来，作为全局覆写先于其他覆写执行。')].concat(content));
        }
        function render(body) {
            var policy = null, problem = '';
            try { policy = JSON.parse(body || '{}'); }
            catch (err) { problem = err.message; }
            if (!problem && (!policy || typeof policy !== 'object' || Array.isArray(policy))) problem = '内容不是对象';
            if (problem) {
                // An unparsable body stays editable as raw YAML instead of a dead end.
                var raw = field('覆写内容', body, { area: true, rows: 12, code: true });
                host.replaceChildren(card([E('p', { 'class': 'fii-notice' }, '内容无法解析为表单（' + problem + '），以 YAML 文本编辑。'),
                    raw.node, actions([button('保存内容', async function() {
                        self.basicsBody = null;
                        await self.save({ kind: 'overrides', id: override.id, fields: { body: raw.read() } });
                    }, true)])]));
                return;
            }
            var dns = policy.dns && typeof policy.dns === 'object' ? policy.dns : null;
            var tun = policy.tun && typeof policy.tun === 'object' ? policy.tun : null;
            var ipv6 = toggle('IPv6 解析', !!policy.ipv6, '同时作用于 DNS 的 IPv6 解析。');
            var mode = dns && 'enhanced-mode' in dns ? field('DNS 模式', dns['enhanced-mode'], { select: [['fake-ip', 'Fake-IP'], ['redir-host', 'Redir-Host']] }) : null;
            var nameservers = dns && Array.isArray(dns.nameserver) ? entries('DNS 服务器', dns.nameserver, 'https://dns.alidns.com/dns-query') : null;
            var bootstrap = dns && Array.isArray(dns['default-nameserver']) ? entries('引导 DNS（IP）', dns['default-nameserver'], '223.5.5.5') : null;
            var filters = dns && Array.isArray(dns['fake-ip-filter']) ? entries('Fake-IP 过滤', dns['fake-ip-filter'], '+.lan') : null;
            var proxyDns = dns && Array.isArray(dns['proxy-server-nameserver']) ? entries('代理服务器 DNS', dns['proxy-server-nameserver'], 'https://dns.alidns.com/dns-query') : null;
            var stack = tun && 'stack' in tun ? field('TUN 协议栈', tun.stack, { select: [['mixed', 'Mixed（默认）'], ['system', 'System'], ['gvisor', 'GVisor']] }) : null;
            var mtu = tun && typeof tun.mtu === 'number' ? field('TUN MTU', tun.mtu, { type: 'number', min: 1280, max: 9000, required: true }) : null;
            var grid = [];
            if (mode) grid.push(mode.node);
            if (stack) grid.push(stack.node);
            if (mtu) grid.push(mtu.node);
            host.replaceChildren(card([ipv6.node,
                grid.length ? E('div', { 'class': 'fii-settings-grid' }, grid) : '',
                nameservers ? nameservers.node : '', bootstrap ? bootstrap.node : '',
                filters ? filters.node : '', proxyDns ? proxyDns.node : '',
                actions([button('保存基础配置', async function() {
                    policy.ipv6 = ipv6.read();
                    if (dns) dns.ipv6 = ipv6.read();
                    if (mode) dns['enhanced-mode'] = mode.read();
                    if (nameservers) dns.nameserver = nameservers.read();
                    if (bootstrap) dns['default-nameserver'] = bootstrap.read();
                    if (filters) dns['fake-ip-filter'] = filters.read();
                    if (proxyDns) dns['proxy-server-nameserver'] = proxyDns.read();
                    if (stack) tun.stack = stack.read();
                    if (mtu) tun.mtu = parseInt(mtu.read(), 10);
                    self.basicsBody = null;
                    await self.save({ kind: 'overrides', id: override.id, fields: { body: JSON.stringify(policy, null, 2) } });
                }, true)]),
                E('p', { 'class': 'fii-muted' }, '保存后需在输出页应用才生效；未在此展示的字段请通过普通覆写调整。')]));
        }
        if (self.basicsBody != null) {
            // Cached from the first fetch; redraws render synchronously instead of
            // coupling every render to another full-content transfer.
            render(self.basicsBody);
            return host;
        }
        (async function() {
            // The page snapshot omits bodies; the full migrated content is fetched once.
            var full = await self.readItem('overrides', override, false, false);
            self.basicsBody = full && full.body != null ? full.body : '';
            if (!host.isConnected) return;
            render(self.basicsBody);
        })().catch(function(err) {
            if (!host.isConnected) return;
            host.replaceChildren(card([E('p', { 'class': 'fii-notice' }, '读取基础配置失败：' + err.message)]));
        });
        return host;
    },

    sourcesPage: function() {
        var self = this;
        return E('div', {}, [this.pageHeading('订阅源', '一个订阅只维护一次，可用于多个输出。', button('添加订阅源', function() { return self.editSource(); }, true)),
            this.data.sources.length ? E('div', { 'class': 'fii-grid fii-sources-list' }, this.data.sources.map(function(source) {
                var used = (source.uploadBytes || 0) + (source.downloadBytes || 0);
                var exhausted = isExhausted(source);
                return E('article', { 'class': 'fii-card' }, [E('div', { 'class': 'fii-card-heading' }, [E('h3', {}, source.name),
                    badge(source.enabled === false ? '已禁用' : exhausted ? '流量耗尽' : '已启用', exhausted ? 'warning' : source.enabled === false ? '' : 'success')]),
                    E('p', { 'class': 'fii-muted' }, (source.nodeCount != null ? source.nodeCount + ' 个缓存节点 · ' : '') + date(source.lastRefreshAt)),
                    source.totalBytes > 0 ? E('div', {}, [E('p', {}, bytes(used) + ' / ' + bytes(source.totalBytes)), E('progress', { max: source.totalBytes, value: Math.min(used, source.totalBytes), 'aria-label': source.name + '流量使用' })]) : E('p', { 'class': 'fii-muted' }, '未报告有限流量额度'),
                    source.expireAtSeconds ? E('p', { 'class': 'fii-muted' }, '到期 ' + date(source.expireAtSeconds)) : '',
                    exhausted ? E('p', { 'class': 'fii-notice' }, '生成输出时自动排除，额度恢复后自动加入。') : '',
                    source.lastError ? E('p', { 'class': 'fii-error' }, source.lastError) : '',
                    actions([button('刷新', async function() { var result = await call('refresh', { kind: 'sources', id: source.id }); notify(result.message, !result.success); await self.reload(); }, false, { disabled: !source.url }),
                        button('节点', function() { return self.nodes(source); }), button('编辑', function() { return self.editSource(source); }), button('删除', function() { self.remove('sources', source); })])]);
            })) : empty('添加第一个订阅', '支持 Mihomo YAML，以及 SS、VMess、VLESS、Trojan、Hysteria2 常见分享链接。')]);
    },

    pageHeading: function(title, subtitle, action) {
        // The tab bar already names the page, so only the primary action is
        // shown here; title and subtitle parameters are ignored.
        return action ? E('div', { 'class': 'fii-actions fii-page-actions' }, [action]) : '';
    },

    editor: function(title, fields, submit) {
        var form = E('form', { 'class': 'fii-editor', submit: function(event) { event.preventDefault(); } }, fields.map(function(f) { return f.node || f; }));
        var error = E('p', { 'class': 'fii-error', role: 'alert' });
        form.appendChild(error);
        var save = button('保存', async function() {
            if (!form.reportValidity()) return;
            try { await submit(); ui.hideModal(); }
            catch (err) { error.textContent = err.message; error.scrollIntoView({ block: 'nearest' }); throw err; }
        }, true);
        ui.showModal(title, [form, actions([button('取消', function() { ui.hideModal(); }), save])], 'fii-modal');
        window.setTimeout(function() { var input = form.querySelector('input,textarea,select'); if (input) input.focus(); }, 50);
    },

    readItem: async function(kind, item, metadataOnly, editing) {
        if (!item) return item;
        // These fields are already present in the page snapshot. Saving still
        // checks its revision, and opening a cache/script fetches full content.
        if (metadataOnly || kind === 'outputs') {
            var current = this.data[kind].find(function(value) { return value.id === item.id; });
            if (!current) throw new Error('配置项不存在，请重新载入');
            return Object.assign({}, current);
        }
        var result = await call('get', { kind: kind, id: item.id, sync: true, metadataOnly: !!metadataOnly });
        if (editing && result.revision !== this.data.revision) throw new Error('编辑期间配置已更新，请重新打开编辑窗口以读取最新内容');
        this.data = result.data; this.draw();
        return result.item;
    },

    editSource: async function(source) {
        if (source) {
            var pending = E('p', { role: 'status' }, '正在读取订阅…');
            ui.showModal('编辑订阅源', [pending, actions([button('取消', function() { ui.hideModal(); })])], 'fii-modal');
            try { source = await this.readItem('sources', source, true); }
            catch (err) { if (pending.isConnected) { pending.textContent = err.message; pending.setAttribute('role', 'alert'); } return; }
            if (!pending.isConnected || !document.body.classList.contains('modal-overlay-active')) return;
        }
        var self = this, item = Object.assign({ id: 's_' + Date.now().toString(36), name: '', url: '', enabled: true, autoRefresh: !source, refreshMinutes: 720 }, source || {});
        var original = Object.assign({}, item);
        var contentLoaded = !item.contentOmitted, loading = false;
        var name = field('名称', item.name, { required: true });
        var url = field('订阅 URL', item.url, { type: 'url', help: 'URL 和本地内容至少填写一项。远程下载失败时保留上次成功缓存。' });
        var enabled = toggle('启用订阅', item.enabled);
        var prefix = field('节点前缀', item.prefix || '');
        var include = field('保留正则', item.includeRegex || '', { help: '为空时保留所有节点。筛选发生在添加前缀之前。' });
        var exclude = field('排除正则', item.excludeRegex || '');
        var agent = field('User-Agent', item.userAgent || '', { help: '留空使用路由器设置中的全局值。' });
        var auto = toggle('自动刷新', item.autoRefresh);
        var interval = field('刷新间隔（分钟）', item.refreshMinutes, { type: 'number', min: 5, max: 525600, required: true });
        var body = field('本地内容 / 上次成功缓存', item.cachedYaml || '', { area: true, code: true, rows: 8, help: '粘贴 Mihomo YAML 或分享链接。只提供 URL 时，保存后点击刷新。' });
        var contentStatus = E('p', { role: 'status', 'class': 'fii-muted' }, '');
        var content = E('details', {}, [E('summary', {}, '本地内容 / 上次成功缓存'), contentStatus, body.node]);
        body.control.disabled = !contentLoaded;
        content.addEventListener('toggle', async function() {
            if (!content.open || contentLoaded || loading) return;
            loading = true; contentStatus.textContent = '正在读取内容…';
            try {
                var full = await self.readItem('sources', item, false, true);
                body.control.value = full.cachedYaml || ''; original.cachedYaml = full.cachedYaml;
                item.cachedYaml = full.cachedYaml; contentLoaded = true; body.control.disabled = false;
                contentStatus.textContent = '';
            } catch (err) { contentStatus.textContent = err.message; }
            finally { loading = false; }
        });
        var advanced = E('details', {}, [E('summary', {}, '筛选、刷新与本地内容'), prefix.node, include.node, exclude.node, agent.node, auto.node, interval.node, content]);
        this.editor(source ? '编辑订阅源' : '添加订阅源', [name, url, enabled, advanced], async function() {
            Object.assign(item, { name: name.read(), url: url.read(), enabled: enabled.read(), prefix: prefix.read(),
                includeRegex: include.read(), excludeRegex: exclude.read(), userAgent: agent.read(), autoRefresh: auto.read(), refreshMinutes: interval.read() });
            if (contentLoaded) item.cachedYaml = body.read();
            await self.put('sources', item, original);
        });
    },

    nodes: async function(source) {
        var result = await call('nodes', { id: source.id });
        ui.showModal(source.name + ' · 筛选后节点（' + result.nodes.length + '）', [E('div', { 'class': 'fii-node-list' },
            result.nodes.map(function(n) { return E('p', {}, [E('strong', {}, n.name), E('small', {}, n.type + ' · ' + n.server)]); })),
            actions([button('关闭', function() { ui.hideModal(); })])]);
    },

    overridesPage: function() {
        var self = this, all = this.data.overrides;
        // The migrated Mihomo basics live on the Mihomo tab; keep real indexes for ordering.
        var shown = all.filter(function(item) { return item.origin !== 'legacy-mihomo-settings'; });
        return E('div', {}, [this.pageHeading('覆写', '全局覆写先执行，专属覆写按输出中的选择顺序执行，同一覆写只执行一次。', button('添加覆写', function() { return self.editOverride(); }, true)),
            E('p', { 'class': 'fii-muted' }, '这里管理 Mihomo 的 DNS、规则、代理组和通用参数。全局覆写先按列表顺序执行，专属覆写在输出中选择和排序。路由器流量接管请到路由器设置。'),
            shown.length ? E('div', { 'class': 'fii-override-list', 'aria-label': '覆写列表' }, shown.map(function(item) {
                var index = all.indexOf(item);
                return E('article', { 'class': 'fii-card fii-override-row', 'data-id': item.id }, [E('div', { 'class': 'fii-card-heading' }, [
                    E('div', { 'class': 'fii-order-heading' }, [E('span', { 'class': 'fii-order-number', 'aria-label': '列表第 ' + (index + 1) + ' 项' }, String(index + 1)), E('h3', {}, item.name)]),
                    actions([badge(item.type), badge(item.global ? '全局' : '专属'), badge(item.enabled === false ? '已禁用' : '已启用', item.enabled === false ? '' : 'success')])]),
                    E('div', { 'class': 'fii-override-meta' }, [E('p', { 'class': 'fii-muted' }, item.url ? '远程覆写 · ' + date(item.lastRefreshAt) : '本地覆写 · 编辑内容后保存'),
                        E('details', {}, [E('summary', {}, '内容摘要'), E('pre', { 'class': 'fii-snippet' }, (item.snippet || item.body || '空覆写').slice(0, 240))])]),
                    item.lastError ? E('p', { 'class': 'fii-error' }, item.lastError) : '',
                    actions([button('上移', function() { return self.moveOverride(item, index - 1); }, false, { disabled: index === 0, 'aria-label': '上移 ' + item.name }),
                        button('下移', function() { return self.moveOverride(item, index + 1); }, false, { disabled: index === all.length - 1, 'aria-label': '下移 ' + item.name }),
                        button('移动到…', function() { self.positionOverride(item); }, false, { 'aria-label': '移动 ' + item.name + ' 到指定位置' }),
                        button('编辑', function() { return self.editOverride(item); }),
                        item.url ? button('刷新远程内容', async function() { var r = await call('refresh', { kind: 'overrides', id: item.id }); notify(r.message, !r.success); await self.reload(); }) : '',
                        button('删除', function() { self.remove('overrides', item); })])]);
            })) : empty('按需扩展配置', '覆写可以添加代理组、插入规则，或调整 Mihomo 配置。无需覆写也可以使用默认 PROXY 代理组。')]);
    },

    moveOverride: async function(item, position) {
        var order = this.data.overrides.map(function(value) { return value.id; }), current = order.indexOf(item.id);
        if (current < 0 || position < 0 || position >= order.length || position === current) return;
        order.splice(current, 1); order.splice(position, 0, item.id);
        await this.save({ kind: 'overrides', order: order });
        var row = Array.from(this.body.querySelectorAll('.fii-override-row')).find(function(node) { return node.dataset.id === item.id; });
        if (row) { row.scrollIntoView({ block: 'nearest' }); row.querySelector('button').focus({ preventScroll: true }); }
    },

    positionOverride: function(item) {
        var self = this, position = field('移动到第几项', this.data.overrides.findIndex(function(value) { return value.id === item.id; }) + 1,
            { type: 'number', min: 1, max: this.data.overrides.length, required: true });
        this.editor('移动“' + item.name + '”', [position], async function() { await self.moveOverride(item, position.read() - 1); });
    },

    editOverride: async function(override) {
        var pending;
        if (override) {
            pending = E('p', { role: 'status', 'class': 'fii-muted' }, '正在读取覆写…');
            ui.showModal('编辑覆写', [pending, actions([button('取消', function() { ui.hideModal(); })])], 'fii-modal');
        }
        try { override = await this.readItem('overrides', override); }
        catch (err) {
            if (!pending) throw err;
            if (pending.isConnected && document.body.classList.contains('modal-overlay-active')) {
                pending.textContent = err.message; pending.setAttribute('role', 'alert');
            }
            return;
        }
        if (pending && (!pending.isConnected || !document.body.classList.contains('modal-overlay-active'))) return;
        var self = this, item = Object.assign({ id: 'o_' + Date.now().toString(36), name: '', type: 'YAML', body: 'rules+: []\n', enabled: true, global: false, refreshMinutes: 720 }, override || {});
        var original = Object.assign({}, item);
        var name = field('名称', item.name, { required: true });
        var type = field('类型', item.type, { select: [['YAML', 'YAML 覆写'], ['RULES', '规则列表'], ['JS', 'JavaScript 覆写']] });
        var enabled = toggle('启用覆写', item.enabled), global = toggle('全局覆写', item.global, '自动应用于所有输出。');
        var body = field('覆写内容', item.body, { area: true, code: true, rows: 14,
            help: 'YAML：+rules 前插、rules+ 追加、rules! 替换；{{proxy_names}} 展开节点名列表。RULES：每行一条，插入到 rules 开头。JS：同步 main(config)，返回完整配置对象。' });
        var defaults = { YAML: 'rules+: []\n', RULES: '', JS: 'function main(config) {\n  config.rules.unshift("DOMAIN,example.com,DIRECT");\n  return config;\n}\n' };
        type.control.addEventListener('change', function() {
            if (Object.keys(defaults).some(function(key) { return body.read() === defaults[key]; })) body.control.value = defaults[type.read()];
        });
        var url = field('远程更新 URL（可选）', item.url || '', { type: 'url' });
        var auto = toggle('自动更新远程覆写', item.autoRefresh);
        var interval = field('刷新间隔（分钟）', item.refreshMinutes, { type: 'number', min: 5, max: 525600, required: true });
        this.editor(override ? '编辑覆写' : '添加覆写', [name, type, enabled, global, body,
            E('details', {}, [E('summary', {}, '远程更新'), url.node, auto.node, interval.node])], async function() {
            await self.put('overrides', Object.assign(item, { name: name.read(), type: type.read(), enabled: enabled.read(), global: global.read(), body: body.read(),
                url: url.read(), autoRefresh: auto.read(), refreshMinutes: interval.read() }), original);
        });
    },

    outputsPage: function() {
        var self = this;
        return E('div', {}, [this.pageHeading('输出配置', '组合订阅和覆写，预览后更新运行配置。内核停止时保持停止；运行时重新加载新配置。', button('创建输出', function() { self.editOutput(); }, true)),
            this.data.outputs.length ? E('div', { 'class': 'fii-grid' }, this.data.outputs.map(function(item) {
                var current = self.status.activeOutputId === item.id;
                return E('article', { 'class': 'fii-card' + (current ? ' fii-selected' : '') }, [E('div', { 'class': 'fii-card-heading' }, [E('h3', {}, item.name), current ? badge('当前配置', 'success') : badge(item.enabled === false ? '已禁用' : '未应用')]),
                    E('p', {}, item.sourceIds.length + ' 个订阅 · ' + effectiveOverrides(self.data.overrides, item.overrideIds).length + ' 个生效覆写'),
                    E('p', { 'class': 'fii-muted' }, item.sourceIds.map(function(id) { var s = self.data.sources.find(function(v) { return v.id === id; }); return s ? s.name : id; }).join(' → ') || '未选择订阅'),
                    E('details', {}, [E('summary', {}, '覆写执行顺序'), executionList(effectiveOverrides(self.data.overrides, item.overrideIds))]),
                    actions([button('更新运行配置', function() { return self.updateRuntime(item.id); }, true, { disabled: item.enabled === false || !self.status.coreInstalled }),
                        button('预览', function() { return self.preview(item); }), button('编辑', function() { return self.editOutput(item); }), button('删除', function() { self.remove('outputs', item); })])]);
            })) : empty('创建一份可运行的输出', '选择订阅和覆写，校验并生成运行配置，再按需手动启动内核。')]);
    },

    editOutput: async function(output) {
        if (output) {
            var pending = E('p', { role: 'status' }, '正在读取输出…');
            ui.showModal('编辑输出配置', [pending, actions([button('取消', function() { ui.hideModal(); })])], 'fii-modal');
            try { output = await this.readItem('outputs', output); }
            catch (err) { if (pending.isConnected) { pending.textContent = err.message; pending.setAttribute('role', 'alert'); } return; }
            if (!pending.isConnected || !document.body.classList.contains('modal-overlay-active')) return;
        }
        var self = this, item = Object.assign({ id: 'p_' + Date.now().toString(36), name: '', enabled: true, sourceIds: [], overrideIds: [] }, output || {});
        var name = field('名称', item.name, { required: true }), enabled = toggle('启用输出', item.enabled);
        var sources = choices(this.data.sources, item.sourceIds), overrides = choices(this.data.overrides, item.overrideIds, true);
        this.editor(output ? '编辑输出配置' : '创建输出配置', [name, enabled, E('h4', {}, '订阅源（按顺序聚合）'), sources.node,
            E('h4', {}, '覆写'), overrides.node], async function() {
            await self.put('outputs', Object.assign(item, { name: name.read(), enabled: enabled.read(), sourceIds: sources.read(), overrideIds: overrides.read() }));
        });
    },

    preview: async function(output) {
        var self = this, pending = E('p', { role: 'status', 'aria-live': 'polite' }, '正在生成预览…');
        ui.showModal(output.name + ' · 预览', [pending, actions([button('关闭', function() { ui.hideModal(); })])], 'fii-modal');
        var result;
        try {
            result = await call('preview', { id: output.id }, function(progress) {
                pending.textContent = progress.message + (progress.total ? ' ' + Math.round(progress.received * 100 / progress.total) + '%' : '');
            });
        } catch (err) {
            if (pending.isConnected && document.body.classList.contains('modal-overlay-active')) {
                pending.textContent = err.message; pending.setAttribute('role', 'alert'); pending.className = 'fii-error';
            }
            return;
        }
        if (!pending.isConnected || !document.body.classList.contains('modal-overlay-active')) return;
        var area = field('生成的订阅 YAML', result.yaml, { area: true, code: true, rows: 20 }); area.control.readOnly = true;
        var note = E('p', { 'class': 'fii-muted' }, result.nodeCount + ' 个节点。Mihomo 参数由覆写管理；运行预览仅适配路由器接入所需的监听、控制器和路由字段。');
        var warnings = E('p', { 'class': 'fii-notice', role: 'status' }, result.warnings.join('；')); warnings.hidden = !result.warnings.length;
        var runtimeProgress = E('p', { role: 'status', 'aria-live': 'polite', 'class': 'fii-muted' }, '');
        ui.showModal(output.name + ' · 预览', [note, E('details', {}, [E('summary', {}, '本次执行的覆写顺序'), executionList(result.appliedOverrides || [])]),
            warnings, area.node, runtimeProgress,
            actions([button('下载 YAML', function() { download('fii-' + output.id + '.yaml', area.read(), 'application/yaml'); }),
                button('查看路由器运行 YAML', async function() {
                    runtimeProgress.textContent = '正在生成路由器运行配置…';
                    try {
                        var r = await call('preview', { id: output.id, runtime: true }, function(p) { runtimeProgress.textContent = p.message; });
                        area.control.value = r.yaml; note.textContent = '路由器运行配置预览，包含控制器密钥，请妥善保管。'; runtimeProgress.textContent = '';
                        warnings.textContent = r.warnings.join('；'); warnings.hidden = !r.warnings.length;
                    } catch (err) { runtimeProgress.textContent = err.message; }
                }),
                button('关闭', function() { ui.hideModal(); })])], 'fii-modal');
    },

    settingsPage: function() {
        var self = this, s = this.data.settings;
        var network = this.network || {};
        var master = toggle('启用透明代理', s.proxyEnabled !== false && s.mode !== 'core');
        var mode = field('TCP 代理方式', s.tcpMode === 'inherit' ? (s.mode === 'redirect' ? 'redirect' : 'tproxy') : s.tcpMode, { select: [['off', '关闭'], ['redirect', 'REDIRECT'], ['tproxy', 'TPROXY'], ['tun', 'TUN']] });
        var udpMode = field('UDP 代理方式', s.udpMode === 'inherit' ? (s.udpProxy ? 'tproxy' : 'off') : s.udpMode, { select: [['off', '关闭'], ['tproxy', 'TPROXY'], ['tun', 'TUN']] });
        var lan = toggle('代理局域网设备', s.lanProxy !== false);
        var router = toggle('代理路由器本机', !!s.routerProxy);
        var ipv4 = toggle('代理 IPv4 流量', s.ipv4Proxy !== false);
        var ipv6 = toggle('代理 IPv6 流量', s.ipv6);
        var dns4 = toggle('接管 IPv4 DNS', s.dnsIpv4Hijack !== false);
        var ping = toggle('接管 Fake-IP Ping', !!s.fakeIpPing, '使 Fake-IP 地址能够响应 Ping。');
        var devices = (network.devices || []).filter(function(d) { return d.up || d.networks.length || s.interfaces.indexOf(d.name) >= 0; });
        var interfaces = selection('局域网接口', devices.map(function(d) {
            return { id: d.name, name: (d.networks.length ? d.networks.join(' / ') + ' · ' : '') + d.name + (d.up ? '' : ' · 未连接') };
        }), s.interfaces);
        var lanAcl = accessEditor('lan', legacyAccess(s, 'lan'), network);
        var routerAcl = accessEditor('router', legacyAccess(s, 'router'), network);
        function deviceLabel(value) {
            var matches = (network.hosts || []).filter(function(h) {
                return h.ip === value || (h.mac || '').toLowerCase() === value.toLowerCase();
            });
            var host = matches.find(function(h) { return h.name; }) || matches[0];
            return host ? Array.from(new Set([host.name, host.ip, host.mac].filter(Boolean))).join(' · ') : value;
        }
        var directDevices = entries('直连设备（MAC / IP / 网段）', s.lanBypassDevices || [], 'AA:BB:CC:DD:EE:FF 或 192.168.1.100',
            (network.hosts || []).filter(function(h) { return h.mac; }).map(function(h) {
                return [h.mac, deviceLabel(h.mac)];
            }), deviceLabel);
        var dns = toggle('接管局域网 DNS', s.dnsHijack, '将设备的常规 DNS 查询交给 Mihomo。');
        var routerDns = toggle('接管路由器本机 DNS', s.routerDnsHijack !== false);
        var dns6 = toggle('接管 IPv6 DNS', s.dnsIpv6Hijack && (s.dnsFamilyIndependent || s.ipv6));
        var boot = toggle('开机自动启动', s.autostart);
        var auto = toggle('订阅更新后自动应用', s.autoApply, '刷新成功后更新当前输出，校验失败时保留运行配置。');
        var bypass = entries('直接连接的目标网段', s.bypass, '203.0.113.0/24');
        var china4 = toggle('绕过中国大陆 IPv4', !!s.bypassChina4), china6 = toggle('绕过中国大陆 IPv6', !!s.bypassChina6);
        var dscp = entries('绕过 DSCP', s.bypassDscp || [], '0–63');
        var marks = entries('绕过 FWMark', s.bypassMarks || [], '0x100/0xff00');
        function portRange(label, value, common) {
            var input = field(label, value, { required: true, help: '支持单个端口或范围，使用空格或逗号分隔。' });
            return { node: E('div', {}, [input.node, actions([button('所有端口', function() { input.control.value = '0-65535'; }),
                button('常用端口', function() { input.control.value = common; })])]), read: input.read };
        }
        var tcpPorts = portRange('TCP 代理目标端口', s.tcpPorts || '0-65535', '21 22 80 110 143 194 443 465 853 993 995 8080 8443');
        var udpPorts = portRange('UDP 代理目标端口', s.udpPorts || '0-65535', '123 443 8443');
        var reserved4 = entries('IPv4 保留地址', s.reservedIpv4 || [], '192.168.0.0/16');
        var reserved6 = entries('IPv6 保留地址', s.reservedIpv6 || [], 'fc00::/7');
        var tunTimeout = field('TUN 接口等待时间（秒）', s.tunTimeout || 30, { type: 'number', min: 1, max: 60, required: true });
        var tunInterval = field('TUN 接口检查间隔（秒）', s.tunInterval || 1, { type: 'number', min: 1, max: 10, required: true });
        var agent = field('订阅下载 User-Agent', s.userAgent);
        function card(title, children) { return E('section', { 'class': 'fii-card' }, [E('h3', {}, title)].concat(children)); }
        var lanOptions = E('div', {}, [interfaces.node, E('h4', {}, '访问控制'), lanAcl.node]);
        var routerOptions = E('div', {}, [E('h4', {}, '访问控制'), routerAcl.node]);
        var scopeInfo = E('p', { 'class': 'fii-muted', role: 'status' });
        var lanPanel = card('局域网代理', [lan.node, lanOptions]);
        var directDnsNotice = E('p', { 'class': 'fii-notice' }, '已开启路由器本机代理和 DNS 接管。若直连设备使用路由器 DNS，请在本机访问规则中让 DNS 进程直连并关闭其 DNS 接管，确保上游返回真实 IP。');
        var accessPanel = card('访问控制', [E('p', {}, '指定设备跳过 FII 透明代理，全部直连。名单优先于局域网访问规则，同时跳过 TCP / UDP 代理、DNS 劫持和 Fake-IP Ping 接管。'),
            directDevices.node,
            E('p', { 'class': 'fii-muted' }, '推荐从设备列表选择 MAC 地址，可同时匹配该设备的 IPv4 / IPv6 流量。也可手动填写 IP 或网段；IP 条目只匹配对应地址。仅对所选局域网接口生效。'),
            E('p', { 'class': 'fii-muted' }, '直连设备应使用能返回真实 IP 的 DNS。若设备缓存了 Fake-IP，应用后请重连网络或清理 DNS 缓存；若自行将 DNS 指向 Mihomo，请改用正常 DNS。'), directDnsNotice]);
        var routerPanel = card('路由器本机代理', [router.node, routerOptions]);
        var panels = [card('代理设置', [master.node, E('div', { 'class': 'fii-settings-grid' }, [mode.node, udpMode.node, ipv4.node, ipv6.node, dns4.node, dns6.node]), ping.node, scopeInfo])];
        var dnsPanel = E('div', {}, [card('DNS 接管', [E('p', { 'class': 'fii-muted' }, '这里只决定哪些设备的 DNS 请求交给 Mihomo。解析服务器、DNS 模式、过滤与域名策略在覆写中配置。'), dns.node, routerDns.node,
            actions([button('前往 Mihomo 覆写', function() { self.tab = 'overrides'; history.replaceState(null, '', '#overrides'); self.draw(); })])])]);
        var bypassPanel = E('div', {}, [card('绕过策略', [E('div', { 'class': 'fii-settings-grid' }, [china4.node, china6.node]),
            E('p', { 'class': 'fii-muted' }, '内置大陆 IPv4 / IPv6 地址列表，随 FII 软件包更新。'),
            E('div', { 'class': 'fii-settings-grid' }, [tcpPorts.node, udpPorts.node]), E('div', { 'class': 'fii-settings-grid' }, [dscp.node, marks.node]), bypass.node])]);
        var miscPanel = E('div', {}, [card('保留地址', [E('p', { 'class': 'fii-muted' }, '保留地址直接连接，Fake-IP 地址仍按代理规则处理。'), E('div', { 'class': 'fii-settings-grid' }, [reserved4.node, reserved6.node])]),
            card('TUN 接口等待', [E('p', { 'class': 'fii-muted' }, '设置路由器等待接口就绪的时间。Mihomo 的 TUN 协议栈与 MTU 在覆写中配置。'), E('div', { 'class': 'fii-settings-grid' }, [tunTimeout.node, tunInterval.node])])]);
        var servicePanel = E('div', {}, [card('启动与更新', [boot.node, auto.node]), card('订阅下载', [agent.node])]);
        var proxyPanel = E('div', {}, panels);
        var backups = card('备份与迁移', [E('p', {}, '导入 FII 或 SubConverter Flutter 备份，迁移订阅、覆写和输出，保留当前路由器设置。'), actions([
            button('导出备份', async function() { var r = await call('export'); download('fii-backup.json', JSON.stringify(r.data, null, 2)); }),
            button('导入备份', function() { self.importBackup(); })])]);
        var allPanels = { proxy: proxyPanel, lan: lanPanel, access: accessPanel, router: routerPanel, bypass: bypassPanel, misc: miscPanel, dns: dnsPanel, service: servicePanel, backup: backups };
        var tabs = E('nav', { 'class': 'fii-settings-tabs', 'aria-label': '路由器设置分类' });
        var currentSection = self.settingsSection || 'proxy';
        function showSection(key) {
            self.settingsSection = key;
            if (saveActions) saveActions.hidden = key === 'backup';
            Object.keys(allPanels).forEach(function(name) { allPanels[name].hidden = name !== key; });
            Array.from(tabs.children).forEach(function(tab) {
                tab.classList.toggle('active', tab.dataset.section === key);
                tab.setAttribute('aria-pressed', String(tab.dataset.section === key));
            });
        }
        [['proxy', '代理设置'], ['lan', '局域网代理'], ['access', '访问控制'], ['router', '本机代理'], ['bypass', '绕过设置'], ['misc', '其他设置'], ['dns', 'DNS 接管'], ['service', '运行设置'], ['backup', '备份与迁移']].forEach(function(pair) {
            tabs.appendChild(button(pair[1], function() { showSection(pair[0]); }, false, { 'class': 'fii-settings-tab', 'data-section': pair[0] }));
        });
        function updateVisibility() {
            var transparent = master.read();
            mode.control.disabled = !transparent; udpMode.control.disabled = !transparent;
            lanOptions.hidden = !lan.read(); routerOptions.hidden = !router.read();
            dns.control.disabled = !transparent || !lan.read();
            routerDns.control.disabled = !transparent || !router.read();
            directDnsNotice.hidden = !transparent || !router.read() || !routerDns.read();
            [ipv4, ipv6, dns4, dns6, ping].forEach(function(f) { f.control.disabled = !transparent; });
            scopeInfo.textContent = !transparent ? '仅提供代理和 DNS 监听服务。' : [lan.read() ? '局域网设备' : '', router.read() ? '路由器本机' : ''].filter(Boolean).join('、') + (lan.read() || router.read() ? '的流量交由 Mihomo 规则处理。' : '请选择需要代理的流量范围。');
        }
        [master, mode, udpMode, lan, router, ipv6, dns, routerDns].forEach(function(f) { f.control.addEventListener('change', updateVisibility); });
        updateVisibility(); showSection(currentSection);
        async function saveSettings(apply) {
                // Hidden sections can still contain invalid fields; reveal the first one.
                var invalid = Array.from(form.querySelectorAll('input,select,textarea')).find(function(el) { return !el.disabled && !el.checkValidity(); });
                if (invalid) {
                    Object.keys(allPanels).forEach(function(key) { if (allPanels[key].contains(invalid)) showSection(key); });
                    var parent = invalid.parentElement; while (parent && parent !== form) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }
                    invalid.reportValidity(); return;
                }
                if (master.read() && lan.read() && !interfaces.read().length) throw new Error('请选择至少一个局域网接口');
                var settings = Object.assign({}, self.data.settings, { mode: !master.read() ? 'core' : mode.read() === 'redirect' ? 'redirect' : 'tproxy',
                    proxyEnabled: master.read(), tcpMode: mode.read(), udpMode: udpMode.read(), ipv4Proxy: ipv4.read(),
                    dnsIpv4Hijack: dns4.read(), dnsFamilyIndependent: true, fakeIpPing: ping.read(),
                    bypassChina4: china4.read(), bypassChina6: china6.read(), tcpPorts: tcpPorts.read(), udpPorts: udpPorts.read(),
                    bypassDscp: dscp.read(), bypassMarks: marks.read(), reservedIpv4: reserved4.read(), reservedIpv6: reserved6.read(),
                    tunTimeout: tunTimeout.read(), tunInterval: tunInterval.read(),
                    interfaces: interfaces.read(), ipv6: ipv6.read(), dnsHijack: dns.read(),
                    lanProxy: lan.read(), routerProxy: router.read(), udpProxy: udpMode.read() !== 'off', routerDnsHijack: routerDns.read(), dnsIpv6Hijack: dns6.read(),
                    lanRules: lanAcl.read(), routerRules: routerAcl.read(), lanBypassDevices: directDevices.read(),
                    autostart: boot.read(), autoApply: auto.read(), bypass: bypass.read(), userAgent: agent.read() });
                var changedSettings = {};
                Object.keys(settings).forEach(function(key) {
                    if (JSON.stringify(settings[key]) !== JSON.stringify(self.data.settings[key])) changedSettings[key] = settings[key];
                });
                if (Object.keys(changedSettings).length) {
                    var saved = await call('save', { patch: { settings: changedSettings }, compact: true, revision: self.data.revision });
                    self.data = saved.data;
                }
                if (apply) {
                    try {
                        var result = await call('apply', { id: self.status.activeOutputId });
                        notify(result.message + ((result.warnings || []).length ? '；' + result.warnings.join('；') : ''));
                    } catch (err) { throw new Error('设置已保存，更新运行配置失败：' + err.message); }
                    finally { await self.reload(); }
                } else { self.draw(); notify('设置已保存，应用后生效'); }
        }
        var canApply = !!self.status.activeOutputId && self.status.coreInstalled;
        var saveActions = actions([button('保存设置', function() { return saveSettings(false); }),
            button('保存并更新配置', function() { return saveSettings(true); }, true, { disabled: !canApply })]);
        saveActions.classList.add('fii-settings-save');
        saveActions.hidden = currentSection === 'backup';
        var form = E('form', { 'class': 'fii-settings', submit: function(event) { event.preventDefault(); } }, [E('p', { 'class': 'fii-muted' }, '本页只管理路由器接口、流量接管、防火墙与服务。Mihomo 的 DNS、规则、代理组和通用参数在覆写中配置。'), tabs,
            network.error ? E('p', { 'class': 'fii-notice', role: 'alert' }, '读取网络接口失败：' + network.error + '。可重新载入后重试。') : '',
            proxyPanel, lanPanel, accessPanel, routerPanel, bypassPanel, miscPanel, dnsPanel, servicePanel, backups, saveActions,
            !canApply ? E('p', { 'class': 'fii-muted' }, '首次使用请先在输出页生成一份运行配置。') : '']);
        return form;
    },

    importBackup: function() {
        var self = this, input = E('input', { type: 'file', accept: '.json,application/json', 'aria-label': '选择备份 JSON 文件' });
        var control = { cancelled: false, abort: null };
        var progress = E('p', { role: 'status', 'aria-live': 'polite', 'class': 'fii-muted' }, '');
        var failure = E('p', { role: 'alert', 'class': 'fii-notice', style: 'white-space:pre-wrap;overflow-wrap:anywhere' }, '');
        failure.hidden = true;
        function report(p) { progress.textContent = p.message + (p.total ? ' ' + Math.round(p.received * 100 / p.total) + '%' : ''); }
        function release(token) { return call('transfer_release', { id: token }).catch(function() {}); }
        ui.showModal('导入备份', [E('p', {}, '选择 FII 或 SubConverter Flutter 导出的 JSON 文件。下一步会显示导入摘要。'), input, progress, failure, actions([
            button('取消', function() { control.cancelled = true; if (control.abort) control.abort(); ui.hideModal(); }), button('检查备份', async function() {
                failure.hidden = true; failure.textContent = '';
                try {
                    if (!input.files.length) throw new Error('请先选择文件');
                    if (input.files[0].size > 16 * 1024 * 1024) throw new Error('备份不能超过 16 MiB');
                    progress.textContent = '正在准备上传…';
                    var result = await checkBackup(input.files[0], self.data.revision, report, control);
                    if (!result.importToken) throw new Error('FII 后端不支持复用检查结果，请同时升级两个安装包并强制刷新页面。');
                    if (!input.isConnected || !document.body.classList.contains('modal-overlay-active')) {
                        await release(result.importToken); return;
                    }
                    var confirmFailure = E('p', { role: 'alert', 'class': 'fii-notice', style: 'white-space:pre-wrap' }, '');
                    confirmFailure.hidden = true;
                    var cancel = button('取消', async function() { ui.hideModal(); await release(result.importToken); });
                    ui.showModal('确认导入', [E('p', {}, '将替换为 ' + result.summary.sources + ' 个订阅、' + result.summary.overrides + ' 个覆写、' + result.summary.outputs + ' 个输出。'),
                        E('p', { 'class': 'fii-notice' }, result.warnings.join('；') || '保留路由器设置与当前运行配置。'),
                        E('p', { 'class': 'fii-muted' }, '本次检查结果保留 15 分钟，确认时无需再次上传。'),
                        progress, confirmFailure, actions([cancel, button('导入并替换配置库', async function() {
                            cancel.disabled = true; confirmFailure.hidden = true; progress.textContent = '正在导入已检查的备份…';
                            try {
                                var r = await call('import', { token: result.importToken, revision: result.revision });
                                notify(r.message); await self.reload(); await release(result.importToken); ui.hideModal();
                            } catch (err) {
                                progress.textContent = ''; confirmFailure.textContent = err.message; confirmFailure.hidden = false;
                            } finally { cancel.disabled = false; }
                        }, true)])]);
                } catch (err) {
                    progress.textContent = ''; failure.textContent = err.message; failure.hidden = false;
                }
            }, true)
        ])]);
    },

    logs: function() { this.tab = 'logs'; history.replaceState(null, '', '#logs'); this.draw(); },

    logsPage: function() {
        var self = this, disposed = false, busy = false, timer, snapshot = null, records = [], shown = [], lastSignature = '', updated = '';
        var prefs = self.logPreferences || (self.logPreferences = { source: 'all', level: 'all', query: '', automatic: true, follow: true, wrap: true });
        var levels = { debug: '调试', info: '信息', warning: '警告', error: '错误' };
        var search = E('input', { type: 'search', placeholder: '搜索日志内容', 'aria-label': '搜索日志', value: prefs.query });
        var level = E('select', { 'aria-label': '日志级别' }, [['all', '全部级别'], ['error', '错误'], ['warning', '警告'], ['info', '信息'], ['debug', '调试']].map(function(pair) { return E('option', { value: pair[0] }, pair[1]); })); level.value = prefs.level;
        var sources = E('nav', { 'class': 'fii-log-sources', 'aria-label': '日志来源' });
        var status = E('span', { role: 'status', 'aria-live': 'polite', 'class': 'fii-muted' }, '正在读取…');
        var error = E('p', { role: 'alert', 'class': 'fii-error' }); error.hidden = true;
        var summary = E('span', { 'class': 'fii-muted' });
        var list = E('div', { 'class': 'fii-log-lines' });
        var viewport = E('div', { 'class': 'fii-log-viewport', tabindex: '0', role: 'region', 'aria-label': '日志内容' }, list);
        function check(label, key) {
            var input = E('input', { type: 'checkbox' }); input.checked = prefs[key];
            return { input: input, node: E('label', { 'class': 'fii-log-check' }, [input, label]) };
        }
        var follow = check('跟随最新', 'follow'), wrap = check('自动换行', 'wrap');
        var pause = button(prefs.automatic ? '暂停刷新' : '继续刷新', function() {
            prefs.automatic = !prefs.automatic; pause.textContent = prefs.automatic ? '暂停刷新' : '继续刷新';
            clearTimeout(timer); updateStatus(); if (prefs.automatic) refresh();
        });
        var refreshButton = button('刷新日志', function() { return refresh(true); });
        var exportButton = button('导出当前结果', function() {
            download('fii-' + prefs.source + '-' + new Date().toISOString().replace(/[:.]/g, '-') + '.log', shown.map(function(row) { return row.raw; }).join('\n') + '\n', 'text/plain;charset=utf-8');
        }, false, { disabled: true });
        var jump = button('跳到最新', function() { prefs.follow = follow.input.checked = true; viewport.scrollTop = viewport.scrollHeight; updateStatus(); });
        function updateStatus() {
            status.textContent = (busy ? '正在读取…' : !prefs.automatic ? '已暂停' : document.hidden ? '页面隐藏，已暂停' : '每 3 秒刷新') +
                (updated ? ' · ' + updated : '') + (prefs.follow ? '' : ' · 正在查看历史');
        }
        function renderRows(filterChanged) {
            var oldTop = viewport.scrollTop, anchor, offset;
            if (!prefs.follow && !filterChanged) {
                anchor = Array.from(list.children).find(function(row) { return row.offsetTop + row.offsetHeight > oldTop; });
                if (anchor) offset = oldTop - anchor.offsetTop;
            }
            var query = prefs.query.toLocaleLowerCase();
            shown = records.filter(function(row) { return (prefs.source === 'all' || row.source === prefs.source) &&
                (prefs.level === 'all' || row.level === prefs.level) && (!query || row.raw.toLocaleLowerCase().indexOf(query) >= 0); });
            list.replaceChildren.apply(list, shown.length ? shown.map(function(row) {
                var node = E('div', { 'class': 'fii-log-row is-' + row.level }, [
                    E('time', { title: row.time }, document.createTextNode(row.time)), E('span', { 'class': 'fii-log-level' }, levels[row.level]),
                    E('span', { 'class': 'fii-log-source', title: row.tag }, row.source === 'core' ? '内核' : '应用'),
                    E('span', { 'class': 'fii-log-message' }, document.createTextNode(row.message))]);
                node.logKey = row.key; return node;
            }) : [E('div', { 'class': 'fii-log-empty' }, !snapshot ? '正在读取日志…' : !records.length ? '暂无日志。服务停止时不会产生新的内核日志。' :
                prefs.source === 'core' && !records.some(function(row) { return row.source === 'core'; }) ? '暂无内核日志。内核停止时不会产生新记录。' : '没有符合筛选条件的日志')]);
            if (prefs.follow) viewport.scrollTop = viewport.scrollHeight;
            else if (filterChanged) viewport.scrollTop = 0;
            else { var target = anchor && Array.from(list.children).find(function(row) { return row.logKey === anchor.logKey; }); viewport.scrollTop = target ? target.offsetTop + offset : oldTop; }
            summary.textContent = '显示 ' + shown.length + ' / ' + records.length + ' 条' + (snapshot && snapshot.truncated ? ' · 较长日志已截取末尾' : '');
            exportButton.disabled = !shown.length;
            Array.from(sources.children).forEach(function(node) { node.setAttribute('aria-pressed', String(node.dataset.source === prefs.source)); });
        }
        function schedule() {
            clearTimeout(timer);
            if (!disposed && prefs.automatic && !document.hidden) timer = setTimeout(function() { refresh(); }, 3000);
        }
        async function refresh(manual) {
            if (disposed || busy || (!manual && (!prefs.automatic || document.hidden))) return;
            busy = refreshButton.disabled = true; updateStatus();
            try {
                var result = await call('logs');
                if (disposed || (!manual && !prefs.automatic)) return;
                snapshot = result; error.hidden = true;
                var signature = JSON.stringify([result.text, result.prepareText]);
                if (signature !== lastSignature) { records = parseLogSnapshot(result); lastSignature = signature; renderRows(false); }
                updated = new Date().toLocaleTimeString();
            } catch (err) { if (!disposed) { error.textContent = '读取失败：' + err.message + '。可点击“刷新日志”重试。'; error.hidden = false; } }
            finally { busy = refreshButton.disabled = false; if (!disposed) { updateStatus(); schedule(); } }
        }
        [['all', '全部日志'], ['app', '应用日志'], ['core', '内核日志']].forEach(function(pair) {
            sources.appendChild(button(pair[1], function() { prefs.source = pair[0]; renderRows(true); }, false, { 'data-source': pair[0] }));
        });
        search.addEventListener('input', function() { prefs.query = search.value; renderRows(true); });
        level.addEventListener('change', function() { prefs.level = level.value; renderRows(true); });
        follow.input.addEventListener('change', function() { prefs.follow = follow.input.checked; if (prefs.follow) viewport.scrollTop = viewport.scrollHeight; updateStatus(); });
        wrap.input.addEventListener('change', function() { prefs.wrap = wrap.input.checked; viewport.classList.toggle('nowrap', !prefs.wrap); });
        viewport.classList.toggle('nowrap', !prefs.wrap);
        viewport.addEventListener('scroll', function() {
            if (disposed || !viewport.isConnected || !viewport.clientHeight) return;
            prefs.follow = follow.input.checked = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 24; updateStatus();
        });
        function visibility() { clearTimeout(timer); updateStatus(); if (!document.hidden) refresh(); }
        document.addEventListener('visibilitychange', visibility);
        var panel = E('section', { 'class': 'fii-logs-panel' }, [
            E('div', { 'class': 'fii-page-heading' }, [E('div', {}, [E('h3', {}, '运行日志'), E('p', { 'class': 'fii-muted' }, '应用与内核日志分开查看，便于定位启动、配置与连接问题。')])]),
            E('div', { 'class': 'fii-log-toolbar' }, [sources, E('div', { 'class': 'fii-actions' }, [pause, refreshButton, exportButton])]),
            E('div', { 'class': 'fii-log-filters' }, [search, level, wrap.node, follow.node]), error,
            E('div', { 'class': 'fii-log-console' }, [E('div', { 'class': 'fii-log-console-head' }, [status, summary]), viewport,
                E('div', { 'class': 'fii-log-console-foot' }, [E('span', {}, '最近 500 条服务日志 · 另含最近一次配置准备输出'), jump])])]);
        self.disposeLogs = function() { disposed = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
        renderRows(true);
        requestAnimationFrame(function() { if (disposed) return; refresh(true); });
        return panel;
    }
});
