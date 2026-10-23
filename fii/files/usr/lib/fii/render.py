"""Mihomo renderer. Semantics follow sub-converter-flutter/mihomo_yaml_service.dart."""
import base64
import copy
import hashlib
import json
import os
import re
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from .model import check, exhausted

BASE = """mixed-port: 7890
allow-lan: false
proxy-groups:
  - name: PROXY
    type: select
    proxies: '{{proxy_names}}'
rules:
  - DOMAIN-SUFFIX,lan,DIRECT
  - DOMAIN-SUFFIX,local,DIRECT
  - MATCH,PROXY
"""


class Loader(getattr(yaml, 'CSafeLoader', yaml.SafeLoader)):
    """YAML 1.2-like booleans: node names ON / OFF must stay strings."""

    def construct_mapping(self, node, deep=False):
        result = super().construct_mapping(node, deep=deep)
        # REALITY IDs are hexadecimal text, not YAML numbers. Reading the scalar
        # spelling here retains leading zeroes/octal-looking digits before JS
        # or JSON can lose them. Do not mutate shared alias nodes or resolvers.
        for key, value in reversed(node.value):
            if key.tag == 'tag:yaml.org,2002:str' and key.value == 'short-id':
                if value.tag in ('tag:yaml.org,2002:str', 'tag:yaml.org,2002:int', 'tag:yaml.org,2002:float'):
                    result['short-id'] = value.value
                break
        return result


class Dumper(getattr(yaml, 'CSafeDumper', yaml.SafeDumper)):
    def represent_mapping(self, tag, mapping, flow_style=None):
        node = super().represent_mapping(tag, mapping, flow_style=flow_style)
        # SafeDumper's YAML 1.1 resolver would leave 0089-like strings bare,
        # while other YAML implementations may read them as numbers.
        for key, value in node.value:
            if key.value == 'short-id' and value.tag == 'tag:yaml.org,2002:str':
                value.style = "'"
        return node


Loader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for key, resolvers in Loader.yaml_implicit_resolvers.items():
    Loader.yaml_implicit_resolvers[key] = [(tag, rx) for tag, rx in resolvers
                                         if tag != "tag:yaml.org,2002:bool"]
Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"), list("tTfF"))


def load_map(body):
    try:
        # Reject cycles and alias expansion bombs before constructing the document.
        aliases = sum(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(body, Loader=Loader))
        check(aliases <= 64, "YAML 别名过多")
        result = yaml.load(body, Loader=Loader)
        check(result is None or isinstance(result, dict), "YAML 根节点必须是对象")
        serialized = json.dumps(result, ensure_ascii=False)
        check(len(serialized) <= 8 * 1024 * 1024, "展开后的 YAML 过大")
        return result or {}
    except (yaml.YAMLError, TypeError, RecursionError, OverflowError) as exc:
        raise ValueError("YAML 解析失败: " + str(exc)[:300]) from None


def dump(data):
    return yaml.dump(data, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=120)


def decode64(value):
    return base64.urlsafe_b64decode(value.encode() + b"=" * (-len(value) % 4)).decode("utf-8-sig")


def parse_link(line):
    scheme = line.split(":", 1)[0].lower()
    if scheme == "vmess":
        v = json.loads(decode64(line[8:]))
        p = {"name": v.get("ps") or v["add"], "type": "vmess", "server": v["add"],
             "port": int(v["port"]), "uuid": v["id"], "alterId": int(v.get("aid", 0)),
             "cipher": v.get("scy", "auto"), "udp": True}
        net = v.get("net", "tcp")
        check(net in ("tcp", "ws", "grpc"), "VMess 传输暂不支持: " + net)
        if v.get("tls") == "tls":
            p.update({"tls": True, "servername": v.get("sni") or v.get("host") or v["add"]})
        if net == "ws":
            p.update({"network": "ws", "ws-opts": {"path": v.get("path") or "/", "headers": {"Host": v.get("host") or v["add"]}}})
        if net == "grpc":
            p.update({"network": "grpc", "grpc-opts": {"grpc-service-name": v.get("path", "")}})
        return p
    if scheme == "ss":
        value = line[5:]
        payload, _, fragment = value.partition("#")
        payload, _, query_string = payload.partition("?")
        if "@" not in payload:
            payload = decode64(payload)
        credentials, host = payload.rsplit("@", 1)
        if ":" not in credentials:
            credentials = decode64(credentials)
        method, password = unquote(credentials).split(":", 1)
        parsed = urlsplit("ss://" + host)
        check(parsed.hostname and parsed.port, "SS 地址缺少主机或端口")
        check(not parse_qs(query_string).get("plugin"), "带插件的 SS 链接请转换为 Mihomo YAML 后导入")
        return {"name": unquote(fragment) or parsed.hostname, "type": "ss", "server": parsed.hostname,
                "port": parsed.port, "cipher": method, "password": password, "udp": True}
    check(scheme in ("trojan", "vless", "hysteria2", "hy2"), "不支持的分享链接类型: " + scheme)
    parsed = urlsplit(line)
    check(parsed.hostname and parsed.port and parsed.username, "分享链接缺少主机、端口或凭证")
    query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    p = {"name": unquote(parsed.fragment) or parsed.hostname, "type": "hysteria2" if scheme == "hy2" else scheme,
         "server": parsed.hostname, "port": parsed.port, "udp": True}
    p["uuid" if scheme == "vless" else "password"] = unquote(parsed.username)
    if scheme == "vless":
        security = query.get("security", "none")
        check(security in ("none", "tls", "reality"), "不支持的 VLESS security")
        p["tls"] = security in ("tls", "reality")
        if query.get("flow"):
            p["flow"] = query["flow"]
        if security == "reality":
            check(query.get("pbk"), "Reality 缺少 public key")
            p["reality-opts"] = {"public-key": query["pbk"], "short-id": query.get("sid", "")}
    if query.get("sni") or query.get("peer"):
        p["servername" if scheme == "vless" else "sni"] = query.get("sni") or query["peer"]
    if query.get("fp"):
        p["client-fingerprint"] = query["fp"]
    if query.get("alpn"):
        p["alpn"] = query["alpn"].split(",")
    if query.get("allowInsecure") == "1" or query.get("insecure") == "1":
        p["skip-cert-verify"] = True
    net = query.get("type", "tcp")
    check(net in ("tcp", "ws", "grpc"), "分享链接传输暂不支持: " + net)
    if net == "ws":
        p.update({"network": "ws", "ws-opts": {"path": query.get("path", "/"), "headers": {"Host": query.get("host", parsed.hostname)}}})
    if net == "grpc":
        p.update({"network": "grpc", "grpc-opts": {"grpc-service-name": query.get("serviceName", "")}})
    if scheme in ("hysteria2", "hy2") and query.get("obfs"):
        p.update({"obfs": query["obfs"], "obfs-password": query.get("obfs-password", "")})
    return p


def extract(body):
    # Subscription YAML changes far less often than editors/previews are opened.
    # Cache only parsed nodes in tmpfs, keyed by content (never source ID/name).
    from .store import rooted, lock, write_json
    if os.name == 'nt' and not os.environ.get('FII_ROOT'):
        return extract_uncached(body)
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    directory = rooted('/tmp/fii/node-cache-v1')
    path = directory / (digest + '.json')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, list) and all(isinstance(p, dict) and isinstance(p.get('name'), str) and p['name'] for p in value):
            return value
    except (OSError, ValueError):
        pass
    value = extract_uncached(body)
    # Bounded RAM usage; failure to cache must not prevent validation/rendering.
    try:
        with lock('node-cache'):
            directory.mkdir(parents=True, exist_ok=True)
            files = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime)
            size = sum(p.stat().st_size for p in files)
            encoded_size = len((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
            if encoded_size > 32 * 1024 * 1024:
                return value
            while files and (len(files) >= 32 or size + encoded_size > 32 * 1024 * 1024):
                old = files.pop(0)
                size -= old.stat().st_size
                old.unlink(missing_ok=True)
            write_json(path, value)
    except (OSError, ValueError):
        pass
    return value


def extract_uncached(body):
    body = body.strip()
    try:
        root = load_map(body)
    except ValueError:
        root = {}
    if "proxies" in root:
        proxies = root["proxies"]
        check(isinstance(proxies, list) and all(isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"] for p in proxies),
              "proxies 必须是有名称的节点对象列表")
        return copy.deepcopy(proxies)
    if "://" not in body:
        try:
            body = decode64(body)
        except (ValueError, UnicodeError):
            raise ValueError("订阅必须包含 proxies 列表或支持的分享链接；provider-only 配置请先展开节点") from None
    links = [s.strip() for s in body.splitlines() if s.strip() and not s.lstrip().startswith("#")]
    check(bool(links), "订阅内容为空")
    proxies = []
    for index, link in enumerate(links, 1):
        try:
            proxies.append(parse_link(link))
        except (ValueError, KeyError, TypeError, IndexError):
            raise ValueError("第 %d 条分享链接无效或使用暂不支持的类型/传输；请先转换成完整 Mihomo YAML" % index) from None
    return proxies


def expand(value, names):
    if isinstance(value, dict):
        return {str(k): expand(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, names) for v in value]
    if value == "{{proxy_names}}":
        return names[:]
    if isinstance(value, str):
        return value.replace("{{proxy_names_csv}}", ",".join(names))
    return value


def merge(target, patch):
    for raw_key, value in patch.items():
        raw_key = str(raw_key)
        replace = raw_key.endswith("!")
        prepend = raw_key.startswith("+") and not replace
        append = raw_key.endswith("+") and not prepend and not replace
        key = raw_key[1:] if prepend else raw_key[:-1] if (append or replace) else raw_key
        key = key[1:-1] if key.startswith("<") and key.endswith(">") else key
        if prepend or append:
            check(isinstance(value, list), "覆写 %s 使用 + 语法时必须是列表" % raw_key)
            old = target.get(key, [])
            old = old if isinstance(old, list) else []
            target[key] = copy.deepcopy(value + old if prepend else old + value)
        elif isinstance(value, dict) and not replace:
            old = target.get(key, {})
            target[key] = merge(copy.deepcopy(old) if isinstance(old, dict) else {}, value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def validate_override(override):
    if override["type"] == "JS":
        from .javascript import validate
        try:
            validate(override.get('body', ''))
        except ValueError as exc:
            raise ValueError('JS 覆写「%s」校验失败: %s' % (override.get('name', override.get('id', '')), exc)) from None
    elif override["type"] == "YAML":
        merge({}, load_map(override.get("body", "")))
    else:
        for line in override.get("body", "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                check("," in line and len(line.split(",")) >= (2 if line.startswith("MATCH,") else 3),
                      "规则格式无效: " + line[:100])


def render(data, output_id, progress=None):
    output = next((p for p in data["outputs"] if p["id"] == output_id), None)
    check(output is not None and output.get("enabled", True), "输出不存在或已禁用")
    sources = {s["id"]: s for s in data["sources"]}
    proxies, warnings, used = [], [], set()
    for ident in output["sourceIds"]:
        source = sources[ident]
        if not source.get("enabled", True):
            continue
        if exhausted(source):
            warnings.append("流量耗尽，已排除: " + source["name"])
            continue
        if not source.get("cachedYaml"):
            warnings.append("尚无缓存，请先刷新: " + source["name"])
            continue
        if source.get("lastError"):
            warnings.append("使用上次成功缓存: " + source["name"])
        if progress:
            progress('正在解析订阅：' + source['name'])
        for p in extract(source["cachedYaml"]):
            name = p["name"]
            if source.get("includeRegex") and not re.search(source["includeRegex"], name):
                continue
            if source.get("excludeRegex") and re.search(source["excludeRegex"], name):
                continue
            name = source.get("prefix", "") + name
            original, suffix = name, 2
            while name in used or name in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL", "PROXY"):
                name = "%s (%d)" % (original, suffix)
                suffix += 1
            p["name"] = name
            used.add(name)
            proxies.append(p)
    names = [p["name"] for p in proxies]
    root = expand(load_map(BASE), names or ["DIRECT"])
    from .mihomo import DEFAULTS, seed
    seed(root)
    # Follow-the-ISP DNS belongs to the defaults layer: it merges before
    # overrides run, so an override that sets nameserver explicitly — or
    # replaces the whole dns! block — keeps full control of the list.
    if data["settings"].get("dnsUseWanDns"):
        from .network import wan_dns
        base = list(data["settings"].get("dnsNameservers") or DEFAULTS["dns"]["nameserver"])
        upstream = [server for server in wan_dns() if server not in base]
        if upstream:
            root["dns"]["nameserver"] = (upstream + base if data["settings"].get("dnsWanDnsPrefer")
                                         else base + upstream)
        else:
            warnings.append("已开启跟随运营商 DNS，但未在 WAN 上检测到 DNS 服务器")
    root["proxies"] = proxies
    overrides = {o["id"]: o for o in data["overrides"]}
    ordered_ids = list(dict.fromkeys([o["id"] for o in data["overrides"] if o.get("global")] + output["overrideIds"]))
    applied = []
    enabled = [overrides[ident] for ident in ordered_ids if overrides[ident].get('enabled', True)]
    index = 0
    while index < len(enabled):
        override = enabled[index]
        if progress:
            progress('正在执行覆写 %d：%s' % (len(applied) + 1, override['name']))
        applied.append({key: override.get(key) for key in ('id', 'name', 'type', 'global')})
        if override["type"] == "JS":
            from .javascript import execute_many
            batch = [override]
            while index + 1 < len(enabled) and enabled[index + 1]['type'] == 'JS':
                index += 1
                item = enabled[index]
                batch.append(item)
                applied.append({key: item.get(key) for key in ('id', 'name', 'type', 'global')})
                if progress:
                    progress('正在执行覆写 %d：%s' % (len(applied), item['name']))
            try:
                root = execute_many(batch, root)
            except ValueError as exc:
                if len(batch) == 1:
                    raise ValueError('JS 覆写「%s」失败: %s' % (override['name'], exc)) from None
                raise
        elif override["type"] == "YAML":
            validate_override(override)
            merge(root, expand(load_map(override.get("body", "")), names))
        else:
            validate_override(override)
            rules = [s.strip() for s in override.get("body", "").splitlines()
                     if s.strip() and not s.lstrip().startswith("#")]
            root["rules"] = rules + root.get("rules", [])
        index += 1
    seed(root, final=True)
    check(isinstance(root.get('proxies', []), list), '生成配置的 proxies 必须是列表')
    if progress:
        progress('正在生成 YAML…')
    return {"yaml": dump(root), "nodeCount": len(root.get("proxies", [])), "warnings": warnings, "name": output["name"],
            "appliedOverrides": applied}
