#!/usr/bin/env python3
"""Validate generated configs using the official Windows Mihomo core, without starting it."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fii/files/usr/lib"))
from fii.model import defaults
from fii.render import render, load_map, dump
from fii.runtime import overlay, ready, groups, select_proxy


def main():
    directory = ROOT / "build/mihomo-verify"
    directory.mkdir(parents=True, exist_ok=True)
    archive = ROOT / ".local/downloads/mihomo-windows-amd64-compatible-v1.19.32.zip"
    with zipfile.ZipFile(archive) as zipped:
        name = next(n for n in zipped.namelist() if n.endswith(".exe"))
        executable = directory / "mihomo.exe"
        executable.write_bytes(zipped.read(name))
    data = defaults()
    data["sources"] = [{"id": "demo", "name": "Demo", "cachedYaml": "proxies:\n  - {name: HK, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: example-only}\n"}]
    data["outputs"] = [{"id": "demo", "name": "Demo", "sourceIds": ["demo"], "overrideIds": []}]
    data["overrides"] = [{"id": "groups", "name": "Groups", "global": True, "type": "YAML", "body": (ROOT / "examples/groups.yaml").read_text(encoding="utf-8")}]
    rendered = render(data, "demo")["yaml"]
    cases = {"subscription": rendered}
    for mode in ("tproxy", "redirect", "core"):
        data["settings"]["mode"] = mode
        cases[mode] = overlay(rendered, data["settings"])
    data["settings"]["ipv6"] = True
    cases["ipv6"] = overlay(rendered, data["settings"])
    results = []
    for name, content in cases.items():
        config = directory / (name + ".yaml")
        config.write_text(content, encoding="utf-8")
        proc = subprocess.run([str(executable), "-t", "-d", str(directory), "-f", str(config)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        results.append({"case": name, "exitCode": proc.returncode, "output": proc.stdout + proc.stderr})
    version = subprocess.check_output([str(executable), "-v"], text=True).strip()
    # Exercise the real authenticated controller without TPROXY, DNS or proxy listeners.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        controller_port = probe.getsockname()[1]
    data["settings"]["controllerPort"] = controller_port
    body = load_map(overlay(render(data, "demo")["yaml"], data["settings"]))
    body.update({"redir-port": 0, "tproxy-port": 0, "listeners": [], "dns": {"enable": False},
                 "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["HK", "DIRECT"]}]})
    controller_config = directory / "controller.yaml"
    controller_config.write_text(dump(body), encoding="utf-8")
    bundle = {"settings": data["settings"]}
    with open(directory / "controller.log", "w", encoding="utf-8") as log:
        process = subprocess.Popen([str(executable), "-d", str(directory), "-f", str(controller_config)],
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for attempt in range(40):
                if ready(bundle):
                    break
                time.sleep(0.1)
            if not ready(bundle):
                raise RuntimeError("Test controller did not start")
            with patch("fii.runtime.active", return_value=bundle):
                before = groups()
                select_proxy("PROXY", "DIRECT")
                after = groups()
                assert after["groups"][0]["now"] == "DIRECT"
            controller_result = {"ready": True, "before": before, "after": after, "selectionPassed": True}
        finally:
            process.terminate()
            process.wait(timeout=10)
    evidence = {"coreVersion": version, "platform": "Windows config validation only; no router packet-path test", "cases": results}
    evidence["controller"] = controller_result
    (directory / "results.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if any(r["exitCode"] != 0 for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
