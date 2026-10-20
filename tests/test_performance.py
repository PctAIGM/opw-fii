import copy
import json
import os
import time
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample, YAML
from fii import cli, javascript, render
from fii.store import Store, rooted, write_json


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT': self.temp.name})
        self.env.start()
        Store().save(sample(), Store().read()['revision'])

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_compact_cache_reuses_validated_metadata_and_tracks_external_writes(self):
        first = cli.dispatch('get', {'compact': True})
        with patch.object(Store, 'read', side_effect=AssertionError('reread unchanged state')):
            self.assertEqual(cli.dispatch('get', {'compact': True}), first)
        data = Store().read()
        data['sources'][0]['name'] = 'External edit'
        write_json(Store().path, data)  # external replacement with the same revision
        self.assertEqual(cli.dispatch('get', {'compact': True})['data']['sources'][0]['name'], 'External edit')
        rooted('/tmp/fii/state-view.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(cli.dispatch('get', {'compact': True})['data'], cli.view_data(Store().read()))

    def test_saved_metadata_is_published_for_native_readers_without_a_second_state_read(self):
        saved = cli.dispatch('save', {'patch': {'settings': {'userAgent': 'native-rpc'}},
                                     'revision': Store().read()['revision'], 'compact': True})
        with patch.object(Store, 'read', side_effect=AssertionError('metadata was not published')):
            self.assertEqual(cli.dispatch('get', {'compact': True})['data'], saved['data'])
        snapshot = json.loads(rooted('/tmp/fii/state-view.json').read_text())
        self.assertEqual(snapshot['stamp'], list(Store().signature()))

    def test_native_rpc_exports_the_existing_contract_without_exposing_its_worker(self):
        import re
        from test_fii import ROOT
        source = (ROOT / 'fii/files/usr/share/fii/rpc.uc').read_text(encoding='utf-8')
        exports = re.search(r'const methods = \[(.*?)\];', source, re.S).group(1)
        self.assertEqual(set(re.findall(r"'([^']+)'", exports)), set(cli.READ_METHODS + cli.WRITE_METHODS))
        acl = json.loads((ROOT / 'luci-app-fii/root/usr/share/rpcd/acl.d/luci-app-fii.json').read_text())['luci-app-fii']
        for permission in ('read', 'write'):
            self.assertNotIn('luci.fii.worker', acl[permission]['ubus'])

    def test_node_cache_is_by_content_and_does_not_leak_mutations(self):
        first = render.extract(YAML)
        first[0]['name'] = 'Mutated'
        with patch.object(render, 'extract_uncached', side_effect=AssertionError('reparsed unchanged source')):
            self.assertEqual(render.extract(YAML)[0]['name'], 'HK')
        changed = YAML.replace('HK', 'New name')
        self.assertEqual(render.extract(changed)[0]['name'], 'New name')
        for f in rooted('/tmp/fii/node-cache-v1').glob('*.json'):
            f.write_text('{broken', encoding='utf-8')
        self.assertEqual(render.extract(YAML)[0]['name'], 'HK')

    def test_node_cache_evicts_old_entries_to_keep_its_count_bounded(self):
        directory = rooted('/tmp/fii/node-cache-v1')
        directory.mkdir(parents=True)
        for i in range(32):
            (directory / ('%064x.json' % i)).write_text('[]', encoding='utf-8')
        self.assertEqual(len(render.extract(YAML)), 2)
        self.assertEqual(len(list(directory.glob('*.json'))), 32)

    def test_compact_cache_failure_does_not_break_reads_or_cache_a_raced_snapshot(self):
        with patch.object(cli, 'write_json', side_effect=OSError('full tmpfs')):
            self.assertEqual(cli.dispatch('get', {'compact': True})['data'], cli.view_data(Store().read()))
        original_read = Store.read
        def replace_after_read(store):
            data = original_read(store)
            newer = copy.deepcopy(data)
            newer['sources'][0]['name'] = 'Replaced during read'
            write_json(store.path, newer)
            return data
        with patch.object(Store, 'read', replace_after_read):
            cli.dispatch('get', {'compact': True})
        self.assertEqual(cli.dispatch('get', {'compact': True})['data']['sources'][0]['name'], 'Replaced during read')

    def test_metadata_saves_skip_jobs_but_changed_content_keeps_async_validation(self):
        for change in ({'settings': {}}, {'kind': 'outputs', 'id': 'p1', 'fields': {'name': 'New'}},
                       {'kind': 'overrides', 'order': []}, {'kind': 'sources', 'remove': 's1'}):
            self.assertFalse(cli.background('save', {'patch': change}))
        for change in ({'kind': 'sources', 'id': 's1', 'fields': {'cachedYaml': YAML}},
                       {'kind': 'overrides', 'id': 'js', 'fields': {'type': 'JS'}}):
            self.assertTrue(cli.background('save', {'patch': change}))
        self.assertTrue(cli.background('apply', {}))

    def test_store_snapshot_detects_replacement_during_validation(self):
        store = Store()
        before = store.read()
        concurrent = copy.deepcopy(before)
        concurrent['settings']['userAgent'] = 'concurrent'
        Store().save(concurrent, before['revision'])
        with self.assertRaisesRegex(ValueError, '其他操作'):
            store.save(before, before['revision'])
        self.assertEqual(Store().read()['settings']['userAgent'], 'concurrent')

    def test_job_wait_returns_completion_and_rejects_unbounded_waits(self):
        ident = 'a' * 32
        write_json(cli.job_path(ident), {'done': False, 'method': 'preview', 'createdAt': int(time.time())})
        done = {'done': True, 'ok': True, 'result': {'value': 'completed'}}
        with patch.object(cli.time, 'sleep', side_effect=lambda _: write_json(cli.job_path(ident), done)):
            self.assertEqual(cli.dispatch('job', {'id': ident, 'waitMs': 600}), done)
        for value in (-1, 1001, True, '600'):
            with self.assertRaisesRegex(ValueError, '等待时间'):
                cli.dispatch('job', {'id': ident, 'waitMs': value})

    def test_compact_metadata_requests_preserve_omission_and_full_item_reads(self):
        summary = cli.dispatch('get', {'compact': True})['data']
        with patch.object(Store, 'read', side_effect=AssertionError('reread unchanged state')):
            source = cli.dispatch('get', {'kind': 'sources', 'id': 's1', 'metadataOnly': True, 'sync': True})
            self.assertTrue(source['item']['contentOmitted'])
            self.assertEqual(source['data'], summary)
            output = cli.dispatch('get', {'kind': 'outputs', 'id': 'p1'})
            self.assertEqual(output['item'], summary['outputs'][0])
        self.assertEqual(cli.dispatch('get', {'kind': 'sources', 'id': 's1'})['item']['cachedYaml'], YAML)

    def test_batch_keeps_fresh_context_result_isolation_and_failure_name(self):
        config = {'trace': []}
        overrides = [
            {'name': 'First', 'body': 'var leak=1; function main(c){Object.prototype.leaked=1;c.trace.push("first");return c;}'},
            {'name': 'Second', 'body': 'function main(c){if(typeof leak!=="undefined"||c.leaked)throw Error("leak");c.trace.push("second");return c;}'}]
        self.assertEqual(javascript.execute_many(overrides, config), {'trace': ['first', 'second']})
        self.assertEqual(config, {'trace': []})
        overrides[1]['body'] = 'function main(c){throw Error("test failure");}'
        with self.assertRaisesRegex(ValueError, 'Second.*test failure'):
            javascript.execute_many(overrides, config)

    def test_batch_preserves_yaml_interleaving_and_applied_order(self):
        data = sample()
        data['overrides'] = [
            {'id': 'a', 'name': 'A', 'type': 'JS', 'body': 'function main(c){c.trace=["a"];return c;}'},
            {'id': 'b', 'name': 'B', 'type': 'JS', 'body': 'function main(c){c.trace.push("b");return c;}'},
            {'id': 'y', 'name': 'YAML', 'type': 'YAML', 'body': 'trace+: [yaml]'},
            {'id': 'c', 'name': 'C', 'type': 'JS', 'body': 'function main(c){c.trace.push("c");return c;}'}]
        data['outputs'][0]['overrideIds'] = ['a', 'b', 'y', 'c']
        before = copy.deepcopy(data)
        result = render.render(data, 'p1')
        self.assertEqual(render.load_map(result['yaml'])['trace'], ['a', 'b', 'yaml', 'c'])
        self.assertEqual([item['id'] for item in result['appliedOverrides']], ['a', 'b', 'y', 'c'])
        self.assertEqual(data, before)
