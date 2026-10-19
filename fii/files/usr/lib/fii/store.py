"""Atomic JSON persistence with cross-process locks and optimistic revisions."""
import contextlib
import json
import os
from pathlib import Path
import tempfile
import time

from .model import defaults, validate


def rooted(path):
    root = os.environ.get("FII_ROOT", "")
    return Path(root) / path.lstrip("/") if root else Path(path)


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".fii-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@contextlib.contextmanager
def lock(name="state", timeout=10):
    path = rooted("/var/lock/fii-" + name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.write(b"\0")
            stream.flush()
            def acquire():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("另一个配置操作正在进行，请稍后重试") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


class Store:
    def __init__(self):
        self.path = rooted("/etc/fii/state.json")

    def read(self):
        if self.path.exists():
            return validate(json.loads(self.path.read_text(encoding="utf-8")))
        data = defaults()
        write_json(self.path, data)
        return data

    def save(self, data, expected_revision):
        current = self.read()
        if type(expected_revision) is not int or expected_revision != current["revision"]:
            raise ValueError("配置已被其他操作更新；请重新载入页面后再保存")
        data["revision"] = current["revision"] + 1
        validate(data)
        write_json(self.path, data)
        return data
