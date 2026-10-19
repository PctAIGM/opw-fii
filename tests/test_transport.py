import base64
import io
import json
import os
import sys
import time
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample
from fii import cli, transport
from fii.store import Store


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def upload(self, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        ident = transport.begin(len(raw))['id']
        for offset in range(0, len(raw), transport.CHUNK):
            transport.append(ident, offset, base64.b64encode(raw[offset:offset + transport.CHUNK]).decode())
        return ident

    def test_multibyte_request_survives_arbitrary_chunk_boundaries_and_is_consumed(self):
        data = {'text': '订阅🚀' * 20000}
        ident = self.upload(data)
        self.assertEqual(transport.consume(ident), data)
        self.assertEqual(list(transport.directory().iterdir()), [])
        with self.assertRaisesRegex(ValueError, '失效'):
            transport.consume(ident)

    def test_large_response_round_trips_with_small_rpc_frames(self):
        result = {'data': '备份🚀' * 20000}
        transfer = json.loads(transport.response(result))['responseTransfer']
        content = bytearray()
        while len(content) < transfer['size']:
            part = transport.read(transfer['id'], len(content))
            self.assertLess(len(transport.response(part, chunk_reply=True).encode()), 32 * 1024)
            content.extend(base64.b64decode(part['data']))
        self.assertEqual(json.loads(content), {'ok': True, 'result': result})
        transport.release(transfer['id'])
        self.assertEqual(list(transport.directory().iterdir()), [])

    def test_invalid_ids_offsets_data_and_incomplete_requests_are_rejected(self):
        for ident in ('../state', '', None):
            with self.assertRaises(ValueError):
                transport.consume(ident)
        ident = transport.begin(4)['id']
        with self.assertRaises(ValueError):
            transport.append(ident, 1, 'e30=')
        with self.assertRaises(ValueError):
            transport.append(ident, 0, '%%%%')
        transport.append(ident, 0, 'e30=')
        with self.assertRaisesRegex(ValueError, '尚未'):
            transport.consume(ident)
        with self.assertRaises(ValueError):
            transport.append(ident, 2, 'eHh4eA==')

    def test_lost_acknowledgement_allows_smaller_identical_retry_without_duplication(self):
        ident = transport.begin(8)['id']
        transport.append(ident, 0, base64.b64encode(b'abcdefgh').decode())
        for offset in (0, 2, 4, 6):
            data = b'abcdefgh'[offset:offset+2]
            self.assertEqual(transport.append(ident, offset, base64.b64encode(data).decode())['received'],offset+2)
        with self.assertRaisesRegex(ValueError, '不一致'):
            transport.append(ident, 0, base64.b64encode(b'xx').decode())
        self.assertEqual(transport.path_for(ident,'.in').read_bytes(),b'abcdefgh')

    def test_response_reader_honors_negotiated_chunk_size(self):
        meta = json.loads(transport.response({'data':'x'*10000}))['responseTransfer']
        data = transport.read(meta['id'],0,1024)
        self.assertEqual(len(base64.b64decode(data['data'])),1024)
        self.assertLess(len(transport.response(data,chunk_reply=True).encode()),2048)
        for size in (0, transport.CHUNK+1, '1024'):
            with self.assertRaises(ValueError):
                transport.read(meta['id'],0,size)

    def test_quota_reserves_declared_sizes_and_expires_abandoned_uploads(self):
        with patch.object(transport, 'QUOTA', 10):
            ident = transport.begin(8)['id']
            with self.assertRaisesRegex(ValueError, '空间'):
                transport.begin(8)
            old = time.time() - transport.TTL - 1
            for suffix in ('.in', '.meta'):
                os.utime(transport.path_for(ident, suffix), (old, old))
            transport.begin(8)
            self.assertFalse(transport.path_for(ident, '.in').exists())

    def test_transport_cannot_invoke_transport_or_unknown_methods(self):
        ident = self.upload({})
        for operation in ('transfer_invoke', 'shell', None):
            with self.assertRaisesRegex(ValueError, '操作'):
                cli.dispatch('transfer_invoke', {'id': ident, 'method': operation})

    def test_large_job_payload_is_spooled_instead_of_command_line(self):
        payload = {'data': 'x' * 1024 * 1024}
        with patch.object(cli.subprocess, 'Popen') as spawn:
            job = cli.queue_job('import_preview', payload)
        args = spawn.call_args.args[0]
        self.assertEqual(args[-3:], ['job-run', 'import_preview', job['jobId']])
        self.assertLess(len(str(args)), 1024)
        with patch.object(cli, 'dispatch', return_value={'summary': {}}) as dispatch:
            cli.run_job('import_preview', job['jobId'])
        dispatch.assert_called_once_with('import_preview', payload)
        self.assertFalse(cli.job_path(job['jobId']).with_suffix('.payload').exists())
        self.assertTrue(cli.dispatch('job', {'id': job['jobId']})['ok'])

    def test_import_preview_is_backgrounded_after_chunked_upload(self):
        payload = {'data': sample()}
        ident = self.upload(payload)
        with patch.object(cli, 'queue_job', return_value={'jobId': 'a' * 32}) as queue:
            result = cli.dispatch('transfer_invoke', {'id': ident, 'method': 'import_preview'})
        queue.assert_called_once_with('import_preview', payload)
        self.assertIn('jobId', result)

    def test_failed_import_keeps_configuration_and_success_returns_only_summary(self):
        data = sample()
        current = Store().read()
        Store().save(data, current['revision'])
        original = Store().read()
        bad = sample()
        bad['overrides'] = [{'id':'bad', 'name':'Bad', 'type':'UNKNOWN'}]
        cli.run_job('import', 'a' * 32, {'data':bad, 'revision':original['revision']})
        self.assertFalse(cli.dispatch('job', {'id':'a' * 32})['ok'])
        self.assertEqual(Store().read(), original)
        result = cli.dispatch('import_preview', {'data':sample()})
        self.assertEqual(result['summary']['sources'], 1)
        self.assertNotIn('data', result)
        self.assertEqual(Store().read(), original)

    def test_rpc_large_reply_uses_transfer_and_new_worker_entrypoint(self):
        with patch.object(sys, 'argv', ['fii', 'rpc-call', 'get']), patch.object(sys, 'stdin', io.StringIO('{}')), \
                patch.object(cli, 'dispatch', return_value={'data':'x' * 100000}), patch('sys.stdout', new_callable=io.StringIO) as out:
            cli.main()
        self.assertIn('responseTransfer', json.loads(out.getvalue()))
        with patch.object(sys, 'argv', ['fii', 'job-run', 'import', 'a' * 32]), patch.object(cli, 'run_job') as worker:
            cli.main()
        worker.assert_called_once_with('import', 'a' * 32)
