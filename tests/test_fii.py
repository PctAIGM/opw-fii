import base64
import copy
import json
import os
import secrets
import shutil
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / "build/test-tmp"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class TestDirectory:
    """Avoid Python 3.13's Windows-only 0700 ACL excluding sandbox identity."""
    def __init__(self, dir=TEMP_ROOT):
        self.path = Path(dir) / ("test-" + secrets.token_hex(8))
        self.path.mkdir(mode=0o777)
        self.name = str(self.path)

    def __enter__(self):
        return self.name

    def __exit__(self, *args):
        self.cleanup()

    def cleanup(self):
        resolved = self.path.resolve()
        if resolved.parent != TEMP_ROOT.resolve():
            raise ValueError("Refusing cleanup outside test directory")
        shutil.rmtree(resolved)
sys.path.insert(0, str(ROOT / "fii/files/usr/lib"))

from fii import cli, fetch, model, render, runtime
from fii.store import Store, lock, rooted, write_json

YAML = """proxies:
  - {name: HK, type: ss, server: hk.example.com, port: 443, cipher: aes-128-gcm, password: test}
  - {name: JP, type: ss, server: jp.example.com, port: 443, cipher: aes-128-gcm, password: test}
"""


def sample():
    data = model.defaults()
    data["sources"] = [{"id": "s1", "name": "Airport", "url": "https://example.com/sub", "cachedYaml": YAML}]
    data["outputs"] = [{"id": "p1", "name": "Home", "sourceIds": ["s1"], "overrideIds": []}]
    return data


class RendererTests(unittest.TestCase):
    def test_sources_not_whole_remote_configuration_are_merged(self):
        data = sample()
        data["sources"][0]["cachedYaml"] += "rules: [MATCH,DIRECT]\nsecret: airport-secret\n"
        config = render.load_map(render.render(data, "p1")["yaml"])
        self.assertEqual([p["name"] for p in config["proxies"]], ["HK", "JP"])
        self.assertEqual(config["rules"][-1], "MATCH,PROXY")
        self.assertNotIn("secret", config)

    def test_filter_before_prefix(self):
        data = sample()
        data["sources"][0].update(prefix="A-", includeRegex="HK|JP", excludeRegex="JP")
        config = render.load_map(render.render(data, "p1")["yaml"])
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["A-HK"])

    def test_duplicate_suffixes_cannot_collide_with_original_names(self):
        data = sample()
        data["sources"][0]["cachedYaml"] = "proxies: [{name: HK}, {name: HK}, {name: 'HK (2)'}, {name: PROXY}, {name: DIRECT}]"
        names = [p["name"] for p in render.load_map(render.render(data, "p1")["yaml"])["proxies"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("PROXY", names)
        self.assertNotIn("DIRECT", names)

    def test_association_order_and_global_first_deduplicated(self):
        data = sample()
        data["sources"].append({"id": "s2", "name": "B", "url": "https://example.com", "cachedYaml": YAML, "prefix": "B-"})
        data["outputs"][0]["sourceIds"] = ["s2", "s1"]
        data["overrides"] = [
            {"id": "o1", "name": "Global", "global": True, "type": "YAML", "body": "rules+: ['DOMAIN,a.test,DIRECT']"},
            {"id": "o2", "name": "Second", "type": "YAML", "body": "log-level: warning"},
            {"id": "o3", "name": "Third", "type": "YAML", "body": "log-level: error"},
        ]
        data["outputs"][0]["overrideIds"] = ["o3", "o1", "o2"]
        config = render.load_map(render.render(data, "p1")["yaml"])
        self.assertEqual(config["proxies"][0]["name"], "B-HK")
        self.assertEqual(config["log-level"], "warning")
        self.assertEqual(config["rules"].count("DOMAIN,a.test,DIRECT"), 1)

    def test_yaml_merge_prepend_append_replace_nested(self):
        target = {"rules": ["old"], "dns": {"enable": True, "nameserver": ["old"]}}
        render.merge(target, {"+<rules>": ["first"], "rules+": ["last"], "dns": {"nameserver!": ["new"]}})
        self.assertEqual(target, {"rules": ["first", "old", "last"], "dns": {"enable": True, "nameserver": ["new"]}})
        with self.assertRaises(ValueError):
            render.merge({}, {"rules+": "bad"})

    def test_rules_override_precedes_match(self):
        data = sample()
        data["overrides"] = [{"id": "o1", "name": "Rules", "global": True, "type": "RULES", "body": "# comment\nDOMAIN,foo.test,DIRECT\n"}]
        self.assertEqual(render.load_map(render.render(data, "p1")["yaml"])["rules"][0], "DOMAIN,foo.test,DIRECT")

    def test_exhausted_filtered_without_disabling_and_recovers(self):
        data = sample()
        source = data["sources"][0]
        source.update(uploadBytes=40, downloadBytes=60, totalBytes=100)
        before = copy.deepcopy(data)
        self.assertEqual(render.render(data, "p1")["nodeCount"], 0)
        self.assertEqual(data, before)
        source["totalBytes"] = 200
        self.assertEqual(render.render(data, "p1")["nodeCount"], 2)

    def test_missing_zero_and_partial_quota(self):
        for usage in ({}, {"totalBytes": 0, "downloadBytes": 100}, {"totalBytes": 100}, {"downloadBytes": 100}):
            self.assertFalse(model.exhausted(usage))
        self.assertTrue(model.exhausted({"totalBytes": 100, "downloadBytes": 100}))

    def test_on_off_node_names_stay_strings(self):
        self.assertEqual(render.load_map("name: ON\nvalue: false"), {"name": "ON", "value": False})

    def test_unsafe_and_cyclic_yaml_rejected(self):
        for body in ("!!python/object/apply:os.system ['echo unsafe']", "a: &a [*a]", "[not, an, object]"):
            with self.assertRaises(ValueError):
                render.load_map(body)

    def test_invalid_regex_rejected_at_save(self):
        data = sample()
        data["sources"][0]["includeRegex"] = "["
        with self.assertRaises(ValueError):
            model.validate(data)

    def test_ss_base64_and_common_links(self):
        b64 = base64.b64encode(b"aes-128-gcm:pass").decode()
        links = ["ss://%s@server.example:443#Hong%%20Kong" % b64,
                 "vless://uuid@v.example:443?security=reality&pbk=key&sid=ff&sni=example.com&type=ws&path=%2Fx#VLESS",
                 "trojan://pass@t.example:443?sni=test.example#Trojan",
                 "hy2://pass@h.example:443?obfs=salamander&obfs-password=obfs#HY2"]
        nodes = render.extract("\n".join(links))
        self.assertEqual([n["type"] for n in nodes], ["ss", "vless", "trojan", "hysteria2"])
        self.assertEqual(nodes[0]["name"], "Hong Kong")
        self.assertEqual(nodes[1]["reality-opts"]["public-key"], "key")
        encoded = base64.b64encode("\n".join(links).encode()).decode()
        self.assertEqual(render.extract(encoded), nodes)

    def test_vmess_ws(self):
        vmess = {"ps": "VM", "add": "v.example", "port": "443", "id": "uuid", "aid": "0", "net": "ws", "tls": "tls", "host": "cdn.example", "path": "/ws"}
        node = render.extract("vmess://" + base64.b64encode(json.dumps(vmess).encode()).decode())[0]
        self.assertEqual(node["ws-opts"]["path"], "/ws")
        self.assertTrue(node["tls"])

    def test_unsupported_links_do_not_silently_disappear(self):
        for body in ("socks5://user:pass@server.example:443", "vless://uuid@v.example:443?type=xhttp", "proxies: bad"):
            with self.assertRaises(ValueError):
                render.extract(body)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TestDirectory()
        self.env = patch.dict(os.environ, {"FII_ROOT": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def save_sample(self):
        with lock():
            data = sample()
            return Store().save(data, 0)

    def test_revision_conflicts_prevent_lost_updates(self):
        data = self.save_sample()
        self.assertEqual(data["revision"], 1)
        with lock(), self.assertRaises(ValueError):
            Store().save(sample(), 0)
        self.assertEqual(Store().read(), data)

    def test_invalid_save_keeps_disk_unchanged(self):
        data = self.save_sample()
        invalid = copy.deepcopy(data)
        invalid["settings"]["mixedPort"] = invalid["settings"]["dnsPort"]
        with lock(), self.assertRaises(ValueError):
            Store().save(invalid, data["revision"])
        self.assertEqual(Store().read(), data)

    def test_cross_process_lock_timeout(self):
        with lock("held"), self.assertRaises(ValueError):
            with lock("held", timeout=0):
                pass

    def test_refresh_failure_keeps_cache_and_traffic(self):
        data = self.save_sample()
        with patch.object(fetch, "download", side_effect=ValueError("temporary failure")):
            self.assertFalse(cli.refresh_one("sources", "s1")["success"])
        source = Store().read()["sources"][0]
        self.assertEqual(source["cachedYaml"], YAML)
        self.assertEqual(source["lastError"], "temporary failure")

    def test_refresh_parses_quota_and_clears_missing_headers(self):
        self.save_sample()
        with patch.object(fetch, "download", return_value=(YAML, {"Subscription-Userinfo": "upload=10; download=20; total=30; expire=1234"})):
            self.assertTrue(cli.refresh_one("sources", "s1")["success"])
        source = Store().read()["sources"][0]
        self.assertTrue(model.exhausted(source))
        self.assertEqual(cli.dispatch("preview", {"id": "p1"})["nodeCount"], 0)
        with patch.object(fetch, "download", return_value=(YAML, {})):
            cli.refresh_one("sources", "s1")
        self.assertEqual(cli.dispatch("preview", {"id": "p1"})["nodeCount"], 2)

    def test_refresh_cannot_overwrite_concurrent_edit(self):
        self.save_sample()
        def download(*args):
            with lock():
                latest = Store().read()
                latest["sources"][0]["name"] = "Edited"
                Store().save(latest, latest["revision"])
            return YAML, {}
        with patch.object(fetch, "download", side_effect=download), self.assertRaises(ValueError):
            cli.refresh_one("sources", "s1")
        self.assertEqual(Store().read()["sources"][0]["name"], "Edited")

    def test_lock_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            cli.refresh_one("sources", "../../bad")
        with self.assertRaises(ValueError):
            cli.dispatch("job", {"id": "../../bad"})

    def test_job_result_and_failure_are_observable(self):
        self.save_sample()
        ident = "a" * 32
        with patch.object(cli, "dispatch", return_value={"message": "applied"}):
            cli.run_job("apply", ident, {"id": "p1"})
        self.assertEqual(cli.dispatch("job", {"id": ident})["result"]["message"], "applied")
        with patch.object(cli, "dispatch", side_effect=ValueError("failure")):
            cli.run_job("apply", ident, {})
        self.assertFalse(cli.dispatch("job", {"id": ident})["ok"])

    def test_rpc_background_job_cli_entrypoint(self):
        with patch.object(sys, "argv", ["fii", "job-run", "apply", "a" * 32, '{"id":"p1"}']), \
                patch.object(cli, "run_job") as run:
            cli.main()
        run.assert_called_once_with("apply", "a" * 32, {"id": "p1"})

    def test_apply_validates_before_persisting(self):
        self.save_sample()
        with patch.object(runtime, "core_test", side_effect=ValueError("invalid config")), self.assertRaises(ValueError):
            runtime.activate("p1")
        self.assertIsNone(runtime.active())

    def test_failed_apply_restores_previous_snapshot(self):
        self.save_sample()
        previous = {"outputId": "previous", "name": "Old", "yaml": "old", "settings": model.defaults()["settings"]}
        write_json(rooted("/etc/fii/active.json"), previous)
        with patch.object(runtime, "core_test"), patch.object(runtime, "run"), \
                patch.object(runtime, "service_active", return_value=True), \
                patch.object(runtime, "restart_and_wait", side_effect=[ValueError("failed"), None]) as restart:
            with self.assertRaisesRegex(ValueError, "恢复上一次"):
                runtime.activate("p1")
        self.assertEqual(runtime.active(), previous)
        self.assertEqual(restart.call_count, 2)

    def test_first_apply_failure_cleans_up(self):
        self.save_sample()
        with patch.object(runtime, "core_test"), patch.object(runtime, "run") as run, \
                patch.object(runtime, "service_active", return_value=True), \
                patch.object(runtime, "restart_and_wait", side_effect=ValueError("failed")):
            with self.assertRaises(ValueError):
                runtime.activate("p1")
        self.assertIsNone(runtime.active())
        run.assert_called_with([runtime.INIT, "stop"], required=False)

    def test_no_nodes_preserves_running_snapshot(self):
        data = self.save_sample()
        data["sources"][0].update(totalBytes=100, downloadBytes=100)
        Store().save(data, data["revision"])
        with patch.object(runtime, "core_test") as test, self.assertRaisesRegex(ValueError, "没有可用节点"):
            runtime.activate("p1")
        test.assert_not_called()

    def test_successful_apply_records_revision_and_boot_preference(self):
        data = self.save_sample()
        with patch.object(runtime, "core_test"), patch.object(runtime, "service_active", return_value=True), patch.object(runtime, "restart_and_wait"), patch.object(runtime, "run") as run:
            runtime.activate("p1")
        self.assertEqual(runtime.active()["revision"], data["revision"])
        run.assert_called_with([runtime.INIT, "disable"])

    def test_stopped_apply_updates_revision_without_starting_or_restarting(self):
        data = self.save_sample()
        previous = {'outputId':'old', 'name':'Old', 'yaml':'old', 'settings':data['settings'], 'revision':0}
        write_json(rooted('/etc/fii/active.json'), previous)
        with patch.object(runtime, 'core_test'), patch.object(runtime, 'service_active', return_value=False), \
                patch.object(runtime, 'restart_and_wait') as restart, patch.object(runtime, 'run') as run:
            result = runtime.activate('p1')
        self.assertFalse(result['restarted'])
        self.assertIn('保持停止', result['message'])
        self.assertEqual(runtime.active()['revision'], data['revision'])
        self.assertEqual(json.loads(rooted('/etc/fii/active.previous.json').read_text()), previous)
        restart.assert_not_called()
        self.assertEqual([call.args[0] for call in run.call_args_list], [[runtime.INIT, 'disable']])
        self.assertFalse(rooted('/tmp/fii/ready').exists())

    def test_first_apply_while_stopped_creates_snapshot_without_start(self):
        self.save_sample()
        with patch.object(runtime, 'core_test'), patch.object(runtime, 'service_active', return_value=False), \
                patch.object(runtime, 'restart_and_wait') as restart, patch.object(runtime, 'run'):
            result = runtime.activate('p1')
        self.assertIsNotNone(runtime.active())
        self.assertFalse(result['restarted'])
        restart.assert_not_called()

    def test_unknown_service_state_does_not_replace_snapshot(self):
        self.save_sample()
        previous = {'outputId':'old', 'yaml':'old'}
        write_json(rooted('/etc/fii/active.json'), previous)
        with patch.object(runtime, 'core_test'), patch.object(runtime, 'service_active', side_effect=ValueError('unknown')), \
                patch.object(runtime, 'restart_and_wait') as restart, self.assertRaisesRegex(ValueError, 'unknown'):
            runtime.activate('p1')
        self.assertEqual(runtime.active(), previous)
        restart.assert_not_called()

    def test_service_state_includes_starting_or_respawning_instances(self):
        for response, expected in [('{}', False), ('{"fii":{"instances":{}}}',False),
                ('{"fii":{"instances":{"mihomo":{"running":true}}}}', True),
                ('{"fii":{"instances":{"mihomo":{"running":false,"command":["/usr/libexec/fii/core-run"]}}}}',True)]:
            with self.subTest(response=response), patch.object(runtime,'run',return_value=response):
                self.assertEqual(runtime.service_active(), expected)
        for response in ('invalid', '[]', '{"fii":null}', '{"fii":{"instances":null}}', '{"fii":{"instances":{"mihomo":null}}}'):
            with self.subTest(response=response), patch.object(runtime,'run',return_value=response), \
                    self.assertRaisesRegex(ValueError, '无法确认 FII 服务状态'):
                runtime.service_active()

    def test_scheduler_honors_retry_backoff(self):
        data = self.save_sample()
        data["sources"][0].update(autoRefresh=True, lastAttemptAt=int(__import__('time').time()), lastRefreshAt=1)
        Store().save(data, data["revision"])
        with patch.object(cli, "refresh_one") as refresh:
            cli.scheduled()
        refresh.assert_not_called()


class BackupTests(unittest.TestCase):
    def test_url_errors_identify_all_items_and_reasons_without_addresses(self):
        raw = {"format": "subconverter-backup", "version": 1,
               "sources": [{"id": 7, "name": "缺少协议", "url": "example.com/private-token"},
                           {"id": 8, "name": "认证订阅", "url": "https://user:private-password@example.com/sub"},
                           {"id": 9, "name": "空订阅", "url": ""}],
               "templates": [{"id": 2, "name": "远程脚本", "type": "JS", "remoteUrl": "https:///private-token", "yamlBody": ""}],
               "outputProfiles": []}
        original = copy.deepcopy(raw)
        with self.assertRaises(ValueError) as caught:
            model.import_backup(raw, model.defaults()['settings'])
        message = str(caught.exception)
        for fragment in ('4 项地址错误', '订阅「缺少协议」（ID: 7）', 'http:// 或 https://',
                         '订阅「认证订阅」（ID: 8）', '内嵌用户名密码', '订阅「空订阅」（ID: 9）',
                         'URL 为空', '覆写「远程脚本」（ID: 2）', '缺少域名'):
            self.assertIn(fragment, message)
        for secret in ('private-token', 'private-password', 'example.com'):
            self.assertNotIn(secret, message)
        self.assertEqual(raw, original)

    def test_url_parser_errors_do_not_leak_addresses_and_credentials_are_detected(self):
        for address, reason in (
                ('https://[private-token/sub', '格式无效'),
                ('https://example.com:private-token/sub', '格式无效'),
                ('https://example.com:65536/sub', '格式无效'),
                ('https://:private-token@example.com/sub', '内嵌用户名密码'),
                ('https://example.com/private token', '空白'),
                ('https://example.com/\nprivate-token', '非法字符')):
            with self.subTest(address=address), self.assertRaises(ValueError) as caught:
                model.url(address)
            self.assertIn(reason, str(caught.exception))
            self.assertNotIn('private', str(caught.exception))
        model.url('https://[::1]:8080/sub?token=test')

    def test_import_trims_url_edges_and_retains_local_cached_sources(self):
        raw = {"format": "subconverter-backup", "version": 1,
               "sources": [{"id": 7, "name": "远程", "url": " \nhttps://example.com/sub?token=test\t ", "cachedYaml": YAML},
                           {"id": 8, "name": "本地", "url": "  ", "cachedYaml": YAML}],
               "templates": [{"id": 2, "name": "远程覆写", "type": "YAML", "remoteUrl": " https://example.com/rules ", "yamlBody": "rules+: []"}],
               "outputProfiles": []}
        data, _ = model.import_backup(raw, model.defaults()['settings'])
        self.assertEqual(data['sources'][0]['url'], 'https://example.com/sub?token=test')
        self.assertEqual(data['sources'][1]['url'], '')
        self.assertEqual(data['sources'][1]['cachedYaml'], YAML)
        self.assertEqual(data['overrides'][0]['url'], 'https://example.com/rules')

    def test_url_import_failure_keeps_saved_state_and_active_snapshot(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {'FII_ROOT': tmp}):
            original = Store().read()
            snapshot = {'outputId': 'old', 'yaml': 'old'}
            write_json(rooted('/etc/fii/active.json'), snapshot)
            raw = {'format': 'subconverter-backup', 'version': 1,
                   'sources': [{'id': 7, 'name': '坏地址', 'url': 'not-http'}], 'templates': [], 'outputProfiles': []}
            for method in ('import_preview', 'import'):
                with self.subTest(method=method), self.assertRaisesRegex(ValueError, '订阅「坏地址」'):
                    cli.dispatch(method, {'data': raw, 'revision': original['revision']})
                self.assertEqual(Store().read(), original)
                self.assertEqual(runtime.active(), snapshot)

    def test_subconverter_import_preserves_order_and_router_settings(self):
        raw = {"format": "subconverter-backup", "version": 1,
               "sources": [{"id": 7, "name": "Airport", "url": "https://example.com", "cachedYaml": YAML}],
               "templates": [{"id": 2, "name": "Rules", "type": "RULES", "yamlBody": "DOMAIN,x.test,DIRECT"}],
               "outputProfiles": [{"id": 9, "name": "Home", "sourceIds": "7,7,99", "overrideIds": "2"}]}
        settings = model.defaults()["settings"]
        data, warnings = model.import_backup(raw, settings)
        self.assertEqual(data["outputs"][0]["sourceIds"], ["7"])
        self.assertEqual(data["outputs"][0]["overrideIds"], ["2"])
        self.assertEqual(data["settings"], settings)
        self.assertTrue(warnings)
        self.assertEqual(render.render(data, "9")["nodeCount"], 2)

    def test_js_import_preserves_script_and_type(self):
        raw = {"format": "subconverter-backup", "version": 1, "sources": [], "outputProfiles": [],
               "templates": [{"id": 1, "name": "JS", "type": "JS", "yamlBody": "function main(c){return c}"}]}
        data, _ = model.import_backup(raw, model.defaults()["settings"])
        self.assertEqual(data['overrides'][0]['type'], 'JS')
        self.assertEqual(data['overrides'][0]['body'], 'function main(c){return c}')

    def test_fii_import_cannot_turn_on_autostart_or_change_credentials(self):
        original = model.defaults()["settings"]
        backup = sample()
        backup["settings"]["autostart"] = True
        data, _ = model.import_backup(backup, original)
        self.assertEqual(data["settings"], original)


class NetworkGenerationTests(unittest.TestCase):
    def test_network_choices_read_devices_networks_and_hosts(self):
        from fii import network
        with TestDirectory() as tmp, patch.dict(os.environ, {"FII_ROOT": tmp}):
            rooted("/tmp").mkdir(parents=True)
            rooted("/etc").mkdir(parents=True)
            rooted("/tmp/dhcp.leases").write_text("0 aa:bb:cc:dd:ee:ff 192.168.1.2 Laptop *\n")
            rooted("/etc/passwd").write_text("root:x:0:0::/:/bin/ash\n")
            responses = [json.dumps({"interface": [{"interface": "lan", "l3_device": "br-lan", "up": True}]}),
                         json.dumps([{"ifname": "lo", "flags": ["UP"]}, {"ifname": "br-lan", "flags": ["UP"]}])]
            with patch.object(network, "run", side_effect=responses):
                result = network.choices()
            self.assertEqual(result["devices"], [{"name": "br-lan", "networks": ["lan"], "up": True}])
            self.assertEqual(result["hosts"][0]["name"], "Laptop")
            self.assertEqual(result["users"], [{"id": "0", "name": "root"}])

    def test_network_choices_tolerate_unavailable_platform(self):
        from fii import network
        with TestDirectory() as tmp, patch.dict(os.environ, {"FII_ROOT": tmp}), patch.object(network, "run", return_value=""):
            self.assertEqual(network.choices(), {"devices": [], "hosts": [], "users": [], "groups": [], "cgroups": [], "cgroupV2": False})

    def test_old_settings_keep_scope_and_dns_on_read(self):
        data = sample()
        for key in ("lanProxy", "routerProxy", "udpProxy", "dnsIpv6Hijack"):
            del data["settings"][key]
        settings = model.validate(data)["settings"]
        self.assertTrue(settings["lanProxy"])
        self.assertFalse(settings["routerProxy"])
        self.assertNotIn("dnsMode", settings)
        self.assertNotIn("hook output", runtime.nftables(settings))

    def test_router_proxy_has_loop_prevention_and_loopback_tproxy(self):
        settings = model.defaults()["settings"]
        settings.update(routerProxy=True, routerBypassUsers=["453"], lanProxy=False, interfaces=[])
        model.validate({**model.defaults(), "settings": settings})
        nft = runtime.nftables(settings)
        self.assertIn("type route hook output", nft)
        self.assertIn('iifname "lo" meta mark & 0x0000ffff == 0x0000f101', nft)
        self.assertIn("meta skuid { 453 } goto router_rule_0_traffic", nft)
        self.assertIn("chain router_rule_0_traffic {\n    return", nft)
        output = nft.split("chain output {")[1]
        self.assertLess(output.index("0x0000f100 return"), output.index("jump router_acl_traffic"))

    def test_include_and_exclude_devices_apply_to_both_dns_and_traffic(self):
        settings = model.defaults()["settings"]
        settings.update(lanAccessMode="include", lanAccess=["192.168.1.2", "2001:db8::/64", "AA:BB:CC:DD:EE:FF"])
        nft = runtime.nftables(settings)
        self.assertIn("ip saddr 192.168.1.2/32 goto lan_rule_0_traffic", nft)
        self.assertIn("ip6 saddr 2001:db8::/64 goto lan_rule_0_dns", nft)
        self.assertIn("ether saddr aa:bb:cc:dd:ee:ff goto lan_rule_0_dns", nft)
        settings["lanAccessMode"] = "exclude"
        nft = runtime.nftables(settings)
        self.assertIn("chain lan_rule_0_traffic {\n    return", nft)
        self.assertIn("goto lan_traffic", nft)
        self.assertIn("goto dns_hijack", nft)

    def test_udp_disable_does_not_disable_dns_or_tcp(self):
        settings = model.defaults()["settings"]
        settings.update(udpProxy=False, routerProxy=True, mode="redirect")
        nft = runtime.nftables(settings)
        self.assertNotIn("tproxy to", nft)
        self.assertIn("redirect to :7893", nft)
        self.assertIn("th dport 53 counter redirect", nft)
        self.assertNotIn("meta mark set 0x0000f101", nft)

    def test_mihomo_dns_policy_is_independent_of_router_traffic_settings(self):
        settings = model.defaults()["settings"]
        settings.update(ipv6=True, dnsIpv6Hijack=False, routerProxy=True, routerDnsHijack=False, dnsMode="redir-host",
                        proxyNameservers=["1.1.1.1"], defaultNameservers=["8.8.8.8"], localDns="127.0.0.1:5353", fakeIpFilter=["+.example.com"])
        cfg = render.load_map(runtime.overlay("ipv6: false\ndns: {ipv6: true, enhanced-mode: redir-host, fake-ip-filter: ['+.example.com'], proxy-server-nameserver: [1.1.1.1], default-nameserver: [8.8.8.8], nameserver-policy: {'+.lan': ['127.0.0.1:5353']}}", settings))["dns"]
        self.assertEqual(cfg["enhanced-mode"], "redir-host")
        self.assertEqual(cfg["fake-ip-filter"], ["+.example.com"])
        self.assertEqual(cfg["proxy-server-nameserver"], ["1.1.1.1"])
        self.assertEqual(cfg["default-nameserver"], ["8.8.8.8"])
        self.assertEqual(cfg["nameserver-policy"]["+.lan"], ["127.0.0.1:5353"])
        nft = runtime.nftables(settings)
        self.assertIn("meta nfproto { ipv4 }", nft.split("chain dns_hijack")[1])
        self.assertNotIn("jump dns_hijack", nft.split("chain nat_output")[1])

    def test_router_options_reject_invalid_or_injectable_values(self):
        for key, value in [("routerProxy", 1), ("udpProxy", "true"), ("lanAccess", ['1.1.1.1; flush ruleset']),
                           ("routerBypassUsers", ["root"]), ("routerBypassUsers", ["4294967295"]),
                           ("dnsMode", "unknown"), ("defaultNameservers", ["dns.example.com"]),
                           ("localDns", "127.0.0.1:1053"), ("interfaces", ["lo"])]:
            with self.subTest(key=key, value=value):
                data = sample()
                data["settings"][key] = value
                with self.assertRaises(ValueError):
                    model.validate(data)

    def test_runtime_owns_integration_fields_after_overrides(self):
        settings = model.defaults()["settings"]
        config = render.load_map(runtime.overlay("tun: {enable: true}\nlisteners: []\nrouting-mark: 1\nexternal-controller: 0.0.0.0:8888\n", settings))
        self.assertFalse(config["tun"]["enable"])
        self.assertEqual(config["routing-mark"], 0xF100)
        self.assertEqual(config["external-controller"], "127.0.0.1:9090")
        self.assertEqual(config["listeners"][0]["listen"], "127.0.0.1")
        self.assertEqual(config["dns"]["proxy-server-nameserver"], config["dns"]["nameserver"])
        self.assertEqual(config["dns"]["nameserver-policy"]["+.lan"], ["127.0.0.1:53"])

    def test_firewall_only_intercepts_selected_lan(self):
        settings = model.defaults()["settings"]
        nft = runtime.nftables(settings)
        self.assertIn('iifname != { "br-lan" } return', nft)
        self.assertIn("tproxy to :7895", nft)
        self.assertIn("redirect to :1053", nft)
        self.assertIn("fib daddr type local return", nft)
        self.assertNotIn("hook output", nft)
        self.assertIn("ip daddr != 198.18.0.0/16 return", nft)
        self.assertNotIn("flush ruleset", nft)

    def test_ipv6_dns_and_redirect_mode(self):
        settings = model.defaults()["settings"]
        settings.update(ipv6=True, mode="redirect", interfaces=["br-lan", "br-guest"], bypass=["203.0.113.0/24", "2001:db8::/32"])
        nft = runtime.nftables(settings)
        self.assertIn("meta l4proto udp th dport { 0-65535 } meta mark set", nft)
        self.assertIn("redirect to :7893", nft)
        self.assertIn("2001:db8::/32", nft)
        self.assertNotIn("meta nfproto != ipv4 return", nft)
        self.assertEqual(render.load_map(runtime.overlay("", settings))["dns"]["listen"], "[::]:1053")

    def test_core_mode_has_no_firewall(self):
        settings = model.defaults()["settings"]
        settings["mode"] = "core"
        self.assertEqual(runtime.nftables(settings), "")

    def test_invalid_interface_cannot_enter_nft_source(self):
        data = sample()
        data["settings"]["interfaces"] = ['br-lan"; flush ruleset']
        with self.assertRaises(ValueError):
            model.validate(data)

    def test_firewall_reload_replaces_own_table_atomically(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {"FII_ROOT": tmp}):
            bundle = {"settings": model.defaults()["settings"], "yaml": ""}
            def command(args, **kw):
                return "table inet fii" if args == ["/usr/sbin/nft", "list", "tables"] else ""
            with patch.object(runtime, "active", return_value=bundle), patch.object(runtime, "ready", return_value=True), patch.object(runtime, "run", side_effect=command) as run:
                runtime.firewall_up()
            content = rooted("/tmp/fii/firewall.nft").read_text()
            self.assertTrue(content.startswith("delete table inet fii\n"))
            self.assertTrue(rooted("/tmp/fii/ready").exists())
            self.assertTrue(any(c.args[0][1:3] == ["-c", "-f"] for c in run.call_args_list))

    def test_firewall_failure_removes_interception(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {"FII_ROOT": tmp}):
            def command(args, **kw):
                if args[:2] == ["/usr/sbin/nft", "-f"]:
                    raise ValueError("nft failed")
                return ""
            with patch.object(runtime, "active", return_value={"settings": model.defaults()["settings"], "yaml": ""}), \
                    patch.object(runtime, "ready", return_value=True), patch.object(runtime, "run", side_effect=command) as run:
                with self.assertRaises(ValueError):
                    runtime.firewall_up()
            self.assertFalse(rooted("/tmp/fii/ready").exists())
            self.assertTrue(any(c.args[0] == ["/usr/sbin/nft", "delete", "table", "inet", "fii"] for c in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
