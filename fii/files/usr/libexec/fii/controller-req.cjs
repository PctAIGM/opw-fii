'use strict';
// Minimal localhost bridge to the Mihomo controller. uclient-fetch cannot
// issue the PUT/DELETE requests proxy selection needs, so rpc.uc runs this
// helper with node (already required for JavaScript overrides). argv:
//   controller-req.cjs <method> <host> <port> <secret> <path> <seconds> [body]
// Always exits 0 and prints {"code":<http>,"error":true} shaped JSON on stdout
// so the caller never needs to inspect stderr or exit codes.
const http = require('node:http');
const [method, host, port, secret, target, seconds, body] = process.argv.slice(2);
const payload = body === undefined ? null : Buffer.from(body, 'utf8');
const request = http.request({
    host: (host || '127.0.0.1').replace(/^\[|\]$/g, ''), port: parseInt(port, 10) || 9090, method: method, path: target,
    headers: Object.assign({ Authorization: 'Bearer ' + secret },
        payload ? { 'Content-Type': 'application/json', 'Content-Length': payload.length } : {}),
    timeout: (parseInt(seconds, 10) || 4) * 1000
}, (response) => {
    let received = 0;
    const chunks = [];
    response.on('data', (chunk) => {
        received += chunk.length;
        if (received <= 4096) chunks.push(chunk);
    });
    response.on('end', () => {
        process.stdout.write(JSON.stringify({ code: response.statusCode, body: Buffer.concat(chunks).toString('utf8') }));
        process.exit(0);
    });
});
request.on('timeout', () => request.destroy(new Error('timeout')));
request.on('error', () => {
    process.stdout.write('{"code":0,"error":true}');
    process.exit(0);
});
if (payload) request.write(payload);
request.end();
