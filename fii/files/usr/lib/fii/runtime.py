"""OpenWrt runtime generation and transactional activation."""
import copy
import json
import os
import subprocess
import time
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from .model import check, defaults
from .render import dump, load_map, render
from .store import Store, atomic_write, lock, rooted, write_json

CORE = "/etc/fii/bin/mihomo"
INIT = "/etc/init.d/fii"
BYPASS_MARK = 0xF100
PROXY_MARK = 0xF101
ROUTE_TABLE = 20230
ROUTE_PRIORITY = 30000


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


def overlay(body, settings):
    settings = {**defaults()["settings"], **settings}
    from .proxy import modes
    protocols = modes(settings)
    tun = "tun" in protocols.values()
    root = load_map(body)
    # These are owned by the router integration. User overrides cannot desynchronise routing.
    for key in ("port", "socks-port", "listeners", "external-controller-tls", "external-controller-unix",
                "external-controller-pipe", "external-ui", "external-ui-url", "external-ui-name",
                "authentication", "lan-allowed-ips", "lan-disallowed-ips"):
        root.pop(key, None)
    root.update({"mixed-port": 0, "redir-port": settings["redirPort"] if protocols["tcp"] == "redirect" else 0,
                 "tproxy-port": settings["tproxyPort"] if "tproxy" in protocols.values() else 0, "allow-lan": True, "bind-address": "*",
                 "listeners": [{"name": "fii-mixed", "type": "mixed", "listen": "127.0.0.1", "port": settings["mixedPort"]}],
                 "external-controller": "127.0.0.1:%d" % settings["controllerPort"],
                 "secret": settings["secret"], "ipv6": settings["ipv6"],
                 "routing-mark": BYPASS_MARK, "tun": {"enable": tun, "device": "fii-tun", "stack": settings["tunStack"],
                 "mtu": settings["tunMtu"], "auto-route": False, "auto-redirect": False,
                 "auto-detect-interface": False, "dns-hijack": [], "inet6-address": ["fdfe:dcba:9876::1/126"] if settings["ipv6"] else []}})
    dns = root.get("dns", {})
    check(isinstance(dns, dict), "dns 覆写必须是对象")
    from .proxy_nft import dns_families
    dns.update({"enable": True, "listen": "[::]:%d" % settings["dnsPort"] if settings["ipv6"] or "ipv6" in dns_families(settings) else "0.0.0.0:%d" % settings["dnsPort"],
                "ipv6": settings["ipv6"], "enhanced-mode": settings["dnsMode"], "fake-ip-range": "198.18.0.1/16", "fake-ip-range6": "fc00::/18",
                "default-nameserver": settings["defaultNameservers"],
                "proxy-server-nameserver": settings["proxyNameservers"] or settings["nameservers"], "nameserver": settings["nameservers"]})
    if settings["mode"] == "core":
        dns["listen"] = ("[::1]:%d" if settings["ipv6"] else "127.0.0.1:%d") % settings["dnsPort"]
    dns["fake-ip-filter"] = settings["fakeIpFilter"]
    policy = dns.setdefault("nameserver-policy", {})
    check(isinstance(policy, dict), "dns.nameserver-policy 必须是对象")
    for domain in ("+.lan", "+.local"):
        policy.setdefault(domain, [settings["localDns"]])
    root["dns"] = dns
    root["profile"] = {"store-selected": True, "store-fake-ip": True}
    return dump(root)


def nftables(settings):
    from .proxy_nft import generate
    return generate(settings)


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


def controller(path, payload=None):
    bundle = active()
    check(bundle is not None and ready(bundle), "Mihomo 尚未运行")
    settings = bundle["settings"]
    content = json.dumps(payload).encode() if payload is not None else None
    req = Request("http://127.0.0.1:%d%s" % (settings["controllerPort"], path), data=content,
                  headers={"Authorization": "Bearer " + settings["secret"], "Content-Type": "application/json"},
                  method="PUT" if payload is not None else "GET")
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=4) as response:
            body = response.read(2 * 1024 * 1024)
            return json.loads(body) if body else {}
    except (OSError, ValueError):
        raise ValueError("Mihomo 控制器请求失败，请检查服务状态") from None


def groups():
    proxies = controller("/proxies").get("proxies", {})
    return {"groups": [{"name": key, "type": value.get("type"), "now": value.get("now", ""),
                         "all": value.get("all", [])} for key, value in proxies.items()
                        if "all" in value and key != "GLOBAL"]}


def select_proxy(group, name):
    check(isinstance(group, str) and isinstance(name, str), "代理组和节点名称必须是文本")
    available = next((g for g in groups()["groups"] if g["name"] == group), None)
    check(available is not None and available["type"] == "Selector" and name in available["all"], "代理组不能手动切换或节点不存在")
    controller("/proxies/" + quote(group, safe=""), {"name": name})
    return {"message": "已切换到 " + name}


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
    finally:
        candidate.unlink(missing_ok=True)


def prepare():
    bundle = active()
    check(bundle is not None, "请先在输出页应用一个配置")
    core_test(bundle["yaml"])
    atomic_write(rooted("/tmp/fii/config.yaml"), bundle["yaml"])
    atomic_write(rooted("/tmp/fii/firewall.nft"), nftables(bundle["settings"]))


def fake6_link():
    try:
        links = json.loads(run(["/sbin/ip", "-j", "-d", "link", "show", "dev", "fii-dummy"], required=False) or "[]")
        return links[0] if links else None
    except (ValueError, IndexError):
        return None


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
        content = nftables(settings)
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
            if settings["ipv6"] and settings["dnsMode"] == "fake-ip":
                link = fake6_link()
                check(link is None or (link.get("ifalias") == "fii-fake-ip" and link.get("linkinfo", {}).get("info_kind") == "dummy"), "fii-dummy 接口已被其他服务占用")
                existing_route = run(["/sbin/ip", "-6", "route", "show", "exact", "fc00::/18"])
                check(not existing_route or "dev fii-dummy" in existing_route, "Fake-IP IPv6 路由 fc00::/18 已被占用")
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
                run(["/sbin/ip", "-6", "route", "replace", "fc00::/18", "dev", "fii-dummy"])
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


def activate(output_id):
    with lock("operation", timeout=1):
        with lock():
            data = Store().read()
        rendered = render(data, output_id)
        check(rendered["nodeCount"] > 0, "没有可用节点，保留正在运行的配置；请检查订阅流量、缓存或筛选条件")
        body = overlay(rendered["yaml"], data["settings"])
        check_capabilities(data["settings"])
        core_test(body)
        previous = active()
        bundle = {"outputId": output_id, "name": rendered["name"], "settings": copy.deepcopy(data["settings"]),
                  "yaml": body, "appliedAt": int(time.time()), "revision": data["revision"]}
        if previous:
            write_json(rooted("/etc/fii/active.previous.json"), previous)
        write_json(rooted("/etc/fii/active.json"), bundle)
        try:
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
        return {"message": "配置已应用", "warnings": rendered["warnings"]}


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
