'use strict';
'require view';
'require rpc';
'require ui';
'require poll';

// SPDX-License-Identifier: GPL-3.0-or-later
var methods = {};
var rpcChunkSize = 16 * 1024;
function transportError(err) {
    return /XHR|RPC reply|HTTP error (?:0|413|502|504)|Failed to fetch|NetworkError/i.test(err.message || '');
}
function reduceChunk(size, onProgress) {
    if (size <= 1024) return false;
    rpcChunkSize = Math.min(rpcChunkSize, Math.max(1024, Math.floor(size / 2)));
    if (onProgress) onProgress({ message: '正在调整传输大小并重试…' });
    return true;
}
['get', 'status', 'save', 'refresh', 'preview', 'apply', 'service', 'export', 'import',
 'import_preview', 'logs', 'job', 'nodes', 'groups', 'select', 'core_info', 'core_check', 'core_update', 'core_channel', 'network',
 'transfer_begin', 'transfer_append', 'transfer_invoke', 'transfer_read', 'transfer_release'].forEach(function(name) {
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
            while (offset < bytes.length) {
                var part, requestedSize = rpcChunkSize;
                try {
                    part = await rpcReply('transfer_read', { id: transfer.id, offset: offset, size: requestedSize });
                } catch (err) {
                    if (transportError(err) && reduceChunk(requestedSize, onProgress)) continue;
                    throw err;
                }
                var data = Uint8Array.from(atob(part.result.data), function(c) { return c.charCodeAt(0); });
                if (!data.length || offset + data.length > bytes.length) throw new Error('响应分块大小无效');
                bytes.set(data, offset); offset += data.length;
                if (onProgress) onProgress({ message: '正在读取检查结果…', received: offset, total: bytes.length });
            }
            reply = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
        } finally {
            await methods.transfer_release(JSON.stringify({ id: transfer.id })).catch(function() {});
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
                var size = Math.min(upload.chunkSize, rpcChunkSize);
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
        for (var i = 0; i < (name === 'core_update' ? 1200 : 260); i++) {
            await new Promise(function(resolve) { window.setTimeout(resolve, 750); });
            var job = await call('job', { id: result.jobId }, onProgress);
            if (onProgress && job.progress) onProgress(job.progress);
            if (job.done) {
                if (!job.ok) throw new Error(job.error);
                return job.result;
            }
        }
        throw new Error('任务仍未完成，请重新载入并查看日志');
    }
    return result;
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

function choices(items, initial) {
    var selected = initial.slice(), host = E('div', { 'class': 'fii-choices' });
    function draw() {
        var ordered = selected.map(function(id) { return items.find(function(item) { return item.id === id; }); }).filter(Boolean)
            .concat(items.filter(function(item) { return selected.indexOf(item.id) < 0; }));
        host.replaceChildren.apply(host, ordered.map(function(item) {
            var index = selected.indexOf(item.id);
            var checkbox = E('input', { type: 'checkbox', change: function() {
                if (checkbox.checked) selected.push(item.id); else selected = selected.filter(function(id) { return id !== item.id; });
                draw();
            } });
            checkbox.checked = index >= 0;
            return E('div', { 'class': 'fii-choice' }, [E('label', {}, [checkbox,
                E('span', {}, item.name + (item.global ? ' · 全局' : '') + (item.enabled === false ? ' · 已禁用' : ''))]),
                index >= 0 ? actions([
                    button('上移', function() { var tmp = selected[index - 1]; selected[index - 1] = item.id; selected[index] = tmp; draw(); }, false, { disabled: index === 0, 'aria-label': '上移 ' + item.name }),
                    button('下移', function() { var tmp = selected[index + 1]; selected[index + 1] = item.id; selected[index] = tmp; draw(); }, false, { disabled: index === selected.length - 1, 'aria-label': '下移 ' + item.name })
                ]) : '' ]);
        }));
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

function entries(label, initial, placeholder, suggestions) {
    var values = initial.slice(), list = E('div', { 'class': 'fii-entry-list' });
    var draft = field('添加' + label, '', {});
    draft.control.placeholder = placeholder || '';
    function draw() {
        list.replaceChildren.apply(list, values.map(function(value) {
            return E('div', { 'class': 'fii-entry' }, [E('span', {}, value), button('移除', function() {
                values = values.filter(function(v) { return v !== value; }); draw();
            }, false, { 'aria-label': '移除 ' + value })]);
        }));
    }
    function add() {
        var value = draft.read().trim();
        if (value && values.indexOf(value) < 0) values.push(value);
        draft.control.value = ''; draw(); draft.control.focus();
    }
    draft.control.addEventListener('keydown', function(event) { if (event.key === 'Enter') { event.preventDefault(); add(); } });
    var pick = suggestions && suggestions.length ? field('选择' + label, '', { select: [['', '从列表选择…']].concat(suggestions) }) : null;
    if (pick) pick.control.addEventListener('change', function() {
        if (pick.read() && values.indexOf(pick.read()) < 0) values.push(pick.read());
        pick.control.value = ''; draw();
    });
    draw();
    return { node: E('fieldset', { 'class': 'fii-selection fii-entries' }, [E('legend', {}, label), list,
        pick ? pick.node : '', E('div', { 'class': 'fii-entry-add' }, [draft.node, button('添加', add)])]),
        read: function() { return Array.from(new Set(values.concat(draft.read().trim() ? [draft.read().trim()] : []))); } };
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

return view.extend({
    handleSaveApply: null,
    handleSave: null,
    handleReset: null,

    load: function() { return Promise.all([call('get'), call('status'), call('network').catch(function(err) { return { error: err.message }; })]); },

    render: function(result) {
        this.data = result[0].data;
        this.status = result[1];
        this.network = result[2] || {};
        this.tab = ['overview', 'sources', 'overrides', 'outputs', 'settings'].indexOf(location.hash.slice(1)) >= 0 ? location.hash.slice(1) : 'overview';
        if (!document.getElementById('fii-style')) document.head.appendChild(E('link', { id: 'fii-style', rel: 'stylesheet', href: L.resource('fii/style.css') }));
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
    },

    reload: async function() {
        var results = await this.load();
        this.data = results[0].data; this.status = results[1]; this.network = results[2]; this.draw();
    },

    draw: function() {
        var self = this;
        this.liveStatus = null;
        this.runtimeStamp = null;
        this.nav.replaceChildren.apply(this.nav, [
            ['overview', '概览'], ['sources', '订阅源'], ['overrides', '覆写'], ['outputs', '输出配置'], ['settings', '路由器设置']
        ].map(function(tab) {
            return button(tab[1], function() { self.tab = tab[0]; history.replaceState(null, '', '#' + tab[0]); self.draw(); }, false,
                { 'class': 'fii-tab ' + (self.tab === tab[0] ? 'active' : ''), 'aria-current': self.tab === tab[0] ? 'page' : null });
        }));
        this.body.replaceChildren(this[this.tab + 'Page']()); this.drawStatus();
    },

    save: async function(next) {
        var result = await call('save', { data: next, revision: this.data.revision });
        this.data = result.data; this.draw(); notify(result.message);
    },

    put: async function(kind, item) {
        var next = JSON.parse(JSON.stringify(this.data)), index = next[kind].findIndex(function(v) { return v.id === item.id; });
        if (index < 0) next[kind].push(item); else next[kind][index] = item;
        await this.save(next);
    },

    remove: function(kind, item) {
        var self = this;
        ui.showModal('删除“' + item.name + '”', [E('p', {}, '将移除这个配置项及它在输出中的关联。正在运行的配置保留到下次应用。'), actions([
            button('取消', function() { ui.hideModal(); }),
            button('删除', async function() {
                var next = JSON.parse(JSON.stringify(self.data));
                next[kind] = next[kind].filter(function(v) { return v.id !== item.id; });
                next.outputs.forEach(function(v) {
                    if (kind === 'sources') v.sourceIds = v.sourceIds.filter(function(id) { return id !== item.id; });
                    if (kind === 'overrides') v.overrideIds = v.overrideIds.filter(function(id) { return id !== item.id; });
                });
                await self.save(next); ui.hideModal();
            }, false, { 'class': 'cbi-button cbi-button-negative' })
        ])]);
    },

    runtimeCard: function() {
        var self = this, s = this.status;
        return E('section', { 'class': 'fii-card' }, [E('div', { 'class': 'fii-card-heading' }, [E('h3', {}, '当前运行'), badge(s.running ? '运行中' : '已停止', s.running ? 'success' : '')]),
            E('p', { 'class': 'fii-active-name' }, s.activeName || '还没有应用配置'),
            E('p', { 'class': 'fii-muted' }, s.activeOutputId ? '应用于 ' + date(s.appliedAt) : '添加订阅并创建输出后，即可在这里启动。'),
            s.activeOutputId && s.appliedRevision !== this.data.revision ? E('p', { 'class': 'fii-notice' }, '配置库有更新。运行服务使用上次应用的版本。') : '',
            E('p', { 'class': 'fii-muted' }, s.mode === 'core' || s.proxyEnabled === false ? '仅运行内核' : s.mode ? [s.lanProxy !== false ? '局域网 · ' + s.interfaces.join(', ') : '', s.routerProxy ? '路由器本机' : '',
                s.protocols ? 'TCP ' + s.protocols.tcp.toUpperCase() + ' · UDP ' + s.protocols.udp.toUpperCase() : s.mode.toUpperCase()].filter(Boolean).join(' / ') : ''),
            actions([button(s.running ? '重启服务' : '启动服务', async function() { var r = await call('service', { action: s.running ? 'restart' : 'start' }); notify(r.message); await self.reload(); }, false, { disabled: !s.activeOutputId || !s.coreInstalled }),
                button('停止服务', async function() { var r = await call('service', { action: 'stop' }); notify(r.message); await self.reload(); }, false, { disabled: !s.running }),
                button('运行日志', function() { return self.logs(); })])
        ]);
    },

    overviewPage: function() {
        var self = this;
        this.liveStatus = E('div');
        var groupHost = E('div', {}, E('p', { 'class': 'fii-muted' }, '启动服务后可以查看和切换代理组。'));
        async function loadGroups() {
            if (!self.status.running) return;
            var result = await call('groups');
            groupHost.replaceChildren.apply(groupHost, result.groups.map(function(group) {
                if (group.type !== 'Selector') return E('p', {}, group.name + ' · ' + group.type + ' · ' + group.now);
                var selection = field(group.name, group.now, { select: group.all.map(function(name) { return [name, name]; }) });
                return E('div', {}, [selection.node, button('切换节点', async function() {
                    var r = await call('select', { group: group.name, name: selection.read() }); notify(r.message); await loadGroups();
                })]);
            }));
            if (!result.groups.length) groupHost.appendChild(E('p', {}, '运行配置中没有代理组。'));
        }
        if (this.status.running) loadGroups().catch(function(err) { groupHost.textContent = err.message; });
        return E('div', {}, [E('div', { 'class': 'fii-stats' }, [['订阅源', this.data.sources.length], ['覆写', this.data.overrides.length], ['输出配置', this.data.outputs.length]].map(function(v) {
            return E('div', { 'class': 'fii-stat' }, [E('span', {}, v[0]), E('strong', {}, String(v[1]))]);
        })), this.liveStatus, this.corePanel(),
            E('section', { 'class': 'fii-card' }, [E('div', { 'class': 'fii-card-heading' }, [E('h3', {}, '代理组'), button('刷新代理组', loadGroups)]), groupHost]),
            E('section', { 'class': 'fii-card' }, [E('h3', {}, '三步开始'), E('ol', { 'class': 'fii-steps' }, [
                E('li', {}, [E('strong', {}, '添加订阅源'), E('p', {}, '输入订阅 URL，也可以粘贴完整 Mihomo YAML 或分享链接。')]),
                E('li', {}, [E('strong', {}, '按需添加覆写'), E('p', {}, '添加代理组、分流规则或 DNS 配置。全局覆写可复用于多个输出。')]),
                E('li', {}, [E('strong', {}, '组合输出并应用'), E('p', {}, '选择订阅和覆写，预览生成结果，然后应用到路由器。')])
            ]), actions([button('添加订阅源', function() { self.editSource(); }, true), button('从备份导入', function() { self.importBackup(); })])]),
            E('p', { 'class': 'fii-muted' }, '适配 OpenWrt 24.10 / firewall4。FII 与其他透明代理插件使用时，应先停止其他插件的接管。')
        ]);
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

    sourcesPage: function() {
        var self = this;
        return E('div', {}, [this.pageHeading('订阅源', '一个订阅只维护一次，可用于多个输出。', button('添加订阅源', function() { self.editSource(); }, true)),
            this.data.sources.length ? E('div', { 'class': 'fii-grid' }, this.data.sources.map(function(source) {
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
                        button('节点', function() { return self.nodes(source); }), button('编辑', function() { self.editSource(source); }), button('删除', function() { self.remove('sources', source); })])]);
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

    editSource: function(source) {
        var self = this, item = Object.assign({ id: 's_' + Date.now().toString(36), name: '', url: '', enabled: true, autoRefresh: true, refreshMinutes: 720 }, source || {});
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
        var advanced = E('details', {}, [E('summary', {}, '筛选、刷新与本地内容'), prefix.node, include.node, exclude.node, agent.node, auto.node, interval.node, body.node]);
        this.editor(source ? '编辑订阅源' : '添加订阅源', [name, url, enabled, advanced], async function() {
            await self.put('sources', Object.assign(item, { name: name.read(), url: url.read(), enabled: enabled.read(), prefix: prefix.read(),
                includeRegex: include.read(), excludeRegex: exclude.read(), userAgent: agent.read(), autoRefresh: auto.read(), refreshMinutes: interval.read(), cachedYaml: body.read() }));
        });
    },

    nodes: async function(source) {
        var result = await call('nodes', { id: source.id });
        ui.showModal(source.name + ' · 筛选后节点（' + result.nodes.length + '）', [E('div', { 'class': 'fii-node-list' },
            result.nodes.map(function(n) { return E('p', {}, [E('strong', {}, n.name), E('small', {}, n.type + ' · ' + n.server)]); })),
            actions([button('关闭', function() { ui.hideModal(); })])]);
    },

    overridesPage: function() {
        var self = this;
        return E('div', {}, [this.pageHeading('覆写', '全局覆写先执行，专属覆写按输出中的选择顺序执行，同一覆写只执行一次。', button('添加覆写', function() { self.editOverride(); }, true)),
            this.data.overrides.length ? E('div', { 'class': 'fii-grid' }, this.data.overrides.map(function(item) {
                return E('article', { 'class': 'fii-card' }, [E('div', { 'class': 'fii-card-heading' }, [E('h3', {}, item.name), badge(item.type)]),
                    E('p', { 'class': 'fii-muted' }, (item.global ? '全局覆写' : '专属覆写') + ' · ' + (item.enabled === false ? '已禁用' : '已启用')),
                    E('pre', { 'class': 'fii-snippet' }, (item.body || '空覆写').slice(0, 240)), item.lastError ? E('p', { 'class': 'fii-error' }, item.lastError) : '',
                    actions([button('编辑', function() { self.editOverride(item); }), button('刷新', async function() { var r = await call('refresh', { kind: 'overrides', id: item.id }); notify(r.message, !r.success); await self.reload(); }, false, { disabled: !item.url }),
                        button('删除', function() { self.remove('overrides', item); })])]);
            })) : empty('按需扩展配置', '覆写可以添加代理组、插入规则，或调整 Mihomo 配置。无需覆写也可以使用默认 PROXY 代理组。')]);
    },

    editOverride: function(override) {
        var self = this, item = Object.assign({ id: 'o_' + Date.now().toString(36), name: '', type: 'YAML', body: 'rules+: []\n', enabled: true, global: false, refreshMinutes: 720 }, override || {});
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
                url: url.read(), autoRefresh: auto.read(), refreshMinutes: interval.read() }));
        });
    },

    outputsPage: function() {
        var self = this;
        return E('div', {}, [this.pageHeading('输出配置', '把多个订阅和覆写组合成一份配置。保存后预览，应用后接管路由器流量。', button('创建输出', function() { self.editOutput(); }, true)),
            this.data.outputs.length ? E('div', { 'class': 'fii-grid' }, this.data.outputs.map(function(item) {
                var current = self.status.activeOutputId === item.id;
                return E('article', { 'class': 'fii-card' + (current ? ' fii-selected' : '') }, [E('div', { 'class': 'fii-card-heading' }, [E('h3', {}, item.name), current ? badge('当前配置', 'success') : badge(item.enabled === false ? '已禁用' : '未应用')]),
                    E('p', {}, item.sourceIds.length + ' 个订阅 · ' + item.overrideIds.length + ' 个专属覆写'),
                    E('p', { 'class': 'fii-muted' }, item.sourceIds.map(function(id) { var s = self.data.sources.find(function(v) { return v.id === id; }); return s ? s.name : id; }).join(' → ') || '未选择订阅'),
                    actions([button('应用到路由器', async function() { var r = await call('apply', { id: item.id }); notify(r.message + (r.warnings.length ? '；' + r.warnings.join('；') : '')); await self.reload(); }, true, { disabled: item.enabled === false || !self.status.coreInstalled }),
                        button('预览', function() { return self.preview(item); }), button('编辑', function() { self.editOutput(item); }), button('删除', function() { self.remove('outputs', item); })])]);
            })) : empty('创建一份可运行的输出', '选择一个或多个订阅，按需关联专属覆写。配置校验成功后才会切换运行。')]);
    },

    editOutput: function(output) {
        var self = this, item = Object.assign({ id: 'p_' + Date.now().toString(36), name: '', enabled: true, sourceIds: [], overrideIds: [] }, output || {});
        var name = field('名称', item.name, { required: true }), enabled = toggle('启用输出', item.enabled);
        var sources = choices(this.data.sources, item.sourceIds), overrides = choices(this.data.overrides, item.overrideIds);
        this.editor(output ? '编辑输出配置' : '创建输出配置', [name, enabled, E('h4', {}, '订阅源（按顺序聚合）'), sources.node,
            E('h4', {}, '专属覆写（按顺序执行）'), overrides.node], async function() {
            await self.put('outputs', Object.assign(item, { name: name.read(), enabled: enabled.read(), sourceIds: sources.read(), overrideIds: overrides.read() }));
        });
    },

    preview: async function(output) {
        var self = this, result = await call('preview', { id: output.id });
        var area = field('生成的订阅 YAML', result.yaml, { area: true, code: true, rows: 20 }); area.control.readOnly = true;
        var note = E('p', { 'class': 'fii-muted' }, result.nodeCount + ' 个节点。路由器应用时统一设置监听端口、DNS、控制器与流量标记。');
        ui.showModal(output.name + ' · 预览', [note, result.warnings.length ? E('p', { 'class': 'fii-notice' }, result.warnings.join('；')) : '', area.node,
            actions([button('下载 YAML', function() { download('fii-' + output.id + '.yaml', area.read(), 'application/yaml'); }),
                button('查看路由器运行 YAML', async function() { var r = await call('preview', { id: output.id, runtime: true }); area.control.value = r.yaml; note.textContent = '路由器运行配置预览，包含控制器密钥，请妥善保管。'; }),
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
        var dns = toggle('接管局域网 DNS', s.dnsHijack, '将设备的常规 DNS 查询交给 Mihomo。');
        var routerDns = toggle('接管路由器本机 DNS', s.routerDnsHijack !== false);
        var dns6 = toggle('接管 IPv6 DNS', s.dnsIpv6Hijack && (s.dnsFamilyIndependent || s.ipv6));
        var dnsMode = field('DNS 模式', s.dnsMode || 'fake-ip', { select: [['fake-ip', 'Fake-IP'], ['redir-host', 'Redir-Host']] });
        var servers = entries('上游 DNS', s.nameservers, 'https://dns.alidns.com/dns-query');
        var bootstrap = entries('引导 DNS', s.defaultNameservers || ['223.5.5.5', '119.29.29.29'], '223.5.5.5');
        var nodeDns = entries('节点域名 DNS', s.proxyNameservers || [], '留空跟随上游 DNS');
        var fakeFilter = entries('Fake-IP 过滤域名', s.fakeIpFilter || ['+.lan', '+.local'], '+.example.com');
        var localDns = field('本地域名 DNS', s.localDns || '127.0.0.1:53', { help: '用于解析 .lan、.local，可填写本机 DNS 地址与端口。' });
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
        var tunStack = field('TUN 协议栈', s.tunStack || 'mixed', { select: [['mixed', 'Mixed'], ['system', 'System'], ['gvisor', 'gVisor']] });
        var tunMtu = field('TUN MTU', s.tunMtu || 1500, { type: 'number', min: 1280, max: 9000, required: true });
        var tunTimeout = field('TUN 接口等待时间（秒）', s.tunTimeout || 30, { type: 'number', min: 1, max: 60, required: true });
        var tunInterval = field('TUN 接口检查间隔（秒）', s.tunInterval || 1, { type: 'number', min: 1, max: 10, required: true });
        var ports = {};
        [['mixedPort', '本机混合代理端口'], ['redirPort', 'REDIRECT 端口'], ['tproxyPort', 'TPROXY 端口'], ['dnsPort', 'DNS 端口'], ['controllerPort', '本机控制器端口']].forEach(function(pair) {
            ports[pair[0]] = field(pair[1], s[pair[0]], { type: 'number', min: 1024, max: 65535, required: true });
        });
        var secret = field('控制器密钥', s.secret, { type: 'password', required: true, help: '至少 16 字符，控制器只监听 127.0.0.1。' });
        var agent = field('订阅下载 User-Agent', s.userAgent);
        function card(title, children) { return E('section', { 'class': 'fii-card' }, [E('h3', {}, title)].concat(children)); }
        var lanOptions = E('div', {}, [interfaces.node, E('h4', {}, '访问控制'), lanAcl.node]);
        var routerOptions = E('div', {}, [E('h4', {}, '访问控制'), routerAcl.node]);
        var scopeInfo = E('p', { 'class': 'fii-muted', role: 'status' });
        var lanPanel = card('局域网代理', [lan.node, lanOptions]);
        var routerPanel = card('路由器本机代理', [router.node, routerOptions]);
        var panels = [card('代理设置', [master.node, E('div', { 'class': 'fii-settings-grid' }, [mode.node, udpMode.node, ipv4.node, ipv6.node, dns4.node, dns6.node]), ping.node, scopeInfo])];
        var dnsPanel = E('div', {}, [card('DNS 设置', [dnsMode.node, dns.node, routerDns.node,
            E('div', { 'class': 'fii-settings-grid' }, [servers.node, nodeDns.node]),
            E('details', {}, [E('summary', {}, '高级 DNS 设置'), bootstrap.node, localDns.node]), fakeFilter.node])]);
        var bypassPanel = E('div', {}, [card('绕过策略', [E('div', { 'class': 'fii-settings-grid' }, [china4.node, china6.node]),
            E('p', { 'class': 'fii-muted' }, '内置大陆 IPv4 / IPv6 地址列表，随 FII 软件包更新。'),
            E('div', { 'class': 'fii-settings-grid' }, [tcpPorts.node, udpPorts.node]), E('div', { 'class': 'fii-settings-grid' }, [dscp.node, marks.node]), bypass.node])]);
        var miscPanel = E('div', {}, [card('保留地址', [E('p', { 'class': 'fii-muted' }, '保留地址直接连接，Fake-IP 地址仍按代理规则处理。'), E('div', { 'class': 'fii-settings-grid' }, [reserved4.node, reserved6.node])]),
            card('TUN 设置', [E('div', { 'class': 'fii-settings-grid' }, [tunStack.node, tunMtu.node, tunTimeout.node, tunInterval.node])])]);
        var servicePanel = E('div', {}, [card('启动与更新', [boot.node, auto.node]), card('连接与端口', [
            E('details', {}, [E('summary', {}, '监听端口与控制器'), E('div', { 'class': 'fii-settings-grid' }, Object.keys(ports).map(function(k) { return ports[k].node; })), secret.node]), agent.node])]);
        var proxyPanel = E('div', {}, panels);
        var backups = card('备份与迁移', [E('p', {}, '导入 FII 或 SubConverter Flutter 备份，迁移订阅、覆写和输出，保留当前路由器设置。'), actions([
            button('导出备份', async function() { var r = await call('export'); download('fii-backup.json', JSON.stringify(r.data, null, 2)); }),
            button('导入备份', function() { self.importBackup(); })])]);
        var allPanels = { proxy: proxyPanel, lan: lanPanel, router: routerPanel, bypass: bypassPanel, misc: miscPanel, dns: dnsPanel, service: servicePanel, backup: backups };
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
        [['proxy', '代理设置'], ['lan', '局域网代理'], ['router', '本机代理'], ['bypass', '绕过设置'], ['misc', '其他设置'], ['dns', 'DNS'], ['service', '运行设置'], ['backup', '备份与迁移']].forEach(function(pair) {
            tabs.appendChild(button(pair[1], function() { showSection(pair[0]); }, false, { 'class': 'fii-settings-tab', 'data-section': pair[0] }));
        });
        function updateVisibility() {
            var transparent = master.read();
            mode.control.disabled = !transparent; udpMode.control.disabled = !transparent;
            lanOptions.hidden = !lan.read(); routerOptions.hidden = !router.read();
            fakeFilter.node.hidden = dnsMode.read() !== 'fake-ip';
            dns.control.disabled = !transparent || !lan.read();
            routerDns.control.disabled = !transparent || !router.read();
            [ipv4, ipv6, dns4, dns6, ping].forEach(function(f) { f.control.disabled = !transparent; });
            scopeInfo.textContent = !transparent ? '仅提供代理和 DNS 监听服务。' : [lan.read() ? '局域网设备' : '', router.read() ? '路由器本机' : ''].filter(Boolean).join('、') + (lan.read() || router.read() ? '的流量交由 Mihomo 规则处理。' : '请选择需要代理的流量范围。');
        }
        [master, mode, udpMode, lan, router, dnsMode, ipv6, dns, routerDns].forEach(function(f) { f.control.addEventListener('change', updateVisibility); });
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
                var next = JSON.parse(JSON.stringify(self.data));
                Object.assign(next.settings, { mode: !master.read() ? 'core' : mode.read() === 'redirect' ? 'redirect' : 'tproxy',
                    proxyEnabled: master.read(), tcpMode: mode.read(), udpMode: udpMode.read(), ipv4Proxy: ipv4.read(),
                    dnsIpv4Hijack: dns4.read(), dnsFamilyIndependent: true, fakeIpPing: ping.read(),
                    bypassChina4: china4.read(), bypassChina6: china6.read(), tcpPorts: tcpPorts.read(), udpPorts: udpPorts.read(),
                    bypassDscp: dscp.read(), bypassMarks: marks.read(), reservedIpv4: reserved4.read(), reservedIpv6: reserved6.read(),
                    tunStack: tunStack.read(), tunMtu: tunMtu.read(), tunTimeout: tunTimeout.read(), tunInterval: tunInterval.read(),
                    interfaces: interfaces.read(), ipv6: ipv6.read(), dnsHijack: dns.read(),
                    lanProxy: lan.read(), routerProxy: router.read(), udpProxy: udpMode.read() !== 'off', routerDnsHijack: routerDns.read(), dnsIpv6Hijack: dns6.read(),
                    lanRules: lanAcl.read(), routerRules: routerAcl.read(),
                    dnsMode: dnsMode.read(), defaultNameservers: bootstrap.read(), proxyNameservers: nodeDns.read(), fakeIpFilter: fakeFilter.read(), localDns: localDns.read(),
                    autostart: boot.read(), autoApply: auto.read(), nameservers: servers.read(), bypass: bypass.read(), secret: secret.read(), userAgent: agent.read() });
                Object.keys(ports).forEach(function(k) { next.settings[k] = ports[k].read(); });
                var saved = await call('save', { data: next, revision: self.data.revision });
                self.data = saved.data;
                if (apply) {
                    try {
                        var result = await call('apply', { id: self.status.activeOutputId });
                        notify(result.message + ((result.warnings || []).length ? '；' + result.warnings.join('；') : ''));
                    } catch (err) { throw new Error('设置已保存，应用失败：' + err.message); }
                    finally { await self.reload(); }
                } else { self.draw(); notify('设置已保存，应用后生效'); }
        }
        var canApply = !!self.status.activeOutputId && self.status.coreInstalled;
        var saveActions = actions([button('保存设置', function() { return saveSettings(false); }),
            button('保存并应用', function() { return saveSettings(true); }, true, { disabled: !canApply })]);
        saveActions.classList.add('fii-settings-save');
        saveActions.hidden = currentSection === 'backup';
        var form = E('form', { 'class': 'fii-settings', submit: function(event) { event.preventDefault(); } }, [tabs,
            network.error ? E('p', { 'class': 'fii-notice', role: 'alert' }, '读取网络接口失败：' + network.error + '。可重新载入后重试。') : '',
            proxyPanel, lanPanel, routerPanel, bypassPanel, miscPanel, dnsPanel, servicePanel, backups, saveActions,
            !canApply ? E('p', { 'class': 'fii-muted' }, '首次使用请先在输出配置中选择一份配置并应用。') : '']);
        return form;
    },

    importBackup: function() {
        var self = this, input = E('input', { type: 'file', accept: '.json,application/json', 'aria-label': '选择备份 JSON 文件' });
        var progress = E('p', { role: 'status', 'aria-live': 'polite', 'class': 'fii-muted' }, '');
        function report(p) { progress.textContent = p.message + (p.total ? ' ' + Math.round(p.received * 100 / p.total) + '%' : ''); }
        ui.showModal('导入备份', [E('p', {}, '选择 FII 或 SubConverter Flutter 导出的 JSON 文件。下一步会显示导入摘要。'), input, progress, actions([
            button('取消', function() { ui.hideModal(); }), button('检查备份', async function() {
                if (!input.files.length) throw new Error('请先选择文件');
                if (input.files[0].size > 16 * 1024 * 1024) throw new Error('备份不能超过 16 MiB');
                var raw = JSON.parse(await input.files[0].text());
                progress.textContent = '正在检查备份…';
                var result = await call('import_preview', { data: raw }, report), revision = self.data.revision;
                ui.showModal('确认导入', [E('p', {}, '将替换为 ' + result.summary.sources + ' 个订阅、' + result.summary.overrides + ' 个覆写、' + result.summary.outputs + ' 个输出。'),
                    E('p', { 'class': 'fii-notice' }, result.warnings.join('；') || '保留路由器设置与当前运行配置。'),
                    progress, actions([button('取消', function() { ui.hideModal(); }), button('导入并替换配置库', async function() {
                        progress.textContent = '正在导入备份…';
                        var r = await call('import', { data: raw, revision: revision }, report); notify(r.message); await self.reload(); ui.hideModal();
                    }, true)])]);
            }, true)
        ])]);
    },

    logs: async function() {
        var area = field('最近 150 条 FII 日志', (await call('logs')).text || '暂无日志', { area: true, rows: 18, code: true }); area.control.readOnly = true;
        ui.showModal('运行日志', [area.node, actions([button('刷新', async function() { area.control.value = (await call('logs')).text || '暂无日志'; }), button('关闭', function() { ui.hideModal(); })])], 'fii-modal');
    }
});
