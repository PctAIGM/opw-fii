"""Generate FII's independent nft table with ordered, terminal ACL decisions."""
import ipaddress
import json
from pathlib import Path

from .model import check, defaults
from .proxy import modes, ports, mark, rules

def dns_families(s):
    return (["ipv4"] if s["dnsIpv4Hijack"] else []) + (["ipv6"] if s["dnsIpv6Hijack"] and (s["dnsFamilyIndependent"] or s["ipv6"]) else [])


def generate(settings, config=None):
    s = {**defaults()["settings"], **settings}
    if s["mode"] == "core" or not s["proxyEnabled"]:
        return ""
    from .mihomo import dns_options
    output_dns = dns_options(config)
    fake4, fake6 = output_dns['fake4'], output_dns['fake6']
    protocols = modes(s)
    families = (["ipv4"] if s["ipv4Proxy"] else []) + (["ipv6"] if s["ipv6"] else [])
    dns = dns_families(s)
    chunks = []
    def chain(name, lines, hook=None):
        lines = ([hook] if hook else []) + lines
        chunks.append("  chain %s {\n    %s\n  }" % (name, ";\n    ".join(lines) + (";" if lines else "")))
    def nftset(name, values, datatype):
        chunks.append("  set %s {\n    type %s; flags interval; auto-merge;\n    elements = { %s };\n  }" % (name, datatype, ", ".join(values)))
    for version in (4, 6):
        if s["bypassChina" + str(version)]:
            path = Path(__file__).resolve().parents[2] / "share/fii" / ("china-ipv%d.txt" % version)
            check(path.is_file(), "大陆 IP 数据文件缺失，请重新安装 FII")
            networks = [str(ipaddress.ip_network(line)) for line in path.read_text().splitlines() if line and not line.startswith("#")]
            check(bool(networks), "大陆 IP 数据为空")
            nftset("china%d" % version, networks, "ipv4_addr" if version == 4 else "ipv6_addr")
    common = ["meta mark & 0x0000ffff == 0x0000f100 return", "ct direction reply return", "fib daddr type local return"]
    if not families:
        common.append("return")
    elif len(families) == 1:
        common.insert(0, "meta nfproto != %s return" % families[0])
    for version in (4, 6):
        expression, fake = ("ip", fake4) if version == 4 else ("ip6", fake6)
        reserved = s["reservedIpv%d" % version] + [x for x in s["bypass"] if ipaddress.ip_network(x, strict=False).version == version]
        reserved = list(ipaddress.collapse_addresses(ipaddress.ip_network(x, strict=False) for x in reserved))
        if reserved:
            common.append("%s daddr { %s } %s daddr != %s return" % (expression, ", ".join(map(str, reserved)), expression, fake))
        if s["bypassChina%d" % version]:
            common.append("%s daddr @china%d %s daddr != %s return" % (expression, version, expression, fake))
        if s["bypassDscp"]:
            common.append("%s dscp { %s } %s daddr != %s return" % (expression, ", ".join(str(int(x)) for x in s["bypassDscp"]), expression, fake))
    for value in s["bypassMarks"]:
        tag, mask = mark(value)
        common.append("meta mark & %s == %s return" % (mask, tag))
    dns_family = "meta nfproto { %s } " % ", ".join(dns) if dns else None
    chain("dns_hijack", ([dns_family + "meta l4proto { tcp, udp } th dport 53 counter redirect to :%d" % s["dnsPort"]] if dns else []))

    def conditions(rule, scope):
        matches = []
        if scope == "lan":
            for value in rule.get("ips", []):
                network = ipaddress.ip_network(value, strict=False)
                matches.append(("ip saddr " if network.version == 4 else "ip6 saddr ") + str(network))
            matches += ["ether saddr " + value.lower() for value in rule.get("macs", [])]
        else:
            for key, expr in (("users", "meta skuid"), ("groups", "meta skgid")):
                if rule.get(key):
                    matches.append(expr + " { " + ", ".join(str(int(v)) for v in rule[key]) + " }")
            matches += ["socket cgroupv2 level %d %s" % (len(value.split("/")), json.dumps(value)) for value in rule.get("cgroups", [])]
        return matches or [""]

    def acl(scope, action):
        entries = []
        for index, rule in enumerate(rules(s, scope)):
            if not rule["enabled"]:
                continue
            dest = "%s_rule_%d_%s" % (scope, index, action)
            body = []
            enabled = rule["dns" if action == "dns" else "proxy"]
            if enabled:
                if action == "dns":
                    if (s["dnsHijack"] if scope == "lan" else s["routerDnsHijack"]):
                        body.append("goto dns_hijack")
                elif action == "ping":
                    body.append("goto fake_ping")
                else:
                    if rule["dns"] and dns_family and (s["dnsHijack"] if scope == "lan" else s["routerDnsHijack"]):
                        body.append(dns_family + "meta l4proto { tcp, udp } th dport 53 return")
                    body.append("goto %s_traffic" % scope)
            chain(dest, body or ["return"])
            for match in conditions(rule, scope):
                entries.append((match + " " if match else "") + "goto " + dest)
        chain(scope + "_acl_" + action, entries)

    def dispatch(scope, nat=False):
        lines = common[:]
        for proto, mode in protocols.items():
            if mode == "off" or (mode == "redirect") != nat:
                continue
            if nat:
                verdict = "counter redirect to :%d" % s["redirPort"]
            elif mode == "tun":
                verdict = "meta mark set meta mark & 0xffff0000 | 0x0000f102 counter accept"
            elif scope == "router":
                verdict = "meta mark set meta mark & 0xffff0000 | 0x0000f101 counter accept"
            else:
                verdict = "meta mark set meta mark & 0xffff0000 | 0x0000f101 tproxy to :%d counter accept" % s["tproxyPort"]
            values = ", ".join(ports(s[proto + "Ports"]))
            for version in (4, 6):
                expr, fake = ("ip", fake4) if version == 4 else ("ip6", fake6)
                family_verdict = verdict.replace("tproxy to", "tproxy " + expr + " to")
                lines.append("meta l4proto %s %s daddr %s %s" % (proto, expr, fake, family_verdict))
            lines.append("meta l4proto %s th dport { %s } %s" % (proto, values, verdict))
        return lines

    fake_ping = []
    if s["fakeIpPing"] and output_dns['enabled'] and output_dns['mode'] == 'fake-ip':
        if s["ipv4Proxy"]:
            fake_ping.append("icmp type echo-request ip daddr %s counter redirect" % fake4)
        if s["ipv6"]:
            fake_ping.append("icmpv6 type echo-request ip6 daddr %s counter redirect" % fake6)
    chain("fake_ping", fake_ping)
    for scope in ("lan", "router"):
        for action in ("traffic", "dns", "ping"):
            acl(scope, action)
        chain(scope + "_traffic", dispatch(scope))
        # REDIRECT uses the same ordered decisions, but a separate traffic target.
        # Reuse rule chains by generating a NAT variant of the traffic ACL.
        nat_rules = []
        for index, rule in enumerate(rules(s, scope)):
            if rule["enabled"]:
                for match in conditions(rule, scope):
                    nat_rules.append((match + " " if match else "") + ("goto %s_redirect" % scope if rule["proxy"] else "return"))
        chain(scope + "_acl_redirect", nat_rules)
        # DNS is processed before redirect; if DNS interception is disabled,
        # proxy-enabled rules may still proxy TCP/53 as ordinary TCP traffic.
        chain(scope + "_redirect", dispatch(scope, nat=True))
    bypass = ["meta mark & 0x0000ffff == 0x0000f100 return", 'iifname "fii-tun" return']
    prerouting = bypass[:]
    if s["routerProxy"] and "tproxy" in protocols.values():
        prerouting.append('iifname "lo" meta mark & 0x0000ffff == 0x0000f101 meta l4proto { tcp, udp } tproxy to :%d counter accept' % s["tproxyPort"])
    scope = ["iifname != { %s } return" % ", ".join(json.dumps(i) for i in s["interfaces"])] if s["lanProxy"] else ["return"]
    chain("prerouting", prerouting + scope + ["jump lan_acl_traffic"], "type filter hook prerouting priority -150; policy accept")
    chain("dstnat", bypass + scope + ["jump lan_acl_dns", "jump lan_acl_ping", "jump lan_acl_redirect"], "type nat hook prerouting priority -101; policy accept")
    if s["routerProxy"]:
        output_bypass = ["meta mark & 0x0000ffff == 0x0000f100 return", "ct direction reply return", "fib daddr type local return"]
        chain("output", output_bypass + ["jump router_acl_traffic"], "type route hook output priority -150; policy accept")
        chain("nat_output", output_bypass + ["jump router_acl_dns", "jump router_acl_ping", "jump router_acl_redirect"], "type nat hook output priority -101; policy accept")
    return "table inet fii {\n" + "\n".join(chunks) + "\n}\n"
