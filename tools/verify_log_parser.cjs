// Exercise the actual view parser with procd's tag and both core log formats.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('luci-app-fii/htdocs/luci-static/resources/view/fii/app.js', 'utf8');
const start = source.indexOf('function parseLogSnapshot('), end = source.indexOf('\nreturn view.extend(', start);
assert(start >= 0 && end > start);
const parse = vm.runInNewContext(source.slice(start, end) + '\nparseLogSnapshot;');
const prefix = 'Tue Oct 20 12:00:00 2026 ';
const snapshot = {
    text: [
        prefix + 'daemon.info core-run[123]: time="2026-10-20T12:00:00+08:00" level=warning msg="connect timeout"',
        prefix + 'daemon.err core-run[123]: startup failed',
        prefix + 'daemon.info core-run: {"level":"error","msg":"JSON failure"}',
        prefix + 'daemon.info mihomo[456]: level=debug msg="debug trace"',
        prefix + 'user.notice fii: Mihomo ready',
        prefix + 'user.err fii-prepare[789]: configuration failed'
    ].join('\n'),
    prepareText: 'level=error msg="preparation failure"\n'
};
const records = parse(snapshot);
assert.equal(records.length, 7);
assert.deepEqual(Array.from(records, r => r.source), ['app', 'core', 'core', 'core', 'core', 'app', 'app']);
assert.deepEqual(Array.from(records, r => r.level), ['error', 'warning', 'error', 'error', 'debug', 'info', 'error']);
assert.equal(records[1].message, 'connect timeout');
assert.equal(records[2].message, 'startup failed');
assert.equal(records[3].message, 'JSON failure');
assert.equal(records[1].time, '2026-10-20T12:00:00+08:00');
assert.equal(parse({text: snapshot.text}).length, 6);
assert.equal(parse({}).length, 0);
console.log('Log parser passed: core-run with/without PID, raw startup errors, logfmt/JSON, legacy mihomo, application/preparation separation.');
