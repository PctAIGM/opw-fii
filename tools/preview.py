#!/usr/bin/env python3
"""Local LuCI view harness; real config backend, simulated router-only operations.

This is a development preview, never installed on the router. Bind loopback only.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fii/files/usr/lib"))
os.environ["FII_ROOT"] = str(ROOT / "build/preview-root")
from fii.cli import dispatch, READ_METHODS, WRITE_METHODS
from fii.core import read_json, CHANNEL_PATH

STATUS = {"running": False, "coreInstalled": False, "coreVersion": "",
          "activeOutputId": "", "activeName": "", "appliedAt": None, "appliedRevision": None, "mode": "", "interfaces": []}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.headers.get("Host") not in ("127.0.0.1:8765", "localhost:8765"):
            self.send_error(403)
            return
        if self.path in ("/", "/index.html"):
            self.path = "/tools/preview.html"
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path not in ("/tools/preview.html", "/luci-app-fii/htdocs/luci-static/resources/view/fii/app.js", "/luci-app-fii/htdocs/luci-static/resources/fii/style.css"):
            self.send_error(404)
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api" or self.headers.get("Host") not in ("127.0.0.1:8765", "localhost:8765"):
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 16 * 1024 * 1024:
                raise ValueError("请求超过 16 MiB")
            request = json.loads(self.rfile.read(size))
            method, payload = request["method"], request.get("payload", {})
            if method == 'transfer_invoke':
                from fii.transport import consume
                method = payload.get('method')
                if method not in READ_METHODS + WRITE_METHODS or method.startswith('transfer_'):
                    raise ValueError('传输操作无效')
                payload = consume(payload.get('id'))
            if method == "status":
                result = {**STATUS, "coreSelectedChannel": read_json(CHANNEL_PATH).get("channel", "release")}
            elif method == "core_info":
                result = {"installed": False, "versionText": "", "channel": "", "version": "", "updatedAt": None,
                          "selectedChannel": read_json(CHANNEL_PATH).get("channel", "release"), "supported": True,
                          "architecture": "预览目标：Linux x86_64"}
            elif method == "groups":
                result = {"groups": []}
            elif method in ("apply", "service", "refresh", "select", "resource_update", "core_update"):
                raise ValueError("本地预览不执行路由器操作。配置编辑、导入与输出预览使用真实后端。")
            elif method == "logs":
                result = {"text": "本地预览没有 OpenWrt 日志。"}
            else:
                result = dispatch(method, payload)
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        content = json.dumps(response, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    print("FII local preview: http://127.0.0.1:8765", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
