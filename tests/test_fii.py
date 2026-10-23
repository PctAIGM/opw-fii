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
            self.assertEqual(network.choices(), {"devices": [], "hosts": [], "users": [], "groups": [], "cgroups": [], "cgroupV2": False, "wanDns": []})

    def test_wan_dns_reads_netifd_upstream_file(self):
        from fii import network
        with TestDirectory() as tmp, patch.dict(os.environ, {"FII_ROOT": tmp}):
            upstream = Path(tmp) / "tmp/resolv.conf.d/resolv.conf.auto"
            upstream.parent.mkdir(parents=True)
            upstream.write_text("# Interface wan\nnameserver 10.0.2.3\nnameserver ::1\nnameserver 127.0.0.1\n"
                                "nameserver not-an-ip\nnameserver 10.0.2.3\nnameserver fe80::1\n"
                                "nameserver 2001:db8::53\n", encoding="utf-8")
            self.assertEqual(network.wan_dns(), ["10.0.2.3", "2001:db8::53"])
            with patch.object(network, "run", return_value=""):
                self.assertEqual(network.choices()["wanDns"], ["10.0.2.3", "2001:db8::53"])

    def test_wan_dns_follow_merges_upstream_into_nameserver(self):
        from fii import network
        data = sample()
        settings = data["settings"]
        base = list(settings["dnsNameservers"])
        with patch.object(network, "wan_dns", return_value=["10.0.2.3", "10.0.2.4"]):
            # Off by default: the list stays exactly as configured.
            self.assertEqual(render.load_map(render.render(data, "p1")["yaml"])["dns"]["nameserver"], base)
            settings["dnsUseWanDns"] = True
            self.assertEqual(render.load_map(render.render(data, "p1")["yaml"])["dns"]["nameserver"],
                             base + ["10.0.2.3", "10.0.2.4"])
            settings["dnsWanDnsPrefer"] = True
            self.assertEqual(render.load_map(render.render(data, "p1")["yaml"])["dns"]["nameserver"],
                             ["10.0.2.3", "10.0.2.4"] + base)
            # Upstream entries already present are not merged twice.
            settings["dnsNameservers"] = ["223.5.5.5"]
            with patch.object(network, "wan_dns", return_value=["223.5.5.5", "10.0.2.3"]):
                self.assertEqual(render.load_map(render.render(data, "p1")["yaml"])["dns"]["nameserver"],
                                 ["10.0.2.3", "223.5.5.5"])
            # Overrides run after the merge: an explicit nameserver — including a
            # full dns! block replacement — keeps full control of the list.
            settings["dnsNameservers"] = base
            data["overrides"] = [{"id": "o1", "name": "DNS 接管", "enabled": True, "global": False,
                                  "type": "YAML", "body": "dns: {nameserver: [1.1.1.1]}"}]
            data["outputs"][0]["overrideIds"] = ["o1"]
            self.assertEqual(render.load_map(render.render(data, "p1")["yaml"])["dns"]["nameserver"], ["1.1.1.1"])
            data["overrides"][0]["body"] = "dns!: {nameserver: [9.9.9.9]}"
            self.assertEqual(render.load_map(render.render(data, "p1")["yaml"])["dns"]["nameserver"], ["9.9.9.9"])
        with patch.object(network, "wan_dns", return_value=[]):
            result = render.render(sample(), "p1")
            self.assertEqual(render.load_map(result["yaml"])["dns"]["nameserver"], base)
            follow = sample()
            follow["settings"]["dnsUseWanDns"] = True
            result = render.render(follow, "p1")
            self.assertTrue(any("运营商" in warning for warning in result["warnings"]))

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
                           ("localDns", "127.0.0.1:1053"), ("interfaces", ["lo"]),
                           ("controllerAddress", "192.168.1.1:9090"), ("controllerAddress", "router.lan"),
                           ("controllerAddress", 9090), ("coreMode", "bogus"), ("logLevel", "trace"),
                           ("findProcessMode", "auto"), ("keepAliveInterval", 0), ("keepAliveIdle", 3601),
                           ("geoUpdateInterval", 0), ("unifiedDelay", 1), ("etagSupport", "on"),
                           ("geoxUrls", {"unknown": "https://example.com/x"}), ("geoxUrls", {"geoip": "ftp://example.com/x"}),
                           ("geoxUrls", {"geoip": "not a url"}), ("geoxUrls", ["https://example.com/x"]),
                           ("externalUi", "/etc/ui"), ("externalUi", "a b"), ("externalUi", "../ui"),
                           ("externalUiName", "a/b"), ("externalUiUrl", "https://example.com/x"),
                           ("externalUiName", "xd"),
                           ("snifferHttpPorts", [0]), ("snifferTlsPorts", [70000]), ("snifferQuicPorts", ["443"]),
                           ("snifferHttpPorts", "80"), ("snifferSkipDomain", [123]), ("snifferSkipDomain", [""]),
                           ("coreTunStack", "bogus"), ("coreTunStack", "mips"), ("coreTunMtu", 1279),
                           ("coreTunMtu", 9001), ("coreTunMtu", "1500"), ("tunGsoMaxSize", 0),
                           ("tunGsoMaxSize", 1048577),
                           ("dnsEnhancedMode", "bogus"), ("dnsCacheAlgorithm", "fifo"),
                           ("dnsFakeIpFilterMode", "gray"), ("dnsFakeIpRange", "10.0.0.300/24"),
                           ("dnsFakeIpRange", "fc00::/18"), ("dnsFakeIpRange6", "198.18.0.1/16"),
                           ("dnsNameservers", []), ("dnsNameservers", "223.5.5.5"),
                           ("dnsNameservers", ["https://a.example.com/dns-query", "a b"]),
                           ("dnsDefaultNameservers", ["dns.example.com"]),
                           ("dnsDefaultNameservers", [223]),
                           ("dnsProxyServerNameservers", [" a"]), ("dnsDirectNameservers", [""]),
                           ("dnsFallback", [[]]), ("dnsFakeIpFilter", ["+.ok", "bad domain"]),
                           ("dnsFakeIpFilter", "80"), ("dnsNameserverPolicy", {"+.lan": []}),
                           ("dnsNameserverPolicy", {"a b": ["1.1.1.1"]}),
                           ("dnsNameserverPolicy", {"+.lan": "1.1.1.1"}),
                           ("dnsProxyServerNameserverPolicy", {"+.lan": [1]}),
                           ("dnsProxyServerNameserverPolicy", ["1.1.1.1"]),
                           ("dnsUseWanDns", "on"), ("dnsWanDnsPrefer", 1),
                           ("dnsHosts", ["1.1.1.1"]), ("dnsHosts", {"a b": ["1.1.1.1"]}),
                           ("dnsHosts", {"+.lan": "1.1.1.1"}), ("dnsHosts", {"+.lan": ["not-an-ip"]}),
                           ("dnsHosts", {"+.lan": [223]})]:
            with self.subTest(key=key, value=value):
                data = sample()
                data["settings"][key] = value
                with self.assertRaises(ValueError):
                    model.validate(data)
        for key in ("snifferEnable", "snifferForceDnsMapping", "snifferParsePureIp", "snifferOverrideDestination",
                    "tunGso", "tunEndpointIndependentNat", "dnsEnable", "dnsIpv6", "dnsRespectRules",
                    "dnsPreferH3", "dnsUseSystemHosts", "dnsUseWanDns", "dnsWanDnsPrefer", "dnsUseHosts"):
            with self.subTest(key=key):
                data = sample()
                data["settings"][key] = "on"
                with self.assertRaises(ValueError):
                    model.validate(data)

    def test_controller_listen_address_follows_settings(self):
        for address, expected in [("127.0.0.1", "127.0.0.1:9090"), ("0.0.0.0", "0.0.0.0:9090"),
                                  ("192.168.1.1", "192.168.1.1:9090"), ("::", "[::]:9090"),
                                  ("fd00::1", "[fd00::1]:9090")]:
            with self.subTest(address=address):
                settings = dict(model.defaults()["settings"], controllerAddress=address)
                config = render.load_map(runtime.overlay("", settings))
                self.assertEqual(config["external-controller"], expected)
                self.assertEqual(runtime.controller_host(settings),
                                 "[fd00::1]" if address == "fd00::1" else
                                 ("127.0.0.1" if address in ("0.0.0.0", "::") else address))
        legacy = dict(model.defaults()["settings"])
        legacy.pop("controllerAddress")
        data = sample()
        data["settings"].pop("controllerAddress", None)
        model.validate(data)
        self.assertEqual(data["settings"]["controllerAddress"], "127.0.0.1")

    def test_runtime_owns_integration_fields_after_overrides(self):
        settings = model.defaults()["settings"]
        config = render.load_map(runtime.overlay("tun: {enable: true}\nlisteners: []\nrouting-mark: 1\nexternal-controller: 0.0.0.0:8888\n", settings))
        self.assertFalse(config["tun"]["enable"])
        self.assertEqual(config["routing-mark"], 0xF100)
        self.assertEqual(config["listeners"][0]["listen"], "127.0.0.1")
        self.assertEqual(config["dns"]["proxy-server-nameserver"], config["dns"]["nameserver"])
        self.assertEqual(config["dns"]["nameserver-policy"]["+.lan"], ["127.0.0.1:53"])
        # An explicit override endpoint wins; absent fields fall back to defaults.
        self.assertEqual(config["external-controller"], "0.0.0.0:8888")
        config = render.load_map(runtime.overlay("", settings))
        self.assertEqual(config["external-controller"], "127.0.0.1:9090")
        self.assertEqual(config["tproxy-port"], settings["tproxyPort"])

    def test_general_defaults_seed_runtime_and_overrides_win(self):
        settings = model.defaults()["settings"]
        config = render.load_map(runtime.overlay("", settings))
        self.assertEqual(config["mode"], "rule")
        self.assertEqual(config["log-level"], "info")
        self.assertEqual(config["find-process-mode"], "off")
        self.assertFalse(config["ipv6"])
        self.assertEqual(config["keep-alive-interval"], 15)
        self.assertEqual(config["keep-alive-idle"], 15)
        self.assertFalse(config["unified-delay"])
        self.assertFalse(config["tcp-concurrent"])
        self.assertFalse(config["geodata-mode"])
        self.assertTrue(config["geo-auto-update"])
        self.assertEqual(config["geo-update-interval"], 24)
        self.assertNotIn("geox-url", config)
        self.assertTrue(config["etag-support"])
        tuned = dict(settings, coreMode="direct", logLevel="debug", findProcessMode="strict", keepAliveInterval=30,
                     keepAliveIdle=120, unifiedDelay=True, tcpConcurrent=True, geodataMode=True,
                     coreIpv6=True, geoAutoUpdate=False, geoUpdateInterval=48, etagSupport=False,
                     geoxUrls={"geoip": "https://example.com/geoip.dat"})
        config = render.load_map(runtime.overlay("", tuned))
        self.assertEqual(config["mode"], "direct")
        self.assertEqual(config["log-level"], "debug")
        self.assertEqual(config["find-process-mode"], "strict")
        self.assertTrue(config["ipv6"])
        self.assertEqual(config["keep-alive-interval"], 30)
        self.assertEqual(config["keep-alive-idle"], 120)
        self.assertTrue(config["unified-delay"])
        self.assertTrue(config["tcp-concurrent"])
        self.assertTrue(config["geodata-mode"])
        self.assertFalse(config["geo-auto-update"])
        self.assertEqual(config["geo-update-interval"], 48)
        # Only user-supplied kinds are written; the core fills the rest itself.
        self.assertEqual(config["geox-url"], {"geoip": "https://example.com/geoip.dat"})
        self.assertFalse(config["etag-support"])
        self.assertNotIn("external-ui", config)
        self.assertNotIn("external-ui-name", config)
        self.assertNotIn("external-ui-url", config)
        dashboard = dict(tuned, externalUi="ui", externalUiName="metacubexd",
                         externalUiUrl="https://example.com/dashboard.zip")
        config = render.load_map(runtime.overlay("", dashboard))
        self.assertEqual(config["external-ui"], "ui")
        self.assertEqual(config["external-ui-name"], "metacubexd")
        self.assertEqual(config["external-ui-url"], "https://example.com/dashboard.zip")
        # Explicit overrides keep their values; the settings only fill absent fields.
        config = render.load_map(runtime.overlay("mode: global\nlog-level: warning\nkeep-alive-interval: 45\nipv6: true\ngeox-url: {mmdb: 'https://o.example.com/mmdb'}\nexternal-ui: panel\n", tuned))
        self.assertEqual(config["mode"], "global")
        self.assertEqual(config["log-level"], "warning")
        self.assertEqual(config["keep-alive-interval"], 45)
        self.assertEqual(config["find-process-mode"], "strict")
        self.assertTrue(config["ipv6"])
        self.assertEqual(config["geox-url"], {"mmdb": "https://o.example.com/mmdb"})
        # An override dashboard directory is no longer stripped by the runtime overlay.
        self.assertEqual(config["external-ui"], "panel")
        self.assertNotIn("external-ui-name", config)

    def test_general_defaults_backfill_on_old_state(self):
        data = sample()
        new_keys = ("coreMode", "logLevel", "findProcessMode", "coreIpv6", "unifiedDelay", "tcpConcurrent",
                    "keepAliveInterval", "keepAliveIdle", "geodataMode", "geoAutoUpdate",
                    "geoUpdateInterval", "geoxUrls", "etagSupport",
                    "snifferEnable", "snifferForceDnsMapping", "snifferParsePureIp",
                    "snifferOverrideDestination", "snifferHttpPorts", "snifferTlsPorts",
                    "snifferQuicPorts", "snifferSkipDomain",
                    "coreTunStack", "coreTunMtu", "tunGso", "tunGsoMaxSize", "tunEndpointIndependentNat",
                    "externalUi", "externalUiName", "externalUiUrl")
        for key in new_keys:
            data["settings"].pop(key, None)
        model.validate(data)
        expected = model.defaults()["settings"]
        for key in new_keys:
            self.assertEqual(data["settings"][key], expected[key], key)
        # Blank GEO addresses are dropped instead of reaching the running config.
        data["settings"]["geoxUrls"] = {"geoip": "  ", "geosite": "https://example.com/geosite.dat"}
        model.validate(data)
        self.assertEqual(data["settings"]["geoxUrls"], {"geosite": "https://example.com/geosite.dat"})

    def test_sniffer_defaults_seed_runtime_and_partial_overrides_win(self):
        settings = model.defaults()["settings"]
        config = render.load_map(runtime.overlay("", settings))
        self.assertEqual(config["sniffer"], {
            "enable": True, "force-dns-mapping": True, "parse-pure-ip": True, "override-destination": False,
            "sniff": {"HTTP": {"ports": [80, 8080, 8880]}, "TLS": {"ports": [443, 8443]}, "QUIC": {"ports": [443, 8443]}},
            "skip-domain": ["+.lan", "+.local", "+.home.arpa", "Mijia Cloud"]})
        tuned = dict(settings, snifferEnable=False, snifferOverrideDestination=True, snifferHttpPorts=[8080],
                     snifferQuicPorts=[], snifferSkipDomain=["+.lan", "Mijia Cloud"])
        config = render.load_map(runtime.overlay("", tuned))
        self.assertEqual(config["sniffer"]["enable"], False)
        self.assertEqual(config["sniffer"]["override-destination"], True)
        self.assertEqual(config["sniffer"]["sniff"]["HTTP"], {"ports": [8080]})
        self.assertNotIn("QUIC", config["sniffer"]["sniff"])
        self.assertEqual(config["sniffer"]["skip-domain"], ["+.lan", "Mijia Cloud"])
        # A partial override keeps its own fields; the rest still get FII defaults.
        config = render.load_map(runtime.overlay("sniffer: {enable: false, sniff: {TLS: {ports: [8443]}}}\n", settings))
        self.assertEqual(config["sniffer"]["enable"], False)
        self.assertEqual(config["sniffer"]["force-dns-mapping"], True)
        self.assertEqual(config["sniffer"]["sniff"]["TLS"], {"ports": [8443]})
        self.assertEqual(config["sniffer"]["sniff"]["HTTP"], {"ports": [80, 8080, 8880]})
        self.assertEqual(config["sniffer"]["skip-domain"], ["+.lan", "+.local", "+.home.arpa", "Mijia Cloud"])

    def test_tun_defaults_seed_runtime_and_overrides_win(self):
        settings = model.defaults()["settings"]
        config = render.load_map(runtime.overlay("", settings))
        self.assertEqual(config["tun"]["stack"], "mixed")
        self.assertEqual(config["tun"]["mtu"], 1500)
        self.assertFalse(config["tun"]["gso"])
        self.assertEqual(config["tun"]["gso-max-size"], 65536)
        self.assertFalse(config["tun"]["endpoint-independent-nat"])
        # Router-owned fields stay forced regardless of settings or overrides.
        self.assertFalse(config["tun"]["enable"])
        self.assertEqual(config["tun"]["device"], "fii-tun")
        self.assertEqual(config["tun"]["dns-hijack"], [])
        tuned = dict(settings, coreTunStack="gvisor", coreTunMtu=9000, tunGso=True,
                     tunGsoMaxSize=32768, tunEndpointIndependentNat=True)
        config = render.load_map(runtime.overlay("", tuned))
        self.assertEqual(config["tun"]["stack"], "gvisor")
        self.assertEqual(config["tun"]["mtu"], 9000)
        self.assertTrue(config["tun"]["gso"])
        self.assertEqual(config["tun"]["gso-max-size"], 32768)
        self.assertTrue(config["tun"]["endpoint-independent-nat"])
        # Explicit overrides win per field; the remaining fields get FII defaults.
        config = render.load_map(runtime.overlay("tun: {stack: system, mtu: 1420}\n", settings))
        self.assertEqual(config["tun"]["stack"], "system")
        self.assertEqual(config["tun"]["mtu"], 1420)
        self.assertFalse(config["tun"]["gso"])
        self.assertEqual(config["tun"]["gso-max-size"], 65536)
        # Router-owned fields ignore override attempts.
        config = render.load_map(
            runtime.overlay("tun: {enable: true, device: evil, auto-route: true, dns-hijack: ['any:53']}\n", settings))
        self.assertFalse(config["tun"]["enable"])
        self.assertEqual(config["tun"]["device"], "fii-tun")
        self.assertFalse(config["tun"]["auto-route"])
        self.assertEqual(config["tun"]["dns-hijack"], [])

    def test_dns_defaults_seed_runtime_and_overrides_win(self):
        settings = model.defaults()["settings"]
        config = render.load_map(runtime.overlay("", settings))["dns"]
        self.assertTrue(config["enable"])
        self.assertEqual(config["enhanced-mode"], "redir-host")
        self.assertFalse(config["ipv6"])
        self.assertEqual(config["cache-algorithm"], "lru")
        self.assertFalse(config["respect-rules"])
        self.assertFalse(config["prefer-h3"])
        self.assertTrue(config["use-system-hosts"])
        self.assertFalse(config["use-hosts"])
        self.assertEqual(config["fake-ip-range"], "198.18.0.1/16")
        self.assertEqual(config["fake-ip-range6"], "fc00::/18")
        self.assertEqual(config["fake-ip-filter"], ["+.lan", "+.local"])
        self.assertEqual(config["fake-ip-filter-mode"], "blacklist")
        self.assertEqual(config["nameserver"], settings["dnsNameservers"])
        self.assertEqual(config["default-nameserver"], settings["dnsDefaultNameservers"])
        self.assertEqual(config["nameserver-policy"], {"+.lan": ["127.0.0.1:53"], "+.local": ["127.0.0.1:53"]})
        # proxy-server-nameserver mirrors nameserver; optional lists stay absent.
        self.assertEqual(config["proxy-server-nameserver"], config["nameserver"])
        self.assertNotIn("direct-nameserver", config)
        self.assertNotIn("fallback", config)
        self.assertNotIn("proxy-server-nameserver-policy", config)
        self.assertEqual(config["listen"], "0.0.0.0:1053")
        tuned = dict(settings, dnsEnable=True, dnsEnhancedMode="fake-ip", dnsIpv6=True, dnsCacheAlgorithm="arc",
                     dnsRespectRules=True, dnsPreferH3=True, dnsUseSystemHosts=False,
                     dnsFakeIpRange="28.0.0.1/8", dnsFakeIpRange6="fd00::/8",
                     dnsFakeIpFilter=["+.example.com"], dnsFakeIpFilterMode="whitelist",
                     dnsNameservers=["https://dns.example.com/dns-query"], dnsDefaultNameservers=["9.9.9.9"],
                     dnsProxyServerNameservers=["223.5.5.5"], dnsDirectNameservers=["119.29.29.29"],
                     dnsFallback=["8.8.8.8"],
                     dnsNameserverPolicy={"+.cn": ["https://doh.pub/dns-query"]},
                     dnsProxyServerNameserverPolicy={"geosite:cn": ["223.5.5.5"]},
                     dnsUseHosts=True, dnsHosts={"router.lan": ["192.168.1.1"], "*.corp": ["10.0.0.2", "fd00::2"]})
        model.validate({**model.defaults(), "settings": tuned})
        config = render.load_map(runtime.overlay("", tuned))["dns"]
        self.assertEqual(config["enhanced-mode"], "fake-ip")
        self.assertTrue(config["use-hosts"])
        self.assertTrue(config["ipv6"])
        self.assertEqual(config["cache-algorithm"], "arc")
        self.assertTrue(config["respect-rules"])
        self.assertTrue(config["prefer-h3"])
        self.assertFalse(config["use-system-hosts"])
        self.assertEqual(config["fake-ip-range"], "28.0.0.1/8")
        self.assertEqual(config["fake-ip-range6"], "fd00::/8")
        self.assertEqual(config["fake-ip-filter"], ["+.example.com"])
        self.assertEqual(config["fake-ip-filter-mode"], "whitelist")
        self.assertEqual(config["nameserver"], ["https://dns.example.com/dns-query"])
        self.assertEqual(config["default-nameserver"], ["9.9.9.9"])
        self.assertEqual(config["proxy-server-nameserver"], ["223.5.5.5"])
        self.assertEqual(config["direct-nameserver"], ["119.29.29.29"])
        self.assertEqual(config["fallback"], ["8.8.8.8"])
        self.assertEqual(config["nameserver-policy"], {"+.cn": ["https://doh.pub/dns-query"]})
        self.assertEqual(config["proxy-server-nameserver-policy"], {"geosite:cn": ["223.5.5.5"]})
        # The hosts table lands at the document root and backs dns.use-hosts.
        hosts = render.load_map(runtime.overlay("", tuned))
        self.assertEqual(hosts["hosts"], {"router.lan": ["192.168.1.1"], "*.corp": ["10.0.0.2", "fd00::2"]})
        self.assertNotIn("hosts", render.load_map(runtime.overlay("", settings)))
        # An explicit override keeps full control of the hosts table.
        overridden = render.load_map(runtime.overlay("hosts: {pin.example.com: [1.1.1.1]}\n", tuned))
        self.assertEqual(overridden["hosts"], {"pin.example.com": ["1.1.1.1"]})
        self.assertTrue(overridden["dns"]["use-hosts"])
        # Explicit override fields win per field; default-valued fields follow settings.
        body = ("dns: {fake-ip-filter: ['+.override.com'],\n"
                "  nameserver-policy: {'+.corp': ['127.0.0.1:5353']}, proxy-server-nameserver: [1.2.3.4],\n"
                "  fallback: [8.8.4.4]}\n")
        config = render.load_map(runtime.overlay(body, tuned))["dns"]
        self.assertEqual(config["fake-ip-filter"], ["+.override.com"])
        # Policy entries merge with the built-in local entries; a whole-map
        # replacement only happens while the policy is still the built-in one.
        self.assertEqual(config["nameserver-policy"], {"+.corp": ["127.0.0.1:5353"],
                                                       "+.lan": ["127.0.0.1:53"], "+.local": ["127.0.0.1:53"]})
        self.assertEqual(config["proxy-server-nameserver"], ["1.2.3.4"])
        self.assertEqual(config["fallback"], ["8.8.4.4"])
        # Fields left at the built-in default adopt the settings value instead.
        self.assertEqual(config["nameserver"], ["https://dns.example.com/dns-query"])
        self.assertEqual(config["default-nameserver"], ["9.9.9.9"])
        # An override equal to the built-in default is indistinguishable from absent.
        config = render.load_map(runtime.overlay("dns: {enhanced-mode: redir-host}\n", tuned))["dns"]
        self.assertEqual(config["enhanced-mode"], "fake-ip")
        # A non-default explicit override still wins.
        config = render.load_map(runtime.overlay("dns: {enhanced-mode: fake-ip}\n", settings))["dns"]
        self.assertEqual(config["enhanced-mode"], "fake-ip")
        # Settings patches accept the new DNS keys; legacy names stay rejected.
        patched = cli.apply_patch(model.defaults(), {"settings": {"dnsEnhancedMode": "redir-host"}})
        self.assertEqual(patched["settings"]["dnsEnhancedMode"], "redir-host")
        with self.assertRaises(ValueError):
            cli.apply_patch(model.defaults(), {"settings": {"dnsMode": "redir-host"}})

    def test_controller_secret_has_no_minimum_length(self):
        for value in ("", "short", "1234"):
            with self.subTest(value=value):
                data = sample()
                data["settings"]["secret"] = value
                model.validate(data)
        settings = model.defaults()["settings"]
        self.assertEqual(runtime.reconcile(settings, "secret: ''\n")["secret"], "")

    def test_effective_values_follow_explicit_overrides(self):
        settings = model.defaults()["settings"]
        body = ("external-controller: 192.168.1.1:9443\nsecret: override-secret-123456\n"
                "tproxy-port: 12345\nredir-port: 12346\ndns: {enable: true, listen: \":5353\"}\n")
        effective = runtime.reconcile(settings, body)
        self.assertEqual(effective["controllerPort"], 9443)
        self.assertEqual(effective["controllerAddress"], "192.168.1.1")
        self.assertEqual(effective["secret"], "override-secret-123456")
        self.assertEqual(effective["tproxyPort"], 12345)
        self.assertEqual(effective["redirPort"], 12346)
        self.assertEqual(effective["dnsPort"], 5353)
        effective = runtime.reconcile(settings, "external-controller: '[::1]:9091'\n")
        self.assertEqual(effective["controllerAddress"], "::1")
        self.assertEqual(effective["controllerPort"], 9091)
        effective = runtime.reconcile(settings, "")
        for key in ("controllerPort", "controllerAddress", "secret", "tproxyPort", "redirPort", "dnsPort"):
            self.assertEqual(effective[key], settings[key])
        for body in ("tproxy-port: seven\n", "external-controller: no-port\n", "dns: {listen: ':bad'}\n"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                runtime.reconcile(settings, body)

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
