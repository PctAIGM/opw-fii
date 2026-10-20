"""OpenWrt runtime generation and transactional activation."""
import copy
import json
import os
import re
import subprocess
import time
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from .model import check, defaults
from .store import Store, atomic_write, lock, rooted, write_json

CORE = "/etc/fii/bin/mihomo"
INIT = "/etc/init.d/fii"
BYPASS_MARK = 0xF100
PROXY_MARK = 0xF101
ROUTE_TABLE = 20230
ROUTE_PRIORITY = 30000


def dump(*args, **kwargs):
    from .render import dump as operation
    return operation(*args, **kwargs)


def load_map(*args, **kwargs):
    from .render import load_map as operation
    return operation(*args, **kwargs)


def render(*args, **kwargs):
    from .render import render as operation
    return operation(*args, **kwargs)


def run(args, timeout=30, required=True):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        if not required:
            return ""
        raise ValueError("命令无法执行或超时: " + args[0]) from None
    if required and result.returncode:
        raise ValueError("命令执行失败: " + args[0] + "\n" + (result.stderr or result.stdout)[-2000:])
    return result.stdout.strip()


def overlay(body, settings, warnings=None):
    settings = {**defaults()["settings"], **settings}
    from .proxy import modes
    protocols = modes(settings)
    tun = "tun" in protocols.values()
    root = load_map(body)
    original = copy.deepcopy(root) if warnings is not None else None
    from .mihomo import seed, dns_options
    seed(root, final=True)
    # These are owned by the router integration. User overrides cannot desynchronise routing.
    for key in ("port", "socks-port", "listeners", "external-controller-tls", "external-controller-unix",
                "external-controller-pipe", "external-ui", "external-ui-url", "external-ui-name",
                "authentication", "lan-allowed-ips", "lan-disallowed-ips"):
        root.pop(key, None)
    root.update({"mixed-port": 0, "redir-port": settings["redirPort"] if protocols["tcp"] == "redirect" else 0,
                 "tproxy-port": settings["tproxyPort"] if "tproxy" in protocols.values() else 0, "allow-lan": True, "bind-address": "*",
                 "listeners": [{"name": "fii-mixed", "type": "mixed", "listen": "127.0.0.1", "port": settings["mixedPort"]}],
                 "external-controller": "127.0.0.1:%d" % settings["controllerPort"],
                 "secret": settings["secret"],
                 "routing-mark": BYPASS_MARK})
    tun_options = root.setdefault("tun", {})
    check(isinstance(tun_options, dict), "tun 覆写必须是对象")
    tun_options.update({"enable": tun, "device": "fii-tun", "auto-route": False, "auto-redirect": False,
                        "auto-detect-interface": False, "dns-hijack": [],
                        "inet6-address": ["fdfe:dcba:9876::1/126"] if settings["ipv6"] else []})
    dns = root.get("dns", {})
    check(isinstance(dns, dict), "dns 覆写必须是对象")
    from .proxy_nft import dns_families
    dns_options(root)  # Validate the routing inputs before producing a preview.
    from .proxy import rules
    intercepted = settings['mode'] != 'core' and settings['proxyEnabled'] and dns_families(settings) and any(
        settings[scope + 'Proxy'] and settings['dnsHijack' if scope == 'lan' else 'routerDnsHijack'] and
        any(rule['enabled'] and rule['dns'] for rule in rules(settings, scope)) for scope in ('lan', 'router'))
    check(dns['enable'] or not intercepted, 'Mihomo 配置已关闭 DNS；请在覆写中启用 dns.enable，或在路由器设置中关闭 DNS 接管')
    if dns['enable']:
        dns['listen'] = '[::]:%d' % settings['dnsPort'] if settings['ipv6'] or 'ipv6' in dns_families(settings) else '0.0.0.0:%d' % settings['dnsPort']
        if settings['mode'] == 'core':
            dns['listen'] = ('[::1]:%d' if settings['ipv6'] else '127.0.0.1:%d') % settings['dnsPort']
    dns.setdefault('proxy-server-nameserver', copy.deepcopy(dns['nameserver']))
    root["dns"] = dns
    profile = root.setdefault("profile", {})
    check(isinstance(profile, dict), "profile 覆写必须是对象")
    profile.setdefault("store-selected", True)
    profile.setdefault("store-fake-ip", True)
    if warnings is not None:
        changed = []
        def differences(before, after, prefix=""):
            for key, value in before.items():
                label = prefix + str(key)
                if isinstance(value, dict) and isinstance(after.get(key), dict):
                    differences(value, after[key], label + ".")
                elif key not in after or value != after[key]:
                    changed.append(label)
        differences(original, root)
        if changed:
            warnings.append("路由器集成字段使用 FII 设置：" + "、".join(sorted(changed)))
    return dump(root)


def nftables(settings, config=None):
    from .proxy_nft import generate
    return generate(settings, config)


def active():
    path = rooted("/etc/fii/active.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def ready(bundle=None):
    bundle = bundle or active()
    if not bundle:
        return False
    settings = bundle["settings"]
    req = Request("http://127.0.0.1:%d/version" % settings["controllerPort"],
                  headers={"Authorization": "Bearer " + settings["secret"]})
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=1) as response:
            return isinstance(json.loads(response.read(8192)), dict)
    except (OSError, ValueError):
        return False


def controller(path, payload=None, *, method=None, timeout=4):
    bundle = active()
    check(bundle is not None and ready(bundle), "Mihomo 尚未运行")
    settings = bundle["settings"]
    content = json.dumps(payload).encode() if payload is not None else None
    req = Request("http://127.0.0.1:%d%s" % (settings["controllerPort"], path), data=content,
                  headers={"Authorization": "Bearer " + settings["secret"], "Content-Type": "application/json"},
                  method=method or ("PUT" if payload is not None else "GET"))
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=timeout) as response:
            body = response.read(2 * 1024 * 1024)
            return json.loads(body) if body else {}
    except (OSError, ValueError):
        raise ValueError("Mihomo 控制器请求失败，请检查服务状态") from None


def groups():
    bundle = active()
    if bundle and rooted('/tmp/fii/ready').exists():
        proxies = controller('/proxies').get('proxies', {})
        check(isinstance(proxies, dict), '代理组响应无效')
        result, nodes = [], {}
        for name, value in proxies.items():
            if not isinstance(value, dict):
                continue
            history = value.get('history')
            last = history[-1] if isinstance(history, list) and history and isinstance(history[-1], dict) else {}
            delay = last.get('delay')
            nodes[name] = {'type': value.get('type', ''),
                           'alive': value.get('alive') if type(value.get('alive')) is bool else None,
                           'delay': delay if type(delay) is int and 0 <= delay <= 65535 else None,
                           'testedAt': last.get('time') if isinstance(last.get('time'), str) else ''}
            members = value.get('all')
            if isinstance(members, list) and name != 'GLOBAL':
                result.append({'name': name, 'type': value.get('type', ''),
                    'now': value.get('now', ''), 'fixed':value.get('fixed') if isinstance(value.get('fixed'), str) else '',
                    'all': [item for item in members if isinstance(item, str)]})
        return {'groups': result, 'nodes': nodes, 'running': True, 'source': 'runtime'}
    # Stopped cores still have a useful configuration to inspect. Do not call
    # the controller, render scripts, or guess persisted/automatic selections.
    config = load_map(bundle['yaml']) if bundle else {}
    types = {'select':'Selector', 'url-test':'URLTest', 'fallback':'Fallback', 'load-balance':'LoadBalance', 'relay':'Relay'}
    nodes = {'DIRECT': {'type':'Direct'}, 'REJECT': {'type':'Reject'}, 'REJECT-DROP': {'type':'RejectDrop'}}
    for node in config.get('proxies') or []:
        if isinstance(node, dict) and isinstance(node.get('name'), str):
            nodes[node['name']] = {'type': node.get('type', '')}
    result = []
    for group in config.get('proxy-groups') or []:
        if not isinstance(group, dict) or not isinstance(group.get('name'), str):
            continue
        name = group['name']; kind = types.get(group.get('type'), group.get('type', ''))
        nodes[name] = {'type':kind}
        members = group.get('proxies') or []
        result.append({'name':name, 'type':kind, 'now':'', 'fixed':None,
            'all':[item for item in members if isinstance(item, str)],
            'dynamic':bool(group.get('use') or group.get('include-all') or group.get('include-all-proxies') or group.get('include-all-providers'))})
    return {'groups':result, 'nodes':nodes, 'running':False, 'source':'snapshot' if bundle else 'none'}


def select_proxy(group, name=None, action='select'):
    check(isinstance(group, str), '代理组名称必须是文本')
    check(action in ('select', 'unpin'), '代理组操作无效')
    if action == 'select':
        check(isinstance(name, str), '节点名称必须是文本')
    else:
        check(name is None, '取消钉住不接受节点名称')
    view = groups()
    check(view['running'], '内核已停止，当前仅展示配置中的代理组')
    available = next((g for g in view['groups'] if g['name'] == group), None)
    check(available is not None, '代理组不存在')
    automatic = available['type'] in ('URLTest', 'Fallback')
    path = '/proxies/' + quote(group, safe='')
    if action == 'unpin':
        check(automatic, '只有自动测速组和故障转移组支持取消钉住')
        controller(path, method='DELETE')
        return {'message':'已取消钉住，恢复自动选择'}
    check((available['type'] == 'Selector' or automatic) and name in available['all'], '代理组不支持选择或节点不存在')
    # Fallback.Set may synchronously health-check a dead node for up to 5s.
    if available['type'] == 'Fallback':
        controller(path, {'name':name}, timeout=8)
    else:
        controller(path, {'name':name})
    return {'message':('已钉住 ' if automatic else '已切换到 ') + name}


def core_test(body):
    binary = str(rooted(CORE))
    check(os.path.isfile(binary) and os.access(binary, os.X_OK), "尚未下载 Mihomo 内核，请在概览的内核管理中选择 Release 或 Alpha 并下载")
    directory = rooted("/etc/fii/core")
    directory.mkdir(parents=True, exist_ok=True)
    import secrets
    candidate = rooted("/tmp/fii/candidate-" + secrets.token_hex(8) + ".yaml")
    atomic_write(candidate, body)
    try:
        run([binary, "-t", "-d", str(directory), "-f", str(candidate)], timeout=25)
    except ValueError as exc:
        match = re.search(r'proxy (\d+): invalid REALITY short ID', str(exc))
        if match:
            index = int(match.group(1))
            nodes = load_map(body).get('proxies', [])
            if isinstance(nodes, list) and index < len(nodes) and isinstance(nodes[index], dict):
                name = nodes[index].get('name', '未命名节点')
                raise ValueError('节点「%s」（输出第 %d 项）的 REALITY short-id 无效。'
                                 '应为 0–16 个十六进制字符且长度为偶数；请检查该节点及相关覆写。'
                                 '当前运行配置未替换。' % (name, index + 1)) from None
        raise
    finally:
        candidate.unlink(missing_ok=True)


def prepare():
    bundle = active()
    check(bundle is not None, "请先在输出页应用一个配置")
    core_test(bundle["yaml"])
    atomic_write(rooted("/tmp/fii/config.yaml"), bundle["yaml"])
    atomic_write(rooted("/tmp/fii/firewall.nft"), nftables(bundle["settings"], bundle["yaml"]))


def fake6_link():
    try:
        links = json.loads(run(["/sbin/ip", "-j", "-d", "link", "show", "dev", "fii-dummy"], required=False) or "[]")
        return links[0] if links else None
    except (ValueError, IndexError):
        return None


def remove_old_fake6_routes(keep):
    """Only prune routes on FII's already-verified dedicated dummy interface."""
    import ipaddress
    try:
        routes = json.loads(run(['/sbin/ip', '-6', '-j', 'route', 'show', 'dev', 'fii-dummy'], required=False) or '[]')
    except ValueError:
        routes = []
    for route in routes:
        try:
            network = ipaddress.ip_network(route.get('dst', ''), strict=False)
        except (ValueError, AttributeError):
            continue
        if network.version == 6 and str(network) != keep:
            run(['/sbin/ip', '-6', 'route', 'del', str(network), 'dev', 'fii-dummy'], required=False)


def check_capabilities(settings):
    from .proxy import modes, rules
    if "tun" in modes(settings).values():
        check(rooted("/dev/net/tun").exists(), "TUN 设备不可用，请安装与固件匹配的 kmod-tun")
    if settings.get("routerProxy") and settings.get("proxyEnabled", True) and settings["mode"] != "core":
        for rule in rules(settings, "router"):
            if rule["enabled"]:
                for group in rule.get("cgroups", []):
                    check(rooted("/sys/fs/cgroup/cgroup.controllers").exists() and rooted("/sys/fs/cgroup/" + group).is_dir(),
                          "CGroup 不存在或系统未启用 cgroup v2: " + group)


def firewall_down_unlocked():
    run(["/usr/sbin/nft", "delete", "table", "inet", "fii"], required=False)
    for family in ("-4", "-6"):
        run(["/sbin/ip", family, "rule", "del", "priority", str(ROUTE_PRIORITY),
             "fwmark", "0xf101/0xffff", "lookup", str(ROUTE_TABLE)], required=False)
        run(["/sbin/ip", family, "route", "del", "local", "default", "dev", "lo", "table", str(ROUTE_TABLE)], required=False)
        run(["/sbin/ip", family, "rule", "del", "priority", "30001", "fwmark", "0xf102/0xffff", "lookup", "20231"], required=False)
        run(["/sbin/ip", family, "route", "del", "default", "dev", "fii-tun", "table", "20231"], required=False)
    link = fake6_link()
    if link and link.get("ifalias") == "fii-fake-ip" and link.get("linkinfo", {}).get("info_kind") == "dummy":
        run(["/sbin/ip", "link", "del", "dev", "fii-dummy"], required=False)
    rooted("/tmp/fii/ready").unlink(missing_ok=True)


def firewall_down():
    with lock("firewall"):
        firewall_down_unlocked()


def firewall_up():
    with lock("firewall"):
        bundle = active()
        check(bundle is not None and ready(bundle), "内核未就绪，未接管流量")
        settings = {**defaults()["settings"], **bundle["settings"]}
        content = nftables(settings, bundle["yaml"])
        from .mihomo import dns_options
        dns_config = dns_options(bundle['yaml'])
        if not content:
            firewall_down_unlocked()
            atomic_write(rooted("/tmp/fii/ready"), "core\n")
            return
        from .proxy import modes
        check_capabilities(settings)
        protocols = modes(settings)
        families = (["-4"] if settings["ipv4Proxy"] else []) + (["-6"] if settings["ipv6"] else [])
        policies = []
        if "tproxy" in protocols.values():
            policies.append(("30000", "0xf101/0xffff", "20230", ["local", "default", "dev", "lo"]))
        if "tun" in protocols.values():
            deadline = time.monotonic() + settings["tunTimeout"]
            while True:
                try:
                    links = json.loads(run(["/sbin/ip", "-j", "link", "show", "dev", "fii-tun"], required=False) or "[]")
                except ValueError:
                    links = []
                if links and "UP" in links[0].get("flags", []):
                    break
                check(time.monotonic() < deadline, "等待 TUN 接口超时，请检查内核日志")
                time.sleep(settings["tunInterval"])
            # The kernel link becomes UP before Mihomo's system-stack TCP
            # listener finishes opening. Keep routing unchanged for one poll
            # interval on cold start; fw4 reloads need no such delay.
            if not rooted("/tmp/fii/ready").exists():
                time.sleep(settings["tunInterval"])
            policies.append(("30001", "0xf102/0xffff", "20231", ["default", "dev", "fii-tun"]))
        for family in families:
            for priority, mark, table, _ in policies:
                current_rules = run(["/sbin/ip", family, "rule", "show", "priority", priority])
                check(not current_rules or (mark in current_rules and table in current_rules), "策略路由优先级 " + priority + " 已被其他服务占用")
        existing = run(["/usr/sbin/nft", "list", "tables"])
        batch = ("delete table inet fii\n" if "table inet fii" in existing.splitlines() else "") + content
        path = rooted("/tmp/fii/firewall.nft")
        atomic_write(path, batch)
        run(["/usr/sbin/nft", "-c", "-f", str(path)])
        try:
            if settings["ipv6"] and dns_config['enabled'] and dns_config['mode'] == 'fake-ip':
                link = fake6_link()
                check(link is None or (link.get("ifalias") == "fii-fake-ip" and link.get("linkinfo", {}).get("info_kind") == "dummy"), "fii-dummy 接口已被其他服务占用")
                fake6 = dns_config['fake6']
                existing_route = run(["/sbin/ip", "-6", "route", "show", "exact", fake6])
                check(not existing_route or "dev fii-dummy" in existing_route, "Fake-IP IPv6 路由 " + fake6 + " 已被占用")
                if link is None:
                    run(["/sbin/ip", "link", "add", "name", "fii-dummy", "type", "dummy"])
                    try:
                        run(["/sbin/ip", "link", "set", "dev", "fii-dummy", "alias", "fii-fake-ip"])
                        check((fake6_link() or {}).get("ifalias") == "fii-fake-ip", "无法标记 FII 虚拟接口")
                    except ValueError:
                        # This invocation just created the interface, so it is
                        # safe to remove even if setting the alias failed.
                        run(["/sbin/ip", "link", "del", "dev", "fii-dummy"], required=False)
                        raise
                run(["/sbin/ip", "link", "set", "dev", "fii-dummy", "up"])
                remove_old_fake6_routes(fake6)
                run(["/sbin/ip", "-6", "route", "replace", fake6, "dev", "fii-dummy"])
            else:
                link = fake6_link()
                if link and link.get('ifalias') == 'fii-fake-ip' and link.get('linkinfo', {}).get('info_kind') == 'dummy':
                    run(['/sbin/ip', 'link', 'del', 'dev', 'fii-dummy'], required=False)
            for family in ("-4", "-6"):
                for priority, mark, table, route in (("30000", "0xf101/0xffff", "20230", ["local", "default", "dev", "lo"]),
                                                     ("30001", "0xf102/0xffff", "20231", ["default", "dev", "fii-tun"])):
                    if family not in families or not any(p[0] == priority for p in policies):
                        run(["/sbin/ip", family, "rule", "del", "priority", priority, "fwmark", mark, "lookup", table], required=False)
                        run(["/sbin/ip", family, "route", "del"] + route + ["table", table], required=False)
            for family in families:
                for priority, mark, table, route in policies:
                    run(["/sbin/ip", family, "route", "replace"] + route + ["table", table])
                    current_rules = run(["/sbin/ip", family, "rule", "show", "priority", priority])
                    if not current_rules:
                        run(["/sbin/ip", family, "rule", "add", "priority", priority, "fwmark", mark, "lookup", table])
            run(["/usr/sbin/nft", "-f", str(path)])
            atomic_write(rooted("/tmp/fii/ready"), "transparent\n")
        except ValueError:
            firewall_down_unlocked()
            raise


def restart_and_wait(bundle):
    run([INIT, "restart"], timeout=15)
    deadline = time.monotonic() + 18 + bundle["settings"].get("tunTimeout", 30)
    while time.monotonic() < deadline:
        if rooted("/tmp/fii/ready").exists() and ready(bundle):
            return
        time.sleep(0.4)
    raise ValueError("Mihomo 启动或透明代理准备失败，请查看运行日志")


def service_active():
    """Read procd's intent, including a managed instance awaiting respawn.

    A stopped service has no instances. Fail before changing the snapshot if
    procd cannot be queried, rather than guessing and changing service state.
    """
    try:
        raw = run(['/bin/ubus', 'call', 'service', 'list', '{"name":"fii"}'], timeout=5)
        services = json.loads(raw)
        check(isinstance(services, dict), '服务状态响应无效')
        service = services.get('fii', {})
        check(isinstance(service, dict), '服务状态响应无效')
        instances = service.get('instances', {})
        check(isinstance(instances, dict), '服务实例状态无效')
        check(all(isinstance(value, dict) for value in instances.values()), '服务实例状态无效')
        return bool(instances)
    except (ValueError, TypeError):
        raise ValueError('无法确认 FII 服务状态，未更新运行配置；请稍后重试') from None


def activate(output_id):
    with lock("operation", timeout=1):
        with lock():
            data = Store().read()
        rendered = render(data, output_id)
        check(rendered["nodeCount"] > 0, "没有可用节点，保留正在运行的配置；请检查订阅流量、缓存或筛选条件")
        body = overlay(rendered["yaml"], data["settings"], warnings=rendered["warnings"])
        check_capabilities(data["settings"])
        core_test(body)
        previous = active()
        was_active = service_active()
        bundle = {"outputId": output_id, "name": rendered["name"], "settings": copy.deepcopy(data["settings"]),
                  "yaml": body, "appliedAt": int(time.time()), "revision": data["revision"]}
        if previous:
            write_json(rooted("/etc/fii/active.previous.json"), previous)
        write_json(rooted("/etc/fii/active.json"), bundle)
        try:
            if was_active:
                restart_and_wait(bundle)
        except ValueError as exc:
            if previous:
                write_json(rooted("/etc/fii/active.json"), previous)
                try:
                    restart_and_wait(previous)
                    rollback = "已恢复上一次运行配置"
                except ValueError:
                    run([INIT, "stop"], required=False)
                    rollback = "恢复失败，已停止服务并清理接管；请查看日志"
            else:
                rooted("/etc/fii/active.json").unlink(missing_ok=True)
                run([INIT, "stop"], required=False)
                rollback = "已停止服务并清理接管"
            raise ValueError(str(exc) + "；" + rollback) from None
        run([INIT, "enable" if data["settings"]["autostart"] else "disable"])
        return {"message": "运行配置已更新，服务已重启" if was_active else "运行配置已更新，内核保持停止",
                "warnings": rendered["warnings"], "restarted": was_active}


def status():
    from .core import local_info
    from .proxy import modes
    bundle = active()
    core = local_info()
    return {"coreInstalled": core["installed"], "coreVersion": core["versionText"],
            "coreChannel": core["channel"], "coreSelectedChannel": core["selectedChannel"],
            "running": rooted("/tmp/fii/ready").exists() and ready(bundle),
            "activeOutputId": bundle["outputId"] if bundle else "",
            "activeName": bundle["name"] if bundle else "",
            "appliedAt": bundle.get("appliedAt") if bundle else None,
            "appliedRevision": bundle.get("revision") if bundle else None,
            "mode": bundle["settings"]["mode"] if bundle else "",
            "proxyEnabled": bundle["settings"].get("proxyEnabled", True) if bundle else False,
            "protocols": modes(bundle["settings"]) if bundle else {},
            "lanProxy": bundle["settings"].get("lanProxy", True) if bundle else False,
            "routerProxy": bundle["settings"].get("routerProxy", False) if bundle else False,
            "interfaces": bundle["settings"]["interfaces"] if bundle else []}
