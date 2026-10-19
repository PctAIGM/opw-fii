"""Transparent-proxy options and ordered access-control rules."""
import ipaddress
import re

from .model import check

RESERVED4 = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
             "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4"]
RESERVED6 = ["::/128", "::1/128", "::ffff:0:0/96", "100::/64", "64:ff9b::/96", "2001::/32",
             "2001:10::/28", "2001:20::/28", "2001:db8::/32", "2002::/16", "fc00::/7", "fe80::/10", "ff00::/8"]
OPTIONS = {"proxyEnabled": True, "tcpMode": "inherit", "udpMode": "inherit", "ipv4Proxy": True,
           "dnsIpv4Hijack": True, "dnsFamilyIndependent": False, "fakeIpPing": False, "bypassChina4": False, "bypassChina6": False,
           "tcpPorts": "0-65535", "udpPorts": "0-65535", "bypassDscp": [], "bypassMarks": [],
           "reservedIpv4": RESERVED4, "reservedIpv6": RESERVED6, "lanRules": None, "routerRules": None,
           "tunStack": "mixed", "tunMtu": 1500, "tunTimeout": 30, "tunInterval": 1}
MAC = r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"


def modes(s):
    if s.get("mode") == "core" or not s.get("proxyEnabled", True):
        return {"tcp": "off", "udp": "off"}
    tcp = s.get("tcpMode", "inherit")
    udp = s.get("udpMode", "inherit")
    return {"tcp": s["mode"] if tcp == "inherit" else tcp,
            "udp": ("tproxy" if s.get("udpProxy", True) else "off") if udp == "inherit" else udp}


def ports(value):
    check(isinstance(value, str) and len(value) <= 2048, "代理端口范围无效")
    parts = value.replace(",", " ").split()
    check(1 <= len(parts) <= 128, "请设置代理端口范围")
    ranges = []
    for part in parts:
        check(bool(re.fullmatch(r"\d{1,5}(?:-\d{1,5})?", part)), "端口格式无效: " + part)
        ends = [int(x) for x in part.split("-")]
        low, high = ends[0], ends[-1]
        check(0 <= low <= high <= 65535, "端口范围必须在 0–65535 内")
        ranges.append((low, high))
    merged = []
    for low, high in sorted(ranges):
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return [str(low) if low == high else "%d-%d" % (low, high) for low, high in merged]


def mark(value):
    check(isinstance(value, str) and bool(re.fullmatch(r"(?:0x[0-9a-fA-F]{1,8}|[0-9]{1,10})(?:/(?:0x[0-9a-fA-F]{1,8}|[0-9]{1,10}))?", value)), "FWMark 格式无效")
    def number(v):
        return int(v, 16 if v.lower().startswith("0x") else 10)
    fields = value.split("/")
    tag, mask = number(fields[0]), number(fields[1]) if len(fields) == 2 else 0xffffffff
    check(0 <= tag <= 0xffffffff and 1 <= mask <= 0xffffffff and tag & mask == tag, "FWMark 值或掩码无效")
    return "0x%08x" % tag, "0x%08x" % mask


def rules(s, scope):
    explicit = s.get(scope + "Rules")
    if explicit is not None:
        return explicit
    allow = {"enabled": True, "proxy": True, "dns": True}
    if scope == "router":
        users = s.get("routerBypassUsers", [])
        return ([{**allow, "users": users, "proxy": False, "dns": False}] if users else []) + [allow]
    entries, mode = s.get("lanAccess", []), s.get("lanAccessMode", "all")
    if mode == "all":
        return [allow]
    rule = {**allow, "ips": [e for e in entries if not re.fullmatch(MAC, e)],
            "macs": [e for e in entries if re.fullmatch(MAC, e)], "proxy": mode == "include", "dns": mode == "include"}
    return ([rule] if entries else []) + ([allow] if mode == "exclude" else [])


def validate(s):
    for key in ("proxyEnabled", "ipv4Proxy", "dnsIpv4Hijack", "dnsFamilyIndependent", "fakeIpPing", "bypassChina4", "bypassChina6"):
        check(type(s[key]) is bool, key + "必须是布尔值")
    check(s["tcpMode"] in ("inherit", "off", "redirect", "tproxy", "tun"), "TCP 模式无效")
    check(s["udpMode"] in ("inherit", "off", "tproxy", "tun"), "UDP 模式无效")
    ports(s["tcpPorts"])
    ports(s["udpPorts"])
    check(isinstance(s["bypassDscp"], list) and len(s["bypassDscp"]) <= 64 and
          all(isinstance(x, str) and x.isdigit() and 0 <= int(x) <= 63 for x in s["bypassDscp"]), "DSCP 必须为 0–63")
    check(isinstance(s["bypassMarks"], list) and len(s["bypassMarks"]) <= 64, "FWMark 列表无效")
    for value in s["bypassMarks"]:
        mark(value)
    for key, version in (("reservedIpv4", 4), ("reservedIpv6", 6)):
        check(isinstance(s[key], list) and len(s[key]) <= 256, "保留地址列表无效")
        for value in s[key]:
            check(isinstance(value, str), "保留地址必须是文本")
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError:
                raise ValueError("保留地址无效: " + value) from None
            check(network.version == version, "保留地址 IP 类型错误")
    for scope in ("lan", "router"):
        entries = s[scope + "Rules"]
        if entries is None:
            continue
        check(isinstance(entries, list) and len(entries) <= 256, "访问控制规则无效或超过 256 项")
        allowed = ("ips", "macs") if scope == "lan" else ("users", "groups", "cgroups")
        for rule in entries:
            check(isinstance(rule, dict), "访问控制规则必须是对象")
            check(set(rule) <= {"name", "enabled", "proxy", "dns", *allowed}, "访问控制含不适用的条件")
            check(isinstance(rule.get("name", ""), str) and len(rule.get("name", "")) <= 128, "规则名称无效")
            for flag in ("enabled", "proxy", "dns"):
                check(type(rule.get(flag)) is bool, "访问控制开关无效")
            for key in allowed:
                values = rule.get(key, [])
                check(isinstance(values, list) and len(values) <= 64, "规则条件无效或超过 64 项")
                for value in values:
                    check(isinstance(value, str), "规则条件必须是文本")
                    if key == "ips":
                        try:
                            ipaddress.ip_network(value, strict=False)
                        except ValueError:
                            raise ValueError("设备地址无效: " + value) from None
                    elif key == "macs":
                        check(bool(re.fullmatch(MAC, value)), "MAC 地址无效: " + value)
                    elif key in ("users", "groups"):
                        check(bool(re.fullmatch(r"[0-9]{1,10}", value)) and int(value) <= 4294967294, "UID / GID 无效")
                    else:
                        check(len(value) <= 240 and all(re.fullmatch(r"[A-Za-z0-9_:@.-]{1,64}", p) and p not in (".", "..") for p in value.split("/")), "CGroup 路径无效")
    check(s["tunStack"] in ("mixed", "system", "gvisor"), "TUN 协议栈无效")
    for key, low, high in (("tunMtu", 1280, 9000), ("tunTimeout", 1, 60), ("tunInterval", 1, 10)):
        check(type(s[key]) is int and low <= s[key] <= high, key + "超出允许范围")
    check(s["tunInterval"] <= s["tunTimeout"], "TUN 检查间隔不能大于等待时间")
