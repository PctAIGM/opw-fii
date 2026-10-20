import json
import os
import time
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample
from fii import cli, transport
from fii.store import Store


class FileUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def uploaded(self, raw):
        info = transport.begin(len(raw), file_upload=True)
        transport.path_for(info['id'], '.in').write_bytes(raw)
        return info

    def test_direct_upload_keeps_exact_unicode_and_bom_and_reserves_private_path(self):
        data = {'text': '订阅🚀' * 10000}
        raw = b'\xef\xbb\xbf' + json.dumps(data, ensure_ascii=False).encode()
        info = self.uploaded(raw)
        self.assertEqual(info['uploadPath'], '/tmp/fii/transfers/' + info['id'] + '.in')
        self.assertEqual(transport.upload_status(info['id']), {'received': len(raw), 'complete': True})
        if os.name != 'nt':
            self.assertEqual(transport.path_for(info['id'], '.in').stat().st_mode & 0o777, 0o600)
        self.assertEqual(transport.consume(info['id'], file_upload=True), data)
        self.assertEqual(list(transport.directory().iterdir()), [])

    def test_partial_missing_and_oversize_files_cannot_be_read_or_checked(self):
        info = transport.begin(8, file_upload=True)
        path = transport.path_for(info['id'], '.in')
        for body in (b'', b'{}', b'x' * 9):
            path.write_bytes(body)
            self.assertFalse(transport.upload_status(info['id'])['complete'])
            with self.assertRaisesRegex(ValueError, '尚未'):
                transport.consume(info['id'], file_upload=True)
        path.unlink()
        self.assertEqual(transport.upload_status(info['id']), {'received': 0, 'complete': False})
        transport.release(info['id'])
        with self.assertRaisesRegex(ValueError, '失效'):
            transport.upload_status(info['id'])

    def test_raw_upload_cannot_be_invoked_as_an_rpc_envelope(self):
        info = self.uploaded(b'{}')
        with self.assertRaisesRegex(ValueError, '类型不匹配'):
            cli.dispatch('transfer_invoke', {'id': info['id'], 'method': 'save'})
        legacy = transport.begin(2)
        transport.path_for(legacy['id'], '.in').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, '类型不匹配'):
            transport.consume(legacy['id'], file_upload=True)

    def test_direct_check_stages_same_backup_without_changing_saved_config(self):
        original = Store().read()
        backup = sample()
        info = self.uploaded(json.dumps(backup).encode())
        result = cli.dispatch('import_preview', {'upload': info['id'], 'stage': True, 'revision': original['revision']})
        self.assertEqual(Store().read(), original)
        self.assertEqual(result['summary'], {'sources': 1, 'overrides': 0, 'outputs': 1})
        self.assertFalse(transport.path_for(info['id'], '.in').exists())
        cli.dispatch('import', {'token': result['importToken'], 'revision': result['revision']})
        self.assertEqual(Store().read()['sources'][0]['cachedYaml'], backup['sources'][0]['cachedYaml'])

    def test_invalid_json_and_bad_backup_clean_uploaded_data_and_preserve_state(self):
        original = Store().read()
        for raw in (b'{bad json', b'{}'):
            info = self.uploaded(raw)
            with self.assertRaises(ValueError):
                cli.dispatch('import_preview', {'upload': info['id']})
            self.assertFalse(transport.path_for(info['id'], '.in').exists())
            self.assertFalse(transport.path_for(info['id'], '.meta').exists())
            self.assertEqual(Store().read(), original)

    def test_a_second_cgi_write_between_stat_and_open_is_bounded_and_rejected(self):
        info = self.uploaded(b'{}')
        path = transport.path_for(info['id'], '.in')
        real_open = open
        lengths = []
        class Reader:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def read(self, length):
                lengths.append(length)
                return self.stream.read(length)
            def __exit__(self, *args): self.stream.close()
        def raced_open(name, *args, **kwargs):
            if name == path and args == ('rb',):
                path.write_bytes(b'{}' + b' ' * 1000000)
                return Reader(real_open(name, *args, **kwargs))
            return real_open(name, *args, **kwargs)
        with patch('builtins.open', side_effect=raced_open), self.assertRaisesRegex(ValueError, '大小已变化'):
            transport.consume(info['id'], file_upload=True)
        self.assertEqual(lengths, [3])
        self.assertFalse(path.exists())
        self.assertFalse(transport.path_for(info['id'], '.meta').exists())

    def test_direct_upload_uses_quota_expiry_and_explicit_import_preview_only(self):
        with patch.object(transport, 'QUOTA', 10):
            info = transport.begin(8, file_upload=True)
            with self.assertRaisesRegex(ValueError, '空间'):
                transport.begin(8, file_upload=True)
            old = time.time() - transport.TTL - 1
            for suffix in ('.meta', '.in'):
                os.utime(transport.path_for(info['id'], suffix), (old, old))
            transport.begin(8, file_upload=True)
        info = self.uploaded(b'{}')
        with self.assertRaisesRegex(ValueError, '只用于'):
            cli.dispatch('import', {'upload': info['id']})
        with self.assertRaisesRegex(ValueError, '只用于'):
            cli.dispatch('import_preview', {'upload': info['id'], 'data': {}})
        with self.assertRaises(ValueError):
            transport.begin(transport.MAX_DOCUMENT + 1, file_upload=True)
        with self.assertRaises(ValueError):
            transport.begin(2, file_upload='yes')

    def test_upload_acl_is_scoped_to_incoming_files_and_write_methods(self):
        from test_fii import ROOT
        acl = json.loads((ROOT / 'luci-app-fii/root/usr/share/rpcd/acl.d/luci-app-fii.json').read_text())['luci-app-fii']
        self.assertEqual(acl['write']['file'], {'/tmp/fii/transfers/*.in': ['write']})
        self.assertEqual(acl['write']['cgi-io'], ['upload'])
        self.assertEqual(acl['read']['file'], {'/tmp/fii/transfers/*.out': ['read']})
        self.assertEqual(acl['read']['cgi-io'], ['download'])
        self.assertIn('transfer_status', acl['write']['ubus']['luci.fii'])
