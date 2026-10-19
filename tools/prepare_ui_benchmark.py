"""Temporary performance fixture for the disposable QEMU VM only."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, '/usr/lib')
from fii.store import Store, write_json

backup = Path('/tmp/fii-ui-benchmark-original.json')
if os.environ.get('FII_VM_TEST') != '1':
    raise SystemExit('FII_VM_TEST=1 is required')
store = Store()
def cached_yaml():
    yaml = 'proxies:\n' + ''.join(' - {name: Node%d, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n' % i for i in range(500))
    return yaml + '# ' + 'x' * (1950000 - len(yaml)) + '\n'

if sys.argv[1] == 'setup':
    if backup.exists():
        raise SystemExit('Restore the existing benchmark before starting another')
    original = store.read()
    write_json(backup, original)
    data = json.loads(json.dumps(original))
    yaml = cached_yaml()
    data['sources'] = [{'id': 'bench-source', 'name': 'Benchmark Source', 'cachedYaml': yaml, 'nodeCount': 500}]
    data['overrides'] = [{'id': 'bench-js%d' % i, 'name': 'Benchmark JS%d' % i, 'type': 'JS', 'body': 'function main(c){return c;}'} for i in range(6)]
    data['outputs'] = [{'id': 'bench-output', 'name': 'Benchmark Output', 'sourceIds': ['bench-source'], 'overrideIds': []}]
    store.save(data, original['revision'])
    print(json.dumps({'bytes': len(json.dumps(data).encode()), 'nodes': 500, 'scripts': 6}))
elif sys.argv[1] == 'verify':
    data = store.read()
    assert data['sources'][0]['cachedYaml'] == cached_yaml(), 'Cached YAML changed'
    assert all(item['body'] == 'function main(c){return c;}' for item in data['overrides']), 'Script changed'
    print('Cached YAML and scripts remain exact after UI edits')
elif sys.argv[1] == 'restore':
    store.save(json.loads(backup.read_text()), store.read()['revision'])
    backup.unlink()
    print('Original saved configuration restored')
else:
    raise SystemExit('Unknown operation')
