#!/usr/bin/env python3
"""Run inside the disposable QEMU OpenWrt VM, never on a production router.

Uses dedicated network namespaces and restores the configuration after testing.
Copy this file to /tmp and run `python3 /tmp/verify_router_settings_vm.py`.
"""
import copy
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time


def server():
    def listen(kind, port):
        sock = socket.socket(socket.AF_INET6, kind)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::", port))
        if kind == socket.SOCK_STREAM:
            sock.listen(16)
            while True:
                conn, _ = sock.accept()
                with conn:
                    conn.recv(4096)
                    conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 6\r\n\r\nfii-ok")
        else:
            while True:
                data, peer = sock.recvfrom(4096)
                sock.sendto(data, peer)
    threading.Thread(target=listen, args=(socket.SOCK_STREAM, 18080), daemon=True).start()
    listen(socket.SOCK_DGRAM, 18081)


def probe(kind, host):
    if os.environ.get("FII_TEST_CGROUP"):
        Path("/sys/fs/cgroup/" + os.environ["FII_TEST_CGROUP"] + "/cgroup.procs").write_text(str(os.getpid()))
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    tcp = kind in ("tcp", "dns-tcp")
    sock = socket.socket(family, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
    sock.settimeout(float(os.environ.get("FII_TEST_TIMEOUT", "1")))
    if os.environ.get("FII_TEST_DSCP"):
        level, option = (socket.IPPROTO_IP, socket.IP_TOS) if family == socket.AF_INET else (socket.IPPROTO_IPV6, socket.IPV6_TCLASS)
        sock.setsockopt(level, option, int(os.environ["FII_TEST_DSCP"]) << 2)
    if os.environ.get("FII_TEST_MARK"):
        sock.setsockopt(socket.SOL_SOCKET, 36, int(os.environ["FII_TEST_MARK"], 0))
    try:
        port = 53 if kind.startswith("dns") else 18080 if tcp else 18081
        sock.connect((host, port))
        if kind.startswith("dns"):
            query = struct.pack("!6H", 0xF123, 0x100, 1, 0, 0, 0) + b"\x10fii-verification\x07example\0\0\1\0\1"
            sock.sendall(struct.pack("!H", len(query)) + query if tcp else query)
            data = sock.recv(4096)
            if tcp:
                data = data[2:]
            ok = len(data) >= 12 and data[:2] == b"\xf1\x23" and struct.unpack("!H", data[6:8])[0] > 0
        else:
            sock.sendall(b"GET / HTTP/1.0\r\n\r\n" if tcp else b"fii-udp-ok")
            data = sock.recv(4096)
            ok = b"fii-ok" in data if tcp else data == b"fii-udp-ok"
    except OSError:
        ok = False
    finally:
        sock.close()
    sys.exit(0 if ok else 2)


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--server":
            server()
        else:
            probe(sys.argv[2], sys.argv[3])
        return
    if not Path("/etc/openwrt_release").exists() or os.environ.get("FII_VM_TEST") != "1":
        raise SystemExit("Run only in the disposable VM with FII_VM_TEST=1")
    sys.path.insert(0, "/usr/lib")
    from fii.model import defaults
    from fii.runtime import activate
    from fii.store import Store
    script = str(Path(__file__).resolve())
    results = []
    original_state = Path("/etc/fii/state.json").read_text()
    original_active = Path("/etc/fii/active.json").read_text()
    previous_path = Path("/etc/fii/active.previous.json")
    original_previous = previous_path.read_text() if previous_path.exists() else None
    original_boot = Path("/etc/rc.d/S99fii").exists()
    process = None
    trace_process = None

    def run(*args, required=True):
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if required and result.returncode:
            raise RuntimeError(str(args) + ": " + result.stderr)
        return result

    def cleanup_network():
        for dev in ("fii-lan", "fii-wan"):
            run("ip", "link", "del", dev, required=False)
        for ns in ("fii-client", "fii-server"):
            run("ip", "netns", "del", ns, required=False)

    def allow_test_interfaces():
        for chain in ("input", "forward"):
            for dev in ("fii-lan", "fii-wan"):
                run("nft", "insert", "rule", "inet", "fw4", chain, "iifname", dev, "accept", "comment", '"fii VM test"')

    def configure(**changes):
        data = copy.deepcopy(base)
        if changes.pop("direct", False):
            data["overrides"][0]["body"] = "rules!: ['MATCH,DIRECT']\n"
        data["settings"].update(changes)
        Store().save(data, Store().read()["revision"])
        activate("vm-settings-test")

    def expect(name, kind="tcp", client=False, success=True, ipv6=False, env=None, host=None):
        cmd = ["python3", script, "--probe", kind, host or ("2001:db8:230::2" if ipv6 else "198.19.0.2")]
        env = {"FII_TEST_TIMEOUT": "3" if success else "1", **(env or {})}
        cmd = ["env"] + [key + "=" + value for key, value in env.items()] + cmd
        if client:
            cmd = ["ip", "netns", "exec", "fii-client"] + cmd
        actual = run(*cmd, required=False).returncode == 0
        results.append({"name": name, "passed": actual == success})
        print(json.dumps(results[-1]), flush=True)
        if actual != success:
            diagnostics = {}
            for key, cmd in (("logs", ["logread", "-e", "core-run"]), ("nft", ["nft", "list", "table", "inet", "fii"]),
                             ("route4", ["ip", "-4", "route", "show", "table", "all"]), ("rules4", ["ip", "-4", "rule", "show"]),
                             ("tun", ["ip", "-d", "addr", "show", "dev", "fii-tun"])):
                diagnostics[key] = run(*cmd, required=False).stdout
            Path("/tmp/fii-router-settings-failure.json").write_text(json.dumps(diagnostics, indent=2))
            raise AssertionError(name)

    try:
        cleanup_network()
        for ns in ("fii-client", "fii-server"):
            run("ip", "netns", "add", ns)
            run("ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up")
        for side, peer, ns, addr4, addr6, client4, client6 in (
            ("fii-lan", "fii-c", "fii-client", "192.168.230.1/24", "fd00:230::1/64", "192.168.230.2/24", "fd00:230::2/64"),
            ("fii-wan", "fii-s", "fii-server", "198.19.0.1/24", "2001:db8:230::1/64", "198.19.0.2/24", "2001:db8:230::2/64")):
            run("ip", "link", "add", side, "type", "veth", "peer", "name", peer)
            run("ip", "link", "set", peer, "netns", ns)
            for addr in (addr4, addr6):
                run("ip", "addr", "add", addr, "dev", side)
            run("ip", "link", "set", side, "up")
            for addr in (client4, client6):
                run("ip", "netns", "exec", ns, "ip", "addr", "add", addr, "dev", peer)
            run("ip", "netns", "exec", ns, "ip", "link", "set", peer, "up")
            run("ip", "netns", "exec", ns, "ip", "route", "add", "default", "via", addr4.split("/")[0])
            run("ip", "netns", "exec", ns, "ip", "-6", "route", "add", "default", "via", addr6.split("/")[0])
        allow_test_interfaces()
        process = subprocess.Popen(["ip", "netns", "exec", "fii-server", "python3", script, "--server"])
        time.sleep(2)
        base = defaults()
        base["settings"].update(interfaces=["fii-lan"], routerProxy=True, dnsMode="fake-ip")
        base["settings"]["reservedIpv6"].remove("2001:db8::/32")
        base["sources"] = [{"id": "vm-source", "name": "VM fixture", "cachedYaml": "proxies:\n - {name: Test, type: ss, server: 127.0.0.1, port: 9, cipher: aes-128-gcm, password: test}\n"}]
        base["overrides"] = [{"id": "vm-rules", "name": "VM rejection proof", "type": "YAML", "body": "rules!:\n - DST-PORT,18080,REJECT\n - DST-PORT,18081,REJECT\n - MATCH,DIRECT\n"}]
        base["outputs"] = [{"id": "vm-settings-test", "name": "VM settings test", "sourceIds": ["vm-source"], "overrideIds": ["vm-rules"]}]
        if os.environ.get("FII_VM_TEST_CASE") == "tun-positive":
            configure(tcpMode="tun", udpMode="tun", ipv6=True, direct=True, tunStack=os.environ.get("FII_TUN_STACK", "mixed"))
            trace = open("/tmp/fii-tun-trace.txt", "w")
            trace_process = subprocess.Popen(["tcpdump", "-l", "-n", "-i", "any", "tcp", "and", "not", "port", "22"], stdout=trace, stderr=trace)
            time.sleep(0.2)
            for client in (False, True):
                for ipv6 in (False, True):
                    for kind in ("tcp", "udp"):
                        expect("TUN positive %s %s %s" % (client, ipv6, kind), kind, client, ipv6=ipv6)
            return
        configure(mode="core")
        for client in (False, True):
            for kind in ("tcp", "udp"):
                expect("baseline %s %s" % (client, kind), kind, client)
        configure()
        for client in (False, True):
            for kind in ("tcp", "udp"):
                expect("TPROXY interception %s %s" % (client, kind), kind, client, success=False)
            for kind in ("dns", "dns-tcp"):
                expect("DNS hijack %s %s" % (client, kind), kind, client)
        configure(lanAccessMode="exclude", lanAccess=["192.168.230.2"], routerBypassUsers=["0"])
        for client in (False, True):
            for kind in ("tcp", "udp"):
                expect("ACL bypass %s %s" % (client, kind), kind, client)
        expect("excluded device DNS untouched", "dns", True, success=False)
        configure(lanAccessMode="include", lanAccess=["192.168.230.3"])
        expect("include unmatched bypass", client=True)
        mac = run("ip", "netns", "exec", "fii-client", "cat", "/sys/class/net/fii-c/address").stdout.strip()
        configure(lanAccessMode="include", lanAccess=[mac])
        expect("include MAC matched", client=True, success=False)
        expect("include MAC DNS matched", "dns", True)
        configure(mode="redirect")
        for client in (False, True):
            for kind in ("tcp", "udp"):
                expect("REDIRECT interception %s %s" % (client, kind), kind, client, success=False)
        configure(udpProxy=False)
        for client in (False, True):
            expect("UDP disabled %s" % client, "udp", client)
            expect("TCP retained %s" % client, "tcp", client, success=False)
        configure(lanProxy=False, interfaces=[])
        expect("LAN disabled bypass", client=True)
        expect("router independently enabled", success=False)
        configure(ipv6=True)
        for client in (False, True):
            for kind in ("tcp", "udp"):
                expect("IPv6 interception %s %s" % (client, kind), kind, client, success=False, ipv6=True)
            expect("IPv6 DNS %s" % client, "dns", client, ipv6=True)
        configure(ipv6=True, dnsIpv6Hijack=False)
        expect("IPv6 DNS disabled", "dns", True, success=False, ipv6=True)
        expect("IPv4 DNS retained", "dns", True)
        for tcp, udp in (("tun", "tun"), ("redirect", "tun"), ("tun", "tproxy")):
            configure(tcpMode=tcp, udpMode=udp, ipv6=True)
            for client in (False, True):
                for kind in ("tcp", "udp"):
                    expect("%s/%s interception %s %s" % (tcp, udp, client, kind), kind, client, success=False)
                    expect("%s/%s IPv6 interception %s %s" % (tcp, udp, client, kind), kind, client, success=False, ipv6=True)
                expect("TUN DNS %s/%s %s" % (tcp, udp, client), "dns", client)
            configure(tcpMode=tcp, udpMode=udp, ipv6=True, direct=True)
            for client in (False, True):
                for ipv6 in (False, True):
                    for kind in ("tcp", "udp"):
                        expect("%s/%s positive forwarding %s %s %s" % (tcp, udp, client, ipv6, kind), kind, client, ipv6=ipv6)
        configure(tcpMode="off", udpMode="tproxy")
        expect("TCP independently disabled", client=True)
        expect("UDP independently enabled", "udp", True, success=False)
        configure(ipv4Proxy=False, ipv6=True, dnsFamilyIndependent=True, dnsIpv4Hijack=True, dnsIpv6Hijack=False)
        expect("IPv4 proxy disabled", client=True)
        expect("IPv6 proxy retained", client=True, success=False, ipv6=True)
        expect("IPv4 DNS independent of proxy", "dns", True)
        configure(ipv6=False, dnsFamilyIndependent=True, dnsIpv6Hijack=True)
        expect("IPv6 DNS independent of proxy", "dns", True, ipv6=True)
        expect("IPv6 traffic bypass with DNS enabled", client=True, ipv6=True)
        configure(tcpPorts="80 443", udpPorts="123")
        expect("TCP port restriction bypass", client=True)
        expect("UDP port restriction bypass", "udp", True)
        expect("DNS survives port restriction", "dns", True)
        configure(ipv6=True, reservedIpv4=["198.19.0.0/24"], reservedIpv6=["2001:db8:230::/64"])
        expect("custom IPv4 reserved address", client=True)
        expect("custom IPv6 reserved address", client=True, ipv6=True)
        run("ip", "netns", "exec", "fii-server", "ip", "addr", "add", "198.18.20.2/32", "dev", "fii-s")
        run("ip", "route", "add", "198.18.20.2/32", "via", "198.19.0.2")
        configure(mode="core")
        expect("Fake-IP endpoint baseline", host="198.18.20.2")
        configure(tcpPorts="80 443", bypassDscp=["4"], reservedIpv4=["198.18.0.0/16"])
        expect("Fake-IP overrides ports DSCP and reserved ranges", host="198.18.20.2", success=False, env={"FII_TEST_DSCP":"4"})
        configure(bypassDscp=["4"], ipv6=True)
        for client in (False, True):
            for ipv6 in (False, True):
                expect("DSCP bypass %s %s" % (client, ipv6), client=client, ipv6=ipv6, env={"FII_TEST_DSCP":"4"})
                expect("DSCP unmatched interception %s %s" % (client, ipv6), client=client, ipv6=ipv6, success=False)
        configure(bypassMarks=["0x100/0xff00"])
        expect("FWMark bypass", env={"FII_TEST_MARK":"0x100"})
        expect("FWMark unmatched interception", env={"FII_TEST_MARK":"0x200"}, success=False)
        allow = {"enabled": True, "proxy": True, "dns": True}
        configure(lanRules=[{**allow, "ips":["192.168.230.2"], "proxy":False}, allow])
        expect("ordered LAN proxy disabled DNS enabled", client=True)
        expect("ordered LAN DNS independently enabled", "dns", True)
        configure(lanRules=[{**allow, "ips":["192.168.230.2"], "dns":False}, allow])
        expect("ordered LAN proxy independently enabled", client=True, success=False)
        expect("ordered LAN DNS disabled", "dns", True, success=False)
        configure(lanRules=[{**allow, "enabled":False, "proxy":False}, allow])
        expect("disabled ACL skipped", client=True, success=False)
        configure(lanRules=[allow, {**allow, "proxy":False}])
        expect("first matching rule wins", client=True, success=False)
        configure(routerRules=[{**allow, "groups":["0"], "proxy":False}, allow])
        expect("router group bypass")
        expect("router group DNS independently enabled", "dns")
        cg = Path("/sys/fs/cgroup/services/fii-vm-test")
        cg.mkdir(exist_ok=True)
        configure(routerRules=[{**allow, "cgroups":["services/fii-vm-test"], "proxy":False, "dns":False}, allow])
        expect("router cgroup bypass", env={"FII_TEST_CGROUP":"services/fii-vm-test"})
        expect("router other cgroup intercepted", success=False)
        cg.rmdir()
        import ipaddress
        cn6 = next(line for line in Path("/usr/share/fii/china-ipv6.txt").read_text().splitlines() if line and not line.startswith("#"))
        cn6host = str(ipaddress.ip_network(cn6).network_address + 2)
        for addr in ("1.0.1.2/32", cn6host + "/128"):
            run("ip", "netns", "exec", "fii-server", "ip", "addr", "add", addr, "dev", "fii-s")
        run("ip", "route", "add", "1.0.1.2/32", "via", "198.19.0.2")
        run("ip", "-6", "route", "add", cn6host + "/128", "via", "2001:db8:230::2")
        configure(ipv6=True)
        expect("mainland IPv4 baseline intercepted", host="1.0.1.2", success=False)
        expect("mainland IPv6 baseline intercepted", host=cn6host, success=False)
        configure(ipv6=True, bypassChina4=True, bypassChina6=True)
        expect("mainland IPv4 bypass", host="1.0.1.2")
        expect("mainland IPv6 bypass", host=cn6host)
        configure(fakeIpPing=True, ipv6=True)
        for host in ("198.18.10.10", "fc00::1010"):
            check = run("ping", "-c", "1", "-W", "2", host, required=False).returncode == 0
            results.append({"name":"Fake-IP ping " + host, "passed":check})
            print(json.dumps(results[-1]), flush=True)
            assert check
        configure()
        run("/etc/init.d/firewall", "reload")
        allow_test_interfaces()
        time.sleep(1)
        run("nft", "list", "table", "inet", "fii")
        expect("fw4 reload restores interception", client=True, success=False)
        run("/etc/init.d/fii", "stop")
        time.sleep(1)
        assert run("nft", "list", "table", "inet", "fii", required=False).returncode != 0
        expect("stop restores LAN path", client=True)
        expect("stop restores router path")
    finally:
        if trace_process:
            trace_process.terminate()
            trace_process.wait(timeout=5)
        run("/etc/init.d/fii", "stop", required=False)
        if process:
            process.terminate()
            process.wait(timeout=5)
        cleanup_network()
        cg = Path("/sys/fs/cgroup/services/fii-vm-test")
        if cg.exists():
            cg.rmdir()
        Path("/etc/fii/state.json").write_text(original_state)
        Path("/etc/fii/active.json").write_text(original_active)
        if original_previous is None:
            previous_path.unlink(missing_ok=True)
        else:
            previous_path.write_text(original_previous)
        run("/etc/init.d/fii", "enable" if original_boot else "disable")
        run("/etc/init.d/firewall", "reload", required=False)
        Path("/tmp/fii-router-settings-results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("Passed %d packet-path checks; test configuration restored." % len(results))


if __name__ == "__main__":
    main()
