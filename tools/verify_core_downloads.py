#!/usr/bin/env python3
"""Live-check both official channels and verify Linux downloads, without executing them."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fii/files/usr/lib"))
from fii.core import release_metadata, download_archive, unpack_archive


def main():
    directory = ROOT / ".local/downloads"
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    for channel in ("release", "alpha"):
        meta = release_metadata(channel)
        path = directory / meta["asset"]
        cached = path.exists() and "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == meta["digest"]
        if not cached:
            download_archive(meta, path, lambda *args: None)
        candidate = directory / ("verified-" + channel + "-linux-core")
        binary_hash = unpack_archive(path, candidate)
        results.append({**meta, "downloadVerified": True, "elfVerified": True,
                        "binarySha256": binary_hash, "reusedVerifiedDownload": cached,
                        "nativeExecution": False})
        candidate.unlink()
        print(channel + ": " + meta["version"] + " SHA-256 and ELF verified", flush=True)
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build/core-download-checks.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
