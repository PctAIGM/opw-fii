import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from test_fii import TestDirectory, ROOT
from fii import cli, model, render, resources, runtime
from fii.store import rooted, write_json


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()
        self.config = {'geodata-mode': True, 'geox-url': {'geosite': 'https://user:secret@example.com/site.dat?token=secret'},
                       'rule-providers': {'Snapshot only': {'type': 'http', 'behavior': 'domain', 'url': 'https://example.com/old'}}}
        self.settings = model.defaults()['settings']
        self.save()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def save(self):
        write_json(rooted('/etc/fii/active.json'), {'settings': self.settings, 'yaml': render.dump(self.config)})

    def live(self):
        rooted('/tmp/fii').mkdir(parents=True, exist_ok=True)
        rooted('/tmp/fii/ready').write_text('ready')

    def test_stopped_view_is_readonly_and_sanitizes_resource_urls(self):
        with patch.object(runtime, 'controller') as controller, patch.object(runtime, 'run') as run:
            result = cli.dispatch('resources')
        controller.assert_not_called()
        run.assert_not_called()
        self.assertEqual(result['source'], 'snapshot')
        self.assertEqual(len(result['geo']), 4)
        self.assertFalse(result['running'])
        self.assertFalse(result['rulesets'][0]['canUpdate'])
        self.assertTrue(result['geodataMode'])
        self.assertEqual(result['geo'][0]['url'], 'https://example.com/site.dat')
        self.assertNotIn('secret', json.dumps(result))
        for payload in ({'kind': 'geo'}, {'kind': 'ruleset', 'name': 'Snapshot only'}):
            with patch.object(runtime, 'controller') as controller, self.assertRaisesRegex(ValueError, '内核已停止'):
                cli.dispatch('resource_update', payload)
            controller.assert_not_called()

    def test_missing_active_snapshot_is_an_explicit_empty_state(self):
        rooted('/etc/fii/active.json').unlink()
        with patch.object(runtime, 'controller') as controller:
            result = resources.view()
        self.assertEqual(result['source'], 'none')
        self.assertEqual(result['rulesets'], [])
        controller.assert_not_called()

    def test_geo_metadata_matches_case_insensitive_mihomo_file_discovery(self):
        home = rooted('/etc/fii/core')
        home.mkdir(parents=True)
        for name in ('geosite.DAT', 'GeoIP.dat', 'ASN.mmdb', 'Country.mmdb', 'geoip.metadb'):
            (home / name).write_bytes(b'data')
        result = resources.view()['geo']
        self.assertTrue(all(item['exists'] and item['size'] == 4 and item['modifiedAt'] for item in result))
        self.assertEqual(result[0]['file'], 'geosite.DAT')
        self.assertEqual(result[3]['file'], 'Country.mmdb')

    def test_live_rulesets_come_only_from_the_runtime_and_keep_compact_metadata(self):
        self.live()
        reply = {'providers': {'Live / 规则': {'vehicleType': 'HTTP', 'behavior': 'Domain', 'ruleCount': 42,
                                             'updatedAt': '2026-10-20T00:00:00Z', 'payload': ['private rule']},
                               'local': {'vehicleType': 'File', 'ruleCount': True},
                               'inline': {'vehicleType': 'Inline'}}}
        with patch.object(runtime, 'ready', return_value=True), patch.object(runtime, 'controller', return_value=reply) as controller:
            result = resources.view()
        controller.assert_called_once_with('/providers/rules')
        self.assertTrue(result['running'])
        self.assertEqual([item['name'] for item in result['rulesets']], ['Live / 规则', 'local', 'inline'])
        self.assertEqual([item['canUpdate'] for item in result['rulesets']], [True, True, False])
        self.assertEqual(result['rulesets'][0]['ruleCount'], 42)
        self.assertIsNone(result['rulesets'][1]['ruleCount'])
        self.assertNotIn('private rule', json.dumps(result))

    def test_failed_rules_read_preserves_geo_and_does_not_fall_back_to_a_stale_list(self):
        self.live()
        with patch.object(runtime, 'ready', return_value=True), patch.object(runtime, 'controller', side_effect=ValueError('offline')):
            result = resources.view()
        self.assertEqual(result['rulesError'], 'offline')
        self.assertEqual(result['rulesets'], [])
        self.assertEqual(len(result['geo']), 4)

    def test_updates_use_authenticated_core_endpoints_without_restart_or_config_writes(self):
        self.live()
        original = rooted('/etc/fii/active.json').read_bytes()
        events = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                events.append((handler.command, handler.path, handler.headers.get('Authorization')))
                body = {'version': 'fixture'} if handler.path == '/version' else {
                    'providers': {'Live / 规则': {'vehicleType': 'HTTP'}, 'local': {'vehicleType': 'File'}, 'inline': {'vehicleType': 'Inline'}}}
                encoded = json.dumps(body).encode()
                handler.send_response(200)
                handler.send_header('Content-Length', str(len(encoded)))
                handler.end_headers()
                handler.wfile.write(encoded)

            def do_POST(handler):
                events.append((handler.command, handler.path, handler.headers.get('Authorization')))
                handler.send_response(204)
                handler.end_headers()

            do_PUT = do_POST

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.settings['controllerPort'] = server.server_port
        self.save()
        original = rooted('/etc/fii/active.json').read_bytes()
        try:
            with patch.object(runtime, 'run') as run:
                progress = []
                resources.update('geo', progress=lambda message: progress.append(message))
                resources.update('ruleset', 'Live / 规则')
                resources.update('ruleset', 'local')
                for name in ('Snapshot only', 'inline', '../configs/geo'):
                    with self.assertRaises(ValueError):
                        resources.update('ruleset', name)
                self.assertTrue(progress)
            run.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        writes = [(verb, path) for verb, path, _ in events if verb != 'GET']
        self.assertEqual(writes, [('POST', '/configs/geo'), ('PUT', '/providers/rules/Live%20%2F%20%E8%A7%84%E5%88%99'), ('PUT', '/providers/rules/local')])
        self.assertTrue(all(auth == 'Bearer ' + self.settings['secret'] for _, _, auth in events))
        self.assertEqual(rooted('/etc/fii/active.json').read_bytes(), original)

    def test_invalid_resource_targets_are_rejected_before_network_access(self):
        for kind, name in (('anything', None), ('geo', 'geosite'), ('ruleset', None), ('ruleset', []), ('ruleset', ''), ('ruleset', 'x' * 1025)):
            with patch.object(runtime, 'controller') as controller, self.assertRaises(ValueError):
                resources.update(kind, name)
            controller.assert_not_called()

    def test_update_failure_surfaces_without_retrying_or_restarting(self):
        self.live()
        with patch.object(runtime, 'ready', return_value=True), patch.object(runtime, 'run') as run, \
                patch.object(runtime, 'controller', side_effect=ValueError('HTTP 503')) as controller:
            with self.assertRaisesRegex(ValueError, 'HTTP 503'):
                resources.update('geo')
        run.assert_not_called()
        controller.assert_called_once_with('/configs/geo', method='POST', timeout=600)

    def test_resource_rpc_permissions_and_background_contract(self):
        self.assertIn('resources', cli.READ_METHODS)
        self.assertNotIn('resource_update', cli.READ_METHODS)
        self.assertTrue(cli.background('resource_update', {'kind': 'geo'}))
        acl = json.loads((ROOT / 'luci-app-fii/root/usr/share/rpcd/acl.d/luci-app-fii.json').read_text())['luci-app-fii']
        self.assertIn('resources', acl['read']['ubus']['luci.fii'])
        self.assertNotIn('resource_update', acl['read']['ubus']['luci.fii'])
        self.assertIn('resource_update', acl['write']['ubus']['luci.fii'])
