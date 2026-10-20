// SPDX-License-Identifier: GPL-3.0-or-later
// Loaded once inside rpcd. No shell interpolation, blocking subprocesses or
// locks: helper processes go through asynchronous file.exec calls and every
// contended lock falls back to the validated Python worker.
import * as fs from 'fs';
import { timer } from 'uloop';

const root = getenv('FII_ROOT') || '';
const limit = 32 * 1024 * 1024;
const fallback = 'FII_WORKER_REQUIRED';
// Compile regular expressions once, especially bounded repetition in cgroups.
const idPattern = /^[a-f0-9]{32}$/;
const transferPattern = /^[a-f0-9]{32}\.(meta|in|out|checked)$/;
const checkedPattern = /\.checked$/;
const metaPattern = /\.meta$/;
const resultPattern = /\.(out|checked)$/;
const devicePattern = /^[A-Za-z0-9_.:-]{1,15}$/;
const digitsPattern = /^[0-9]+$/;
const spacePattern = /\s+/;
const cgroupPattern = /^[A-Za-z0-9_:@.-]{1,64}$/;
const methods = ['get', 'status', 'save', 'refresh', 'preview', 'apply', 'service', 'export', 'import',
    'import_preview', 'logs', 'job', 'nodes', 'groups', 'select', 'resources', 'resource_update', 'core_info', 'core_check', 'core_update',
    'core_channel', 'network', 'transfer_begin', 'transfer_append', 'transfer_invoke', 'transfer_read',
    'transfer_release', 'transfer_status'];
let documents = {}, coreCache = null, nextWait = 0, waits = {};
function path(name) { return root + name; }
function check(ok, message) { if (!ok) die(message); }
function message(e) { return type(e) == 'object' ? e.message : '' + e; }
function signature(name) {
    let s = fs.stat(path(name));
    return s ? [s.inode, s.size, s.mtime, s.ctime] : null;
}
function same(a, b) { return sprintf('%J', a) == sprintf('%J', b); }
function read(name, maximum) {
    let s = fs.stat(path(name));
    check(s && s.type == 'file' && s.size <= maximum, fallback);
    let fd = fs.open(path(name), 'r');
    check(fd, fallback);
    try {
        let data = fd.read(maximum + 1);
        check(type(data) == 'string' && length(data) <= maximum, fallback);
        fd.close(); return data;
    }
    catch (e) { fd.close(); die(e); }
}
function document(name, maximum, optional) {
    let stamp = signature(name);
    if (!stamp && optional) return {};
    if (documents[name] && same(documents[name].stamp, stamp)) return documents[name].data;
    let data;
    try { data = json(read(name, maximum)); }
    catch (e) { die(fallback); }
    check(same(stamp, signature(name)), fallback);
    documents[name] = { stamp, data };
    return data;
}
function id(value) {
    check(type(value) == 'string' && match(value, idPattern), '传输或任务 ID 无效');
    return value;
}
function locked(action, kind) {
    fs.mkdir(path('/tmp/fii'), 0700);
    fs.mkdir(path('/tmp/fii/transfers'), 0700);
    fs.mkdir(path('/var/lock'), 0755);
    let fd = fs.open(path('/var/lock/fii-' + (kind || 'transfers') + '.lock'), 'a', 0600);
    check(fd, fallback);
    try {
        check(fd.lock('xn'), fallback);
        let result = action(); fd.close(); return result;
    }
    catch (e) { fd.close(); die(e); }
}
function reserve(size) {
    let used = 0, count = 0;
    for (let file in fs.glob(path('/tmp/fii/transfers/*'))) {
        let name = fs.basename(file), s = fs.stat(file);
        if (!match(name, transferPattern) || !s) continue;
        if (time() - s.mtime > (match(name, checkedPattern) ? 900 : 3600)) { fs.unlink(file); continue; }
        if (match(name, metaPattern)) {
            let info = json(read('/tmp/fii/transfers/' + name, 1024));
            used += info.size; count++;
        }
        else if (match(name, resultPattern)) { used += s.size; count++; }
    }
    check(used + size <= 64 * 1024 * 1024 && count < 64, '临时传输空间已满，请稍后重试');
}
function token() {
    let random = fs.open('/dev/urandom', 'r');
    check(random, fallback);
    let ident;
    try { ident = hexenc(random.read(16)); random.close(); }
    catch (e) { random.close(); die(e); }
    return id(ident);
}
function discard(name) { try { fs.unlink(path(name)); } catch (e) {} }
function replace(name, text) {
    let target = path(name), temp = target + '.tmp-' + token();
    let fd = fs.open(temp, 'wx', 0600);
    check(fd, fallback);
    try {
        check(fd.write(text) == length(text), fallback);
        fd.close();
        fs.rename(temp, target);
    }
    catch (e) { fd.close(); fs.unlink(temp); die(e); }
}
function response(result, chunk) {
    let value = { ok: true, result }, text = sprintf('%J', value);
    if (length(text) <= 2048 || (chunk && length(text) <= 32768)) return value;
    check(length(text) <= limit, '响应超过传输限制');
    return locked(function() {
        reserve(length(text));
        let ident = token();
        let name = '/tmp/fii/transfers/' + ident + '.out';
        let fd = fs.open(path(name), 'wx', 0600);
        check(fd, fallback);
        try {
            check(fd.write(text) == length(text), fallback); fd.close();
            check(fs.stat(path(name))?.size == length(text), fallback);
        }
        catch (e) { fd.close(); fs.unlink(path(name)); die(e); }
        return { ok: true, responseTransfer: { id: ident, size: length(text), downloadPath: name } };
    });
}
function summary() {
    let stamp = signature('/etc/fii/state.json');
    let view = document('/tmp/fii/state-view.json', limit);
    // Store replaces state atomically. Accept only Python-validated metadata
    // for that exact inode/size and timestamp, never an independently built view.
    check(view.schema == 2 && stamp && type(view.stamp) == 'array' && length(view.stamp) == 4 &&
        stamp[0] == view.stamp[0] && stamp[1] == view.stamp[1] &&
        stamp[2] == int(view.stamp[2] / 1000000000) && stamp[3] == int(view.stamp[3] / 1000000000), fallback);
    let data = view.data;
    check(type(data) == 'object' && data.format == 'fii-backup' && data.version == 1 &&
        type(data.revision) == 'int' && type(data.sources) == 'array' && type(data.overrides) == 'array' &&
        type(data.outputs) == 'array' && type(data.settings) == 'object', fallback);
    return data;
}
function get(payload) {
    let data = summary();
    if (!('kind' in payload)) {
        check(payload.compact, fallback);
        return response({ data, version: '0.1.1' });
    }
    let kind = payload.kind;
    check(kind in ['sources', 'overrides', 'outputs'], '配置分类无效');
    let item = filter(data[kind], (entry) => entry.id == payload.id)[0];
    check(item, '配置项不存在');
    if (!payload.metadataOnly && kind != 'outputs') {
        let before = signature('/etc/fii/state.json');
        let full = document('/etc/fii/state.json', 16 * 1024 * 1024);
        check(same(before, signature('/etc/fii/state.json')) && full.revision == data.revision, fallback);
        item = filter(full[kind], (entry) => entry.id == payload.id)[0];
        check(item, fallback);
    }
    let result = { item, revision: data.revision };
    if (payload.sync) result.data = data;
    return response(result);
}
function coreStamp() {
    return [signature('/etc/fii/bin/mihomo'), signature('/etc/fii/core-install.json'), signature('/etc/fii/core-channel.json')];
}
function coreLocal(context, done) {
    // Mirror of the Python core.local_info: version probe plus install metadata.
    let install = document('/etc/fii/core-install.json', 64 * 1024, true);
    let selected = document('/etc/fii/core-channel.json', 64 * 1024, true);
    let info = { installed: false, versionText: '', channel: install.channel || '', version: install.version || '',
        updatedAt: install.updatedAt ?? null, digest: install.digest || '',
        selectedChannel: selected.channel ?? (install.channel || 'release'),
        architecture: 'x86_64 / amd64-compatible', path: '/etc/fii/bin/mihomo', supported: false };
    function finish(text) {
        info.versionText = substr(trim(text || ''), 0, 300);
        info.installed = length(info.versionText) > 0;
        return context.call('file', 'exec', { command: '/bin/uname', params: ['-m'] }, function(code, reply) {
            let machine = !code && reply?.code == 0 ? trim(reply.stdout || '') : '';
            info.supported = machine == 'x86_64' || machine == 'amd64';
            done(info);
        });
    }
    let binary = fs.stat(path('/etc/fii/bin/mihomo'));
    if (!(binary?.type == 'file' && (binary.mode & 0x49) != 0)) return finish('');
    return context.call('file', 'exec', { command: '/etc/fii/bin/mihomo', params: ['-v'] }, function(code, reply) {
        finish(!code && reply?.code == 0 ? reply.stdout || '' : '');
    });
}
function withCore(context, operation) {
    let stamp = coreStamp();
    if (coreCache && same(coreCache.stamp, stamp)) return operation(coreCache.value);
    return coreLocal(context, function(core) {
        if (same(stamp, coreStamp())) coreCache = { stamp, value: core };
        try {
            let result = operation(core);
            if (type(result) == 'object') context.reply(result);
        }
        catch (e) { context.reply({ ok: false, error: message(e) }); }
    });
}
function urlencode(text) {
    let out = '';
    for (let position = 0; position < length(text); position++) {
        let character = substr(text, position, 1), byte = ord(character);
        if ((byte >= 0x41 && byte <= 0x5a) || (byte >= 0x61 && byte <= 0x7a) || (byte >= 0x30 && byte <= 0x39) ||
            byte == 0x2e || byte == 0x2d || byte == 0x5f || byte == 0x7e) out += character;
        else out += sprintf('%%%02X', byte);
    }
    return out;
}
function download(context, url, target, timeout, headers, done) {
    // uclient-fetch writes to a private file because rpcd caps exec stdout.
    let params = ['-q', '-O', path(target), '--timeout=' + timeout, '--proxy=off'];
    for (let header in headers) push(params, '--header=' + header);
    push(params, url);
    return context.call('file', 'exec', { command: '/bin/uclient-fetch', params }, function(code, reply) {
        try { done(code, reply, target); }
        catch (e) { discard(target); context.reply({ ok: false, error: message(e) }); }
    });
}
function controllerHost(settings) {
    // Wildcard binds stay reachable through loopback for local probes.
    let address = settings.controllerAddress || '127.0.0.1';
    return address == '0.0.0.0' || address == '::' ? '127.0.0.1' : address;
}
function controllerUrl(settings, target) {
    let host = controllerHost(settings);
    return 'http://' + (index(host, ':') >= 0 ? '[' + host + ']' : host) + ':' + (settings.controllerPort || 9090) + target;
}
function controllerReq(context, verb, settings, target, body, timeout, done) {
    // uclient-fetch cannot issue the PUT/DELETE selection verbs; the node
    // bridge speaks them over loopback and reports the HTTP status as JSON.
    let params = ['/usr/libexec/fii/controller-req.cjs', verb, controllerHost(settings),
        '' + (settings.controllerPort || 9090), settings.secret || '', target, '' + timeout];
    if (body != null) push(params, body);
    return context.call('file', 'exec', { command: '/usr/bin/node', params: params }, function(code, reply) {
        let ok = false;
        try {
            let answer = json(reply?.stdout || '');
            ok = !code && reply?.code == 0 && type(answer) == 'object' && answer.code >= 200 && answer.code < 300;
        }
        catch (e) {}
        try { done(ok); }
        catch (e) { context.reply({ ok: false, error: message(e) }); }
    });
}
function groupsView(context, done) {
    let bundle = document('/etc/fii/active.json', limit, true);
    if (!bundle.outputId || !fs.stat(path('/tmp/fii/ready'))) return done(null);
    let settings = bundle.settings || {};
    return download(context, controllerUrl(settings, '/proxies'),
        '/tmp/fii/groups-' + token() + '.json', 4,
        ['Authorization: Bearer ' + (settings.secret || '')], function(code, reply, target) {
            let parsed = null;
            try { if (!code && reply?.code == 0) parsed = json(read(target, 2 * 1024 * 1024)); }
            catch (e) {}
            discard(target);
            check(parsed && type(parsed) == 'object', 'Mihomo 控制器请求失败，请检查服务状态');
            check(type(parsed.proxies) == 'object', '代理组响应无效');
            let result = [], nodes = {};
            for (let name, value in parsed.proxies) {
                if (type(value) != 'object') continue;
                let history = type(value.history) == 'array' ? value.history : [];
                let last = length(history) && type(history[length(history) - 1]) == 'object' ? history[length(history) - 1] : {};
                nodes[name] = { type: value.type || '',
                    alive: type(value.alive) == 'bool' ? value.alive : null,
                    delay: type(last.delay) == 'int' && last.delay >= 0 && last.delay <= 65535 ? last.delay : null,
                    testedAt: type(last.time) == 'string' ? last.time : '' };
                if (type(value.all) == 'array' && name != 'GLOBAL')
                    push(result, { name: name, type: value.type || '', now: value.now ?? '',
                        fixed: type(value.fixed) == 'string' ? value.fixed : '',
                        all: filter(value.all, (item) => type(item) == 'string') });
            }
            done({ groups: result, nodes: nodes, running: true, source: 'runtime' });
        });
}
function groupsRpc(context) {
    return groupsView(context, function(view) {
        // A stopped core still needs the YAML snapshot only the worker renders.
        if (!view) return context.worker('groups', {}, context.reply);
        context.reply(response(view));
    });
}
function selectRpc(payload, context) {
    let group = payload.group, name = payload.name, action = payload.action ?? 'select';
    check(type(group) == 'string', '代理组名称必须是文本');
    check(action == 'select' || action == 'unpin', '代理组操作无效');
    if (action == 'select') check(type(name) == 'string', '节点名称必须是文本');
    else check(name == null, '取消钉住不接受节点名称');
    let bundle = document('/etc/fii/active.json', limit, true);
    check(bundle.outputId && fs.stat(path('/tmp/fii/ready')), '内核已停止，当前仅展示配置中的代理组');
    return groupsView(context, function(view) {
        let available = filter(view.groups, (entry) => entry.name == group)[0];
        check(available, '代理组不存在');
        let automatic = available.type == 'URLTest' || available.type == 'Fallback';
        let target = '/proxies/' + urlencode(group);
        if (action == 'unpin') {
            check(automatic, '只有自动测速组和故障转移组支持取消钉住');
            return controllerReq(context, 'DELETE', bundle.settings, target, null, 4, function(ok) {
                check(ok, 'Mihomo 控制器请求失败，请检查服务状态');
                context.reply(response({ message: '已取消钉住，恢复自动选择' }));
            });
        }
        check((available.type == 'Selector' || automatic) && index(available.all, name) >= 0, '代理组不支持选择或节点不存在');
        // Fallback.Set may synchronously health-check a dead node for up to 5s.
        return controllerReq(context, 'PUT', bundle.settings, target, sprintf('%J', { name: name }),
            available.type == 'Fallback' ? 8 : 4, function(ok) {
                check(ok, 'Mihomo 控制器请求失败，请检查服务状态');
                context.reply(response({ message: (automatic ? '已钉住 ' : '已切换到 ') + name }));
            });
    });
}
function channelName(value) {
    check(value == 'release' || value == 'alpha', '内核渠道必须是 Release 或 Alpha');
    return value;
}
function coreChannel(payload) {
    let channel = channelName(payload.channel);
    // The lock name matches the Python worker, so both runtimes exclude each other.
    return locked(function() {
        replace('/etc/fii/core-channel.json', sprintf('%J\n', { channel: channel }));
        return response({ selectedChannel: channel });
    }, 'core-channel');
}
function releaseMetadata(channel, target, code, reply) {
    if (code || reply?.code != 0) {
        let text = (reply?.stderr || '') + (reply?.stdout || '');
        let status = match(text, /HTTP error ([0-9]+)/);
        if (status) {
            let http = int(status[1]);
            die(http == 403 || http == 429 ? 'GitHub 访问受限或达到请求频率限制，请稍后重试'
                : 'GitHub 下载失败 (HTTP ' + http + ')，当前内核保持不变');
        }
        die('无法连接 GitHub (uclient-fetch)，请检查路由器网络后重试');
    }
    let info = fs.stat(path(target));
    check(info?.type == 'file', '无法连接 GitHub (uclient-fetch)，请检查路由器网络后重试');
    check(info.size <= 4 * 1024 * 1024, 'GitHub 发布信息过大');
    let release = json(read(target, 4 * 1024 * 1024));
    check(type(release) == 'object', 'GitHub 发布信息无效');
    check(!release.draft, 'GitHub 返回了未发布版本');
    let tag = release.tag_name;
    if (channel == 'release') check(!release.prerelease && type(tag) == 'string' && match(tag, /^v[0-9]+\.[0-9]+\.[0-9]+$/), 'Release 版本信息无效');
    else check(tag == 'Prerelease-Alpha' && release.prerelease, 'Alpha 版本信息无效');
    let asset = 'mihomo-linux-amd64-compatible-' + tag + '.gz';
    let found = filter(type(release.assets) == 'array' ? release.assets : [], (entry) => type(entry) == 'object' && type(entry.name) == 'string' &&
        (channel == 'release' ? entry.name == asset : !!match(entry.name, /^mihomo-linux-amd64-compatible-(alpha-[A-Za-z0-9]+)\.gz$/)));
    check(length(found) == 1, '未找到唯一的官方 amd64-compatible 内核文件');
    let chosen = found[0];
    let digest = chosen.digest || '';
    check(match(digest, /^sha256:[a-fA-F0-9]{64}$/), '官方内核缺少 SHA-256 摘要，未执行下载');
    check(type(chosen.size) == 'int' && chosen.size > 0 && chosen.size <= 64 * 1024 * 1024, '内核压缩包大小无效');
    let version = channel == 'release' ? tag : match(chosen.name, /^mihomo-linux-amd64-compatible-(alpha-[A-Za-z0-9]+)\.gz$/)[1];
    let url = 'https://github.com/MetaCubeX/mihomo/releases/download/' + urlencode(tag) + '/' + chosen.name;
    check(chosen.browser_download_url == url, '内核下载地址不是预期的官方发布地址');
    return { channel: channel, version: version, tag: tag, asset: chosen.name,
        assetId: chosen.id ?? null, size: chosen.size, digest: lc(digest),
        publishedAt: chosen.updated_at || release.published_at || null,
        releaseUrl: 'https://github.com/MetaCubeX/mihomo/releases/tag/' + urlencode(tag) };
}
function coreCheck(payload, context) {
    let channel = channelName(payload.channel);
    return download(context, 'https://api.github.com/repos/MetaCubeX/mihomo/releases/' + (channel == 'release' ? 'latest' : 'tags/Prerelease-Alpha'),
        '/tmp/fii/core-check-' + token() + '.json', 20,
        ['User-Agent: FII-Core-Manager/0.1.1', 'Accept: application/vnd.github+json'], function(code, reply, target) {
            let remote = releaseMetadata(channel, target, code, reply);
            discard(target);
            // A cache hit returns synchronously; a miss replies from coreLocal.
            let result = withCore(context, function(core) {
                return response({ remote: remote, current: core,
                    updateAvailable: !core.installed || core.digest != remote.digest || core.channel != channel });
            });
            if (type(result) == 'object') context.reply(result);
        });
}
function status(context) {
    let bundle = document('/etc/fii/active.json', limit, true);
    let settings = bundle.settings || {};
    return withCore(context, function(core) {
        let protocols = {};
        if (bundle.outputId) {
            protocols = settings.mode == 'core' || settings.proxyEnabled === false ? { tcp: 'off', udp: 'off' } : {
                tcp: (settings.tcpMode || 'inherit') == 'inherit' ? settings.mode : settings.tcpMode,
                udp: (settings.udpMode || 'inherit') == 'inherit' ? (settings.udpProxy !== false ? 'tproxy' : 'off') : settings.udpMode
            };
        }
        let result = { coreInstalled: core.installed, coreVersion: core.versionText,
            coreChannel: core.channel, coreSelectedChannel: core.selectedChannel, running: false,
            activeOutputId: bundle.outputId || '', activeName: bundle.name || '',
            appliedAt: bundle.appliedAt ?? null, appliedRevision: bundle.revision ?? null,
            mode: settings.mode || '', proxyEnabled: !!bundle.outputId && settings.proxyEnabled !== false,
            protocols, lanProxy: !!bundle.outputId && settings.lanProxy !== false,
            routerProxy: !!bundle.outputId && !!settings.routerProxy, interfaces: settings.interfaces || [] };
        if (!bundle.outputId || !fs.stat(path('/tmp/fii/ready'))) return response(result);
        let activeStamp = signature('/etc/fii/active.json');
        return context.call('file', 'exec', { command: '/bin/uclient-fetch',
            params: ['-q', '-O', '-', '--timeout=1', '--header=Authorization: Bearer ' + settings.secret,
                controllerUrl(settings, '/version')] }, function(code, reply) {
            try { result.running = !code && reply.code == 0 && length(reply.stdout || '') <= 8192 && type(json(reply.stdout || '')) == 'object'; }
            catch (e) { result.running = false; }
            result.running = result.running && !!fs.stat(path('/tmp/fii/ready')) && same(activeStamp, signature('/etc/fii/active.json'));
            context.reply(response(result));
        });
    });
}
function job(payload, context) {
    let ident = id(payload.id), wait = payload.waitMs ?? 0;
    check(type(wait) == 'int' && wait >= 0 && wait <= 1000, '任务等待时间无效');
    function readJob() {
        let value;
        try { value = json(read('/tmp/fii/jobs/' + ident + '.json', limit)); }
        catch (e) { die('任务记录不存在或无法读取，可能路由器已重启'); }
        if (!value.done && time() - value.createdAt > (value.method == 'core_update' || value.method == 'resource_update' ? 900 : 180))
            value = { done: true, ok: false, error: '任务未完成，请刷新页面并查看日志' };
        return value;
    }
    let value = readJob();
    if (value.done || !wait) return response(value);
    // OpenWrt 24.10's rpcd predates request.defer(). Return immediately there;
    // the browser polls cheaply instead of blocking rpcd or spawning a sleeper.
    if (!context.defer) { value.pollAfterMs = 100; return response(value); }
    check(length(keys(waits)) < 32, '等待中的任务过多，请稍后重试');
    let key = '' + nextWait++, remaining = wait;
    waits[key] = timer(min(25, remaining), function() {
        remaining -= min(25, remaining);
        try {
            let result = readJob();
            if (result.done || remaining <= 0) {
                waits[key].cancel(); delete waits[key];
                context.reply(response(result)); return;
            }
            waits[key].set(min(25, remaining));
        }
        catch (e) {
            if (waits[key]) { waits[key].cancel(); delete waits[key]; }
            if (message(e) == fallback) context.worker('job', payload, context.reply);
            else context.reply({ ok: false, error: '读取任务结果失败，请重新载入' });
        }
    });
    return context.defer();
}
function transfer(method, payload) {
    let ident = id(payload.id), prefix = '/tmp/fii/transfers/' + ident;
    return locked(function() {
        if (method == 'transfer_release') {
            for (let suffix in ['.in', '.meta', '.out', '.checked']) fs.unlink(path(prefix + suffix));
            return response({});
        }
        if (method == 'transfer_status') {
            let meta = json(read(prefix + '.meta', 1024)), s = fs.stat(path(prefix + '.in'));
            let received = s ? s.size : 0;
            return response({ received, complete: received == meta.size });
        }
        let offset = payload.offset, size = payload.size ?? 16384, s = fs.stat(path(prefix + '.out'));
        check(type(size) == 'int' && size >= 1 && size <= 16384, '响应分块大小无效');
        check(s, '响应传输已失效，请重新载入');
        check(type(offset) == 'int' && offset >= 0 && offset < s.size, '响应偏移无效');
        let fd = fs.open(path(prefix + '.out'), 'r');
        check(fd, fallback);
        try { check(fd.seek(offset), fallback); let result = response({ data: b64enc(fd.read(size)) }, true); fd.close(); return result; }
        catch (e) { fd.close(); die(e); }
    });
}
function choices(networks, links) {
    let devices = {};
    function device(name) { return type(name) == 'string' && match(name, devicePattern) && !(name in ['lo', 'fii-tun', 'fii-dummy']); }
    for (let link in links) {
        if (device(link.ifname)) devices[link.ifname] = { name: link.ifname, networks: [], up: 'UP' in (link.flags || []) };
    }
    for (let entry in (networks.interface || [])) {
        let name = entry.l3_device || entry.device;
        if (!device(name)) continue;
        if (!devices[name]) devices[name] = { name, networks: [], up: !!entry.up };
        push(devices[name].networks, entry.interface || name);
    }
    function lines(name, count) {
        if (!fs.stat(path(name))) return [];
        return slice(split(read(name, 256 * 1024), '\n'), 0, count);
    }
    let hosts = [], users = [], groups = [], cgroups = [];
    for (let line in lines('/tmp/dhcp.leases', 512)) {
        let parts = split(trim(line), spacePattern);
        if (length(parts) >= 4) push(hosts, { mac: parts[1], ip: parts[2], name: parts[3] == '*' ? '' : parts[3] });
    }
    for (let line in lines('/etc/passwd', 256)) {
        let parts = split(line, ':');
        if (length(parts) > 2 && match(parts[2], digitsPattern)) push(users, { id: parts[2], name: parts[0] });
    }
    for (let line in lines('/etc/group', 256)) {
        let parts = split(line, ':');
        if (length(parts) > 2 && match(parts[2], digitsPattern)) push(groups, { id: parts[2], name: parts[0] });
    }
    let cgroupV2 = !!fs.stat(path('/sys/fs/cgroup/cgroup.controllers'));
    if (cgroupV2) {
        let pending = [{ name: '/sys/fs/cgroup', depth: 0 }];
        while (length(pending) && length(cgroups) < 512) {
            let current = pop(pending);
            if (current.depth >= 6) continue;
            for (let child in fs.glob(path(current.name + '/*'))) {
                let info = fs.lstat(child), name = substr(child, length(path('/sys/fs/cgroup/')));
                if (info?.type != 'directory' || length(filter(split(name, '/'), (part) => !match(part, cgroupPattern)))) continue;
                if (length(cgroups) >= 512) break;
                push(cgroups, { id: name, name });
                push(pending, { name: substr(child, length(root)), depth: current.depth + 1 });
            }
        }
    }
    let list = values(devices);
    sort(list, (a, b) => (!!length(b.networks) - !!length(a.networks)) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    return response({ devices: list, hosts, users, groups, cgroups, cgroupV2 });
}
function network(context) {
    return context.call('network.interface', 'dump', {}, function(code, networks) {
        if (code || type(networks) != 'object') networks = {};
        return context.call('file', 'exec', { command: '/sbin/ip', params: ['-j', 'link', 'show'] }, function(code, result) {
            let links = [];
            try { if (!code && result.code == 0) links = json(result.stdout || '[]'); }
            catch (e) { links = []; }
            try { context.reply(choices(networks, type(links) == 'array' ? links : [])); }
            catch (e) { context.worker('network', {}, context.reply); }
        });
    });
}
function logs(context) {
    // Native asynchronous read: opening a log page never spawns the YAML worker.
    return context.call('file', 'exec', { command: '/sbin/logread',
        // logread matches the raw tag-prefixed message with POSIX BRE.
        params: ['-e', '^\\(fii\\|fii-prepare\\|mihomo\\|core-run\\)\\(\\[[0-9][0-9]*\\]\\)\\{0,1\\}:', '-l', '500'] }, function(code, result) {
        if (code || result?.code != 0) {
            context.reply({ ok: false, error: '读取系统日志失败，请稍后重试' }); return;
        }
        let text = result.stdout || '', clipped = length(text) > 98304, preparation = '';
        if (clipped) { text = substr(text, length(text) - 98304); let newline = index(text, '\n'); text = newline >= 0 ? substr(text, newline + 1) : ''; }
        let info = fs.stat(path('/tmp/fii/prepare.log'));
        try {
            if (info?.type == 'file') {
                let fd = fs.open(path('/tmp/fii/prepare.log'), 'r');
                check(fd, '无法读取准备日志');
                try {
                    if (info.size > 16384) fd.seek(info.size - 16384);
                    preparation = fd.read(16384) || ''; fd.close();
                } catch (e) { fd.close(); die(e); }
                if (info.size > 16384) { let newline = index(preparation, '\n'); preparation = newline >= 0 ? substr(preparation, newline + 1) : ''; }
            }
            context.reply(response({ text, prepareText: preparation, limit: 500, truncated: clipped }));
        } catch (e) { context.reply({ ok: false, error: '读取日志失败：' + message(e) }); }
    });
}
function call(method, raw, context) {
    let payload;
    try { payload = json(raw || '{}'); }
    catch (e) { return { ok: false, error: '请求参数不是有效 JSON' }; }
    if (type(payload) != 'object') return { ok: false, error: '请求参数必须是对象' };
    try {
        if (method == 'get') return get(payload);
        if (method == 'core_info') return withCore(context, (value) => response(value));
        if (method == 'core_channel') return coreChannel(payload);
        if (method == 'core_check') return coreCheck(payload, context);
        if (method == 'groups') return groupsRpc(context);
        if (method == 'select') return selectRpc(payload, context);
        if (method == 'status') return status(context);
        if (method == 'job') return job(payload, context);
        if (method == 'network') return network(context);
        if (method == 'logs') return logs(context);
        if (method in ['transfer_read', 'transfer_release', 'transfer_status']) return transfer(method, payload);
    }
    catch (e) {
        if (message(e) != fallback) return { ok: false, error: message(e) };
    }
    return context.worker(method, payload, context.reply);
}
return { methods, call };
