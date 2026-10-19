import copy
import json
import os
import time
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample
from fii import cli, imports, runtime, transport
from fii.store import Store, write_json, rooted


class CheckedImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()
        self.original = Store().read()
        self.backup = sample()
        self.backup['sources'][0]['cachedYaml'] += '# ' + 'x' * 100000 + '\n'
        self.active = {'outputId': 'old', 'yaml': 'old', 'settings': self.original['settings']}
        write_json(rooted('/etc/fii/active.json'), self.active)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def stage(self, data=None, **extra):
        return cli.dispatch('import_preview', {'data': data or self.backup, 'stage': True,
                                              'revision': self.original['revision'], **extra})

    def confirm(self, result):
        return cli.dispatch('import', {'token': result['importToken'], 'revision': result['revision']})

    def test_checked_import_confirms_with_only_a_small_token_and_no_revalidation(self):
        checked = self.stage()
        self.assertEqual(Store().read(), self.original)
        self.assertEqual(runtime.active(), self.active)
        path = transport.path_for(checked['importToken'], '.checked')
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertGreater(path.stat().st_size, 100000)
        self.assertLess(len(json.dumps({'token': checked['importToken'], 'revision': checked['revision']})), 100)
        with patch.object(cli, 'validate_contents', side_effect=AssertionError('must reuse validation')):
            result = self.confirm(checked)
        saved = Store().read()
        self.assertEqual(saved['sources'][0]['cachedYaml'], self.backup['sources'][0]['cachedYaml'])
        self.assertEqual(saved['settings'], self.original['settings'])
        self.assertEqual(runtime.active(), self.active)
        self.assertEqual(result['summary'], checked['summary'])
        self.assertLess(path.stat().st_size, 2048)
        self.assertNotIn('data', json.loads(path.read_text()))

    def test_confirmation_retry_is_idempotent_and_does_not_undo_later_changes(self):
        checked = self.stage()
        first = self.confirm(checked)
        saved = Store().read()
        self.assertEqual(self.confirm(checked), first)
        self.assertEqual(Store().read(), saved)
        saved['settings']['userAgent'] = 'later-change'
        Store().save(saved, saved['revision'])
        self.assertEqual(self.confirm(checked), first)
        self.assertEqual(Store().read(), saved)

    def test_retry_recovers_if_committed_receipt_write_failed(self):
        checked = self.stage()
        with patch.object(imports, 'write', side_effect=OSError('simulated receipt failure')), self.assertRaises(OSError):
            self.confirm(checked)
        saved = Store().read()
        self.assertEqual(saved['revision'], self.original['revision'] + 1)
        self.confirm(checked)
        self.assertEqual(Store().read(), saved)

    def test_cancel_releases_checked_contents_and_cannot_import_afterwards(self):
        checked = self.stage()
        cli.dispatch('transfer_release', {'id': checked['importToken']})
        with self.assertRaisesRegex(ValueError, '失效'):
            self.confirm(checked)
        self.assertEqual(Store().read(), self.original)

    def test_expired_result_is_rejected_and_removed(self):
        checked = self.stage()
        with patch.object(imports.time, 'time', return_value=checked['expiresAt'] + 1), self.assertRaisesRegex(ValueError, '过期'):
            self.confirm(checked)
        self.assertFalse(transport.path_for(checked['importToken'], '.checked').exists())
        self.assertEqual(Store().read(), self.original)

    def test_revision_change_after_check_preserves_new_settings(self):
        checked = self.stage()
        latest = copy.deepcopy(self.original)
        latest['settings']['userAgent'] = 'concurrent-change'
        Store().save(latest, latest['revision'])
        with self.assertRaisesRegex(ValueError, '检查备份后已变化'):
            self.confirm(checked)
        self.assertEqual(Store().read(), latest)
        self.assertEqual(runtime.active(), self.active)

    def test_invalid_preview_creates_no_checked_result_and_stale_check_fails_early(self):
        bad = copy.deepcopy(self.backup)
        bad['sources'][0]['cachedYaml'] = 'proxies: ['
        with self.assertRaises(ValueError):
            self.stage(bad)
        self.assertEqual(list(transport.directory().glob('*.checked')), [])
        with patch.object(cli, 'validate_contents', side_effect=AssertionError('stale preview must fail first')):
            with self.assertRaisesRegex(ValueError, '配置已变化'):
                self.stage(revision=9)
        self.assertEqual(Store().read(), self.original)

    def test_checked_results_share_quota_and_abandoned_results_expire(self):
        checked = self.stage()
        path = transport.path_for(checked['importToken'], '.checked')
        with patch.object(transport, 'QUOTA', path.stat().st_size + 4):
            with self.assertRaisesRegex(ValueError, '空间'):
                transport.begin(8)
            old = time.time() - transport.IMPORT_TTL - 1
            os.utime(path, (old, old))
            upload = transport.begin(8)
        self.assertFalse(path.exists())
        transport.release(upload['id'])

    def test_tokens_cannot_be_forged_replaced_or_read_as_generic_transfers(self):
        checked = self.stage()
        for token in ('../state', 'a' * 32):
            with self.subTest(token=token), self.assertRaises(ValueError):
                cli.dispatch('import', {'token': token, 'revision': checked['revision']})
        with self.assertRaises(ValueError):
            cli.dispatch('import', {'token': checked['importToken'], 'revision': checked['revision'], 'data': self.backup})
        with self.assertRaises(ValueError):
            cli.dispatch('import', {'token': checked['importToken'], 'revision': True})
        with self.assertRaises(ValueError):
            transport.read(checked['importToken'], 0)
        with self.assertRaises(ValueError):
            transport.append(checked['importToken'], 0, 'e30=')
        self.assertEqual(Store().read(), self.original)


if __name__ == '__main__':
    unittest.main()
