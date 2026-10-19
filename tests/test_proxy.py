import os
import unittest
from unittest.mock import patch

from test_fii import sample, TestDirectory
from fii.store import rooted
from fii import model, runtime
from fii.proxy import ports, mark, modes


class ProxyOptionsTests(unittest.TestCase):
    def test_unavailable_tun_and_missing_cgroup_fail_before_activation(self):
        with TestDirectory() as tmp, patch.dict(os.environ, {"FII_ROOT": tmp}):
            s = model.defaults()["settings"]
            s.update(tcpMode="tun")
            with self.assertRaisesRegex(ValueError, "TUN"):
                runtime.check_capabilities(s)
            s.update(tcpMode="tproxy", routerProxy=True, routerRules=[{"enabled":True,"proxy":False,"dns":False,"cgroups":["services/missing"]}])
            with self.assertRaisesRegex(ValueError, "CGroup"):
                runtime.check_capabilities(s)

    def test_cleanup_preserves_unrelated_interface_with_same_name(self):
        import json
        def command(args, **kw):
            return json.dumps([{"ifalias":"unrelated", "linkinfo":{"info_kind":"dummy"}}]) if "-j" in args else ""
        with TestDirectory() as tmp, patch.dict(os.environ, {"FII_ROOT": tmp}), patch.object(runtime, "run", side_effect=command) as calls:
            runtime.firewall_down_unlocked()
            self.assertFalse(any(c.args[0] == ["/sbin/ip", "link", "del", "dev", "fii-dummy"] for c in calls.call_args_list))
            self.assertTrue(any("0xf102/0xffff" in c.args[0] for c in calls.call_args_list))

    def test_legacy_modes_and_explicit_mixed_modes(self):
        s = model.defaults()["settings"]
        self.assertEqual(modes(s), {"tcp": "tproxy", "udp": "tproxy"})
        s.update(tcpMode="redirect", udpMode="tun")
        self.assertEqual(modes(s), {"tcp": "redirect", "udp": "tun"})
        s["proxyEnabled"] = False
        self.assertEqual(modes(s), {"tcp": "off", "udp": "off"})
        self.assertEqual(runtime.nftables(s), "")

    def test_port_ranges_merge_overlap_and_reject_unsafe_input(self):
        self.assertEqual(ports("443 80-90,85-100 101"), ["80-101", "443"])
        for value in ("", "80; flush ruleset", "65536", "90-80", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ports(value)

    def test_mark_masks_are_checked_without_shell_parsing(self):
        self.assertEqual(mark("0x100/0xff00"), ("0x00000100", "0x0000ff00"))
        for value in ("0x1/0x0", "0x100/0xff", "0x100000000", "1; delete table"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mark(value)

    def test_ordered_acl_separates_dns_from_proxy_and_matches_groups(self):
        s = model.defaults()["settings"]
        s.update(routerProxy=True, routerRules=[
            {"enabled": True, "proxy": False, "dns": True, "groups": ["453"]},
            {"enabled": True, "proxy": True, "dns": False, "users": ["0"], "cgroups": ["services/dnsmasq"]},
            {"enabled": True, "proxy": False, "dns": False}])
        model.validate({**model.defaults(), "settings": s})
        nft = runtime.nftables(s)
        self.assertIn("meta skgid { 453 } goto router_rule_0_traffic", nft)
        self.assertIn('socket cgroupv2 level 2 "services/dnsmasq"', nft)
        self.assertIn("chain router_rule_0_traffic {\n    return", nft)
        self.assertIn("chain router_rule_0_dns {\n    goto dns_hijack", nft)
        self.assertIn("chain router_rule_1_dns {\n    return", nft)
        acl = nft.split("chain router_acl_traffic")[1].split("\n  }")[0]
        self.assertLess(acl.index("rule_0"), acl.index("rule_1"))
        self.assertLess(acl.index("rule_1"), acl.index("rule_2"))

    def test_ipv4_dns_can_be_enabled_without_ipv4_proxy(self):
        s = model.defaults()["settings"]
        s.update(ipv4Proxy=False, ipv6=True, dnsIpv4Hijack=True, dnsIpv6Hijack=False, dnsFamilyIndependent=True)
        nft = runtime.nftables(s)
        self.assertIn("meta nfproto != ipv6 return", nft)
        self.assertIn("meta nfproto { ipv4 } meta l4proto", nft)

    def test_tun_overlay_and_marks_do_not_enable_global_auto_route(self):
        from fii.render import load_map
        s = model.defaults()["settings"]
        s.update(tcpMode="redirect", udpMode="tun", tunStack="system", tunMtu=1400)
        cfg = load_map(runtime.overlay("tun: {auto-route: true, auto-redirect: true}", s))
        self.assertTrue(cfg["tun"]["enable"])
        self.assertFalse(cfg["tun"]["auto-route"])
        self.assertFalse(cfg["tun"]["auto-redirect"])
        self.assertEqual(cfg["tun"]["stack"], "system")
        self.assertEqual(cfg["tun"]["mtu"], 1400)
        nft = runtime.nftables(s)
        self.assertIn("0x0000f102 counter accept", nft)
        self.assertIn("redirect to :7893", nft)
        self.assertNotIn("tproxy to", nft)

    def test_china_sets_dscp_marks_and_fake_ip_exemptions(self):
        s = model.defaults()["settings"]
        s.update(bypassChina4=True, bypassChina6=True, bypassDscp=["4"], bypassMarks=["0x100/0xff00"], tcpPorts="80 443", fakeIpPing=True)
        nft = runtime.nftables(s)
        self.assertIn("1.0.1.0/24", nft)
        self.assertIn("ip daddr @china4", nft)
        self.assertIn("ip6 daddr @china6", nft)
        self.assertIn("ip dscp { 4 } ip daddr != 198.18.0.0/16 return", nft)
        self.assertIn("meta mark & 0x0000ff00 == 0x00000100 return", nft)
        self.assertIn("th dport { 80, 443 }", nft)
        self.assertIn("icmp type echo-request", nft)

    def test_invalid_advanced_settings_cannot_reach_rules(self):
        rule = {"enabled": True, "proxy": True, "dns": True}
        for key, value in (("udpMode", "redirect"), ("bypassDscp", ["64"]), ("tunTimeout", 0),
                           ("reservedIpv4", ["::/0"]), ("tunMtu", 1279),
                           ("routerRules", [{**rule, "cgroups": ["../x"]}]),
                           ("routerRules", [{**rule, "users": ["root"]}]),
                           ("lanRules", [{**rule, "macs": ["ff; flush ruleset"]}])):
            data = sample()
            data["settings"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                model.validate(data)


if __name__ == "__main__":
    unittest.main()
