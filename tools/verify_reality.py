"""Generated REALITY scalar regression; no real subscription or credentials."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/usr/lib' if sys.platform != 'win32' else str(Path(__file__).resolve().parents[1] / 'fii/files/usr/lib'))
from fii.model import defaults
from fii.render import render, load_map
from fii.runtime import overlay

parser = argparse.ArgumentParser()
parser.add_argument('--core', required=True)
parser.add_argument('--out', required=True)
args = parser.parse_args()
directory = Path(args.out)
directory.mkdir(parents=True, exist_ok=True)
public_key = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
def fixture(short_id):
    data = defaults()
    body = 'proxies:\n' + ''.join(' - {name: SS%d, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}\n' % i for i in range(143))
    body += (' - name: REALITY fixture\n   type: vless\n   server: node.example.com\n   port: 443\n'
             '   uuid: 00000000-0000-4000-8000-000000000001\n   tls: true\n   servername: example.com\n'
             '   client-fingerprint: chrome\n   reality-opts:\n     public-key: '+public_key+'\n     short-id: '+short_id+'\n')
    data['sources'] = [{'id': 's', 'name': 'Fixture source', 'cachedYaml': body}]
    data['outputs'] = [{'id': 'p', 'name': 'Fixture output', 'sourceIds': ['s'], 'overrideIds': []}]
    return data

results = []
for label, sid in [('octal', '00112233'), ('zeros', '0000000000000001'), ('numeric', '1234567890123456'),
                   ('decimal-leading-zero', '0012345678901234'), ('scientific-looking', '1e123456'), ('empty', "''")]:
    data = fixture(sid)
    rendered = render(data, 'p')['yaml']
    body = overlay(rendered, data['settings'])
    config = directory / (label+'.yaml')
    config.write_text(body, encoding='utf-8')
    process = subprocess.run([args.core, '-t', '-d', str(directory), '-f', str(config)], capture_output=True, text=True, timeout=30)
    actual = load_map(body)['proxies'][143]['reality-opts']['short-id']
    results.append({'case': label, 'expected': sid.strip("'"), 'actual': actual, 'type': type(actual).__name__,
                    'exitCode': process.returncode, 'shortIdError': 'invalid REALITY short ID' in (process.stdout+process.stderr)})
print(json.dumps({'coreVersion': subprocess.check_output([args.core,'-v'],text=True).splitlines()[0], 'results': results},ensure_ascii=False))
