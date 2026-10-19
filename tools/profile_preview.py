"""Render a generated 1.86 MiB/500-node/six-JS fixture without changing VM state."""
import json
import sys
import time

sys.path.insert(0, '/usr/lib')
from fii.model import defaults, validate
from fii.render import render

data = defaults()
body = 'proxies:\n' + ''.join(' - {name: Node%d, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n' % i for i in range(500))
body += '# ' + 'x' * (1950000 - len(body)) + '\n'
data['sources'] = [{'id': 's', 'name': 'Fixture', 'cachedYaml': body}]
data['overrides'] = [{'id': 'js%d' % i, 'name': 'JS%d' % i, 'type': 'JS', 'global': True,
                      'body': 'function main(c){c.fixture=(c.fixture||0)+1;return c;}'} for i in range(6)]
data['outputs'] = [{'id': 'p', 'name': 'Fixture', 'sourceIds': ['s'], 'overrideIds': []}]
validate(data)
times = []
for _ in range(2):
    start = time.monotonic()
    result = render(data, 'p')
    times.append(round((time.monotonic() - start) * 1000))
    assert result['nodeCount'] == 500
    assert 'fixture: 6' in result['yaml']
print(json.dumps({'bytes': len(body.encode()), 'nodes': 500, 'scripts': 6, 'milliseconds': times}))
