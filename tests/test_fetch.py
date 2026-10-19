import io
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from urllib.response import addinfourl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fii/files/usr/lib'))
from fii import fetch, model


class FetchAuthenticationTests(unittest.TestCase):
    def test_real_http_redirect_flow_delivers_auth_only_to_the_original_origin(self):
        received = []

        class Server(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append((self.path, self.headers.get('Host'), self.headers.get('Authorization')))
                target = {'/start': '/same', '/same': 'https://other.example/end'}.get(self.path)
                self.send_response(302 if target else 200)
                if target:
                    self.send_header('Location', target)
                self.end_headers()
                if not target:
                    self.wfile.write(b'function main(c){return c;}')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        class FixtureHTTPSHandler(HTTPSHandler):
            # Map test HTTPS origins to a real loopback fixture. TLS itself is
            # unchanged in production; this exercises urllib's redirect chain.
            def https_open(self, request):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    connection.request(request.get_method(), request.selector, headers=dict(request.header_items()))
                    response = connection.getresponse()
                    wrapped = addinfourl(io.BytesIO(response.read()), response.headers, request.full_url, response.status)
                    wrapped.msg = response.reason
                    return wrapped
                finally:
                    connection.close()

        try:
            opener = build_opener(ProxyHandler({}), fetch.CredentialRedirectHandler(), FixtureHTTPSHandler())
            with patch.object(fetch, 'build_opener', return_value=opener):
                body, _ = fetch.download('https://test-token@raw.githubusercontent.com/start', 'fii-test')
            self.assertIn('function main', body)
            self.assertEqual(received, [('/start', 'raw.githubusercontent.com', 'Bearer test-token'),
                                        ('/same', 'raw.githubusercontent.com', 'Bearer test-token'),
                                        ('/end', 'other.example', None)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_github_token_url_is_cleaned_and_sent_as_bearer_header(self):
        for info, token in (('test-token', 'test-token'), ('user:test-token', 'test-token'),
                            (':test-token', 'test-token'), ('test%2Dtoken', 'test-token')):
            with self.subTest(info=info):
                address = 'https://' + info + '@raw.githubusercontent.com/owner/repo/refs/heads/master/scripts/dns.js'
                with patch.object(fetch, 'build_opener') as opener:
                    response = opener.return_value.open.return_value.__enter__.return_value
                    response.geturl.return_value = 'https://raw.githubusercontent.com/owner/repo/refs/heads/master/scripts/dns.js'
                    response.read.return_value = b'function main(c){return c;}'
                    response.headers = {}
                    body, _ = fetch.download(address, 'fii-test')
                    request = opener.return_value.open.call_args.args[0]
                    self.assertEqual(request.full_url, response.geturl.return_value)
                    self.assertEqual(request.get_header('Authorization'), 'Bearer ' + token)
                    self.assertNotIn('Authorization', request.headers)
                    self.assertNotIn(token, request.full_url)
                    self.assertEqual(request.get_header('User-agent'), 'fii-test')
                    self.assertIn('function main', body)

    def test_github_auth_url_imports_without_losing_token_or_js(self):
        address = 'https://test-token@raw.githubusercontent.com/owner/repo/master/script.js'
        script = 'function main(c){return c;}'
        raw = {'format': 'subconverter-backup', 'version': 1,
               'sources': [{'id': 1, 'name': 'Source', 'url': address, 'cachedYaml': 'proxies: []'}],
               'templates': [{'id': 2, 'name': 'Script', 'type': 'JS', 'remoteUrl': address, 'yamlBody': script}],
               'outputProfiles': [{'id': 3, 'name': 'Output', 'sourceIds': '1', 'overrideIds': '2'}]}
        result, _ = model.import_backup(raw, model.defaults()['settings'])
        self.assertEqual(result['sources'][0]['url'], address)
        self.assertEqual(result['overrides'][0]['url'], address)
        self.assertEqual(result['overrides'][0]['body'], script)

    def test_only_https_github_hosts_accept_tokens_and_encoded_controls_are_rejected(self):
        for address in ('https://token@github.com.example.com/file',
                        'https://token@evilgithubusercontent.com/file',
                        'http://token@raw.githubusercontent.com/file',
                        'https://user:@raw.githubusercontent.com/file',
                        'https://bad%0D%0Atoken@raw.githubusercontent.com/file',
                        'https://bad%00token@raw.githubusercontent.com/file'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                model.url(address)
        for host in ('github.com', 'api.github.com', 'raw.githubusercontent.com', 'gist.githubusercontent.com'):
            model.url('https://token@' + host + '/file')

    def test_redirects_keep_auth_only_on_same_origin_and_never_restore_it(self):
        handler = fetch.CredentialRedirectHandler()
        request = Request('https://raw.githubusercontent.com/a', headers={'User-Agent': 'fii-test'})
        request.add_unredirected_header('Authorization', 'Bearer test-token')
        same = handler.redirect_request(request, None, 302, 'Found', {}, 'https://raw.githubusercontent.com:443/b')
        self.assertEqual(same.get_header('Authorization'), 'Bearer test-token')
        for target in ('https://example.com/b', 'https://api.github.com/b',
                       'https://raw.githubusercontent.com:8443/b', 'http://raw.githubusercontent.com/b'):
            with self.subTest(target=target):
                redirected = handler.redirect_request(same, None, 302, 'Found', {}, target)
                self.assertIsNone(redirected.get_header('Authorization'))
                back = handler.redirect_request(redirected, None, 302, 'Found', {}, request.full_url)
                self.assertIsNone(back.get_header('Authorization'))

    def test_redirects_reject_non_http_or_new_embedded_credentials(self):
        handler = fetch.CredentialRedirectHandler()
        for target in ('file:///etc/passwd', 'ftp://example.com/file',
                       'https://other-token@raw.githubusercontent.com/file'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(Request('https://raw.githubusercontent.com/a'), None, 302, 'Found', {}, target)

    def test_download_errors_do_not_echo_token_and_failed_refresh_retains_js(self):
        address = 'https://test-token@raw.githubusercontent.com/owner/repo/master/script.js'
        script = 'function main(c){return c;}'
        for error in (HTTPError(address, 401, 'test-token', {}, io.BytesIO()), URLError(address)):
            item = {'url': address, 'body': script, 'type': 'JS'}
            with self.subTest(error=type(error).__name__), patch.object(fetch, 'build_opener') as opener:
                opener.return_value.open.side_effect = error
                self.assertFalse(fetch.refresh(item, model.defaults()['settings'], 'overrides'))
                self.assertEqual(item['body'], script)
                self.assertNotIn('test-token', item['lastError'])
                self.assertNotIn('raw.githubusercontent.com', item['lastError'])


if __name__ == '__main__':
    unittest.main()
