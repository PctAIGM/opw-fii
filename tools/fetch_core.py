#!/usr/bin/env python3
"""Fetch pinned official Mihomo assets and verify GitHub release-asset SHA256 digests."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def fetch(version="1.19.32", windows=False):
    request = Request("https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/v" + version,
                      headers={"User-Agent": "fii-build/0.1"})
    with urlopen(request, timeout=30) as response:
        release = json.load(response)
    name = ("mihomo-windows-amd64-compatible-v%s.zip" if windows else "mihomo-linux-amd64-compatible-v%s.gz") % version
    asset = next(a for a in release["assets"] if a["name"] == name)
    expected = asset.get("digest", "")
    if not expected.startswith("sha256:"):
        raise ValueError("Official asset has no SHA256 digest; refusing an unverified core")
    directory = ROOT / ".local/downloads"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if path.exists():
        content = path.read_bytes()
    else:
        with urlopen(asset["browser_download_url"], timeout=60) as response:
            content = response.read(64 * 1024 * 1024)
    actual = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError("Core SHA256 mismatch")
    path.write_bytes(content)
    evidence = {"repository": "MetaCubeX/mihomo", "tag": release["tag_name"], "asset": name,
                "url": asset["browser_download_url"], "sha256": actual[7:], "assetId": asset["id"]}
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.19.32")
    parser.add_argument("--windows", action="store_true")
    args = parser.parse_args()
    fetch(args.version, args.windows)
