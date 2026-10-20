// Runs only against the generated FII_ROOT fixture from verify_rpc_ucode.py.
import * as fs from 'fs';
import * as uloop from 'uloop';
const root = getenv('FII_ROOT');
if (!root || !match(root, /^\/tmp\/fii-rpc-verify-/)) die('An isolated verification root is required');
const api = loadfile(getenv('FII_RPC_MODULE'))();
const expected = json(fs.readfile(root + '/expected.json'));
let checks = [], workers = 0, received = null, pending = false, async = false, healthy = true, logFailure = false, logOutput = '';
const jobId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
function check(value, text) { if (!value) die('FAILED: ' + text); }
function write(name, content) {
    let fd = fs.open(name, 'w', 0600);
    check(fd && fd.write(content) == length(content), 'fixture write'); fd.close();
}
function same(a, b) { return sprintf('%J', a) == sprintf('%J', b); }
function test(name, action) { action(); push(checks, name); }
const context = {
    reply: function(value) { received = value; if (async) uloop.end(); },
    defer: function() { pending = true; return null; },
    worker: function(method, payload, done) {
        workers++;
        if (method == 'core_info') done({ ok: true, result: { installed: true, versionText: 'Fixture', channel: 'release', selectedChannel: 'release' } });
        else done({ ok: false, error: 'worker-fallback:' + method });
        return null;
    },
    call: function(object, method, payload, done) {
        if (object == 'network.interface') done(0, { interface: [{ interface: 'lan', l3_device: 'br-lan', up: true }] });
        else if (payload.command == '/sbin/ip') done(0, { code: 0, stdout: '[{"ifname":"br-lan","flags":["UP"]},{"ifname":"lo","flags":["UP"]}]' });
        else if (payload.command == '/sbin/logread') {
            check(same(payload.params, ['-e', ' (fii(-prepare)?|mihomo)(\\[[0-9]+\\])?:', '-l', '500']), 'bounded tag-filtered log query');
            done(logFailure ? 1 : 0, { code: logFailure ? 1 : 0, stdout: logOutput });
        }
        else {
            check(payload.command == '/bin/uclient-fetch', 'readiness uses only the HTTP client');
            done(0, { code: 0, stdout: healthy ? '{"version":"fixture"}' : '[]' });
        }
        return null;
    }
};
function invoke(method, payload) {
    received = null; pending = false;
    let value = api.call(method, sprintf('%J', payload || {}), context);
    return value || received;
}
function unwrap(reply) {
    check(reply?.ok, 'successful reply: ' + (reply?.error || 'missing response'));
    if (!reply.responseTransfer) return reply.result;
    let transfer = reply.responseTransfer;
    check(transfer.downloadPath == '/tmp/fii/transfers/' + transfer.id + '.out', 'download path is constrained');
    check(fs.stat(root + transfer.downloadPath).mode == 0600, 'response files remain private');
    let content = fs.readfile(root + transfer.downloadPath);
    check(length(content) == transfer.size, 'download byte size');
    let rebuilt = '';
    for (let offset = 0; offset < transfer.size; offset += 1024) {
        let chunk = invoke('transfer_read', { id: transfer.id, offset, size: 1024 });
        check(chunk?.ok, 'chunk reply'); rebuilt += b64dec(chunk.result.data);
    }
    check(rebuilt == content, 'Unicode content survives byte chunks');
    check(invoke('transfer_release', { id: transfer.id })?.ok, 'transfer released');
    check(!fs.stat(root + transfer.downloadPath), 'private output removed');
    return json(content).result;
}

test('logs use native bounded reads and surface failures without a YAML worker', function() {
    let count = workers;
    logOutput = 'Tue Oct 20 12:00:00 2026 daemon.info fii[123]: level=info msg="fixture"\n';
    let result = unwrap(invoke('logs'));
    check(result.text == logOutput && result.limit == 500 && !result.truncated && result.prepareText == '', 'log snapshot');
    fs.mkdir(root + '/tmp/fii', 0700);
    write(root + '/tmp/fii/prepare.log', 'prepare fixture\n');
    check(unwrap(invoke('logs')).prepareText == 'prepare fixture\n', 'preparation snapshot is separate');
    write(root + '/tmp/fii/prepare.log', 'old\n' + sprintf('%020000d', 1) + '\nlast line\n');
    logOutput = sprintf('%0100000d', 1) + '\nlast line\n';
    result = unwrap(invoke('logs'));
    check(length(result.text) <= 98304 && result.truncated && result.text == 'last line\n', 'bounded complete syslog lines');
    check(length(result.prepareText) <= 16384 && result.prepareText == 'last line\n', 'bounded preparation tail');
    logOutput = sprintf('%0100000d', 1);
    check(unwrap(invoke('logs')).text == '', 'oversized incomplete line is omitted');
    logFailure = true;
    check(!invoke('logs').ok && workers == count, 'failure is visible and no worker starts');
    logFailure = false;
});
test('metadata and full editor contents match the existing validated Python API', function() {
    check(same(unwrap(invoke('get', { compact: true })), expected.compact), 'compact equality');
    for (let entry in [['source', 'sources'], ['override', 'overrides'], ['output', 'outputs']]) {
        let itemId = entry[0] == 'override' ? 'script' : entry[0];
        check(same(unwrap(invoke('get', { kind: entry[1], id: itemId, sync: true })), expected[entry[0]]), 'editor equality ' + entry[0]);
    }
    check(workers == 0, 'reads and result transfers do not start Python');
});
test('invalid arguments are rejected without escaping the private directories', function() {
    for (let value in ['../state', '', null]) check(!invoke('transfer_read', { id: value, offset: 0 }).ok, 'invalid transfer ID');
    check(!api.call('get', '{bad', context).ok, 'invalid JSON');
    check(!api.call('get', '[]', context).ok, 'non-object payload');
    check(!invoke('get', { kind: 'settings', id: 'x', metadataOnly: true }).ok, 'invalid kind');
    check(!invoke('job', { id: jobId, waitMs: 1001 }).ok, 'unbounded wait');
});
test('core metadata is cached and running status retains controller readiness', function() {
    let first = unwrap(invoke('status'));
    check(!first.running && first.coreInstalled && first.protocols.tcp == 'tproxy', 'stopped status');
    let count = workers;
    check(unwrap(invoke('core_info')).versionText == 'Fixture', 'core metadata');
    check(!unwrap(invoke('status')).running && workers == count, 'no repeated Python version calls');
    write(root + '/tmp/fii/ready', 'fixture\n');
    check(unwrap(invoke('status')).running, 'controller-ready status');
    healthy = false;
    check(!unwrap(invoke('status')).running, 'invalid readiness response is not running');
    healthy = true;
    fs.unlink(root + '/tmp/fii/ready');
});
test('network choices use native asynchronous system queries', function() {
    let count = workers, result = unwrap(invoke('network'));
    check(length(result.devices) == 1 && result.devices[0].name == 'br-lan', 'device exclusions');
    check(result.devices[0].networks[0] == 'lan' && result.devices[0].up, 'network mapping');
    check(result.hosts[0].name == 'desktop' && result.users[0].id == '0' && result.groups[1].id == '100', 'host and account parsing');
    check(workers == count, 'network queries do not start Python');
});
test('old rpcd returns pending jobs promptly without requiring request.defer', function() {
    let deferred = context.defer;
    context.defer = null;
    let value = unwrap(invoke('job', { id: jobId, waitMs: 600 }));
    check(!value.done && value.pollAfterMs == 100 && !pending, 'immediate bounded polling hint');
    context.defer = deferred;
});
test('job waiting yields the event loop and returns completion', function() {
    uloop.init();
    let touched = false;
    let writer = uloop.timer(40, function() {
        touched = true;
        write(root + '/tmp/fii/jobs/' + jobId + '.json', '{"done":true,"ok":true,"result":{"value":"finished"}}');
    });
    async = true;
    invoke('job', { id: jobId, waitMs: 600 });
    check(pending, 'deferred request');
    let guard = uloop.timer(1500, () => { die('job wait blocked or failed'); });
    uloop.run(); async = false; guard.cancel(); writer.cancel();
    check(touched && unwrap(received).result.value == 'finished', 'timer callback progressed while request waited');
});
test('busy transfer locks fall back without blocking the RPC event loop', function() {
    let output = invoke('get', { kind: 'sources', id: 'source' }).responseTransfer;
    check(output, 'generated bounded output');
    let lock = fs.open(root + '/var/lock/fii-transfers.lock', 'a', 0600);
    check(lock.lock('xn'), 'test owns transfer lock');
    let value = invoke('transfer_read', { id: output.id, offset: 0 });
    check(value.error == 'worker-fallback:transfer_read', 'contended operation delegates instead of blocking');
    lock.close();
    check(invoke('transfer_read', { id: output.id, offset: 0 }).ok, 'native read recovers after lock release');
    invoke('transfer_release', { id: output.id });
});
test('native response files enforce the shared transfer reservation quota', function() {
    for (let i = 0; i < 64; i++) write(root + '/tmp/fii/transfers/' + sprintf('%032x', i) + '.meta', '{"size":1}');
    check(!invoke('get', { kind: 'sources', id: 'source' }).ok, 'quota rejects additional response reservation');
    for (let i = 0; i < 64; i++) invoke('transfer_release', { id: sprintf('%032x', i) });
    check(unwrap(invoke('get', { compact: true })).data.revision == expected.compact.data.revision, 'quota cleanup recovers');
});
test('atomic state and metadata replacement invalidate cached editor data', function() {
    let state = json(fs.readfile(root + '/etc/fii/state.json'));
    state.sources[0].name = 'Replaced source'; state.revision++;
    write(root + '/etc/fii/state.next', sprintf('%J', state));
    fs.rename(root + '/etc/fii/state.next', root + '/etc/fii/state.json');
    let view = json(fs.readfile(root + '/tmp/fii/state-view.json'));
    let stat = fs.stat(root + '/etc/fii/state.json');
    view.stamp = [stat.inode, stat.size, stat.mtime * 1000000000, stat.ctime * 1000000000];
    view.data.sources[0].name = 'Replaced source'; view.data.revision++;
    write(root + '/tmp/fii/view.next', sprintf('%J', view));
    fs.rename(root + '/tmp/fii/view.next', root + '/tmp/fii/state-view.json');
    check(unwrap(invoke('get', { compact: true })).data.sources[0].name == 'Replaced source', 'metadata invalidated');
    check(unwrap(invoke('get', { kind: 'sources', id: 'source' })).item.name == 'Replaced source', 'full editor invalidated');
});
test('old metadata schema and virtual migration editors use the validator', function() {
    let view = json(fs.readfile(root + '/tmp/fii/state-view.json'));
    view.schema = 1;
    write(root + '/tmp/fii/view.next', sprintf('%J', view));
    fs.rename(root + '/tmp/fii/view.next', root + '/tmp/fii/state-view.json');
    check(invoke('get', { compact: true }).error == 'worker-fallback:get', 'old schema is not served');
    view.schema = 2;
    push(view.data.overrides, { id: 'virtual-base', name: 'Migrated policy', type: 'YAML', contentOmitted: true });
    write(root + '/tmp/fii/view.next', sprintf('%J', view));
    fs.rename(root + '/tmp/fii/view.next', root + '/tmp/fii/state-view.json');
    check(unwrap(invoke('get', { kind: 'overrides', id: 'virtual-base', metadataOnly: true })).item.name == 'Migrated policy', 'normalized metadata is available');
    check(invoke('get', { kind: 'overrides', id: 'virtual-base' }).error == 'worker-fallback:get', 'missing virtual body uses normalized Python read');
});
test('cold or corrupt metadata delegates to the existing validator', function() {
    fs.rename(root + '/tmp/fii/state-view.json', root + '/tmp/fii/state-view.saved');
    let result = invoke('get', { compact: true });
    check(result.error == 'worker-fallback:get', 'cold metadata uses worker');
    write(root + '/tmp/fii/state-view.json', '{broken');
    check(invoke('get', { compact: true }).error == 'worker-fallback:get', 'corrupt metadata uses worker');
});
printf('%J\n', { passed: length(checks), checks });
