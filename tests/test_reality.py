import copy
import json
import os
import unittest
from unittest.mock import patch

import yaml

from test_fii import TestDirectory, sample
from fii import cli, model, render, runtime
from fii.store import Store, rooted, write_json


class RealityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_original_scalar_spelling_and_quoted_emission_survive_repeated_overlay(self):
        for sid in ('00112233', '0000000000000001', '0012345678901234', '1234567890123456',
                    '9999999999999999', '1e123456', 'abcdef01', '0x12345678', '1.23e+04', ''):
            with self.subTest(short_id=sid):
                root = render.load_map("reality-opts: {short-id: " + (sid or "''") + "}\nport: 443\ntls: true")
                self.assertEqual(root['reality-opts']['short-id'], sid)
                self.assertIs(type(root['reality-opts']['short-id']), str)
                self.assertEqual(root['port'], 443)
                self.assertIs(root['tls'], True)
                body = render.dump(root)
                self.assertIn("short-id: '" + sid + "'", body)
                for _ in range(3):
                    body = runtime.overlay(body, model.defaults()['settings'])
                    self.assertEqual(render.load_map(body)['reality-opts']['short-id'], sid)

    def test_aliases_merges_duplicate_keys_and_null_keep_their_meaning(self):
        result = render.load_map('port: &id 00112233\nreality-opts: {short-id: *id}\n')
        self.assertEqual(result['port'], 38043)
        self.assertEqual(result['reality-opts']['short-id'], '00112233')
        result = render.load_map('base: &base {short-id: 00112233}\nreality-opts: {<<: *base, short-id: 00000001}')
        self.assertEqual(result['reality-opts']['short-id'], '00000001')
        self.assertIsNone(render.load_map('short-id: 00112233\nshort-id: null')['short-id'])
        self.assertEqual(render.load_map('short-id: null\nshort-id: 00112233')['short-id'], '00112233')
        self.assertIsNone(render.load_map('short-id: null')['short-id'])
        self.assertEqual(render.load_map("short-id: 'null'")['short-id'], 'null')
        self.assertEqual(yaml.safe_load('short-id: 00112233')['short-id'], 38043, 'Global PyYAML behavior must stay unchanged')

    def test_import_yaml_and_js_overrides_preserve_id_without_reimporting_saved_cache(self):
        data = sample()
        data['sources'][0]['cachedYaml'] = 'proxies: [{name: REALITY, type: vless, port: 443, reality-opts: {short-id: 00112233}}]'
        data['overrides'] = [
            {'id': 'yaml', 'name': 'YAML', 'type': 'YAML', 'global': True,
             'body': 'proxies+: [{name: Second, type: vless, reality-opts: {short-id: 0012345678901234}}]'},
            {'id': 'js', 'name': 'JS', 'type': 'JS', 'global': True,
             'body': 'function main(c){if(c.proxies[0]["reality-opts"]["short-id"]!=="00112233")throw Error("coerced input");return c;}'}]
        original = copy.deepcopy(data)
        Store().save(data, Store().read()['revision'])
        result = cli.dispatch('preview', {'id': 'p1', 'runtime': True})
        nodes = render.load_map(result['yaml'])['proxies']
        self.assertEqual([node['reality-opts']['short-id'] for node in nodes], ['00112233', '0012345678901234'])
        self.assertEqual(Store().read()['sources'], original['sources'])
        self.assertEqual(Store().read()['overrides'], original['overrides'])

    def test_share_link_ids_keep_leading_zeroes_and_empty_id(self):
        for sid in ('00112233', '0000000000000001', ''):
            proxy = render.parse_link('vless://uuid@node.example:443?security=reality&pbk=test&sid=' + sid)
            self.assertEqual(render.load_map(render.dump({'proxies': [proxy]}))['proxies'][0]['reality-opts']['short-id'], sid)

    def test_core_error_identifies_proxy_143_by_name_without_leaking_values(self):
        binary = rooted(runtime.CORE)
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'fixture')
        body = render.dump({'proxies': [{'name': 'Node%d' % i} for i in range(144)]})
        with patch.object(runtime.os, 'access', return_value=True), patch.object(runtime, 'run',
                side_effect=ValueError('proxy 143: invalid REALITY short ID')):
            with self.assertRaisesRegex(ValueError, '节点「Node143」（输出第 144 项）') as raised:
                runtime.core_test(body)
        self.assertIn('当前运行配置未替换', str(raised.exception))
        self.assertEqual(list(rooted('/tmp/fii').glob('candidate-*')), [])

    def test_real_invalid_id_cannot_replace_the_active_snapshot(self):
        data = sample()
        data['sources'][0]['cachedYaml'] = 'proxies: [{name: Invalid, type: vless, reality-opts: {short-id: not-hex}}]'
        Store().save(data, Store().read()['revision'])
        active = {'outputId': 'previous', 'yaml': 'previous', 'settings': data['settings']}
        write_json(rooted('/etc/fii/active.json'), active)
        with patch.object(runtime, 'core_test', side_effect=ValueError('REALITY short-id 无效')), \
                patch.object(runtime, 'restart_and_wait') as restart, self.assertRaises(ValueError):
            runtime.activate('p1')
        self.assertEqual(runtime.active(), active)
        restart.assert_not_called()
