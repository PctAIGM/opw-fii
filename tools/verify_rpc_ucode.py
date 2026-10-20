"""Generate an isolated RPC fixture and run it with OpenWrt's actual ucode VM.

Never starts/applies a core. Point --library at the candidate rpc.uc to validate.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--library', required=True)
parser.add_argument('--harness', default=str(Path(__file__).with_suffix('.uc')))
parser.add_argument('--python-library', help='Candidate Python package parent for isolated validation')
args = parser.parse_args()
sys.path.insert(0, args.python_library or ('/usr/lib' if sys.platform != 'win32' else str(Path(__file__).resolve().parents[1] / 'fii/files/usr/lib')))
from fii import cli, model
from fii.store import Store, write_json

with tempfile.TemporaryDirectory(prefix='fii-rpc-verify-') as temporary:
    os.environ['FII_ROOT'] = temporary
    data = model.defaults()
    body = 'proxies:\n - {name: 香港🚀, type: ss, server: example.com, port: 443, cipher: aes-128-gcm, password: test}\n'
    data['sources'] = [{'id': 'source', 'name': 'Source', 'cachedYaml': body + '# ' + 'x' * 50000}]
    data['overrides'] = [{'id': 'script', 'name': 'Script', 'type': 'JS', 'body': 'function main(c){return c;}' + ' ' * 5000}]
    data['outputs'] = [{'id': 'output', 'name': 'Output', 'sourceIds': ['source'], 'overrideIds': []}]
    Store().save(data, Store().read()['revision'])
    summary = cli.dispatch('get', {'compact': True})
    expected = {'compact': summary, 'source': cli.dispatch('get', {'kind': 'sources', 'id': 'source', 'sync': True}),
                'override': cli.dispatch('get', {'kind': 'overrides', 'id': 'script', 'sync': True}),
                'output': cli.dispatch('get', {'kind': 'outputs', 'id': 'output', 'sync': True})}
    write_json(Path(temporary) / 'expected.json', expected)
    write_json(Path(temporary) / 'etc/fii/active.json', {'outputId': 'output', 'name': 'Output', 'settings': data['settings'], 'revision': 1})
    write_json(Path(temporary) / 'tmp/fii/jobs' / ('a' * 32 + '.json'), {'done': False, 'method': 'preview', 'createdAt': int(__import__('time').time())})
    root = Path(temporary)
    (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\nnobody:x:65534:65534:nobody:/var:/bin/false\n')
    (root / 'etc/group').write_text('root:x:0:\nusers:x:100:\n')
    (root / 'tmp/dhcp.leases').write_text('0 00:11:22:33:44:55 192.0.2.10 desktop *\n')
    os.environ['FII_RPC_MODULE'] = str(Path(args.library).resolve())
    result = subprocess.run(['ucode', args.harness], env=os.environ, text=True)
    raise SystemExit(result.returncode)
