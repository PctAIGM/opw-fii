import copy
import json
import os
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample, YAML
from fii import cli
from fii.store import Store, lock


class PartialSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()
        data = sample()
        data['sources'][0].update(cachedYaml=YAML + '# ' + 'x' * 500000 + '\n', nodeCount=2)
        data['overrides'] = [{'id': 'js', 'name': 'Script', 'type': 'JS', 'body': 'function main(c){return c;}'}]
        data['outputs'][0]['overrideIds'] = ['js']
        self.original = Store().save(data, Store().read()['revision'])

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def save(self, change, **extra):
        return cli.dispatch('save', {'patch': change, 'compact': True,
                                    'revision': Store().read()['revision'], **extra})

    def test_compact_views_are_bounded_and_full_edit_export_contents_remain_exact(self):
        view = cli.dispatch('get', {'compact': True})['data']
        self.assertLess(len(json.dumps(view).encode()), 10000)
        self.assertNotIn('cachedYaml', view['sources'][0])
        self.assertNotIn('body', view['overrides'][0])
        self.assertEqual(view['overrides'][0]['snippet'], self.original['overrides'][0]['body'])
        for kind in ('sources', 'overrides'):
            self.assertEqual(cli.dispatch('get', {'kind': kind, 'id': self.original[kind][0]['id']})['item'], self.original[kind][0])
        self.assertEqual(cli.dispatch('export')['data'], self.original)
        self.assertEqual(Store().read(), self.original)

    def test_metadata_and_settings_changes_skip_unchanged_yaml_and_js_validation(self):
        with patch.object(cli, 'extract') as extract, patch.object(cli, 'validate_override') as validate:
            self.save({'kind': 'outputs', 'id': 'p1', 'fields': {'name': 'Renamed'}})
            result = self.save({'settings': {'userAgent': 'fast-test'}})
            extract.assert_not_called()
            validate.assert_not_called()
        saved = Store().read()
        self.assertEqual(saved['sources'], self.original['sources'])
        self.assertEqual(saved['overrides'], self.original['overrides'])
        self.assertEqual(saved['settings']['userAgent'], 'fast-test')
        self.assertNotIn('cachedYaml', result['data']['sources'][0])

    def test_partial_source_edit_preserves_cache_and_cannot_spoof_node_count(self):
        self.save({'kind': 'sources', 'id': 's1', 'fields': {'prefix': 'New-', 'nodeCount': 999}})
        saved = Store().read()['sources'][0]
        self.assertEqual(saved['cachedYaml'], self.original['sources'][0]['cachedYaml'])
        self.assertEqual(saved['nodeCount'], 2)
        self.assertEqual(saved['prefix'], 'New-')

    def test_changed_yaml_and_js_are_validated_and_failures_keep_the_saved_state(self):
        for kind, ident, fields in (('sources', 's1', {'cachedYaml': 'proxies: ['}),
                                    ('overrides', 'js', {'body': 'function main( {'})):
            before = Store().read()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.save({'kind': kind, 'id': ident, 'fields': fields})
            self.assertEqual(Store().read(), before)

    def test_removal_cleans_associations_and_additions_validate_contents(self):
        self.save({'kind': 'overrides', 'remove': 'js'})
        self.assertEqual(Store().read()['outputs'][0]['overrideIds'], [])
        self.save({'kind': 'sources', 'remove': 's1'})
        self.assertEqual(Store().read()['outputs'][0]['sourceIds'], [])
        self.save({'kind': 'overrides', 'item': {'id': 'new', 'name': 'New', 'type': 'RULES', 'body': 'DOMAIN,x.test,DIRECT'}})
        self.assertEqual(Store().read()['overrides'][0]['id'], 'new')

    def test_compact_replacement_invalid_patches_and_stale_revisions_cannot_drop_data(self):
        for change in ({'kind': 'settings', 'remove': 'x'}, {'kind': 'sources', 'id': 's1', 'fields': {'id': 'other'}},
                       {'kind': 'sources', 'item': {'id': 's1', 'contentOmitted': True}},
                       {'kind': 'sources', 'id': 'missing', 'fields': {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.save(change)
        with self.assertRaises(ValueError):
            cli.dispatch('save', {'data': cli.dispatch('get', {'compact': True})['data'], 'revision': self.original['revision']})
        with self.assertRaisesRegex(ValueError, '其他操作'):
            self.save({'settings': {}}, revision=0)
        self.assertEqual(Store().read(), self.original)

    def test_concurrent_save_during_validation_is_not_overwritten(self):
        concurrent = copy.deepcopy(self.original)
        concurrent['settings']['userAgent'] = 'concurrent-change'
        def other_save(item):
            with lock():
                Store().save(concurrent, self.original['revision'])
        with patch.object(cli, 'validate_override', side_effect=other_save), self.assertRaisesRegex(ValueError, '其他操作'):
            self.save({'kind': 'overrides', 'id': 'js', 'fields': {'body': 'function main(c){c.x=1;return c;}'}})
        self.assertEqual(Store().read(), concurrent)


if __name__ == '__main__':
    unittest.main()
