"""Synchronous main(config) overrides in a disposable JavaScript process."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from .model import check, MAX_TEXT

MAX_CONFIG = 8 * 1024 * 1024
RUNNER = Path(__file__).resolve().parents[2] / 'libexec/fii/js-runner.cjs'


def run(script, config=None, validate_only=False, scripts=None):
    check(isinstance(script, str) and len(script.encode('utf-8')) <= MAX_TEXT, 'JS 覆写超过 4 MiB')
    executable = shutil.which('node')
    check(executable is not None, 'JS 执行器未安装，请先执行 opkg update && opkg install node')
    request = json.dumps({'script': script, 'scripts': scripts, 'config': config, 'validateOnly': validate_only},
                         ensure_ascii=False, allow_nan=False)
    check(config is None or len(json.dumps(config, ensure_ascii=False).encode()) <= MAX_CONFIG, 'JS 输入配置超过 8 MiB')
    options = {}
    if os.name == 'posix' and os.geteuid() == 0:
        options.update(user=65534, group=65534, extra_groups=[])
    # Do not inherit NODE_OPTIONS, proxy settings or credentials into the runner.
    environment = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'SYSTEMROOT') if key in os.environ}
    timeout = 6 * (len(scripts) if scripts else 1)
    try:
        process = subprocess.run([executable, '--max-old-space-size=128', '--stack-size=512', str(RUNNER)],
                                 input=request, capture_output=True, text=True, encoding='utf-8',
                                 errors='replace', timeout=timeout, env=environment, cwd=RUNNER.parent, **options)
    except subprocess.TimeoutExpired:
        raise ValueError('JS 覆写执行超时（最多 %d 秒），已终止脚本' % timeout) from None
    except OSError:
        raise ValueError('无法启动 JS 执行器，请检查 node 安装') from None
    check(process.returncode == 0, 'JS 执行器异常退出，可能超过内存或调用栈限制')
    check(len(process.stdout.encode()) <= MAX_CONFIG + 4096, 'JS 输出配置超过 8 MiB')
    try:
        reply = json.loads(process.stdout)
    except (ValueError, TypeError):
        raise ValueError('JS 执行器返回无效结果') from None
    check(isinstance(reply, dict) and reply.get('ok'), reply.get('error', 'JS 执行失败') if isinstance(reply, dict) else 'JS 执行失败')
    if validate_only:
        return
    check(isinstance(reply.get('config'), dict), 'main(config) 必须返回配置对象')
    return reply['config']


def validate(script):
    run(script, validate_only=True)


def execute(script, config):
    return run(script, config)


def execute_many(overrides, config):
    """One disposable process, with a fresh VM and bounded result per script."""
    if len(overrides) == 1:
        return execute(overrides[0].get('body', ''), config)
    scripts = [{'script': item.get('body', ''), 'name': item['name']} for item in overrides]
    for item in scripts:
        check(isinstance(item['script'], str) and len(item['script'].encode('utf-8')) <= MAX_TEXT, 'JS 覆写超过 4 MiB')
    # Keep the runner's existing aggregate input bound even for large scripts.
    if len(json.dumps(scripts, ensure_ascii=False).encode()) > MAX_TEXT:
        for item in overrides:
            try:
                config = execute(item.get('body', ''), config)
            except ValueError as exc:
                raise ValueError('JS 覆写「%s」失败: %s' % (item['name'], exc)) from None
        return config
    return run('', config, scripts=scripts)
