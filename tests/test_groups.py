import json
import os
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import patch

from test_fii import TestDirectory
from fii import cli, model, render, runtime
from fii.store import rooted, write_json


class ProxyGroupsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TestDirectory();self.env=patch.dict(os.environ,{'FII_ROOT':self.temp.name});self.env.start()
        self.config={'proxies':[{'name':'HK','type':'ss','server':'private.example','password':'secret-node-password'}],
            'proxy-groups':[{'name':'Manual','type':'select','proxies':['Auto','HK','DIRECT']},
                {'name':'Auto','type':'url-test','proxies':['HK']},
                {'name':'Provider','type':'fallback','use':['remote'],'proxies':None}]}
        write_json(rooted('/etc/fii/active.json'),{'settings':model.defaults()['settings'],'yaml':render.dump(self.config),'revision':7})

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_stopped_snapshot_has_readonly_groups_without_contacting_or_starting_core(self):
        with patch.object(runtime,'controller') as controller,patch.object(runtime,'run') as run:
            result=runtime.groups()
        controller.assert_not_called();run.assert_not_called()
        self.assertFalse(result['running']);self.assertEqual(result['source'],'snapshot')
        self.assertEqual([g['type'] for g in result['groups']],['Selector','URLTest','Fallback'])
        self.assertTrue(all(g['now']=='' and g['fixed'] is None for g in result['groups']))
        self.assertTrue(result['groups'][2]['dynamic'])
        self.assertEqual(result['groups'][2]['all'],[])
        self.assertEqual(result['nodes']['HK'],{'type':'ss'})
        text=json.dumps(result)
        self.assertNotIn('secret-node-password',text);self.assertNotIn('private.example',text)

    def test_live_groups_return_only_latest_delay_and_compact_node_metadata(self):
        rooted('/tmp/fii').mkdir(parents=True,exist_ok=True);rooted('/tmp/fii/ready').write_text('ready')
        reply={'proxies':{
            'GLOBAL':{'type':'Selector','all':['Manual']},
            'Manual':{'type':'Selector','all':['HK','DIRECT'],'now':'HK'},
            'Auto':{'type':'URLTest','all':['HK','Failed'],'now':'HK','fixed':'Failed'},
            'HK':{'type':'Shadowsocks','alive':True,'password':'secret','history':[{'time':'older','delay':500},{'time':'latest','delay':62}]},
            'DIRECT':{'type':'Direct','alive':True,'history':[]},
            'Failed':{'type':'VLESS','alive':False,'history':[{'time':'latest','delay':0}]},
            'Invalid':{'type':'VLESS','history':[{'delay':True}]}}}
        with patch.object(runtime,'controller',return_value=reply) as controller:
            result=runtime.groups()
        controller.assert_called_once_with('/proxies')
        self.assertTrue(result['running']);self.assertEqual(result['source'],'runtime')
        self.assertEqual([g['name'] for g in result['groups']],['Manual','Auto'])
        self.assertEqual(result['groups'][1]['fixed'],'Failed')
        self.assertEqual(result['groups'][1]['now'],'HK')
        self.assertEqual(result['nodes']['HK'],{'type':'Shadowsocks','alive':True,'delay':62,'testedAt':'latest'})
        self.assertIsNone(result['nodes']['Invalid']['delay'])
        self.assertFalse(result['nodes']['Failed']['alive'])
        self.assertNotIn('history',json.dumps(result));self.assertNotIn('secret',json.dumps(result))

    def test_stopped_selection_is_rejected_before_any_controller_write(self):
        for args in [('Manual','HK'),('Auto','HK'),('Auto',None,'unpin')]:
            with self.subTest(args=args),patch.object(runtime,'controller') as controller,self.assertRaisesRegex(ValueError,'内核已停止'):
                runtime.select_proxy(*args)
            controller.assert_not_called()

    def test_running_group_order_comes_from_active_yaml_not_json_or_custom_global(self):
        rooted('/tmp/fii').mkdir(parents=True,exist_ok=True);rooted('/tmp/fii/ready').write_text('ready')
        reply={'proxies':{
            'Extra':{'type':'Selector','all':['HK']},
            'Provider':{'type':'Fallback','all':['HK']},
            'Auto':{'type':'URLTest','all':['HK']},
            'Manual':{'type':'Selector','all':['Auto','HK','DIRECT']},
            'GLOBAL':{'type':'Selector','all':['Provider','Auto','Manual']}}}
        with patch.object(runtime,'controller',return_value=reply):
            self.assertEqual([g['name'] for g in runtime.groups()['groups']],['Manual','Auto','Provider','Extra'])
        with patch.object(runtime,'controller') as controller:
            self.assertEqual(cli.dispatch('groups',{'orderOnly':True}),{'order':['Manual','Auto','Provider']})
        controller.assert_not_called()

    def test_manual_selection_checks_membership_and_escapes_names(self):
        data={'running':True,'groups':[{'name':'Group / A','type':'Selector','all':['HK','DIRECT']},
                                     {'name':'Balance','type':'LoadBalance','all':['HK']},
                                     {'name':'Relay','type':'Relay','all':['HK']}]}
        with patch.object(runtime,'groups',return_value=data),patch.object(runtime,'controller') as controller:
            runtime.select_proxy('Group / A','HK')
            controller.assert_called_once_with('/proxies/Group%20%2F%20A',{'name':'HK'})
            controller.reset_mock()
            for group,name in [('Group / A','unknown'),('Balance','HK'),('Relay','HK'),('Missing','HK')]:
                with self.subTest(group=group),self.assertRaises(ValueError):runtime.select_proxy(group,name)
            controller.assert_not_called()

    def test_auto_groups_can_pin_the_current_node_or_another_member(self):
        for kind in ['URLTest','Fallback']:
            data={'running':True,'groups':[{'name':'Auto / A','type':kind,'now':'HK','fixed':'','all':['HK','DIRECT']}]}
            with self.subTest(kind=kind),patch.object(runtime,'groups',return_value=data),patch.object(runtime,'controller') as controller:
                result=runtime.select_proxy('Auto / A','HK')
                self.assertIn('已钉住',result['message'])
                controller.assert_called_once_with('/proxies/Auto%20%2F%20A',{'name':'HK'},**({'timeout':8} if kind=='Fallback' else {}))
                controller.reset_mock()
                runtime.select_proxy('Auto / A','DIRECT')
                self.assertEqual(controller.call_args.args[1],{'name':'DIRECT'})
                with self.assertRaises(ValueError):runtime.select_proxy('Auto / A','outside')

    def test_unpin_is_explicit_and_restricted_to_auto_groups(self):
        for kind in ['URLTest','Fallback']:
            data={'running':True,'groups':[{'name':'Auto / A','type':kind,'fixed':'deleted-node','all':[]}]}
            with self.subTest(kind=kind),patch.object(runtime,'groups',return_value=data),patch.object(runtime,'controller') as controller:
                result=runtime.select_proxy('Auto / A',action='unpin')
                self.assertIn('恢复自动选择',result['message'])
                controller.assert_called_once_with('/proxies/Auto%20%2F%20A',method='DELETE')
        for kind in ['Selector','LoadBalance','Relay']:
            with self.subTest(kind=kind),patch.object(runtime,'groups',return_value={'running':True,'groups':[{'name':'Group','type':kind,'all':['HK']}]}),patch.object(runtime,'controller') as controller:
                with self.assertRaises(ValueError):runtime.select_proxy('Group',action='unpin')
                controller.assert_not_called()

    def test_malformed_actions_never_contact_the_controller(self):
        for args in [(None,'HK'),('Manual',None),('Auto','HK','unpin'),('Auto',None,'unexpected')]:
            with self.subTest(args=args),patch.object(runtime,'groups') as groups,patch.object(runtime,'controller') as controller:
                with self.assertRaises(ValueError):runtime.select_proxy(*args)
                groups.assert_not_called();controller.assert_not_called()

    def test_controller_pin_and_unpin_use_real_http_verbs_without_starting_a_core(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_PUT(self):
                calls.append((self.command,self.path,self.rfile.read(int(self.headers.get('Content-Length','0')))))
                self.send_response(204);self.end_headers()
            def do_DELETE(self):
                calls.append((self.command,self.path,self.rfile.read(int(self.headers.get('Content-Length','0')))))
                self.send_response(204);self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        bundle={'settings':{'controllerPort':server.server_port,'secret':'test-only'}}
        try:
            with patch.object(runtime,'active',return_value=bundle),patch.object(runtime,'ready',return_value=True),patch.object(runtime,'run') as run:
                runtime.controller('/proxies/Auto%20%2F%20A',{'name':'HK'})
                runtime.controller('/proxies/Auto%20%2F%20A',method='DELETE')
                run.assert_not_called()
            self.assertEqual(calls,[('PUT','/proxies/Auto%20%2F%20A',b'{"name": "HK"}'),('DELETE','/proxies/Auto%20%2F%20A',b'')])
        finally:server.shutdown();server.server_close();thread.join()

    def test_missing_snapshot_is_an_empty_state_not_a_service_request(self):
        rooted('/etc/fii/active.json').unlink()
        with patch.object(runtime,'controller') as controller:
            result=runtime.groups()
        self.assertEqual(result['groups'],[]);self.assertEqual(result['source'],'none')
        controller.assert_not_called()

    def test_live_controller_errors_are_not_presented_as_current_snapshot_selections(self):
        rooted('/tmp/fii').mkdir(parents=True,exist_ok=True);rooted('/tmp/fii/ready').write_text('ready')
        with patch.object(runtime,'controller',side_effect=ValueError('connection lost')), \
                patch.object(runtime,'load_map') as load,self.assertRaisesRegex(ValueError,'connection lost'):
            runtime.groups()
        load.assert_not_called()
