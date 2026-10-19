"""Bounded HTTPS fetching; failed refreshes never replace the last working cache."""
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .model import MAX_TEXT, check, url
from .render import extract, validate_override


def download(address, user_agent):
    url(address)
    request = Request(address, headers={"User-Agent": user_agent, "Accept": "*/*", "Accept-Encoding": "identity"})
    try:
        # Router proxy environment must not create a bootstrap dependency on its own core.
        with build_opener(ProxyHandler({})).open(request, timeout=20) as response:
            url(response.geturl())
            content = response.read(MAX_TEXT + 1)
            check(len(content) <= MAX_TEXT, "远程内容超过 4 MiB")
            return content.decode("utf-8-sig"), response.headers
    except (HTTPError, URLError, TimeoutError, UnicodeError, OSError) as exc:
        # Do not reflect a secret-bearing subscription URL from urllib exceptions.
        reason = "HTTP %d" % exc.code if isinstance(exc, HTTPError) else type(exc).__name__
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
