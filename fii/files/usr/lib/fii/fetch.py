"""Bounded HTTPS fetching; failed refreshes never replace the last working cache."""
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .model import MAX_TEXT, check, github_token, url
from .render import extract, validate_override


def origin(address):
    parsed = urlsplit(address)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


class CredentialRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, address):
        url(address)
        parsed = urlsplit(address)
        check(parsed.username is None and parsed.password is None, '重定向地址不能含内嵌认证信息')
        redirected = super().redirect_request(request, response, code, message, headers, address)
        authorization = request.get_header('Authorization')
        if redirected is not None:
            redirected.remove_header('Authorization')
        if redirected is not None and authorization and origin(request.full_url) == origin(address):
            redirected.add_unredirected_header('Authorization', authorization)
        return redirected


def download(address, user_agent):
    url(address)
    parsed = urlsplit(address)
    token = github_token(parsed)
    # Keep the stored URL for editing/export, but remove user-info from the
    # actual request URL and carry the GitHub token only in an auth header.
    cleaned = urlunsplit(parsed._replace(netloc=parsed.netloc.rsplit('@', 1)[-1]))
    request = Request(cleaned, headers={"User-Agent": user_agent, "Accept": "*/*", "Accept-Encoding": "identity"})
    if token is not None:
        request.add_unredirected_header('Authorization', 'Bearer ' + token)
    try:
        # Router proxy environment must not create a bootstrap dependency on its own core.
        with build_opener(ProxyHandler({}), CredentialRedirectHandler()).open(request, timeout=20) as response:
            url(response.geturl())
            content = response.read(MAX_TEXT + 1)
            check(len(content) <= MAX_TEXT, "远程内容超过 4 MiB")
            return content.decode("utf-8-sig"), response.headers
    except (HTTPError, URLError, TimeoutError, UnicodeError, OSError) as exc:
        # Do not reflect a secret-bearing subscription URL from urllib exceptions.
        reason = "HTTP %d" % exc.code if isinstance(exc, HTTPError) else type(exc).__name__
        if isinstance(exc, HTTPError):
            exc.close()
        raise ValueError("下载失败 (%s)，保留上次成功内容" % reason) from None


def refresh(item, settings, kind):
    now = int(time.time())
    item["lastAttemptAt"] = now
    try:
        body, headers = download(item.get("url", ""), item.get("userAgent") or settings["userAgent"])
        if kind == "sources":
            proxies = extract(body)
            check(bool(proxies), "订阅没有节点，保留上次成功缓存")
            usage = {}
            for key, value in re.findall(r"(upload|download|total|expire)\s*=\s*(\d+)", headers.get("Subscription-Userinfo", ""), re.I):
                usage[{"upload": "uploadBytes", "download": "downloadBytes", "total": "totalBytes", "expire": "expireAtSeconds"}[key.lower()]] = int(value)
            for key in ("uploadBytes", "downloadBytes", "totalBytes", "expireAtSeconds"):
                item[key] = usage.get(key)
            item["cachedYaml"] = body
            item["nodeCount"] = len(proxies)
        else:
            validate_override({**item, "body": body})
            item["body"] = body
        item["lastRefreshAt"] = now
        item["lastError"] = ""
        return True
    except ValueError as exc:
        item["lastError"] = str(exc)
        return False
