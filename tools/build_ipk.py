#!/usr/bin/env python3
"""Build application-only OpenWrt 24.10 IPKs. Core downloads belong to the LuCI manager."""
import gzip
import hashlib
import io
import json
import re
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.1-56"
EPOCH = 1792339200  # 2026-10-19 00:00 UTC, deterministic archive timestamps.


def tar_gz(entries):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=EPOCH, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
            seen_dirs = set()
            for name, content, mode in sorted(entries):
                # opkg extracts via tar and needs an explicit member for every
                # parent directory, otherwise it fails with wfopen errors.
                parts = name.split("/")
                for i in range(len(parts) - 1):
                    dir_name = "./" + "/".join(parts[:i + 1])
                    if dir_name not in seen_dirs:
                        seen_dirs.add(dir_name)
                        member = tarfile.TarInfo(dir_name)
                        member.type = tarfile.DIRTYPE
                        member.mode = 0o755
                        member.mtime = EPOCH
                        member.uid = member.gid = 0
                        member.uname = member.gname = "root"
                        archive.addfile(member)
                member = tarfile.TarInfo("./" + name)
                member.size = len(content)
                member.mode = mode
                member.mtime = EPOCH
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                archive.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def collect(directory, prefix=""):
    result = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        name = prefix + path.relative_to(directory).as_posix()
        data = path.read_bytes().replace(b"\r\n", b"\n")
        executable = data.startswith(b"#!") and not name.startswith("usr/lib/fii/")
        result.append((name, data, 0o755 if executable else 0o644))
    return result


def build(name, entries, depends, arch="all", version=VERSION, controls=None):
    data_archive = tar_gz(entries)
    # Match OpenWrt 24.10 scripts/ipkg-build: size of the uncompressed data tar in bytes.
    installed_size = len(gzip.decompress(data_archive))
    control = "Package: %s\nVersion: %s\nArchitecture: %s\nMaintainer: FII contributors\nSection: net\nPriority: optional\nLicense: GPL-3.0-or-later\nInstalled-Size: %d\nDepends: %s\nDescription: FII simple Mihomo manager\n" % (name, version, arch, installed_size, depends)
    control_entries = [("control", control.encode(), 0o644)] + (controls or [])
    content = tar_gz([("debian-binary", b"2.0\n", 0o644),
                      ("control.tar.gz", tar_gz(control_entries), 0o644),
                      ("data.tar.gz", data_archive, 0o644)])
    path = ROOT / "dist" / (name + "_" + version + "_" + arch + ".ipk")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return {"file": path.name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            "architecture": arch, "version": version}


def main():
    # The reproducible tar mtime is not a cache key. Keep the shipped source
    # content-addressed so upgrades and same-version rebuilds get fresh CSS.
    resources = ROOT / 'luci-app-fii/htdocs/luci-static/resources'
    style_hash = hashlib.sha256((resources / 'fii/style.css').read_bytes().replace(b'\r\n', b'\n')).hexdigest()[:16]
    view_path = resources / 'view/fii/app.js'
    source = view_path.read_bytes().replace(b'\r\n', b'\n')
    updated, count = re.subn(rb"var fiiStyleHash = '[0-9a-f]{16}';", b"var fiiStyleHash = '" + style_hash.encode() + b"';", source)
    assert count == 1, 'Missing or duplicate UI style hash marker'
    if updated != source:
        view_path.write_bytes(updated)
    # LuCI fetches views through XHR with its own resource version. Give FII's
    # menu action a content version so an existing browser cache cannot keep
    # serving an older app.js after an application-only update.
    menu_path = ROOT / 'luci-app-fii/root/usr/share/luci/menu.d/luci-app-fii.json'
    menu = json.loads(menu_path.read_text(encoding='utf-8'))
    menu['admin/services/fii']['action']['version'] = hashlib.sha256(updated).hexdigest()[:16]
    menu_path.write_text(json.dumps(menu, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    postinst = b'''#!/bin/sh
[ -n "$IPKG_INSTROOT" ] && exit 0
/bin/sh /etc/uci-defaults/90-fii && rm -f /etc/uci-defaults/90-fii
# prerm also runs on upgrades; restore a service that was running before it.
if [ -f /tmp/fii-was-running ]; then
    rm -f /tmp/fii-was-running
    /etc/init.d/fii start || true
fi
# reload keeps LuCI sessions; some firmwares only pick up ucode plugins on restart.
/etc/init.d/rpcd reload
# Give rpcd a moment to register the object before deciding on a restart.
sleep 2
ubus -S list luci.fii >/dev/null 2>&1 || /etc/init.d/rpcd restart || true
exit 0
'''
    prerm = b'''#!/bin/sh
[ -n "$IPKG_INSTROOT" ] && exit 0
[ -e /tmp/fii/ready ] && touch /tmp/fii-was-running
/etc/init.d/fii stop
/etc/init.d/fii disable
sed -i '/# fii-refresh$/d' /etc/crontabs/root
/etc/init.d/cron reload
exit 0
'''
    postrm = b'''#!/bin/sh
[ -n "$IPKG_INSTROOT" ] && exit 0
uci -q delete firewall.fii
uci commit firewall
/etc/init.d/rpcd reload
exit 0
'''
    manifest = [build("fii", collect(ROOT / "fii/files"),
                      "python3, python3-yaml, node, ucode, ucode-mod-fs, ucode-mod-ubus, ucode-mod-uloop, rpcd-mod-ucode, rpcd-mod-file, uclient-fetch, ca-bundle, firewall4, ip-full, kmod-nft-tproxy, kmod-nft-socket, kmod-tun, kmod-dummy",
                      controls=[("postinst", postinst, 0o755), ("prerm", prerm, 0o755), ("postrm", postrm, 0o755)])]
    luci_entries = collect(ROOT / "luci-app-fii/root") + collect(ROOT / "luci-app-fii/htdocs", "www/")
    luci_postinst = b'''#!/bin/sh
[ -n "$IPKG_INSTROOT" ] && exit 0
rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache
/etc/init.d/rpcd reload
# Give rpcd a moment to register the object before deciding on a restart.
sleep 2
ubus -S list luci.fii >/dev/null 2>&1 || /etc/init.d/rpcd restart || true
exit 0
'''
    manifest.append(build("luci-app-fii", luci_entries, "fii, luci-base, cgi-io", controls=[("postinst", luci_postinst, 0o755)]))
    manifest_path = ROOT / "dist/manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    (ROOT / "dist/SHA256SUMS").write_text("".join(item["sha256"] + "  " + item["file"] + "\n" for item in manifest), encoding="utf-8", newline="\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
