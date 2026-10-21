from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import subprocess
import threading
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample, ROOT
from fii import cli, model, urltest
from fii.store import Store, lock, write_json


class Reply(BaseHTTPRequestHandler):
    def do_GET(self):
        code = {'/ok': 204, '/denied': 403, '/redirect': 302}.get(self.path, 404)
        self.send_response(code)
        if code == 302:
            self.send_header('Location', '/ok')
        # A huge declared body is deliberately not delivered: only headers count.
        self.send_header('Content-Length', '9999999' if code != 204 else '0')
        self.end_headers()

    def log_message(self, *args):
        pass


class UrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Reply)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def test_old_state_defaults_without_disk_write_and_empty_list_stays_empty(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            data = sample(); data['settings'].pop('urlTestSites')
            store = Store(); write_json(store.path, data); before = store.path.read_bytes()
            self.assertEqual(store.read()['settings']['urlTestSites'], urltest.DEFAULT_SITES)
            self.assertEqual(store.path.read_bytes(), before)
            data['settings']['urlTestSites'] = []
            model.validate(data)
            self.assertEqual(data['settings']['urlTestSites'], [])

    def test_site_validation(self):
        valid = {'id': 'local', 'name': '内网测试', 'url': self.base + '/ok'}
        urltest.validate_sites([valid])
        for change in ({'url':'file:///etc/passwd'}, {'url':'https://user:pass@github.com/'},
                       {'url':'http://bad:invalid/'}, {'url':'https://a/\r\n'},
                       {'url':'https://a/#fragment'}, {'url':'https://测试/'},
                       {'name':''}, {'name':'\n'}, {'id':'a/b'}, {'id':None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                urltest.validate_sites([{**valid, **change}])
        for sites in ([valid, valid], {}, [{'id':str(i), 'name':'test', 'url':self.base} for i in range(25)]):
            with self.assertRaises(ValueError):
                urltest.validate_sites(sites)

    def test_previous_version_compact_cache_is_rebuilt_without_changing_state(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            from fii.store import rooted
            store = Store(); data = store.read(); before = store.path.read_bytes()
            old = cli.view_data(data); old['settings'].pop('urlTestSites')
            write_json(rooted('/tmp/fii/state-view.json'), {'schema':2, 'stamp':list(store.signature()), 'data':old})
            current = cli.compact_data(Store())
            self.assertEqual(current['settings']['urlTestSites'], urltest.DEFAULT_SITES)
            self.assertEqual(before, store.path.read_bytes())

    def test_first_response_headers_status_and_environment_proxy_ignored(self):
        with patch.dict(os.environ, {'http_proxy':'http://127.0.0.1:1', 'HTTP_PROXY':'http://127.0.0.1:1', 'NO_PROXY':''}):
            for path, code in (('/ok',204), ('/denied',403), ('/redirect',302)):
                result = urltest.probe(self.base + path)
                self.assertTrue(result['reachable'])
                self.assertEqual(result['status'], code)
                self.assertGreaterEqual(result['delay'], 1)

    def test_saved_id_only_child_process_no_state_changes_and_stale_url(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            data = sample(); data['settings']['urlTestSites'] = [{'id':'local', 'name':'Local', 'url':self.base+'/ok'}]
            store = Store(); store.save(data, store.read()['revision']); before = store.path.read_bytes()
            result = cli.dispatch('url_test', {'id':'local', 'url':self.base+'/ok'})
            self.assertTrue(result['reachable']); self.assertEqual(result['status'], 204)
            self.assertEqual(result['id'], 'local'); self.assertIn('testedAt', result)
            self.assertEqual(before, store.path.read_bytes())
            with self.assertRaisesRegex(ValueError, '已删除'):
                urltest.test_site('missing')
            with self.assertRaisesRegex(ValueError, '已变更'):
                urltest.test_site('local', 'https://different.example/')

    def test_total_deadline_and_global_concurrency_limit(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            Store().read()
            with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('probe', 8)) as run:
                result = urltest.test_site('baidu')
                self.assertFalse(result['reachable']); self.assertIn('8 秒', result['error'])
                self.assertEqual(run.call_args.kwargs['timeout'], 8)
            with lock('url-test-0'), lock('url-test-1'), lock('url-test-2'):
                with self.assertRaisesRegex(ValueError, '其他网址'):
                    urltest.test_site('baidu')

    def test_network_errors_do_not_expose_url_or_exception_details(self):
        from urllib.error import URLError
        for reason, expected in ((socket.gaierror('private'), 'DNS'), (TimeoutError('private'), '超时'),
                                 (OSError('secret-url-token'), '连接失败')):
            with patch('urllib.request.OpenerDirector.open', side_effect=URLError(reason)):
                result = urltest.probe('https://example.com/?token=secret')
                self.assertIn(expected, result['error']); self.assertNotIn('secret', str(result))

    def test_management_save_persists_without_service_operations_and_rpc_is_registered(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}), patch.object(cli, 'run') as run:
            data = sample(); store = Store(); store.save(data, store.read()['revision'])
            result = cli.dispatch('save', {'patch':{'settings':{'urlTestSites':[]}}, 'revision':data['revision']})
            self.assertEqual(store.read()['settings']['urlTestSites'], [])
            self.assertIn('无需应用', result['message']); run.assert_not_called()
        self.assertTrue(cli.background('url_test', {}))
        acl = json.loads((ROOT/'luci-app-fii/root/usr/share/rpcd/acl.d/luci-app-fii.json').read_text())
        self.assertIn('url_test', acl['luci-app-fii']['read']['ubus']['luci.fii'])
        self.assertIn("'url_test'", (ROOT/'fii/files/usr/share/fii/rpc.uc').read_text())
