import copy
import json
import os
import unittest
from unittest.mock import patch

from test_fii import TestDirectory, sample
from fii import cli, model, render, runtime
from fii.mihomo import LEGACY_KEYS, dns_options
from fii.store import Store, write_json


class ConfigBoundaryTests(unittest.TestCase):
    def test_legacy_policy_migrates_visibly_once_without_writing_on_read(self):
        data = sample()
        legacy = {'dnsMode': 'redir-host', 'nameservers': ['1.1.1.1'], 'defaultNameservers': ['9.9.9.9'],
                  'proxyNameservers': [], 'fakeIpFilter': [], 'localDns': '127.0.0.1:5353',
                  'tunStack': 'system', 'tunMtu': 1400}
        data['settings'].update(legacy)
        data['overrides'] = [{'id':'custom', 'name':'Existing DNS', 'global':True, 'type':'YAML',
                              'body':'dns: {nameserver: [8.8.8.8]}'}]
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            store = Store(); write_json(store.path, data)
            before = store.path.read_bytes()
            normalized = store.read()
            self.assertEqual(store.path.read_bytes(), before)
            self.assertFalse(LEGACY_KEYS.intersection(normalized['settings']))
            self.assertEqual(normalized['overrides'][0]['origin'], 'legacy-mihomo-settings')
            migrated = render.load_map(normalized['overrides'][0]['body'])
            self.assertEqual(migrated['dns']['enhanced-mode'], 'redir-host')
            self.assertEqual(migrated['dns']['fake-ip-filter'], [])
            self.assertEqual(migrated['tun'], {'stack':'system','mtu':1400})
            self.assertEqual(migrated['dns']['proxy-server-nameserver'], ['1.1.1.1'])
            self.assertEqual(model.validate(copy.deepcopy(normalized)), normalized)
            result = render.load_map(render.render(normalized, 'p1')['yaml'])
            self.assertEqual(result['dns']['nameserver'], ['8.8.8.8'])
            saved = store.save(normalized, normalized['revision'])
            self.assertEqual(store.read(), saved)
            self.assertEqual(len(store.read()['overrides']), 2)

    def test_legacy_backup_keeps_mihomo_policy_but_preserves_target_router_settings(self):
        old = sample(); old['settings'].update(nameservers=['1.1.1.1'], dnsMode='redir-host')
        old['settings']['interfaces'] = ['br-old']
        target = model.defaults()['settings']; target['interfaces'] = ['br-target']
        imported, _ = model.import_backup(old, target)
        self.assertEqual(imported['settings']['interfaces'], ['br-target'])
        self.assertFalse(LEGACY_KEYS.intersection(imported['settings']))
        self.assertEqual(render.load_map(imported['overrides'][0]['body'])['dns']['nameserver'], ['1.1.1.1'])

    def test_router_edits_do_not_change_mihomo_policy_and_legacy_patch_is_rejected(self):
        data = sample()
        self.assertFalse(LEGACY_KEYS.intersection(data['settings']))
        body = 'ipv6: true\ndns: {ipv6: true, enhanced-mode: redir-host, fake-ip-range: 198.19.0.1/16, fake-ip-range6: fd00:1234::/32, nameserver: [1.1.1.1]}\ntun: {stack: system, mtu: 1400}\n'
        first = render.load_map(runtime.overlay(body, data['settings']))
        edited = cli.apply_patch(data, {'settings':{'ipv6':True, 'mixedPort':7898, 'dnsPort':1055}})
        second = render.load_map(runtime.overlay(body, edited['settings']))
        for key in ('ipv6','enhanced-mode','fake-ip-range','fake-ip-range6','nameserver'):
            self.assertEqual(first['dns'][key], second['dns'][key])
        self.assertEqual(first['ipv6'], second['ipv6'])
        self.assertEqual(first['tun']['stack'], second['tun']['stack'])
        self.assertEqual(first['tun']['mtu'], second['tun']['mtu'])
        self.assertNotEqual(first['dns']['listen'], second['dns']['listen'])
        for key in LEGACY_KEYS:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Mihomo 参数请在覆写中修改'):
                cli.apply_patch(data, {'settings':{key:'unused'}})

    def test_nft_uses_output_dns_mode_and_ranges_instead_of_router_policy(self):
        settings = model.defaults()['settings']; settings.update(fakeIpPing=True, ipv6=True)
        body = 'dns: {enhanced-mode: fake-ip, fake-ip-range: 198.19.0.1/16, fake-ip-range6: fd12:3456::/32}'
        nft = runtime.nftables(settings, body)
        self.assertIn('ip daddr 198.19.0.0/16', nft)
        self.assertIn('ip6 daddr fd12:3456::/32', nft)
        self.assertNotIn('198.18.0.0/16', nft)
        self.assertNotIn('fc00::/18', nft)
        self.assertIn('icmp type echo-request ip daddr 198.19.0.0/16', nft)
        redir = runtime.nftables(settings, body.replace('fake-ip,', 'redir-host,'))
        self.assertNotIn('icmp type echo-request', redir)
        for value in ('dns: {fake-ip-range: "1; delete table"}', 'dns: {fake-ip-range6: 192.0.2.0/24}', 'dns: {fake-ip-range: 0.0.0.0/0}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dns_options(value)

    def test_disabled_mihomo_dns_is_preserved_or_reports_router_hijack_conflict(self):
        settings = model.defaults()['settings']
        with self.assertRaisesRegex(ValueError, '关闭 DNS 接管'):
            runtime.overlay('dns: {enable: false}', settings)
        settings['dnsHijack'] = False
        cfg = render.load_map(runtime.overlay('dns: {enable: false}', settings))
        self.assertFalse(cfg['dns']['enable'])

    def test_old_metadata_cache_cannot_hide_the_migration(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            store=Store();data=sample();data['settings']['dnsMode']='redir-host';write_json(store.path,data)
            from fii.store import rooted
            write_json(rooted('/tmp/fii/state-view.json'), {'stamp':list(store.signature()),'data':{'stale':True}})
            result=cli.dispatch('get',{'compact':True})['data']
            self.assertNotIn('stale', result)
            self.assertEqual(result['overrides'][0]['origin'],'legacy-mihomo-settings')
            self.assertFalse(LEGACY_KEYS.intersection(result['settings']))

    def test_fake6_route_changes_and_mode_changes_use_the_output_snapshot(self):
        settings=model.defaults()['settings'];settings['ipv6']=True
        link={'ifalias':'fii-fake-ip','linkinfo':{'info_kind':'dummy'}}
        body='dns: {enhanced-mode: fake-ip, fake-ip-range6: fd12:3456::/32}'
        def command(args, **kwargs):
            if args==['/sbin/ip','-6','-j','route','show','dev','fii-dummy']:
                return json.dumps([{'dst':'fc00::/18'},{'dst':'fd12:3456::/32'}])
            return ''
        with TestDirectory() as tmp, patch.dict(os.environ,{'FII_ROOT':tmp}), \
                patch.object(runtime,'active',return_value={'settings':settings,'yaml':body}), \
                patch.object(runtime,'ready',return_value=True), patch.object(runtime,'fake6_link',return_value=link), \
                patch.object(runtime,'run',side_effect=command) as calls:
            runtime.firewall_up()
            commands=[call.args[0] for call in calls.call_args_list]
            self.assertIn(['/sbin/ip','-6','route','replace','fd12:3456::/32','dev','fii-dummy'],commands)
            self.assertIn(['/sbin/ip','-6','route','del','fc00::/18','dev','fii-dummy'],commands)
            self.assertNotIn(['/sbin/ip','-6','route','del','fd12:3456::/32','dev','fii-dummy'],commands)
        with TestDirectory() as tmp, patch.dict(os.environ,{'FII_ROOT':tmp}), \
                patch.object(runtime,'active',return_value={'settings':settings,'yaml':body.replace('fake-ip,','redir-host,')}), \
                patch.object(runtime,'ready',return_value=True), patch.object(runtime,'fake6_link',return_value=link), \
                patch.object(runtime,'run',return_value='') as calls:
            runtime.firewall_up()
            self.assertIn(['/sbin/ip','link','del','dev','fii-dummy'],[call.args[0] for call in calls.call_args_list])
