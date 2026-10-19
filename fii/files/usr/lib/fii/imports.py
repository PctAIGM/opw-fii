"""Short-lived, revision-bound import checks; confirmation never reuploads data."""
import json
import secrets
import time

from .model import check
from .store import Store, atomic_write, lock
from . import transport


def write(path, value):
    content = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    atomic_write(path, content)


def stage(data, revision, summary, warnings):
    expires = int(time.time()) + transport.IMPORT_TTL
    cached = {'data': data, 'revision': revision, 'summary': summary, 'warnings': warnings,
              'expiresAt': expires, 'done': False}
    size = len(json.dumps(cached, ensure_ascii=False, separators=(',', ':')).encode())
    with lock('transfers'):
        transport.reserve(size)
        ident = secrets.token_hex(16)
        write(transport.path_for(ident, '.checked'), cached)
    return {'importToken': ident, 'revision': revision, 'expiresAt': expires}


def confirm(ident, revision):
    with lock('transfers'):
        path = transport.path_for(ident, '.checked')
        check(path.exists(), '检查结果已失效，请重新检查备份')
        cached = json.loads(path.read_text(encoding='utf-8'))
        if time.time() >= cached['expiresAt']:
            path.unlink()
            raise ValueError('检查结果已过期，请重新检查备份')
        check(type(revision) is int and revision == cached['revision'], '检查结果与配置版本不一致，请重新检查备份')
        if cached['done']:
            return cached['result']
        with lock():
            store = Store()
            current = store.read()
            data = cached['data']
            if current['revision'] == revision:
                store.save(data, revision)
            else:
                # Recover a lost result/worker crash after the atomic state
                # replacement. A retry must neither import twice nor overwrite
                # changes made after the checked revision.
                data['revision'] = revision + 1
                check(current['revision'] == revision + 1 and current == data,
                      '配置在检查备份后已变化，请重新检查备份')
        result = {'summary': cached['summary'], 'warnings': cached['warnings'], 'revision': data['revision'],
                  'message': '备份已导入，运行配置保持当前版本，请在输出页预览后应用'}
        # Retain a small idempotent receipt until acknowledged/released; discard
        # the large payload as soon as the import has committed.
        write(path, {'revision': revision, 'expiresAt': cached['expiresAt'], 'done': True, 'result': result})
        return result
