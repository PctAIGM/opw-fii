"""Manual router-origin HTTP response timing, isolated with a total deadline."""
DEFAULT_SITES = [
    {'id': 'baidu', 'name': '百度', 'url': 'https://www.baidu.com/'},
    {'id': 'google', 'name': 'Google', 'url': 'https://www.gstatic.com/generate_204'},
    {'id': 'github', 'name': 'GitHub', 'url': 'https://github.com/'},
]
DEADLINE = 8


def validate_sites(sites):
    from .model import check, text, url
    from urllib.parse import urlsplit
    import re
    check(isinstance(sites, list) and len(sites) <= 24, '测试网站最多添加 24 个')
    seen = set()
    for site in sites:
        check(isinstance(site, dict), '测试网站无效')
        ident = site.get('id')
        check(isinstance(ident, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident)
              and ident not in seen, '测试网站 ID 无效或重复')
        seen.add(ident)
        name = text(site.get('name'), '网站名称', 100)
        check(bool(name.strip()) and not any(ord(c) < 32 for c in name), '网站名称不能为空或含控制字符')
        value = text(site.get('url'), '测试网址', 2048)
        url(value)
        parsed = urlsplit(value)
        check(parsed.username is None and parsed.password is None, '测试网址不能包含用户名密码')
        check(not parsed.fragment, '测试网址不能包含 # 片段')
        check(all(32 < ord(c) < 127 for c in value), '测试网址请使用 URL 编码和 ASCII 域名')


def probe(url, proxy=None):
    import socket
    import ssl
    import time
    from urllib import error, request

    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    # An explicit proxy routes the probe through Mihomo's mixed listener, so
    # the measurement follows the running policy and the core's own DNS
    # instead of the router's local (possibly poisoned) resolver. Without
    # one, router routing decides and environment proxies stay ignored.
    proxies = {'http': proxy, 'https': proxy} if proxy else {}
    opener = request.build_opener(request.ProxyHandler(proxies), NoRedirect())
    started = time.monotonic()
    try:
        try:
            response = opener.open(request.Request(url, headers={
                'User-Agent': 'FII-URL-Test/1.0', 'Cache-Control': 'no-cache',
                'Connection': 'close'}), timeout=6)
        except error.HTTPError as exc:
            response = exc  # A 403/404 is still a measured HTTP response.
        with response:
            return {'reachable': True, 'delay': max(1, round((time.monotonic() - started) * 1000)),
                    'status': response.code}
    except Exception as exc:
        reason = exc.reason if isinstance(exc, error.URLError) else exc
        message = ('连接超时' if isinstance(reason, (TimeoutError, socket.timeout)) else
                   'DNS 解析失败' if isinstance(reason, socket.gaierror) else
                   'TLS 证书或握手失败' if isinstance(reason, ssl.SSLError) else '连接失败')
        return {'reachable': False, 'error': message}


def test_site(ident, expected_url=None):
    import json
    from pathlib import Path
    import subprocess
    import sys
    import time
    from .model import check
    from .store import Store, lock
    site = next((item for item in Store().read()['settings']['urlTestSites'] if item['id'] == ident), None)
    check(site is not None, '测试网站已删除，请重新载入')
    check(expected_url is None or expected_url == site['url'], '测试网址已变更，请重新载入')
    # No persistent changes and at most three active probes across browser tabs.
    for slot in range(3):
        try:
            guard = lock('url-test-' + str(slot), timeout=0)
            guard.__enter__()
            break
        except ValueError:
            continue
    else:
        raise ValueError('正在测试其他网址，请稍后重试')
    try:
        try:
            # Route through Mihomo while it runs: the probe then follows the
            # live routing policy and resolves names at the core, not through
            # the router's dnsmasq (which the user may deliberately keep on a
            # direct upstream to avoid DNS loops).
            from .runtime import active, ready
            proxy = None
            bundle = active()
            if bundle and ready(bundle):
                address = bundle['settings']['mixedAddress']
                address = '[%s]' % address if ':' in address else address
                proxy = 'http://%s:%d' % (address, bundle['settings']['mixedPort'])
            output = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                                    input=json.dumps({'url': site['url'], 'proxy': proxy}),
                                    capture_output=True, text=True,
                                    timeout=DEADLINE, check=True)
            result = json.loads(output.stdout)
        except subprocess.TimeoutExpired:
            result = {'reachable': False, 'error': '连接超时（8 秒）'}
        except (subprocess.SubprocessError, ValueError, OSError):
            result = {'reachable': False, 'error': '测试进程失败'}
    finally:
        guard.__exit__(None, None, None)
    return {**result, 'id': ident, 'url': site['url'], 'testedAt': int(time.time())}


if __name__ == '__main__':
    import json
    import sys
    payload = json.loads(sys.stdin.read(8192))
    if isinstance(payload, dict):
        result = probe(payload['url'], payload.get('proxy'))
    else:  # Bare URL: pre-proxy command line and older callers.
        result = probe(payload)
    print(json.dumps(result))
