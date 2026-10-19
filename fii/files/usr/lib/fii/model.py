"""Validated persistent model; mirrors SubConverter's source / override / output flow."""
import copy
import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit

MAX_TEXT = 4 * 1024 * 1024
MAX_DOCUMENT = 16 * 1024 * 1024


def defaults():
    from .proxy import OPTIONS
    return {
        "format": "fii-backup", "version": 1, "revision": 0,
        "sources": [], "overrides": [], "outputs": [],
        "settings": {
            **copy.deepcopy(OPTIONS),
            "autostart": False, "autoApply": False, "mode": "tproxy",
            "interfaces": ["br-lan"], "ipv6": False, "dnsHijack": True,
            "mixedPort": 7890, "redirPort": 7893, "tproxyPort": 7895,
            "dnsPort": 1053, "controllerPort": 9090,
            "secret": secrets.token_urlsafe(24), "userAgent": "clash.meta",
            "nameservers": ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"],
            "bypass": [],
            "lanProxy": True, "routerProxy": False, "udpProxy": True,
            "routerDnsHijack": True, "dnsIpv6Hijack": True,
            "lanAccessMode": "all", "lanAccess": [], "routerBypassUsers": [],
            "dnsMode": "fake-ip", "fakeIpFilter": ["+.lan", "+.local"],
            "defaultNameservers": ["223.5.5.5", "119.29.29.29"],
            "proxyNameservers": [], "localDns": "127.0.0.1:53",
        },
    }


def check(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, label, limit=MAX_TEXT):
    check(isinstance(value, str) and len(value.encode("utf-8")) <= limit, label + "无效或过长")
    return value


def url(value, optional=False):
    if not value and optional:
        return
    parsed = urlsplit(text(value, "URL", 8192))
    check(parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username,
          "URL 必须是 HTTP(S) 地址，不支持内嵌用户名密码")
    check(not any(c in value for c in "\r\n\x00"), "URL 含非法字符")


def ids(value):
    if isinstance(value, str):
        value = [s.strip() for s in value.split(",") if s.strip()]
    check(isinstance(value, list), "关联 ID 必须是列表")
    return list(dict.fromkeys(str(i) for i in value))


def exhausted(source):
    total = source.get("totalBytes")
    usage = [source.get(k) for k in ("uploadBytes", "downloadBytes")
             if source.get(k) is not None]
    return total is not None and total > 0 and bool(usage) and sum(usage) >= total


def validate(data):
    check(isinstance(data, dict) and data.get("format") == "fii-backup" and data.get("version") == 1,
          "不是 FII v1 配置")
    check(type(data.get("revision")) is int and data["revision"] >= 0, "配置版本号无效")
    for collection in ("sources", "overrides", "outputs"):
        values = data.get(collection)
        check(isinstance(values, list) and len(values) <= 500, collection + "列表无效或超过 500 项")
        seen = set()
        for item in values:
            check(isinstance(item, dict), "配置项必须是对象")
            ident = item.get("id")
            check(isinstance(ident, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident), "ID 无效")
            check(ident not in seen, "重复 ID: " + ident)
            seen.add(ident)
            check(bool(text(item.get("name"), "名称", 256).strip()), "名称不能为空")
            check(type(item.get("enabled", True)) is bool, "enabled 必须是布尔值")
            if collection == "sources":
                url(item.get("url", ""), optional=bool(item.get("cachedYaml")))
                for key in ("cachedYaml", "prefix", "includeRegex", "excludeRegex", "userAgent"):
                    text(item.get(key, ""), key)
                for key in ("includeRegex", "excludeRegex"):
                    try:
                        re.compile(item.get(key, ""))
                    except re.error as exc:
                        raise ValueError("%s 正则无效: %s" % (key, exc)) from None
                check(not any(c in item.get("userAgent", "") for c in "\r\n"), "User-Agent 不能换行")
                for key in ("uploadBytes", "downloadBytes", "totalBytes", "expireAtSeconds"):
                    check(item.get(key) is None or (type(item[key]) is int and item[key] >= 0), "流量/时间字段无效")
            if collection == "overrides":
                check(item.get("type") in ("YAML", "RULES"), "覆写支持 YAML / RULES；JS 脚本请先转换为完整 YAML 配置")
                text(item.get("body", ""), "覆写内容")
                url(item.get("url", ""), optional=True)
                check(type(item.get("global", False)) is bool, "global 必须是布尔值")
            if collection != "outputs":
                check(type(item.get("autoRefresh", False)) is bool, "autoRefresh 必须是布尔值")
                interval = item.get("refreshMinutes", 720)
                check(type(interval) is int and 5 <= interval <= 525600, "刷新间隔必须为 5–525600 分钟")
            else:
                item["sourceIds"] = ids(item.get("sourceIds", []))
                item["overrideIds"] = ids(item.get("overrideIds", []))
    source_ids = {s["id"] for s in data["sources"]}
    override_ids = {o["id"] for o in data["overrides"]}
    for output in data["outputs"]:
        check(set(output["sourceIds"]) <= source_ids, "输出引用了不存在的订阅")
        check(set(output["overrideIds"]) <= override_ids, "输出引用了不存在的覆写")
    settings = data.get("settings")
    check(isinstance(settings, dict), "设置无效")
    # Fill newly introduced options on old state/backup files without writing on reads.
    setting_defaults = defaults()["settings"]
    from .proxy import OPTIONS, validate as validate_proxy
    for key in OPTIONS:
        settings.setdefault(key, setting_defaults[key])
    validate_proxy(settings)
    for key in ("lanProxy", "routerProxy", "udpProxy", "routerDnsHijack", "dnsIpv6Hijack", "lanAccessMode",
                "lanAccess", "routerBypassUsers", "dnsMode", "fakeIpFilter", "defaultNameservers", "proxyNameservers", "localDns"):
        if key not in settings:
            settings[key] = setting_defaults[key]
    for key in ("autostart", "autoApply", "ipv6", "dnsHijack", "lanProxy", "routerProxy", "udpProxy",
                "routerDnsHijack", "dnsIpv6Hijack"):
        check(type(settings.get(key)) is bool, key + "必须是布尔值")
    check(settings.get("mode") in ("tproxy", "redirect", "core"), "透明代理模式无效")
    ports = [settings.get(k) for k in ("mixedPort", "redirPort", "tproxyPort", "dnsPort", "controllerPort")]
    check(all(type(p) is int and 1024 <= p <= 65535 for p in ports), "端口必须为 1024–65535")
    check(len(set(ports)) == len(ports), "端口不能重复")
    interfaces = settings.get("interfaces")
    check(isinstance(interfaces, list) and 0 <= len(interfaces) <= 16 and
          all(isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", i) for i in interfaces),
          "局域网接口无效")
    check(not settings["proxyEnabled"] or settings["mode"] == "core" or not settings["lanProxy"] or bool(interfaces), "请选择至少一个局域网接口")
    check(not set(interfaces) & {"lo", "fii-tun", "fii-dummy"}, "回环接口和 FII 虚拟接口不能作为局域网入口")
    check(settings["lanAccessMode"] in ("all", "include", "exclude"), "设备访问控制模式无效")
    entries = settings["lanAccess"]
    check(isinstance(entries, list) and len(entries) <= 256, "设备列表无效或超过 256 项")
    for entry in entries:
        check(isinstance(entry, str), "设备地址必须是文本")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", entry):
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError("设备地址无效: " + entry) from None
    check(settings["lanRules"] is not None or settings["mode"] == "core" or not settings["lanProxy"] or settings["lanAccessMode"] != "include" or bool(entries), "请选择需要代理的设备")
    users = settings["routerBypassUsers"]
    check(isinstance(users, list) and len(users) <= 64 and
          all(isinstance(u, str) and re.fullmatch(r"[0-9]{1,10}", u) and int(u) <= 4294967294 for u in users),
          "绕过用户 UID 无效")
    check(settings["dnsMode"] in ("fake-ip", "redir-host"), "DNS 模式无效")
    check(isinstance(settings["fakeIpFilter"], list) and len(settings["fakeIpFilter"]) <= 256, "Fake-IP 过滤列表无效")
    for domain in settings["fakeIpFilter"]:
        check(bool(text(domain, "过滤域名", 256).strip()) and not any(c.isspace() for c in domain), "过滤域名不能含空白")
    check(bool(re.fullmatch(r"127\.0\.0\.1(?::[0-9]{1,5})?", text(settings["localDns"], "本地 DNS", 64))),
          "本地 DNS 请使用 127.0.0.1 或 127.0.0.1:端口")
    local_port = int(settings["localDns"].split(":")[1]) if ":" in settings["localDns"] else 53
    check(1 <= local_port <= 65535 and local_port not in ports, "本地 DNS 端口无效或与 FII 监听端口冲突")
    bypass = settings.get("bypass")
    check(isinstance(bypass, list) and len(bypass) <= 256, "绕过网段无效")
    for network in bypass:
        ipaddress.ip_network(network, strict=False)
    check(isinstance(settings.get("nameservers"), list) and 1 <= len(settings["nameservers"]) <= 16,
          "至少设置一个上游 DNS")
    for key in ("nameservers", "defaultNameservers", "proxyNameservers"):
        check(isinstance(settings[key], list) and len(settings[key]) <= 16, "DNS 地址列表无效")
        check(key != "defaultNameservers" or bool(settings[key]), "至少设置一个引导 DNS")
        for server in settings[key]:
            text(server, "DNS 地址", 2048)
            check(bool(server.strip()) and not any(c.isspace() for c in server), "DNS 地址不能含空白")
            if key == "defaultNameservers":
                try:
                    ipaddress.ip_address(server)
                except ValueError:
                    raise ValueError("引导 DNS 必须是 IP 地址") from None
    check(len(text(settings.get("secret"), "控制器密钥", 256)) >= 16, "控制器密钥至少 16 字符")
    text(settings.get("userAgent"), "User-Agent", 512)
    check(not any(c in settings["userAgent"] for c in "\r\n"), "User-Agent 不能换行")
    check(len(json.dumps(data, ensure_ascii=False).encode()) <= MAX_DOCUMENT, "配置超过 16 MiB")
    return data


def import_backup(raw, current_settings):
    check(isinstance(raw, dict), "备份必须是 JSON 对象")
    warnings = []
    if raw.get("format") == "fii-backup":
        result = copy.deepcopy(raw)
        # Import configuration without unexpectedly changing router network settings or credentials.
        result["settings"] = copy.deepcopy(current_settings)
    else:
        check(raw.get("format") == "subconverter-backup" and raw.get("version") == 1,
              "不支持的备份格式")
        result = defaults()
        result["settings"] = copy.deepcopy(current_settings)
        for src in raw.get("sources", []):
            for key in ("dnsProtocol", "dnsServer"):
                if src.get(key):
                    warnings.append("订阅 %s 的自定义 DNS 未迁移，使用路由器 DNS" % src.get("name", ""))
                    break
            if src.get("preResolveNodes"):
                warnings.append("订阅 %s 的节点预解析设置未迁移" % src.get("name", ""))
            result["sources"].append({
                **{k: src[k] for k in ("name", "url", "enabled", "prefix", "includeRegex", "excludeRegex",
                    "userAgent", "cachedYaml", "uploadBytes", "downloadBytes", "totalBytes", "expireAtSeconds") if k in src},
                "id": str(src["id"]), "autoRefresh": src.get("autoRefreshEnabled", False),
                "refreshMinutes": src.get("refreshIntervalMinutes", 720),
            })
        for override in raw.get("templates", []):
            result["overrides"].append({
                "id": str(override["id"]), "name": override["name"], "type": override.get("type", "YAML"),
                "enabled": override.get("enabled", True), "global": override.get("global", False),
                "url": override.get("remoteUrl", override.get("url", "")), "body": override.get("yamlBody", ""),
            })
        for profile in raw.get("outputProfiles", []):
            result["outputs"].append({"id": str(profile["id"]), "name": profile["name"],
                "enabled": profile.get("enabled", True), "sourceIds": ids(profile.get("sourceIds", "")),
                "overrideIds": ids(profile.get("overrideIds", ""))})
        warnings.append("仅迁移订阅、YAML/RULES 覆写和输出关联；Gist、服务端口、WebDAV、统计不迁移")
    for output in result["outputs"]:
        output["sourceIds"] = [i for i in ids(output.get("sourceIds", [])) if i in {s["id"] for s in result["sources"]}]
        output["overrideIds"] = [i for i in ids(output.get("overrideIds", [])) if i in {o["id"] for o in result["overrides"]}]
    return validate(result), warnings
