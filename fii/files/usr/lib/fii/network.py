"""Read-only network choices for the router settings form."""
import json
import re

from .runtime import run
from .store import rooted


def choices():
    def query(args):
        try:
            return json.loads(run(args, required=False, timeout=5) or "{}")
        except ValueError:
            return {}

    networks = query(["/bin/ubus", "call", "network.interface", "dump"])
    links = query(["/sbin/ip", "-j", "link", "show"])
    devices = {}
    for link in links if isinstance(links, list) else []:
        name = link.get("ifname", "")
        if name not in ("lo", "fii-tun", "fii-dummy") and re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name):
            devices[name] = {"name": name, "networks": [], "up": "UP" in link.get("flags", [])}
    for network in networks.get("interface", []) if isinstance(networks, dict) else []:
        name = network.get("l3_device") or network.get("device", "")
        if name in ("lo", "fii-tun", "fii-dummy") or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name):
            continue
        device = devices.setdefault(name, {"name": name, "networks": [], "up": bool(network.get("up"))})
        device["networks"].append(network.get("interface", name))
    hosts, users, groups, cgroups = [], [], [], []
    leases = rooted("/tmp/dhcp.leases")
    if leases.exists():
        for line in leases.read_text(encoding="utf-8", errors="replace").splitlines()[:512]:
            parts = line.split()
            if len(parts) >= 4:
                hosts.append({"mac": parts[1], "ip": parts[2], "name": "" if parts[3] == "*" else parts[3]})
    passwd = rooted("/etc/passwd")
    if passwd.exists():
        for line in passwd.read_text(encoding="utf-8", errors="replace").splitlines()[:256]:
            parts = line.split(":")
            if len(parts) > 2 and parts[2].isdigit():
                users.append({"id": parts[2], "name": parts[0]})
    group_file = rooted("/etc/group")
    if group_file.exists():
        for line in group_file.read_text(encoding="utf-8", errors="replace").splitlines()[:256]:
            parts = line.split(":")
            if len(parts) > 2 and parts[2].isdigit():
                groups.append({"id": parts[2], "name": parts[0]})
    cgroup_root = rooted("/sys/fs/cgroup")
    if (cgroup_root / "cgroup.controllers").exists():
        pending = [(cgroup_root, 0)]
        while pending and len(cgroups) < 512:
            parent, depth = pending.pop()
            if depth >= 6:
                continue
            try:
                for child in sorted(parent.iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        name = child.relative_to(cgroup_root).as_posix()
                        if all(re.fullmatch(r"[A-Za-z0-9_:@.-]{1,64}", p) for p in name.split("/")):
                            cgroups.append({"id": name, "name": name})
                            pending.append((child, depth + 1))
            except OSError:
                pass
    return {"devices": sorted(devices.values(), key=lambda d: (not bool(d["networks"]), d["name"])),
            "hosts": hosts, "users": users, "groups": groups, "cgroups": cgroups,
            "cgroupV2": (cgroup_root / "cgroup.controllers").exists()}
