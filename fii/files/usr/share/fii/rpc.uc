// SPDX-License-Identifier: GPL-3.0-or-later
// Loaded once inside rpcd. No shell interpolation, blocking subprocesses or locks.
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
    'import_preview', 'logs', 'job', 'nodes', 'groups', 'select', 'core_info', 'core_check', 'core_update',
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
function locked(action) {
    fs.mkdir(path('/tmp/fii'), 0700);
    fs.mkdir(path('/tmp/fii/transfers'), 0700);
    fs.mkdir(path('/var/lock'), 0755);
    let fd = fs.open(path('/var/lock/fii-transfers.lock'), 'a', 0600);
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
function response(result, chunk) {
    let value = { ok: true, result }, text = sprintf('%J', value);
    if (length(text) <= 2048 || (chunk && length(text) <= 32768)) return value;
    check(length(text) <= limit, '响应超过传输限制');
    return locked(function() {
        reserve(length(text));
        let random = fs.open('/dev/urandom', 'r');
        check(random, fallback);
        let ident;
        try { ident = hexenc(random.read(16)); random.close(); }
        catch (e) { random.close(); die(e); }
        id(ident);
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
function withCore(context, operation) {
    let stamp = coreStamp();
    if (coreCache && same(coreCache.stamp, stamp)) return operation(coreCache.value);
    return context.worker('core_info', {}, function(reply) {
        if (!reply?.ok || !reply.result) { context.reply(reply); return; }
        if (same(stamp, coreStamp())) coreCache = { stamp, value: reply.result };
        let result = operation(reply.result);
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
                'http://127.0.0.1:' + settings.controllerPort + '/version'] }, function(code, reply) {
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
        if (!value.done && time() - value.createdAt > (value.method == 'core_update' ? 900 : 180))
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
        params: ['-e', ' (fii(-prepare)?|mihomo)(\\[[0-9]+\\])?:', '-l', '500'] }, function(code, result) {
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
