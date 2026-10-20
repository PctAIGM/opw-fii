"""Verify the Python fallback's actual query with OpenWrt/Linux's BRE engine.

Run on the target alongside verify_rpc_ucode.py; no syslog injection or service
operations. --python-library selects a candidate package parent.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--python-library', default='/usr/lib')
args = parser.parse_args()
sys.path.insert(0, args.python_library)
from fii import cli

accepted = [tag + ': fixture' for tag in (
    'fii', 'fii[1]', 'fii-prepare', 'fii-prepare[123]',
    'mihomo', 'mihomo[456]', 'core-run', 'core-run[789]')]
rejected = [
    'crond[1]: /usr/bin/fii scheduled',
    'uwsgi[2]: /cgi-bin/luci/admin/services/fii',
    'other[3]: core-run[4]: unrelated embedded tag',
    'fii-other: unrelated service', 'core-runner[1]: unrelated service',
    'core-run[]: invalid PID', 'core-run[abc]: invalid PID',
    ' prefix core-run[1]: not the message tag',
    'Tue Oct 20 12:00:00 2026 daemon.info core-run[1]: already formatted'
]
with tempfile.TemporaryDirectory(prefix='fii-log-filter-') as temporary:
    with patch.dict(os.environ, {'FII_ROOT': temporary}), patch.object(cli, 'run', return_value='') as run:
        cli.dispatch('logs', {})
    command = run.call_args.args[0]
    assert command[:2] == ['/sbin/logread', '-e'] and command[-2:] == ['-l', '500']
    pattern = command[2]
    result = subprocess.run(['grep', '-e', pattern], input='\n'.join(accepted + rejected) + '\n',
                            capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == accepted, result.stdout
print('Actual BRE filter passed: 8 accepted service tags and 9 excluded unrelated/invalid messages.')
