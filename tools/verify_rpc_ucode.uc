// Runs only against the generated FII_ROOT fixture from verify_rpc_ucode.py.
import * as fs from 'fs';
import * as uloop from 'uloop';
const root = getenv('FII_ROOT');
if (!root || !match(root, /^\/tmp\/fii-rpc-verify-/)) die('An isolated verification root is required');
const api = loadfile(getenv('FII_RPC_MODULE'))();
const expected = json(fs.readfile(root + '/expected.json'));
let checks = [], workers = 0, received = null, pending = false, async = false, healthy = true, logFailure = false, logOutput = '';
let versionCalls = 0, machine = 'x86_64', mihomoVersion = 'Mihomo Meta v1.2.3 fixture\n';
let nodeAnswer = { code: 204 }, nodeCalls = [];
let fetchFailure = 0, fetchStderr = '', fetchParams = null;
let githubBody = '{}';
let proxiesBody = '{"proxies":{' +
    '"PROXY":{"type":"Selector","now":"香港🚀","fixed":"","all":["香港🚀","日本","DIRECT"],"alive":true},' +
    '"AUTO":{"type":"URLTest","now":"香港🚀","all":["香港🚀","日本"]},' +
    '"分组🚀":{"type":"Selector","now":"香港🚀","all":["香港🚀"]},' +
    '"香港🚀":{"type":"Shadowsocks","alive":true,"history":[{"delay":42,"time":"2026-10-20T00:00:00Z"}]},' +
    '"日本":{"type":"VMess","history":[]},' +
    '"GLOBAL":{"type":"Selector","now":"PROXY","all":["PROXY","AUTO"]},' +
    '"weird":"not-an-object"}}';
const jobId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
function check(value, text) { if (!value) die('FAILED: ' + text); }
function write(name, content) {
    let fd = fs.open(name, 'w', 0600);
    check(fd && fd.write(content) == length(content), 'fixture write'); fd.close();
}
function writeMode(name, content, mode) {
    let fd = fs.open(name, 'w', mode);
    check(fd && fd.write(content) == length(content), 'fixture write'); fd.close();
}
function same(a, b) { return sprintf('%J', a) == sprintf('%J', b); }
function test(name, action) { action(); push(checks, name); }
const context = {
    reply: function(value) { received = value; if (async) uloop.end(); },
    defer: function() { pending = true; return null; },
    worker: function(method, payload, done) {
        workers++;
        if (method == 'groups' && payload.orderOnly)
            return done({ ok: true, result: { order: expected.groupOrder } });
        done({ ok: false, error: 'worker-fallback:' + method });
        return null;
    },
    call: function(object, method, payload, done) {
        if (object == 'network.interface') done(0, { interface: [{ interface: 'lan', l3_device: 'br-lan', up: true }] });
        else if (payload.command == '/sbin/ip') done(0, { code: 0, stdout: '[{"ifname":"br-lan","flags":["UP"]},{"ifname":"lo","flags":["UP"]}]' });
        else if (payload.command == '/sbin/logread') {
            check(same(payload.params, ['-e', '^\\(fii\\|fii-prepare\\|mihomo\\|core-run\\)\\(\\[[0-9][0-9]*\\]\\)\\{0,1\\}:', '-l', '500']), 'bounded raw-tag BRE log query');
            done(logFailure ? 1 : 0, { code: logFailure ? 1 : 0, stdout: logOutput });
        }
        else if (payload.command == '/etc/fii/bin/mihomo') { versionCalls++; done(0, { code: 0, stdout: mihomoVersion }); }
        else if (payload.command == '/bin/uname') done(0, { code: 0, stdout: machine });
        else if (payload.command == '/usr/bin/node') {
            push(nodeCalls, payload.params);
            done(0, { code: 0, stdout: sprintf('%J', nodeAnswer) });
        }
        else if (payload.command == '/bin/uclient-fetch') {
            fetchParams = payload.params;
            let url = payload.params[length(payload.params) - 1];
            if (payload.params[index(payload.params, '-O') + 1] == '-') done(0, { code: 0, stdout: healthy ? '{"version":"fixture"}' : '[]' });
            else {
                if (!fetchFailure) write(payload.params[index(payload.params, '-O') + 1], match(url, /api\.github\.com/) ? githubBody : proxiesBody);
                done(fetchFailure, { code: fetchFailure, stderr: fetchStderr });
            }
        }
        else die('unexpected exec ' + payload.command);
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
    logOutput = 'Tue Oct 20 12:00:00 2026 daemon.info core-run[123]: level=info msg="fixture"\n';
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
test('core metadata is gathered natively and cached by file signature', function() {
    fs.mkdir(root + '/etc/fii/bin', 0755);
    writeMode(root + '/etc/fii/bin/mihomo', '#!stub\n', 0755);
    let count = workers;
    let info = unwrap(invoke('core_info'));
    check(info.installed && info.versionText == trim(mihomoVersion) && info.supported, 'native version probe');
    check(info.channel == '' && info.selectedChannel == 'release' && info.digest == '' && info.updatedAt == null, 'cold install metadata defaults');
    check(info.architecture == 'x86_64 / amd64-compatible' && info.path == '/etc/fii/bin/mihomo', 'static identity fields');
    check(workers == count && versionCalls == 1, 'no Python involved in core metadata');
    let hex64 = '';
    for (let i = 0; i < 32; i++) hex64 += 'ab';
    write(root + '/etc/fii/core-install.json', sprintf('%J', { channel: 'alpha', version: 'alpha-abc123', digest: 'sha256:' + hex64, updatedAt: 1750000000 }));
    info = unwrap(invoke('core_info'));
    check(info.channel == 'alpha' && info.version == 'alpha-abc123' && info.digest == 'sha256:' + hex64 && info.updatedAt == 1750000000, 'install metadata surfaced');
    check(versionCalls == 2, 'signature changes invalidate the cache');
    machine = 'armv7l';
    writeMode(root + '/etc/fii/bin/mihomo', '#!stub2\n', 0755);
    check(!unwrap(invoke('core_info')).supported && versionCalls == 3, 'architecture support follows the host');
    machine = 'x86_64';
});
test('running status retains controller readiness', function() {
    let count = workers;
    let first = unwrap(invoke('status'));
    check(!first.running && first.coreInstalled && first.protocols.tcp == 'tproxy', 'stopped status with native core metadata');
    check(first.coreChannel == 'alpha' && first.coreSelectedChannel == 'alpha', 'install metadata feeds status');
    check(workers == count, 'status does not start Python');
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
    fs.mkdir(root + '/tmp/resolv.conf.d', 0700);
    write(root + '/tmp/resolv.conf.d/resolv.conf.auto',
        '# Interface wan\nnameserver 10.0.2.3\nnameserver 127.0.0.1\nnameserver bogus\nnameserver 10.0.2.3\n');
    let upstream = unwrap(invoke('network')).wanDns;
    check(type(upstream) == 'array' && length(upstream) == 1 && upstream[0] == '10.0.2.3', 'upstream dns hint');
    check(workers == count, 'network queries do not start Python');
});
test('proxy groups preserve active YAML order with an in-memory snapshot cache', function() {
    fs.mkdir(root + '/tmp/fii', 0700);
    write(root + '/tmp/fii/ready', 'fixture\n');
    let count = workers;
    let view = unwrap(invoke('groups'));
    check(view.running && view.source == 'runtime', 'runtime source');
    check(length(view.groups) == 3 && view.groups[0].name == 'PROXY' && view.groups[0].type == 'Selector' && view.groups[0].now == '香港🚀' && view.groups[0].fixed == '', 'selector group shape');
    check(view.groups[1].name == 'AUTO' && view.groups[1].type == 'URLTest' && same(view.groups[1].all, ['香港🚀', '日本']), 'automatic group retained');
    check(view.nodes['香港🚀'].delay == 42 && view.nodes['香港🚀'].alive == true && view.nodes['香港🚀'].testedAt == '2026-10-20T00:00:00Z' && view.nodes['香港🚀'].type == 'Shadowsocks', 'node history normalized');
    check(view.nodes['日本'].delay == null && view.nodes['日本'].alive == null && view.nodes['日本'].testedAt == '', 'missing history is null');
    check('GLOBAL' in view.nodes && !('weird' in view.nodes), 'GLOBAL node kept, non-object skipped');
    check(workers == count + 1, 'group order is parsed once for the active snapshot');
    let saved = proxiesBody;
    let shuffled = json(proxiesBody);
    shuffled.proxies.GLOBAL.all = ['AUTO', 'PROXY'];
    proxiesBody = sprintf('%J', shuffled);
    view = unwrap(invoke('groups'));
    check(same(map(view.groups, (group) => group.name), expected.groupOrder), 'active YAML order wins over JSON and custom GLOBAL order');
    check(workers == count + 1, 'subsequent group reads reuse the in-memory order');
    proxiesBody = saved;
    let active = json(fs.readfile(root + '/etc/fii/active.json'));
    active.revision = 12345;
    write(root + '/etc/fii/active.json', sprintf('%J', active));
    view = unwrap(invoke('groups'));
    check(workers == count + 2, 'active snapshot changes invalidate the order cache');
    fs.unlink(root + '/tmp/fii/ready');
    check(invoke('groups').error == 'worker-fallback:groups', 'stopped cores delegate snapshots to the worker');
});
test('proxy selection speaks PUT and DELETE through the node bridge', function() {
    fs.mkdir(root + '/tmp/fii', 0700);
    write(root + '/tmp/fii/ready', 'fixture\n');
    let count = workers;
    nodeCalls = [];
    check(unwrap(invoke('select', { group: 'PROXY', name: '日本' })).message == '已切换到 日本', 'selector switch');
    check(unwrap(invoke('select', { group: 'AUTO', name: '香港🚀' })).message == '已钉住 香港🚀', 'automatic groups pin');
    check(unwrap(invoke('select', { group: 'AUTO', action: 'unpin' })).message == '已取消钉住，恢复自动选择', 'unpin restores automation');
    check(unwrap(invoke('select', { group: '分组🚀', name: '香港🚀' })).message == '已切换到 香港🚀', 'unicode groups select');
    check(length(nodeCalls) == 4 && nodeCalls[0][0] == '/usr/libexec/fii/controller-req.cjs', 'bridge invocations');
    check(nodeCalls[0][1] == 'PUT' && nodeCalls[0][5] == '/proxies/PROXY' && nodeCalls[0][6] == '4' && nodeCalls[0][7] == sprintf('%J', { name: '日本' }), 'PUT request shape');
    check(nodeCalls[0][2] == '127.0.0.1' && nodeCalls[0][3] == '9090' && type(nodeCalls[0][4]) == 'string', 'bridge host, port and secret');
    check(nodeCalls[1][1] == 'PUT' && nodeCalls[1][5] == '/proxies/AUTO' && nodeCalls[2][1] == 'DELETE' && nodeCalls[2][5] == '/proxies/AUTO', 'verb and target shape');
    check(nodeCalls[3][5] == '/proxies/%E5%88%86%E7%BB%84%F0%9F%9A%80', 'unicode group encoding');
    nodeAnswer = { code: 400 };
    check(invoke('select', { group: 'PROXY', name: '日本' }).error == 'Mihomo 控制器请求失败，请检查服务状态', 'controller failures surface');
    nodeAnswer = { code: 204 };
    check(invoke('select', { group: 'NOPE', name: '日本' }).error == '代理组不存在', 'unknown group');
    check(invoke('select', { group: 'PROXY', name: 'NOPE' }).error == '代理组不支持选择或节点不存在', 'unknown node');
    check(invoke('select', { group: 'PROXY', action: 'unpin' }).error == '只有自动测速组和故障转移组支持取消钉住', 'unpin needs automatic groups');
    check(invoke('select', { group: 'PROXY' }).error == '节点名称必须是文本', 'select requires a node');
    check(workers == count, 'selection never starts Python');
    fs.unlink(root + '/tmp/fii/ready');
    check(invoke('select', { group: 'PROXY', name: '日本' }).error == '内核已停止，当前仅展示配置中的代理组', 'stopped selection is rejected');
});
test('controller endpoints follow the configured listen address', function() {
    fs.mkdir(root + '/tmp/fii', 0700);
    write(root + '/tmp/fii/ready', 'fixture\n');
    let bundle = json(fs.readfile(root + '/etc/fii/active.json'));
    function replaceBundle() {
        // Production replaces state atomically (new inode); mirror that so the
        // signature cache invalidates even for same-size content.
        write(root + '/etc/fii/active.next', sprintf('%J', bundle));
        fs.rename(root + '/etc/fii/active.next', root + '/etc/fii/active.json');
    }
    for (let entry in [['192.168.1.1', '192.168.1.1', '192.168.1.1'], ['0.0.0.0', '127.0.0.1', '127.0.0.1'],
                       ['fd00::1', '[fd00::1]', 'fd00::1']]) {
        bundle.settings.controllerAddress = entry[0];
        replaceBundle();
        fetchParams = null;
        invoke('status');
        check(index(fetchParams[length(fetchParams) - 1], 'http://' + entry[1] + ':9090/version') == 0, 'probe follows ' + entry[0]);
        fetchParams = null;
        invoke('groups');
        check(index(fetchParams[length(fetchParams) - 1], 'http://' + entry[1] + ':9090/proxies') == 0, 'groups fetch follows ' + entry[0]);
        nodeCalls = [];
        invoke('select', { group: 'PROXY', name: '日本' });
        check(nodeCalls[0][2] == entry[2], 'bridge host follows ' + entry[0]);
    }
    delete bundle.settings.controllerAddress;
    replaceBundle();
    fetchParams = null;
    invoke('status');
    check(index(fetchParams[length(fetchParams) - 1], 'http://127.0.0.1:9090/version') == 0, 'legacy bundles default to loopback');
    fs.unlink(root + '/tmp/fii/ready');
});
test('core channel selection persists through the shared lock', function() {
    let count = workers;
    check(unwrap(invoke('core_channel', { channel: 'alpha' })).selectedChannel == 'alpha', 'channel reply');
    check(json(fs.readfile(root + '/etc/fii/core-channel.json')).channel == 'alpha', 'channel file written');
    check(workers == count, 'channel switch never starts Python');
    check(invoke('core_channel', { channel: 'beta' }).error == '内核渠道必须是 Release 或 Alpha', 'invalid channel');
    let lock = fs.open(root + '/var/lock/fii-core-channel.lock', 'a', 0600);
    check(lock.lock('xn'), 'test owns channel lock');
    check(invoke('core_channel', { channel: 'release' }).error == 'worker-fallback:core_channel', 'contention delegates to the worker');
    lock.close();
    check(json(fs.readfile(root + '/etc/fii/core-channel.json')).channel == 'alpha', 'contended write did not land');
    check(unwrap(invoke('core_channel', { channel: 'release' })).selectedChannel == 'release', 'channel recovers');
});
test('core checks parse official release metadata without Python', function() {
    let hex64 = '';
    for (let i = 0; i < 32; i++) hex64 += 'ab';
    githubBody = sprintf('%J', { tag_name: 'v1.19.32', draft: false, prerelease: false, published_at: '2026-09-30T00:00:00Z',
        assets: [{ id: 99, name: 'mihomo-linux-amd64-compatible-v1.19.32.gz', size: 12345678,
            digest: 'sha256:' + hex64, updated_at: '2026-09-30T01:00:00Z',
            browser_download_url: 'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-compatible-v1.19.32.gz' }] });
    let count = workers;
    let result = unwrap(invoke('core_check', { channel: 'release' }));
    check(result.remote.version == 'v1.19.32' && result.remote.channel == 'release' && result.remote.size == 12345678 && result.remote.asset == 'mihomo-linux-amd64-compatible-v1.19.32.gz', 'release metadata parsed');
    check(result.remote.digest == 'sha256:' + hex64 && result.remote.publishedAt == '2026-09-30T01:00:00Z' && result.remote.assetId == 99, 'digest and asset identity');
    check(result.remote.releaseUrl == 'https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.32', 'release link');
    check(result.updateAvailable == true, 'digest mismatch means update available');
    check(index(fetchParams, '--proxy=off') >= 0 && index(fetchParams, '--timeout=20') >= 0, 'proxy env ignored and bounded');
    check(length(filter(fetchParams, (p) => match(p, /^--header=(User-Agent: FII-Core-Manager\/0\.1\.1|Accept: application\/vnd\.github\+json)$/))) == 2, 'GitHub request headers');
    check(workers == count, 'core checks do not start Python');
    write(root + '/etc/fii/core-install.json', sprintf('%J', { channel: 'release', version: 'v1.19.32', digest: 'sha256:' + hex64 }));
    result = unwrap(invoke('core_check', { channel: 'release' }));
    check(!result.updateAvailable && result.current.installed && result.current.channel == 'release', 'matching install suppresses updates');
    githubBody = sprintf('%J', { tag_name: 'Prerelease-Alpha', draft: false, prerelease: true,
        assets: [{ name: 'mihomo-linux-amd64-compatible-alpha-abc123.gz', size: 999, digest: 'sha256:' + hex64,
            browser_download_url: 'https://github.com/MetaCubeX/mihomo/releases/download/Prerelease-Alpha/mihomo-linux-amd64-compatible-alpha-abc123.gz' }] });
    result = unwrap(invoke('core_check', { channel: 'alpha' }));
    check(result.remote.version == 'alpha-abc123' && result.remote.releaseUrl == 'https://github.com/MetaCubeX/mihomo/releases/tag/Prerelease-Alpha', 'alpha metadata parsed');
    check(result.updateAvailable, 'channel switch is an update');
    fetchFailure = 1;
    fetchStderr = 'Error: HTTP error 403\n';
    check(invoke('core_check', { channel: 'release' }).error == 'GitHub 访问受限或达到请求频率限制，请稍后重试', 'rate limited');
    fetchStderr = 'Error: HTTP error 404\n';
    check(invoke('core_check', { channel: 'release' }).error == 'GitHub 下载失败 (HTTP 404)，当前内核保持不变', 'HTTP failures carry the code');
    fetchStderr = 'Connection failed\n';
    check(invoke('core_check', { channel: 'release' }).error == '无法连接 GitHub (uclient-fetch)，请检查路由器网络后重试', 'connection failures');
    fetchFailure = 0;
    githubBody = sprintf('%J', { tag_name: 'v1.19.32', assets: [{ name: 'mihomo-linux-amd64-compatible-v1.19.32.gz', size: 1, browser_download_url: 'x' }] });
    check(invoke('core_check', { channel: 'release' }).error == '官方内核缺少 SHA-256 摘要，未执行下载', 'missing digest');
    githubBody = sprintf('%J', { tag_name: 'v1.19.32', assets: [
        { name: 'mihomo-linux-amd64-compatible-v1.19.32.gz', size: 1, digest: 'sha256:' + hex64, browser_download_url: 'x' },
        { name: 'mihomo-linux-amd64-compatible-v1.19.32.gz', size: 1, digest: 'sha256:' + hex64, browser_download_url: 'x' }] });
    check(invoke('core_check', { channel: 'release' }).error == '未找到唯一的官方 amd64-compatible 内核文件', 'ambiguous assets rejected');
    githubBody = sprintf('%J', { tag_name: 'v1.19.32', draft: true, assets: [] });
    check(invoke('core_check', { channel: 'release' }).error == 'GitHub 返回了未发布版本', 'draft releases rejected');
    check(length(fs.glob(root + '/tmp/fii/core-check-*.json')) == 0, 'temporary metadata is cleaned up');
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
    let sites = view.data.settings.urlTestSites;
    delete view.data.settings.urlTestSites;
    write(root + '/tmp/fii/view.next', sprintf('%J', view));
    fs.rename(root + '/tmp/fii/view.next', root + '/tmp/fii/state-view.json');
    check(invoke('get', { compact: true }).error == 'worker-fallback:get', 'pre-upgrade site defaults use validator');
    view.data.settings.urlTestSites = sites;
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
