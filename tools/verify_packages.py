#!/usr/bin/env python3
"""Inspect application-only IPKs, their metadata, paths and script permissions."""
import hashlib
import gzip
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def verify():
    manifest = json.loads((ROOT / "dist/manifest.json").read_text())
    assert len(manifest) == 2 and {r["file"].split("_")[0] for r in manifest} == {"fii", "luci-app-fii"}
    assert set(p.name for p in (ROOT / "dist").glob("*.ipk")) == {r["file"] for r in manifest}, "Remove obsolete IPKs from dist"
    result = []
    for record in manifest:
        path = ROOT / "dist" / record["file"]
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as outer:
            assert set(outer.getnames()) == {"./debian-binary", "./control.tar.gz", "./data.tar.gz"}
            assert outer.extractfile("./debian-binary").read() == b"2.0\n"
            with tarfile.open(fileobj=io.BytesIO(outer.extractfile("./control.tar.gz").read()), mode="r:gz") as archive:
                control = archive.extractfile("./control").read().decode()
                assert "Architecture: " + record["architecture"] in control
                assert "Version: " + record["version"] in control
                depends = next(line for line in control.splitlines() if line.startswith("Depends:"))
                assert not any("mihomo" in dep for dep in depends.split(",")), "Application must not depend on a core package"
                for member in archive.getmembers():
                    if member.name != "./control":
                        assert member.mode == 0o755
            size = len(gzip.decompress(outer.extractfile("./data.tar.gz").read()))
            assert "Installed-Size: %d\n" % size in control
            with tarfile.open(fileobj=io.BytesIO(outer.extractfile("./data.tar.gz").read()), mode="r:gz") as archive:
                members = archive.getmembers()
                names = archive.getnames()
                dirs = {m.name for m in members if m.isdir()}
                # opkg needs an explicit directory member for every parent,
                # otherwise on-router extraction fails with wfopen errors.
                for member in members:
                    if member.isfile():
                        parent = "./" + str(Path(member.name).parent).replace("\\", "/").lstrip("./")
                        while parent != "./":
                            assert parent in dirs, "Missing directory member: " + parent
                            parent = "./" + str(Path(parent).parent).replace("\\", "/").lstrip("./")
                assert not any("__pycache__" in name or "state.json" in name or "active.json" in name for name in names)
                for member in members:
                    assert member.name.startswith("./") and ".." not in Path(member.name).parts
                    if member.isdir():
                        assert member.mode == 0o755
                        continue
                    assert member.isfile()
                    data = archive.extractfile(member).read()
                    assert not data.startswith(b"\x7fELF"), "Application IPK must not contain a core binary"
                    if data.startswith(b"#!"):
                        assert member.mode == 0o755 and b"\r\n" not in data
                assert "./usr/bin/mihomo" not in names and "./etc/fii/bin/mihomo" not in names
        result.append({"package": record["file"], "entries": len(names), "verified": True})
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    verify()
