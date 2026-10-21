import os
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample
from fii import cli, model, render, runtime
from fii.store import Store, write_json


class ListenerTests(unittest.TestCase):
    def test_old_settings_get_loopback_default_without_writing_state(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            data = sample(); data['settings'].pop('mixedAddress')
            store = Store(); write_json(store.path, data); before = store.path.read_bytes()
            self.assertEqual(store.read()['settings']['mixedAddress'], '127.0.0.1')
            self.assertEqual(store.path.read_bytes(), before)

    def test_mixed_binding_is_validated_and_reaches_runtime(self):
        for address in ('127.0.0.1', '192.168.100.1', '0.0.0.0', '::1', 'fd00::1', '::'):
            with self.subTest(address=address):
                data = sample(); data['settings']['mixedAddress'] = address; model.validate(data)
                config = render.load_map(runtime.overlay('', data['settings']))
                self.assertEqual(config['listeners'][0]['listen'], address)
        for address in ('', '*', 'router.lan', '127.0.0.1:7890', None, 1):
            with self.subTest(invalid=address), self.assertRaisesRegex(ValueError, '混合代理监听地址'):
                data = sample(); data['settings']['mixedAddress'] = address; model.validate(data)

    def test_listener_override_preserves_authentication_and_routing(self):
        data = sample()
        custom = {'name':'inss', 'type':'shadowsocks', 'listen':'0.0.0.0', 'port':24313,
                  'cipher':'aes-128-gcm', 'password':'fixture-only', 'udp':True,
                  'rule':'custom-rules', 'proxy':'DIRECT'}
        data['overrides'] = [{'id':'listener', 'name':'Listener', 'type':'YAML', 'global':True,
                              'body':render.dump({'+listeners':[custom]})}]
        result = render.render(data, 'p1')
        config = render.load_map(runtime.overlay(result['yaml'], data['settings']))
        self.assertEqual(config['listeners'], [
            {'name':'fii-mixed', 'type':'mixed', 'listen':'127.0.0.1', 'port':7890}, custom])
        self.assertNotIn('listeners', ' '.join(runtime_warning(data, result['yaml'])))

    def test_explicit_managed_name_wins_without_duplicates_and_overlay_is_idempotent(self):
        settings = model.defaults()['settings']
        custom = {'name':'fii-mixed', 'type':'mixed', 'listen':'192.0.2.1', 'port':8888,
                  'users':[{'username':'fixture', 'password':'fixture'}]}
        body = render.dump({'listeners':[custom]})
        first = runtime.overlay(body, settings)
        self.assertEqual(render.load_map(first)['listeners'], [custom])
        self.assertEqual(render.load_map(runtime.overlay(first, settings))['listeners'], [custom])

    def test_invalid_listener_shapes_fail_before_activation(self):
        for value in ({}, 'invalid', [1], ['invalid']):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'listeners'):
                runtime.overlay(render.dump({'listeners':value}), model.defaults()['settings'])

    def test_setting_patch_save_and_export_preserve_the_binding(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            store = Store(); data = sample(); saved = store.save(data, store.read()['revision'])
            changed = cli.apply_patch(saved, {'settings':{'mixedAddress':'192.168.100.1'}})
            store.save(changed, saved['revision'])
            self.assertEqual(store.read()['settings']['mixedAddress'], '192.168.100.1')
            exported = cli.dispatch('export', {})
            self.assertIn('192.168.100.1', str(exported))


def runtime_warning(data, body):
    warnings = []
    runtime.overlay(body, data['settings'], warnings=warnings)
    return warnings
