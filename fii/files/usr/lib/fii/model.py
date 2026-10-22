"""Validated persistent model; mirrors SubConverter's source / override / output flow."""
import copy
import json
import re
import secrets

MAX_TEXT = 4 * 1024 * 1024
MAX_DOCUMENT = 16 * 1024 * 1024


def defaults():
    from .proxy import OPTIONS
    from .urltest import DEFAULT_SITES
    return {
        "format": "fii-backup", "version": 1, "revision": 0,
        "sources": [], "overrides": [], "outputs": [],
        "settings": {
            **copy.deepcopy(OPTIONS),
            "autostart": False, "autoApply": False, "mode": "tproxy",
            "interfaces": ["br-lan"], "ipv6": False, "dnsHijack": True,
            "mixedPort": 7890, "redirPort": 7893, "tproxyPort": 7895,
            "dnsPort": 1053, "controllerPort": 9090,
            "controllerAddress": "127.0.0.1", "mixedAddress": "127.0.0.1",
            "secret": secrets.token_urlsafe(24), "userAgent": "clash.meta",
            "externalUi": "", "externalUiName": "", "externalUiUrl": "",
            "coreMode": "rule", "logLevel": "info", "findProcessMode": "off", "coreIpv6": False,
            "unifiedDelay": False, "tcpConcurrent": False,
            "keepAliveInterval": 15, "keepAliveIdle": 15,
            "geodataMode": False, "geoAutoUpdate": True, "geoUpdateInterval": 24,
            "geoxUrls": {}, "etagSupport": True,
            "snifferEnable": True, "snifferForceDnsMapping": True, "snifferParsePureIp": True,
            "snifferOverrideDestination": False,
            "snifferHttpPorts": [80, 8080, 8880], "snifferTlsPorts": [443, 8443], "snifferQuicPorts": [443, 8443],
            "snifferSkipDomain": ["+.lan", "+.local", "+.home.arpa", "Mijia Cloud"],
            "coreTunStack": "mixed", "coreTunMtu": 1500,
            "tunGso": False, "tunGsoMaxSize": 65536, "tunEndpointIndependentNat": False,
            "bypass": [],
            "lanProxy": True, "routerProxy": False, "udpProxy": True,
            "routerDnsHijack": True, "dnsIpv6Hijack": True,
            "lanAccessMode": "all", "lanAccess": [], "routerBypassUsers": [],
            "bootDelay": 0, "scheduledRestart": False, "restartTime": "03:00",
            "goMaxProcs": 0, "goMemLimit": 0, "disableQuicGso": False,
            "disableQuicEcn": False, "skipCoreIpv6Check": False,
            "urlTestSites": copy.deepcopy(DEFAULT_SITES),
        },
    }


def check(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, label, limit=MAX_TEXT):
    check(isinstance(value, str) and len(value.encode("utf-8")) <= limit, label + "无效或过长")
    return value


def github_token(parsed):
    from urllib.parse import unquote
    if parsed.username is None and parsed.password is None:
        return None
    host = (parsed.hostname or '').lower()
    check(host == 'github.com' or host.endswith('.github.com') or
          host == 'githubusercontent.com' or host.endswith('.githubusercontent.com'),
          'URL 含内嵌用户名密码；目前仅支持 HTTPS GitHub 地址的 Token 认证')
    check(parsed.scheme == 'https', 'GitHub Token 地址必须使用 HTTPS')
    token = unquote(parsed.password if parsed.password is not None else parsed.username or '')
    check(bool(token) and all(33 <= ord(c) <= 126 for c in token), 'GitHub Token 为空或含非法字符')
    return token


def url(value, optional=False):
    if not value and optional:
        return
    value = text(value, "URL", 8192)
    check(bool(value), "URL 为空；请填写 HTTP(S) 地址，或为本地订阅保留节点缓存")
    check(not any(c in value for c in "\r\n\x00"), "URL 含非法字符")
    check(not any(c.isspace() for c in value), "URL 含空白字符；请去掉空格或使用 URL 编码")
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        # urllib errors can include the original authority/port. Never echo
        # secret-bearing addresses from a subscription or remote override.
        raise ValueError("URL 的域名、IPv6 地址或端口格式无效") from None
    check(parsed.scheme in ("http", "https"), "URL 必须以 http:// 或 https:// 开头")
    check(bool(hostname), "URL 缺少域名或 IP 地址")
    github_token(parsed)


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
    import ipaddress
    check(isinstance(data, dict) and data.get("format") == "fii-backup" and data.get("version") == 1,
          "不是 FII v1 配置")
    check(type(data.get("revision")) is int and data["revision"] >= 0, "配置版本号无效")
    from .mihomo import migrate
    migrate(data)
    url_errors = []
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
            if collection in ("sources", "overrides"):
                try:
                    url(item.get("url", ""), optional=collection == "overrides" or bool(item.get("cachedYaml")))
                except ValueError as exc:
                    label = "订阅" if collection == "sources" else "覆写"
                    url_errors.append('%s「%s」（ID: %s）：%s' % (label, item["name"], ident, exc))
            if collection == "sources":
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
                check(item.get("type") in ("YAML", "RULES", "JS"), "覆写类型必须是 YAML、RULES 或 JS")
                text(item.get("body", ""), "覆写内容")
                check(type(item.get("global", False)) is bool, "global 必须是布尔值")
            if collection != "outputs":
                check(type(item.get("autoRefresh", False)) is bool, "autoRefresh 必须是布尔值")
                interval = item.get("refreshMinutes", 720)
                check(type(interval) is int and 5 <= interval <= 525600, "刷新间隔必须为 5–525600 分钟")
            else:
                item["sourceIds"] = ids(item.get("sourceIds", []))
                item["overrideIds"] = ids(item.get("overrideIds", []))
    if url_errors:
        details = url_errors[:20]
        if len(url_errors) > 20:
            details.append('另有 %d 项地址错误，请修正后重新检查' % (len(url_errors) - 20))
        raise ValueError('发现 %d 项地址错误：\n%s' % (len(url_errors), '\n'.join(details)))
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
                "lanAccess", "routerBypassUsers", "bootDelay", "scheduledRestart", "restartTime",
                "goMaxProcs", "goMemLimit", "disableQuicGso", "disableQuicEcn", "skipCoreIpv6Check",
                "coreMode", "logLevel", "findProcessMode", "coreIpv6", "unifiedDelay", "tcpConcurrent",
                "keepAliveInterval", "keepAliveIdle", "geodataMode", "geoAutoUpdate",
                "geoUpdateInterval", "geoxUrls", "etagSupport",
                "snifferEnable", "snifferForceDnsMapping", "snifferParsePureIp",
                "snifferOverrideDestination", "snifferHttpPorts", "snifferTlsPorts",
                "snifferQuicPorts", "snifferSkipDomain",
                "coreTunStack", "coreTunMtu", "tunGso", "tunGsoMaxSize", "tunEndpointIndependentNat",
                "externalUi", "externalUiName", "externalUiUrl"):
        if key not in settings:
            settings[key] = setting_defaults[key]
    settings.setdefault("controllerAddress", setting_defaults["controllerAddress"])
    settings.setdefault("mixedAddress", setting_defaults["mixedAddress"])
    settings.setdefault("urlTestSites", setting_defaults["urlTestSites"])
    from .urltest import validate_sites
    validate_sites(settings['urlTestSites'])
    for key in ("autostart", "autoApply", "ipv6", "dnsHijack", "lanProxy", "routerProxy", "udpProxy",
                "routerDnsHijack", "dnsIpv6Hijack", "scheduledRestart", "disableQuicGso",
                "disableQuicEcn", "skipCoreIpv6Check", "unifiedDelay", "tcpConcurrent",
                "geodataMode", "geoAutoUpdate", "etagSupport", "coreIpv6",
                "snifferEnable", "snifferForceDnsMapping", "snifferParsePureIp",
                "snifferOverrideDestination", "tunGso", "tunEndpointIndependentNat"):
        check(type(settings.get(key)) is bool, key + "必须是布尔值")
    check(type(settings["bootDelay"]) is int and 0 <= settings["bootDelay"] <= 600,
          "开机延迟必须是 0–600 秒")
    check(bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", settings["restartTime"])), "重启时间必须是 HH:MM")
    check(type(settings["goMaxProcs"]) is int and 0 <= settings["goMaxProcs"] <= 64,
          "GOMAXPROCS 必须是 0–64（0 表示不限制）")
    check(type(settings["goMemLimit"]) is int and 0 <= settings["goMemLimit"] <= 65536,
          "内核内存上限必须是 0–65536 MiB（0 表示不限制）")
    check(settings.get("mode") in ("tproxy", "redirect", "core"), "透明代理模式无效")
    check(settings.get("coreMode") in ("rule", "global", "direct"), "内核运行模式无效")
    check(settings.get("logLevel") in ("silent", "error", "warning", "info", "debug"), "日志等级无效")
    check(settings.get("findProcessMode") in ("strict", "always", "off"), "进程匹配模式无效")
    check(settings.get("coreTunStack") in ("mixed", "system", "gvisor"), "TUN 协议栈无效")
    check(type(settings["coreTunMtu"]) is int and 1280 <= settings["coreTunMtu"] <= 9000,
          "TUN MTU 必须是 1280–9000")
    check(type(settings["tunGsoMaxSize"]) is int and 1 <= settings["tunGsoMaxSize"] <= 1048576,
          "TUN GSO 最大长度必须是 1–1048576")
    check(type(settings["keepAliveInterval"]) is int and 1 <= settings["keepAliveInterval"] <= 3600,
          "Keep-Alive 间隔必须是 1–3600 秒")
    check(type(settings["keepAliveIdle"]) is int and 1 <= settings["keepAliveIdle"] <= 3600,
          "Keep-Alive 空闲时间必须是 1–3600 秒")
    check(type(settings["geoUpdateInterval"]) is int and 1 <= settings["geoUpdateInterval"] <= 720,
          "GEO 更新间隔必须是 1–720 小时")
    geox = settings.get("geoxUrls")
    check(isinstance(geox, dict) and len(geox) <= 4, "GEO 下载地址无效")
    for key, value in geox.items():
        check(key in ("geoip", "geosite", "mmdb", "asn"), "GEO 下载地址类型无效: " + str(key))
        text(value, "GEO 下载地址", 2048)
        if value.strip():
            url(value)
    # Empty entries mean "use the built-in address"; dropping them keeps the
    # running config free of blank URLs that the core would reject.
    settings["geoxUrls"] = {k: v for k, v in geox.items() if v.strip()}
    external_ui = settings.get("externalUi")
    check(isinstance(external_ui, str), "外部用户界面目录必须是文本")
    if external_ui:
        check(bool(re.fullmatch(r"[A-Za-z0-9_./-]{1,256}", external_ui)), "外部用户界面目录必须是相对内核工作目录的路径")
        check(not external_ui.startswith("/"), "外部用户界面目录不能是绝对路径")
        check(".." not in external_ui.split("/"), "外部用户界面目录不能包含 ..")
    external_ui_name = settings.get("externalUiName")
    check(isinstance(external_ui_name, str), "外部用户界面名称必须是文本")
    if external_ui_name:
        check(bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", external_ui_name)), "外部用户界面名称无效")
        check(external_ui_name not in (".", ".."), "外部用户界面名称无效")
    external_ui_url = settings.get("externalUiUrl")
    if external_ui_url:
        url(external_ui_url)
        check(bool(external_ui), "使用外部用户界面下载地址前，请先填写外部用户界面目录")
    check(not external_ui_name or bool(external_ui), "使用外部用户界面名称前，请先填写外部用户界面目录")
    for key in ("snifferHttpPorts", "snifferTlsPorts", "snifferQuicPorts"):
        ports = settings[key]
        check(isinstance(ports, list) and len(ports) <= 32, "嗅探端口列表无效")
        check(all(type(p) is int and 1 <= p <= 65535 for p in ports), "嗅探端口必须是 1–65535 的数字")
    skip = settings["snifferSkipDomain"]
    check(isinstance(skip, list) and len(skip) <= 64, "嗅探跳过域名列表无效")
    for entry in skip:
        check(isinstance(entry, str) and 0 < len(entry) <= 256, "嗅探跳过域名无效")
    ports = [settings.get(k) for k in ("mixedPort", "redirPort", "tproxyPort", "dnsPort", "controllerPort")]
    check(all(type(p) is int and 1024 <= p <= 65535 for p in ports), "端口必须为 1024–65535")
    check(len(set(ports)) == len(ports), "端口不能重复")
    check(isinstance(settings["controllerAddress"], str), "控制器监听地址必须是 IP 地址")
    try:
        ipaddress.ip_address(settings["controllerAddress"])
    except ValueError:
        raise ValueError("控制器监听地址必须是 IP 地址") from None
    check(isinstance(settings["mixedAddress"], str), "混合代理监听地址必须是 IP 地址")
    try:
        ipaddress.ip_address(settings["mixedAddress"])
    except ValueError:
        raise ValueError("混合代理监听地址必须是 IP 地址") from None
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
    bypass = settings.get("bypass")
    check(isinstance(bypass, list) and len(bypass) <= 256, "绕过网段无效")
    for network in bypass:
        ipaddress.ip_network(network, strict=False)
    # Any value, including empty (no controller authentication), is accepted.
    text(settings.get("secret"), "控制器密钥", 256)
    text(settings.get("userAgent"), "User-Agent", 512)
    check(not any(c in settings["userAgent"] for c in "\r\n"), "User-Agent 不能换行")
    check(len(json.dumps(data, ensure_ascii=False).encode()) <= MAX_DOCUMENT, "配置超过 16 MiB")
    return data


def import_backup(raw, current_settings):
    check(isinstance(raw, dict), "备份必须是 JSON 对象")
    warnings = []
    if raw.get("format") == "fii-backup":
        result = copy.deepcopy(raw)
        from .mihomo import migrate
        migrate(result)
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
        templates = raw.get("templates", [])
        # Flutter exports database insertion order, but displays/executes templates
        # by sortOrder then numeric id. Old backups without sortOrder keep their
        # supplied order so legacy association ordering is not guessed.
        has_order = any('sortOrder' in item for item in templates)
        if has_order:
            check(all(type(item.get('sortOrder', 0)) is int and type(item.get('id')) is int for item in templates),
                  '覆写排序字段无效')
            templates = sorted(templates, key=lambda item: (item.get('sortOrder', 0), item['id']))
        for override in templates:
            result["overrides"].append({
                "id": str(override["id"]), "name": override["name"], "type": override.get("type", "YAML"),
                "enabled": override.get("enabled", True), "global": override.get("global", False),
                "url": override.get("remoteUrl", override.get("url", "")), "body": override.get("yamlBody", ""),
                "lastRefreshAt": (override.get("lastRefreshAt") or 0) // 1000,
                "lastError": override.get("lastError", ""),
            })
        for profile in raw.get("outputProfiles", []):
            result["outputs"].append({"id": str(profile["id"]), "name": profile["name"],
                "enabled": profile.get("enabled", True), "sourceIds": ids(profile.get("sourceIds", "")),
                "overrideIds": ids(profile.get("overrideIds", ""))})
        if has_order:
            for output in result['outputs']:
                selected = set(output['overrideIds'])
                output['overrideIds'] = [item['id'] for item in result['overrides'] if item['id'] in selected]
        warnings.append("仅迁移订阅、YAML/RULES/JS 覆写和输出关联；Gist、服务端口、WebDAV、统计不迁移")
    for output in result["outputs"]:
        output["sourceIds"] = [i for i in ids(output.get("sourceIds", [])) if i in {s["id"] for s in result["sources"]}]
        output["overrideIds"] = [i for i in ids(output.get("overrideIds", [])) if i in {o["id"] for o in result["overrides"]}]
    for item in result["sources"] + result["overrides"]:
        if isinstance(item.get("url"), str):
            item["url"] = item["url"].strip()
    return validate(result), warnings
