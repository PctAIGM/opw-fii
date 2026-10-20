"""Whitelisted rpcd / CLI operations. All external commands use argv, never shell strings."""
import copy
import json
import re
import secrets
import subprocess
import sys
import time

from . import __version__
from .model import MAX_DOCUMENT, check, import_backup, validate
from .store import Store, lock, rooted, write_json

# rpcd starts a process for every call, including transfer chunks and job polls.
# Keep YAML, urllib and runtime setup off those lightweight paths.
INIT = '/etc/init.d/fii'


def refresh(*args, **kwargs):
    from .fetch import refresh as operation
    return operation(*args, **kwargs)


def extract(*args, **kwargs):
    from .render import extract as operation
    return operation(*args, **kwargs)


def render(*args, **kwargs):
    from .render import render as operation
    return operation(*args, **kwargs)


def validate_override(*args, **kwargs):
    from .render import validate_override as operation
    return operation(*args, **kwargs)


def active():
    from .runtime import active as operation
    return operation()


def ready(*args):
    from .runtime import ready as operation
    return operation(*args)


def status():
    from .runtime import status as operation
    return operation()


def run(*args, **kwargs):
    from .runtime import run as operation
    return operation(*args, **kwargs)


def activate(output_id):
    from .runtime import activate as operation
    return operation(output_id)


def prepare():
    from .runtime import prepare as operation
    return operation()

READ_METHODS = ("get", "status", "preview", "export", "logs", "job", "nodes", "groups", "core_info", "core_check", "network", "transfer_read")
WRITE_METHODS = ("save", "refresh", "apply", "service", "import", "import_preview", "select", "core_update", "core_channel",
                 "transfer_begin", "transfer_append", "transfer_invoke", "transfer_release", "transfer_status")
LONG_METHODS = ("refresh", "apply", "service", "core_update", "core_check", "import", "import_preview", "save", "preview")


def background(method, payload):
    if not isinstance(payload, dict):
        return False  # dispatch() returns the normal validation error.
    if method != 'save':
        return method in LONG_METHODS
    change = payload.get('patch')
    if not isinstance(change, dict):
        return True
    # Metadata changes retain existing YAML/JS and never execute their validators.
    if set(change) == {'settings'} or set(change) == {'kind', 'order'} or set(change) == {'kind', 'remove'}:
        return False
    if set(change) == {'kind', 'id', 'fields'} and isinstance(change['fields'], dict):
        return bool(set(change['fields']) & {'body', 'cachedYaml', 'type'})
    return True


def job_path(ident):
    check(isinstance(ident, str) and bool(re.fullmatch(r"[a-f0-9]{32}", ident)), "任务 ID 无效")
    return rooted("/tmp/fii/jobs/" + ident + ".json")


def queue_job(method, payload):
    ident = secrets.token_hex(16)
    path = job_path(ident)
    write_json(path, {"done": False, "method": method, "createdAt": int(time.time())})
    for old in path.parent.glob("*.json"):
        if re.fullmatch(r"[a-f0-9]{32}.json", old.name) and time.time() - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
            old.with_suffix('.payload').unlink(missing_ok=True)
    try:
        write_json(path.with_suffix('.payload'), payload)
        subprocess.Popen([sys.executable, "/usr/bin/fii", "job-run", method, ident],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    except OSError:
        path.unlink(missing_ok=True)
        path.with_suffix('.payload').unlink(missing_ok=True)
        raise ValueError("无法启动后台任务") from None
    return {"jobId": ident}


def run_job(method, ident, payload=None):
    check(method in LONG_METHODS, "任务操作无效")
    path = job_path(ident)
    started, last_report = int(time.time()), 0
    def progress(message, received=0, total=0):
        nonlocal last_report
        if total and received < total and time.monotonic() - last_report < 1:
            return
        write_json(path, {"done": False, "method": method, "createdAt": started,
                         "progress": {"message": message, "received": received, "total": total}})
        last_report = time.monotonic()
    try:
        if payload is None:
            payload = json.loads(path.with_suffix('.payload').read_text(encoding='utf-8'))
        if method == "core_update":
            from .core import install_core
            operation = install_core(payload.get("channel"), payload.get("digest"), progress)
        else:
            operation = dispatch(method, payload, progress=progress) if method == 'preview' else dispatch(method, payload)
        result = {"done": True, "ok": True, "result": operation}
    except Exception as exc:
        result = {"done": True, "ok": False, "error": str(exc)[:3000]}
    finally:
        path.with_suffix('.payload').unlink(missing_ok=True)
    write_json(path, result)


def validate_contents(data, previous=None):
    validate(data)
    check(not any('contentOmitted' in item for kind in ('sources', 'overrides') for item in data[kind]),
          '不能把列表摘要当作完整配置保存，请重新载入完整内容')
    sources = {item['id']: item for item in previous['sources']} if previous else {}
    overrides = {item['id']: item for item in previous['overrides']} if previous else {}
    for source in data["sources"]:
        if source.get("cachedYaml"):
            old = sources.get(source['id'], {})
            if old.get('cachedYaml') == source['cachedYaml'] and type(old.get('nodeCount')) is int:
                source['nodeCount'] = old['nodeCount']
            else:
                source["nodeCount"] = len(extract(source["cachedYaml"]))
        else:
            source.pop('nodeCount', None)
    for override in data["overrides"]:
        old = overrides.get(override['id'])
        if old is None or (old.get('type'), old.get('body', '')) != (override['type'], override.get('body', '')):
            validate_override(override)
    return data


def view_data(data):
    """Metadata for ordinary views; editor contents are fetched individually."""
    result = {key: value for key, value in data.items() if key not in ('sources', 'overrides')}
    result['sources'] = [{**{k: v for k, v in item.items() if k != 'cachedYaml'}, 'contentOmitted': True}
                         for item in data['sources']]
    result['overrides'] = [{**{k: v for k, v in item.items() if k != 'body'},
                            'snippet': item.get('body', '')[:240], 'contentOmitted': True}
                           for item in data['overrides']]
    return result


def compact_data(store):
    """RAM-backed metadata cache, invalidated by the actual state file identity."""
    path = rooted('/tmp/fii/state-view.json')
    def signature():
        return list(store.signature())
    if store.path.exists():
        stamp = signature()
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            if cached.get('schema') == 2 and cached['stamp'] == stamp:
                return cached['data']
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return cache_view(store, store.read())


def cache_view(store, data):
    """Publish validated metadata for both Python and embedded ucode readers."""
    path = rooted('/tmp/fii/state-view.json')
    result = view_data(data)
    # Caller holds the state lock, so cache and data describe the same revision.
    # Also avoid tagging an old read with a newer, externally replaced file.
    if store.snapshot is not None and store.snapshot[0] == store.signature():
        try:
            write_json(path, {'schema': 2, 'stamp': list(store.snapshot[0]), 'data': result})
        except OSError:
            pass  # A metadata cache is optional, including on a full tmpfs.
    return result


def apply_patch(data, patch):
    check(isinstance(patch, dict), '配置变更必须是对象')
    result = copy.deepcopy(data)
    if set(patch) == {'settings'}:
        check(isinstance(patch['settings'], dict), '设置无效')
        from .mihomo import LEGACY_KEYS
        check(not LEGACY_KEYS.intersection(patch['settings']), 'Mihomo 参数请在覆写中修改，路由器设置只接受接管与服务选项')
        result['settings'].update(patch['settings'])
        return result
    kind = patch.get('kind')
    check(kind in ('sources', 'overrides', 'outputs'), '配置分类无效')
    if set(patch) == {'kind', 'order'}:
        order = patch['order']
        current = {item['id']: item for item in result[kind]}
        check(isinstance(order, list) and all(isinstance(ident, str) for ident in order)
              and len(order) == len(current) and len(set(order)) == len(order) and set(order) == set(current),
              '排序必须包含每个配置项且不能重复')
        result[kind] = [current[ident] for ident in order]
    elif set(patch) == {'kind', 'item'}:
        item = patch['item']
        check(isinstance(item, dict) and isinstance(item.get('id'), str), '配置项无效')
        check(not any(key in item for key in ('contentOmitted', 'snippet')), '请先读取完整配置项后再保存')
        index = next((i for i, old in enumerate(result[kind]) if old['id'] == item['id']), None)
        if index is None:
            result[kind].append(copy.deepcopy(item))
        else:
            result[kind][index] = copy.deepcopy(item)
    elif set(patch) == {'kind', 'remove'}:
        ident = patch['remove']
        check(isinstance(ident, str), '配置 ID 无效')
        result[kind] = [item for item in result[kind] if item['id'] != ident]
        key = {'sources': 'sourceIds', 'overrides': 'overrideIds'}.get(kind)
        if key:
            for output in result['outputs']:
                output[key] = [value for value in output.get(key, []) if value != ident]
    elif set(patch) == {'kind', 'id', 'fields'}:
        fields = patch['fields']
        check(isinstance(fields, dict) and not set(fields) & {'id', 'contentOmitted', 'snippet'}, '配置变更字段无效')
        item = next((item for item in result[kind] if item['id'] == patch['id']), None)
        check(item is not None, '配置项不存在')
        item.update(copy.deepcopy(fields))
    else:
        raise ValueError('配置变更字段无效')
    return result


def refresh_one(kind, ident):
    check(kind in ("sources", "overrides"), "刷新类型无效")
    check(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident)), "配置 ID 无效")
    with lock("refresh-" + kind + "-" + ident, timeout=1):
        with lock():
            data = Store().read()
            original = next((s for s in data[kind] if s["id"] == ident), None)
            check(original is not None, "配置项不存在")
            original = copy.deepcopy(original)
        updated = copy.deepcopy(original)
        success = refresh(updated, data["settings"], kind)
        with lock():
            latest = Store().read()
            index = next((i for i, s in enumerate(latest[kind]) if s["id"] == ident), None)
            check(index is not None and latest[kind][index] == original,
                  "下载期间该配置已修改，未覆盖新数据，请重新刷新")
            latest[kind][index] = updated
            Store().save(latest, latest["revision"])
        return {"success": success, "message": "刷新成功" if success else updated["lastError"]}


def scheduled():
    with lock("scheduler", timeout=0):
        with lock():
            data = Store().read()
        changed = False
        for kind in ("sources", "overrides"):
            for item in data[kind]:
                if not (item.get("enabled", True) and item.get("autoRefresh") and item.get("url")):
                    continue
                now = time.time()
                last = item.get("lastRefreshAt", 0) or 0
                attempt = item.get("lastAttemptAt", 0) or 0
                if now - last >= item.get("refreshMinutes", 720) * 60 and now - attempt >= 300:
                    try:
                        changed = refresh_one(kind, item["id"])["success"] or changed
                    except ValueError:
                        pass
        bundle = active()
        if changed and data["settings"]["autoApply"] and bundle and ready(bundle):
            activate(bundle["outputId"])


def dispatch(method, payload=None, progress=None):
    payload = payload or {}
    check(isinstance(payload, dict), "请求参数必须是对象")
    if method.startswith('transfer_'):
        from . import transport
        if method == 'transfer_begin':
            return transport.begin(payload.get('size'), payload.get('fileUpload', False))
        if method == 'transfer_status':
            return transport.upload_status(payload.get('id'))
        if method == 'transfer_append':
            return transport.append(payload.get('id'), payload.get('offset'), payload.get('data'))
        if method == 'transfer_read':
            return transport.read(payload.get('id'), payload.get('offset'), payload.get('size', transport.CHUNK))
        if method == 'transfer_release':
            return transport.release(payload.get('id'))
        if method == 'transfer_invoke':
            operation = payload.get('method')
            # This write-only entry point cannot bypass read/write ACLs or recurse.
            check(operation in READ_METHODS + WRITE_METHODS and not operation.startswith('transfer_'), '传输操作无效')
            request = transport.consume(payload.get('id'))
            check(isinstance(request, dict), '请求参数必须是对象')
            return queue_job(operation, request) if background(operation, request) else dispatch(operation, request)
    if method == "job":
        path = job_path(payload.get("id"))
        check(path.exists(), "任务记录不存在，可能路由器已重启")
        wait_ms = payload.get('waitMs', 0)
        check(type(wait_ms) is int and 0 <= wait_ms <= 1000, '任务等待时间无效')
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            job = json.loads(path.read_text(encoding="utf-8"))
            remaining = deadline - time.monotonic()
            if job.get('done') or remaining <= 0:
                break
            time.sleep(min(0.025, remaining))
        limit = 900 if job.get("method") == "core_update" else 180
        if not job.get("done") and time.time() - job["createdAt"] > limit:
            return {"done": True, "ok": False, "error": "任务未完成，请刷新页面并查看日志"}
        return job
    if method == "status":
        return status()
    if method == "network":
        from .network import choices
        return choices()
    if method == "core_info":
        from .core import local_info
        return local_info()
    if method == "core_check":
        from .core import check_update
        return check_update(payload.get("channel"))
    if method == "core_channel":
        from .core import select_channel
        return select_channel(payload.get("channel"))
    if method == "core_update":
        from .core import install_core
        return install_core(payload.get("channel"), payload.get("digest"))
    if method == "groups":
        from .runtime import groups
        return groups()
    if method == "select":
        from .runtime import select_proxy
        return select_proxy(payload.get("group"), payload.get("name"), payload.get('action', 'select'))
    if method == "logs":
        # Match service tags, not cron commands / URLs which happen to contain fii.
        output = run(["/sbin/logread", "-e", r" (fii(-prepare)?|mihomo)(\[[0-9]+\])?:", "-l", "500"], timeout=4)
        clipped = len(output.encode('utf-8')) > 98304
        if clipped:
            output = output.encode('utf-8')[-98304:].decode('utf-8', 'replace').partition('\n')[2]
        prepare = rooted('/tmp/fii/prepare.log')
        preparation = ''
        if prepare.is_file():
            with prepare.open('rb') as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 16384))
                preparation = stream.read(16384).decode('utf-8', 'replace')
                if size > 16384:
                    preparation = preparation.partition('\n')[2]
        return {"text": output, "prepareText": preparation, "limit": 500, "truncated": clipped}
    if method == "refresh":
        return refresh_one(payload.get("kind", "sources"), str(payload.get("id", "")))
    if method == "apply":
        return activate(str(payload.get("id", "")))
    if method == 'import' and 'token' in payload:
        check(set(payload) == {'token', 'revision'}, '导入确认只接受检查凭证与配置版本')
        from .imports import confirm
        return confirm(payload['token'], payload['revision'])
    if method == "service":
        action = payload.get("action")
        check(action in ("start", "stop", "restart"), "服务操作无效")
        with lock("operation", timeout=1):
            if action != "stop":
                check(active() is not None, "请先应用一个输出配置")
            run([INIT, action], timeout=15)
            if action != "stop":
                deadline = time.monotonic() + 18 + active()["settings"].get("tunTimeout", 30)
                while time.monotonic() < deadline and not status()["running"]:
                    time.sleep(0.5)
                check(status()["running"], "服务启动失败，请查看日志")
        return {"message": "服务操作完成"}
    with lock():
        store = Store()
        if method == 'get' and (payload.get('compact') or payload.get('metadataOnly') or payload.get('kind') == 'outputs'):
            summary = compact_data(store)
            if 'kind' not in payload:
                return {'data': summary, 'version': __version__}
            kind = payload['kind']
            check(kind in ('sources', 'overrides', 'outputs'), '配置分类无效')
            item = next((item for item in summary[kind] if item['id'] == payload.get('id')), None)
            check(item is not None, '配置项不存在')
            result = {'item': item, 'revision': summary['revision']}
            if payload.get('sync'):
                result['data'] = summary
            return result
        data = store.read()
    if method in ("get", "export"):
        if method == 'get' and 'kind' in payload:
            kind = payload.get('kind')
            check(kind in ('sources', 'overrides', 'outputs'), '配置分类无效')
            item = next((item for item in data[kind] if item['id'] == payload.get('id')), None)
            check(item is not None, '配置项不存在')
            if payload.get('metadataOnly') and kind in ('sources', 'overrides'):
                item = next(value for value in view_data(data)[kind] if value['id'] == item['id'])
            result = {'item': item, 'revision': data['revision']}
            if payload.get('sync'):
                result['data'] = view_data(data)
            return result
        return {"data": view_data(data) if method == 'get' and payload.get('compact') else data, "version": __version__}
    if method == "preview":
        result = render(data, str(payload.get("id", "")), progress=progress)
        if payload.get("runtime"):
            from .runtime import overlay
            result["yaml"] = overlay(result["yaml"], data["settings"], warnings=result["warnings"])
        return result
    if method == "nodes":
        source = next((s for s in data["sources"] if s["id"] == str(payload.get("id", ""))), None)
        check(source is not None and source.get("cachedYaml"), "暂无缓存，请先刷新订阅")
        nodes = extract(source["cachedYaml"])
        nodes = [p for p in nodes if
                 (not source.get("includeRegex") or re.search(source["includeRegex"], p["name"])) and
                 (not source.get("excludeRegex") or not re.search(source["excludeRegex"], p["name"]))]
        return {"nodes": [{"name": source.get("prefix", "") + p["name"], "type": p.get("type", ""),
                           "server": p.get("server", "")} for p in nodes]}
    if method == "save":
        check(type(payload.get('revision')) is int and payload['revision'] == data['revision'],
              '配置已被其他操作更新；请重新载入页面后再保存')
        check(not ('patch' in payload and 'data' in payload), '不能同时提交完整配置与配置变更')
        new_data = apply_patch(data, payload['patch']) if 'patch' in payload else copy.deepcopy(payload.get('data'))
        validate_contents(new_data, data)
        with lock():
            store.save(new_data, payload.get("revision"), validated=True)
            summary = cache_view(store, new_data)
        return {"data": summary if payload.get('compact') else new_data, "message": "已保存；运行配置需在输出页点击应用"}
    if method in ("import", "import_preview"):
        if method == 'import_preview' and payload.get('stage'):
            check(type(payload.get('revision')) is int and payload['revision'] == data['revision'],
                  '配置已变化，请重新载入后再检查备份')
        if 'upload' in payload:
            check(method == 'import_preview' and 'data' not in payload, '文件上传只用于检查备份')
            from .transport import consume
            raw = consume(payload['upload'], file_upload=True)
        else:
            raw = payload.get('data')
        imported, warnings = import_backup(raw, data["settings"])
        validate_contents(imported)
        if method == "import":
            with lock():
                store.save(imported, payload.get("revision"))
        summary = {key: len(imported[key]) for key in ('sources', 'overrides', 'outputs')}
        result = {"summary": summary, "warnings": warnings,
                  "message": "备份已导入，运行配置保持当前版本，请在输出页预览后应用"}
        if method == 'import_preview' and payload.get('stage'):
            from .imports import stage
            result.update(stage(imported, data['revision'], summary, warnings))
        return result
    raise ValueError("未知操作")


def rpc_payload(raw):
    # rpcd exec plugins deliver the ubus call params as flat JSON on stdin;
    # the local preview harness wraps them as {"payload": "<json>"}.
    envelope = json.loads(raw or "{}")
    if isinstance(envelope.get("payload"), str):
        return json.loads(envelope["payload"])
    if isinstance(envelope.get("payload"), dict):
        return envelope["payload"]
    return envelope


def main():
    args = sys.argv[1:]
    try:
        if args == ["rpc-list"]:
            print(json.dumps({method: {"payload": ""} for method in READ_METHODS + WRITE_METHODS}))
            return
        if args and args[0] == "rpc-call":
            method = args[1] if len(args) > 1 else ""
            check(method in READ_METHODS + WRITE_METHODS, "未知 RPC 操作")
            raw = sys.stdin.read(MAX_DOCUMENT + 1)
            check(len(raw) <= MAX_DOCUMENT, "请求过大")
            payload = rpc_payload(raw)
            result = queue_job(method, payload) if background(method, payload) else dispatch(method, payload)
            from .transport import response
            print(response(result, chunk_reply=method == 'transfer_read'))
        elif len(args) == 3 and args[0] == "job-run":
            run_job(args[1], args[2])
        elif len(args) == 4 and args[0] == "job-run":
            run_job(args[1], args[2], json.loads(args[3]))
        elif args == ["prepare"]:
            prepare()
        elif args == ["ready"]:
            sys.exit(0 if ready() else 1)
        elif args == ["scheduled"]:
            scheduled()
        elif args == ["firewall-up"]:
            from .runtime import firewall_up
            firewall_up()
        elif args == ["firewall-down"]:
            from .runtime import firewall_down
            firewall_down()
        elif args == ["sync-boot"]:
            bundle = active()
            run([INIT, "enable" if bundle and bundle["settings"]["autostart"] else "disable"])
        else:
            method = args[0] if args else "get"
            check(method in READ_METHODS + WRITE_METHODS, "用法: fii get|status|preview|save|refresh|apply|service|import|export|logs [JSON]")
            payload = json.loads(args[1]) if len(args) > 1 else {}
            print(json.dumps(dispatch(method, payload), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
        if args and args[0].startswith("rpc-"):
            print(json.dumps({"ok": False, "error": str(exc)[:3000]}, ensure_ascii=False))
        else:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
