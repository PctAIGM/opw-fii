import os
import unittest
from unittest.mock import patch

from test_fii import TestDirectory
from fii import cli
from fii.store import rooted


class LogSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_tag_query_and_separate_prepare_snapshot_never_call_service(self):
        path = rooted('/tmp/fii/prepare.log')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'configuration check failed\n')
        record = 'Tue Oct 20 12:00:00 2026 daemon.err core-run[123]: startup failed\n'
        with patch.object(cli, 'run', return_value=record) as run:
            result = cli.dispatch('logs', {})
        run.assert_called_once_with(['/sbin/logread', '-e', r'^\(fii\|fii-prepare\|mihomo\|core-run\)\(\[[0-9][0-9]*\]\)\{0,1\}:', '-l', '500'], timeout=4)
        self.assertEqual(result, {'text': record, 'prepareText': 'configuration check failed\n', 'limit': 500, 'truncated': False})

    def test_byte_limits_keep_complete_utf8_lines_and_missing_prepare_is_empty(self):
        path = rooted('/tmp/fii/prepare.log')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('中文' * 20000 + '\nprepare tail\n').encode('utf-8'))
        with patch.object(cli, 'run', return_value='中文' * 50000 + '\nsystem tail\n'):
            result = cli.dispatch('logs', {})
        self.assertTrue(result['truncated'])
        self.assertEqual(result['text'], 'system tail\n')
        self.assertEqual(result['prepareText'], 'prepare tail\n')
        path.unlink()
        with patch.object(cli, 'run', return_value=''):
            self.assertEqual(cli.dispatch('logs', {})['prepareText'], '')

    def test_logread_errors_are_not_silently_shown_as_no_logs(self):
        with patch.object(cli, 'run', side_effect=ValueError('logread failed')):
            with self.assertRaisesRegex(ValueError, 'logread failed'):
                cli.dispatch('logs', {})
