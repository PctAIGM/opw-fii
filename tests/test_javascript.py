import copy
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample
from fii import javascript, cli, render, runtime, fetch, model
from fii.store import Store, rooted, write_json


class JavaScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.env = patch.dict(os.environ, {'FII_ROOT':self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_flutter_main_contract_and_unicode_configuration(self):
        config={'proxies':[{'name':'香港🚀','server':'example.com'}],'rules':['MATCH,PROXY']}
        before=copy.deepcopy(config)
        result=javascript.execute('function main(config) { config.rules.unshift("DOMAIN,example.com,DIRECT"); config.names=config.proxies.map(p=>p.name); return config; }',config)
        self.assertEqual(result['rules'][0],'DOMAIN,example.com,DIRECT')
        self.assertEqual(result['names'],['香港🚀'])
        self.assertEqual(config,before)

    def test_global_first_selected_order_interleaving_and_deduplication(self):
        data=sample()
        data['overrides']=[
            {'id':'global','name':'Global','type':'JS','global':True,'body':'function main(c){c.trace=["global"];return c;}'},
            {'id':'yaml','name':'YAML','type':'YAML','body':'trace+: [yaml]'},
            {'id':'js','name':'JS','type':'JS','body':'function main(c){c.trace.push("js");return c;}'},
            {'id':'rules','name':'Rules','type':'RULES','body':'DOMAIN,example.com,DIRECT'}]
        data['outputs'][0]['overrideIds']=['yaml','global','js','rules']
        result=render.load_map(render.render(data,'p1')['yaml'])
        self.assertEqual(result['trace'],['global','yaml','js'])
        self.assertEqual(result['rules'][0],'DOMAIN,example.com,DIRECT')

    def test_fresh_context_for_each_override(self):
        config=javascript.execute('const shared=1; var leak=1; function main(c){c.first=shared;return c;}',{})
        config=javascript.execute('const shared=2; function main(c){if(typeof leak!=="undefined")throw Error("leak");c.second=shared;return c;}',config)
        self.assertEqual(config,{'first':1,'second':2})

    def test_validation_checks_syntax_and_entry_without_running_main(self):
        javascript.validate('function main(c){throw Error("not run during validation");}')
        for script in ('function main( {','const x=1;'):
            with self.subTest(script=script), self.assertRaises(ValueError):
                javascript.validate(script)

    def test_invalid_returns_and_promise_rejected(self):
        for value in ('1','[]','null','undefined','Promise.resolve({})'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,'对象|同步'):
                javascript.execute('function main(c){return '+value+';}',{})

    def test_console_does_not_corrupt_json_and_host_apis_are_absent(self):
        result=javascript.execute('function main(c){console.log("hello");c.apis=[typeof require,typeof process,typeof fetch,typeof setTimeout];return c;}',{})
        self.assertEqual(result['apis'],['undefined']*4)
        with self.assertRaises(ValueError):
            javascript.execute('function main(c){return this.constructor.constructor("return process")();}',{})

    def test_top_level_and_main_infinite_loops_terminate(self):
        for script in ('while(true){};function main(c){return c;}','function main(c){while(true){}}'):
            with self.subTest(script=script), self.assertRaisesRegex(ValueError,'超时'):
                javascript.execute(script,{})

    def test_circular_and_oversized_results_rejected(self):
        for script in ('function main(c){c.self=c;return c;}','function main(c){return {body:"x".repeat(9*1024*1024)};}'):
            with self.subTest(script=script), self.assertRaises(ValueError):
                javascript.execute(script,{})

    def test_missing_runtime_and_parent_timeout_have_actionable_errors(self):
        with patch.object(javascript.shutil,'which',return_value=None), self.assertRaisesRegex(ValueError,'opkg install node'):
            javascript.execute('function main(c){return c;}',{})
        with patch.object(javascript.subprocess,'run',side_effect=subprocess.TimeoutExpired('node',6)), self.assertRaisesRegex(ValueError,'6 秒'):
            javascript.execute('function main(c){return c;}',{})

    def test_remote_js_validation_failure_preserves_cached_script(self):
        item={'id':'js','name':'JS','type':'JS','body':'function main(c){return c;}','url':'https://example.com/js'}
        with patch.object(fetch,'download',return_value=('function main( {',{})):
            self.assertFalse(fetch.refresh(item,model.defaults()['settings'],'overrides'))
        self.assertEqual(item['body'],'function main(c){return c;}')
        self.assertTrue(item['lastError'])

    def test_failed_js_activation_preserves_active_snapshot(self):
        data=sample()
        data['overrides']=[{'id':'js','name':'Broken JS','type':'JS','global':True,'body':'function main(c){throw Error("boom");}'}]
        current=Store().read()
        Store().save(data,current['revision'])
        old={'outputId':'old','yaml':'old','settings':data['settings']}
        write_json(rooted('/etc/fii/active.json'),old)
        with patch.object(runtime,'core_test') as core_test, self.assertRaisesRegex(ValueError,'Broken JS.*boom'):
            runtime.activate('p1')
        self.assertEqual(runtime.active(),old)
        core_test.assert_not_called()

    def test_subconverter_js_backup_import_and_export_round_trip(self):
        script='function main(c){c["log-level"]="warning";return c;}'
        backup={'format':'subconverter-backup','version':1,'sources':[],
                'templates':[{'id':2,'name':'JS','type':'JS','yamlBody':script,'global':True}], 'outputProfiles':[]}
        original=Store().read()
        cli.dispatch('import',{'data':backup,'revision':original['revision']})
        result=cli.dispatch('export')['data']
        self.assertEqual(result['overrides'][0]['type'],'JS')
        self.assertEqual(result['overrides'][0]['body'],script)
        self.assertEqual(result['settings'],original['settings'])
