"""Bounded temporary transfers for LuCI/rpcd's small request and reply frames."""
import base64
import json
import os
import re
import secrets
import time

from .model import MAX_DOCUMENT, check
from .store import lock, rooted, write_json

CHUNK = 16 * 1024
# Ordinary messages stay small; transfer_read explicitly returns a bounded chunk.
INLINE = 2 * 1024
MAX_REQUEST = MAX_DOCUMENT + 4096
MAX_RESPONSE = MAX_DOCUMENT * 2
QUOTA = MAX_DOCUMENT * 4
TTL = 3600


def directory():
    path = rooted('/tmp/fii/transfers')
    path.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == 'nt' else 0o700)
    return path


def path_for(ident, suffix):
    check(isinstance(ident, str) and bool(re.fullmatch(r'[a-f0-9]{32}', ident)), '传输 ID 无效')
    return directory() / (ident + suffix)


def reserve(size):
    used, count = 0, 0
    for path in directory().iterdir():
        if not re.fullmatch(r'[a-f0-9]{32}\.(meta|in|out)', path.name):
            continue
        if time.time() - path.stat().st_mtime > TTL:
            path.unlink(missing_ok=True)
        elif path.suffix == '.meta':
            used += json.loads(path.read_text(encoding='utf-8'))['size']
            count += 1
        elif path.suffix == '.out':
            used += path.stat().st_size
            count += 1
    check(used + size <= QUOTA and count < 64, '临时传输空间已满，请稍后重试')


def release(ident):
    checked_id(ident)
    with lock('transfers'):
        for suffix in ('.in', '.meta', '.out'):
            path_for(ident, suffix).unlink(missing_ok=True)
    return {}


def checked_id(ident):
    path_for(ident, '.meta')
    return ident


def begin(size):
    check(type(size) is int and 0 < size <= MAX_REQUEST, '请求超过 16 MiB 或大小无效')
    with lock('transfers'):
        reserve(size)
        ident = secrets.token_hex(16)
        with open(path_for(ident, '.in'), 'xb'):
            pass
        os.chmod(path_for(ident, '.in'), 0o600)
        write_json(path_for(ident, '.meta'), {'size': size})
    return {'id': ident, 'chunkSize': CHUNK, 'protocol': 2}


def append(ident, offset, data):
    ident = checked_id(ident)
    check(type(offset) is int and offset >= 0, '分块偏移无效')
    check(isinstance(data, str) and len(data) <= (CHUNK + 2) // 3 * 4, '传输分块过大')
    chunk = base64.b64decode(data, validate=True)
    check(0 < len(chunk) <= CHUNK, '传输分块无效')
    with lock('transfers'):
        meta, path = path_for(ident, '.meta'), path_for(ident, '.in')
        check(meta.exists() and path.exists(), '传输已失效，请重新选择备份')
        size = json.loads(meta.read_text(encoding='utf-8'))['size']
        received = path.stat().st_size
        check(offset + len(chunk) <= size, '分块顺序或大小无效')
        # A request may have been accepted before its HTTP acknowledgement was
        # lost. Retrying with a smaller chunk must not append duplicate bytes.
        if offset < received:
            check(offset + len(chunk) <= received, '分块顺序或大小无效')
            with open(path, 'rb') as stream:
                stream.seek(offset)
                check(stream.read(len(chunk)) == chunk, '重试分块与已接收内容不一致')
            return {'received': offset + len(chunk)}
        check(received == offset, '分块顺序或大小无效')
        with open(path, 'ab') as stream:
            stream.write(chunk)
        os.utime(meta, None)
    return {'received': offset + len(chunk)}


def consume(ident):
    ident = checked_id(ident)
    with lock('transfers'):
        meta, path = path_for(ident, '.meta'), path_for(ident, '.in')
        check(meta.exists() and path.exists(), '传输已失效，请重新选择备份')
        size = json.loads(meta.read_text(encoding='utf-8'))['size']
        check(path.stat().st_size == size, '文件尚未传输完成')
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        finally:
            path.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)


def response(result, chunk_reply=False):
    content = json.dumps({'ok': True, 'result': result}, ensure_ascii=False, separators=(',', ':'))
    encoded = content.encode('utf-8')
    if len(encoded) <= INLINE or (chunk_reply and len(encoded) <= CHUNK * 2):
        return content
    check(len(encoded) <= MAX_RESPONSE, '响应超过传输限制')
    with lock('transfers'):
        reserve(len(encoded))
        ident = secrets.token_hex(16)
        path = path_for(ident, '.out')
        with open(path, 'xb') as stream:
            stream.write(encoded)
        os.chmod(path, 0o600)
    return json.dumps({'ok': True, 'responseTransfer': {'id': ident, 'size': len(encoded)}})


def read(ident, offset, size=CHUNK):
    path = path_for(ident, '.out')
    check(type(size) is int and 1 <= size <= CHUNK, '响应分块大小无效')
    with lock('transfers'):
        check(path.exists(), '响应传输已失效，请重新载入')
        check(type(offset) is int and 0 <= offset < path.stat().st_size, '响应偏移无效')
        with open(path, 'rb') as stream:
            stream.seek(offset)
            data = stream.read(size)
        os.utime(path, None)
    return {'data': base64.b64encode(data).decode('ascii')}
