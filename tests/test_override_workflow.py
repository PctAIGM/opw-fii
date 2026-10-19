import copy
import json
import os
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample
from fii import cli, model, render
from fii.store import Store


class OverrideWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_flutter_database_order_is_replaced_with_display_and_execution_order(self):
        templates = [
            {'id': 10, 'sortOrder': 20, 'name': 'Ten', 'type': 'YAML', 'yamlBody': 'trace+: [ten]'},
            {'id': 9, 'sortOrder': 10, 'name': 'Global', 'global': True, 'type': 'YAML', 'yamlBody': 'trace: [global]'},
            {'id': 2, 'sortOrder': 20, 'name': 'Two', 'type': 'YAML', 'yamlBody': 'trace+: [two]',
             'remoteUrl': 'https://example.com/override', 'lastRefreshAt': 1792400000123, 'lastError': 'old error'},
        ]
        raw = {'format': 'subconverter-backup', 'version': 1, 'templates': templates,
               'outputProfiles': [{'id': 1, 'name': 'Output', 'overrideIds': '10,9,2'}]}
        before = copy.deepcopy(raw)
        data, _ = model.import_backup(raw, model.defaults()['settings'])
        self.assertEqual([item['id'] for item in data['overrides']], ['9', '2', '10'])
        self.assertEqual(data['outputs'][0]['overrideIds'], ['9', '2', '10'])
        self.assertEqual(data['overrides'][1]['url'], templates[2]['remoteUrl'])
        self.assertEqual(data['overrides'][1]['lastRefreshAt'], 1792400000)
        self.assertEqual(data['overrides'][1]['lastError'], 'old error')
        result = render.render(data, '1')
        self.assertEqual(render.load_map(result['yaml'])['trace'], ['global', 'two', 'ten'])
        self.assertEqual([item['id'] for item in result['appliedOverrides']], ['9', '2', '10'])
        self.assertEqual(raw, before)

    def test_legacy_and_fii_backups_keep_explicit_order(self):
        raw = {'format': 'subconverter-backup', 'version': 1, 'templates': [
            {'id': 10, 'name': 'Ten', 'type': 'YAML', 'yamlBody': '{}'},
            {'id': 2, 'name': 'Two', 'type': 'YAML', 'yamlBody': '{}'}],
            'outputProfiles': [{'id': 1, 'name': 'Output', 'overrideIds': '2,10'}]}
        data, _ = model.import_backup(raw, model.defaults()['settings'])
        self.assertEqual([item['id'] for item in data['overrides']], ['10', '2'])
        self.assertEqual(data['outputs'][0]['overrideIds'], ['2', '10'])
        data['overrides'][0]['sortOrder'] = 999
        restored, _ = model.import_backup(data, data['settings'])
        self.assertEqual(restored['overrides'], data['overrides'])
        self.assertEqual(restored['outputs'], data['outputs'])

    def test_reordering_preserves_bodies_settings_and_per_output_selection(self):
        data = sample()
        data['overrides'] = [{'id': key, 'name': key, 'type': 'YAML', 'global': True, 'body': 'value: ' + key}
                             for key in ('a', 'b', 'c')]
        data['outputs'][0]['overrideIds'] = ['c', 'a']
        saved = Store().save(data, Store().read()['revision'])
        with patch.object(cli, 'validate_override', side_effect=AssertionError('unchanged content')):
            result = cli.dispatch('save', {'patch': {'kind': 'overrides', 'order': ['c', 'a', 'b']},
                                           'compact': True, 'revision': saved['revision']})
        new = Store().read()
        self.assertEqual([item['id'] for item in new['overrides']], ['c', 'a', 'b'])
        self.assertEqual(new['overrides'][0], data['overrides'][2])
        self.assertEqual(new['settings'], data['settings'])
        self.assertEqual(new['outputs'], data['outputs'])
        self.assertNotIn('body', result['data']['overrides'][0])
        self.assertEqual(render.load_map(render.render(new, 'p1')['yaml'])['value'], 'b')

    def test_bad_or_stale_reorders_cannot_drop_or_duplicate_items(self):
        data = sample()
        data['overrides'] = [{'id': key, 'name': key, 'type': 'YAML', 'body': '{}'} for key in ('a', 'b')]
        saved = Store().save(data, Store().read()['revision'])
        for order in ([], ['a'], ['a', 'a'], ['a', 'x'], ['a', []], 'a,b'):
            with self.subTest(order=order), self.assertRaises(ValueError):
                cli.dispatch('save', {'patch': {'kind': 'overrides', 'order': order}, 'revision': saved['revision']})
            self.assertEqual(Store().read(), saved)
        with self.assertRaisesRegex(ValueError, '其他操作'):
            cli.dispatch('save', {'patch': {'kind': 'overrides', 'order': ['b', 'a']}, 'revision': 0})

    def test_editor_sync_reads_fresh_item_and_matching_compact_revision(self):
        data = sample()
        data['sources'][0]['cachedYaml'] += '# ' + 'x' * 50000
        data['overrides'] = [{'id': 'a', 'name': 'a', 'type': 'YAML', 'body': '# ' + 'x' * 50000}]
        saved = Store().save(data, Store().read()['revision'])
        saved['overrides'][0]['name'] = 'background-refresh'
        Store().save(saved, saved['revision'])
        result = cli.dispatch('get', {'kind': 'overrides', 'id': 'a', 'sync': True})
        self.assertEqual(result['item']['name'], 'background-refresh')
        self.assertEqual(result['data']['revision'], result['revision'])
        self.assertEqual(result['item']['body'], saved['overrides'][0]['body'])
        self.assertNotIn('body', result['data']['overrides'][0])
        self.assertNotIn('cachedYaml', result['data']['sources'][0])
        metadata = cli.dispatch('get', {'kind': 'sources', 'id': 's1', 'sync': True, 'metadataOnly': True})
        self.assertNotIn('cachedYaml', metadata['item'])
        self.assertLess(len(json.dumps(metadata)), 10000)

    def test_preview_reports_exact_deduplicated_enabled_order_and_progress(self):
        data = sample()
        data['overrides'] = [
            {'id': 'a', 'name': 'First', 'type': 'YAML', 'global': True, 'body': '{}'},
            {'id': 'b', 'name': 'Disabled', 'type': 'YAML', 'global': True, 'enabled': False, 'body': '{}'},
            {'id': 'c', 'name': 'Third', 'type': 'YAML', 'body': '{}'}]
        data['outputs'][0]['overrideIds'] = ['c', 'a', 'b']
        saved = Store().save(data, Store().read()['revision'])
        messages = []
        result = cli.dispatch('preview', {'id': 'p1'}, progress=messages.append)
        self.assertEqual([item['id'] for item in result['appliedOverrides']], ['a', 'c'])
        self.assertTrue(any('First' in message for message in messages))
        self.assertFalse(any('Disabled' in message for message in messages))
        self.assertEqual(messages[-1], '正在生成 YAML…')
        self.assertEqual(Store().read(), saved)

    def test_accelerated_yaml_keeps_alias_limits_booleans_and_round_trip(self):
        text = 'node: &node {name: ON, enabled: false}\nitems: [' + ','.join('*node' for _ in range(64)) + ']'
        parsed = render.load_map(text)
        self.assertEqual(parsed['node'], {'name': 'ON', 'enabled': False})
        self.assertEqual(render.load_map(render.dump(parsed)), parsed)
        with self.assertRaisesRegex(ValueError, '别名过多'):
            render.load_map(text.replace('items: [', 'items: [*node,'))
