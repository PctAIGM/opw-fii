"""Download and atomically update FII's private Mihomo core from official channels."""
import gzip
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import struct
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from .model import check
from .store import atomic_write, lock, rooted, write_json

CORE_PATH = "/etc/fii/bin/mihomo"
INSTALL_PATH = "/etc/fii/core-install.json"
CHANNEL_PATH = "/etc/fii/core-channel.json"
API = "https://api.github.com/repos/MetaCubeX/mihomo/releases/"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_BINARY = 128 * 1024 * 1024


def channel_name(channel):
    check(channel in ("release", "alpha"), "内核渠道必须是 Release 或 Alpha")
    return channel


def read_json(path):
    file = rooted(path)
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}


def select_channel(channel):
    channel_name(channel)
    with lock("core-channel"):
        write_json(rooted(CHANNEL_PATH), {"channel": channel})
    return {"selectedChannel": channel}


def local_info():
    from .runtime import run
    installed = read_json(INSTALL_PATH)
    binary = rooted(CORE_PATH)
    exists = binary.is_file() and os.access(binary, os.X_OK)
    version_text = run([str(binary), "-v"], required=False, timeout=3) if exists else ""
    return {"installed": bool(version_text), "versionText": version_text[:300],
            "channel": installed.get("channel", ""), "version": installed.get("version", ""),
            "updatedAt": installed.get("updatedAt"), "digest": installed.get("digest", ""),
            "selectedChannel": read_json(CHANNEL_PATH).get("channel", installed.get("channel") or "release"),
            "architecture": "x86_64 / amd64-compatible", "path": CORE_PATH,
            "supported": platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")}


def open_url(address):
    # TLS verification remains enabled. Ignore proxy env to avoid a self-bootstrap loop.
    request = Request(address, headers={"User-Agent": "FII-Core-Manager/0.1.1", "Accept": "application/vnd.github+json" if address.startswith(API) else "application/octet-stream"})
    return build_opener(ProxyHandler({})).open(request, timeout=20)


def network_error(exc):
    if isinstance(exc, HTTPError):
        if exc.code in (403, 429):
            return "GitHub 访问受限或达到请求频率限制，请稍后重试"
        return "GitHub 下载失败 (HTTP %d)，当前内核保持不变" % exc.code
    return "无法连接 GitHub (%s)，请检查路由器网络后重试" % type(exc).__name__


def release_metadata(channel):
    channel_name(channel)
    endpoint = "latest" if channel == "release" else "tags/Prerelease-Alpha"
    try:
        with open_url(API + endpoint) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            check(len(body) <= 4 * 1024 * 1024, "GitHub 发布信息过大")
            release = json.loads(body)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ValueError(network_error(exc)) from None
    tag = release.get("tag_name", "")
    check(not release.get("draft"), "GitHub 返回了未发布版本")
    if channel == "release":
        check(not release.get("prerelease") and bool(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)), "Release 版本信息无效")
        pattern = r"mihomo-linux-amd64-compatible-(" + re.escape(tag) + r")\.gz"
    else:
        check(tag == "Prerelease-Alpha" and release.get("prerelease"), "Alpha 版本信息无效")
        pattern = r"mihomo-linux-amd64-compatible-(alpha-[A-Za-z0-9]+)\.gz"
    assets = [asset for asset in release.get("assets", []) if re.fullmatch(pattern, asset.get("name", ""))]
    check(len(assets) == 1, "未找到唯一的官方 amd64-compatible 内核文件")
    asset = assets[0]
    digest = asset.get("digest") or ""
    check(bool(re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest)), "官方内核缺少 SHA-256 摘要，未执行下载")
    size = asset.get("size")
    check(type(size) is int and 0 < size <= MAX_ARCHIVE, "内核压缩包大小无效")
    expected_url = "https://github.com/MetaCubeX/mihomo/releases/download/%s/%s" % (quote(tag, safe=""), asset["name"])
    check(asset.get("browser_download_url") == expected_url, "内核下载地址不是预期的官方发布地址")
    version = re.fullmatch(pattern, asset["name"]).group(1)
    return {"channel": channel, "version": version, "tag": tag, "asset": asset["name"],
            "assetId": asset.get("id"), "size": size, "digest": digest.lower(), "url": expected_url,
            # Alpha reuses its tag and release creation date. Asset time/digest identify new builds.
            "publishedAt": asset.get("updated_at") or release.get("published_at"),
            "releaseUrl": "https://github.com/MetaCubeX/mihomo/releases/tag/" + quote(tag, safe="")}


def check_update(channel):
    remote = release_metadata(channel)
    current = local_info()
    return {"remote": remote, "current": current,
            "updateAvailable": not current["installed"] or current["digest"] != remote["digest"] or current["channel"] != channel}


def download_archive(meta, path, progress):
    digest, size = hashlib.sha256(), 0
    deadline = time.monotonic() + 180
    try:
        with open_url(meta["url"]) as response, open(path, "wb") as output:
            while True:
                check(time.monotonic() < deadline, "内核下载超时，当前内核保持不变")
                block = response.read1(128 * 1024)
                if not block:
                    break
                size += len(block)
                check(size <= meta["size"] and size <= MAX_ARCHIVE, "内核压缩包超过预期大小")
                output.write(block)
                digest.update(block)
                progress("正在下载内核", size, meta["size"])
            output.flush()
            os.fsync(output.fileno())
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ValueError(network_error(exc)) from None
    check(size == meta["size"], "内核下载不完整，当前内核保持不变")
    check("sha256:" + digest.hexdigest() == meta["digest"], "内核 SHA-256 校验失败，当前内核保持不变")


def unpack_archive(archive, candidate):
    digest, size = hashlib.sha256(), 0
    try:
        with gzip.open(archive, "rb") as compressed, open(candidate, "wb") as output:
            while True:
                block = compressed.read(128 * 1024)
                if not block:
                    break
                size += len(block)
                check(size <= MAX_BINARY, "解压后的内核超过 128 MiB")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    except (OSError, EOFError) as exc:
        raise ValueError("内核解压失败: " + type(exc).__name__) from None
    with open(candidate, "rb") as stream:
        header = stream.read(64)
        check(len(header) == 64 and header[:6] == b"\x7fELF\x02\x01" and header[18:20] == b"\x3e\x00", "下载文件不是 Linux ELF64 x86_64 内核")
        offset = struct.unpack_from("<Q", header, 32)[0]
        entry_size, count = struct.unpack_from("<HH", header, 54)
        check(entry_size >= 56 and count <= 256 and offset + entry_size * count <= size, "ELF 程序头无效")
        for index in range(count):
            stream.seek(offset + entry_size * index)
            check(struct.unpack("<I", stream.read(4))[0] != 3, "下载内核依赖外部动态加载器，未安装")
    os.chmod(candidate, 0o755)
    return digest.hexdigest()


def validate_candidate(candidate, meta, bundle):
    from .runtime import run
    version = run([str(candidate), "-v"], timeout=5)
    check(version.startswith("Mihomo") and meta["version"] in version, "下载内核的版本输出与发布信息不一致")
    if bundle:
        config = rooted("/tmp/fii/core-update-test.yaml")
        atomic_write(config, bundle["yaml"])
        try:
            run([str(candidate), "-t", "-d", str(rooted("/etc/fii/core")), "-f", str(config)], timeout=30)
        finally:
            config.unlink(missing_ok=True)
    return version[:300]


def managed_running(bundle):
    from .runtime import run, ready
    if rooted("/tmp/fii/ready").exists() or (bundle and ready(bundle)):
        return True
    result = run(["/bin/ubus", "call", "service", "list", '{"name":"fii"}'], required=False, timeout=3)
    try:
        instances = json.loads(result).get("fii", {}).get("instances", {})
        return bool(instances.get("mihomo", {}).get("running"))
    except (ValueError, TypeError):
        return False


def install_core(channel, expected_digest=None, progress=None):
    from . import runtime
    channel_name(channel)
    progress = progress or (lambda message, received=0, total=0: None)
    check(platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64"),
          "当前内核下载仅支持 Linux x86_64（包括 Intel J4125）")
    with lock("core-update", timeout=1):
        progress("正在检查官方发布信息")
        meta = release_metadata(channel)
        check(expected_digest is None or expected_digest == meta["digest"], "官方版本已变化，请重新检查更新后下载")
        directory = rooted("/etc/fii/bin")
        directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(8)
        archive, candidate = directory / (".download-" + token + ".gz"), directory / (".candidate-" + token)
        try:
            download_archive(meta, archive, progress)
            progress("正在校验和解压内核")
            binary_hash = unpack_archive(archive, candidate)
            archive.unlink(missing_ok=True)
            with lock("operation", timeout=1):
                bundle = runtime.active()
                progress("正在测试新内核与当前配置")
                version_text = validate_candidate(candidate, meta, bundle)
                was_running = managed_running(bundle)
                check(not was_running or bundle is not None, "运行快照缺失，无法安全切换内核")
                destination = rooted(CORE_PATH)
                backup = directory / "mihomo.previous"
                previous_meta = read_json(INSTALL_PATH)
                had_previous = destination.exists()
                replaced = False
                try:
                    if was_running:
                        progress("正在停止服务并切换内核")
                        runtime.run([runtime.INIT, "stop"], timeout=45)
                    if had_previous:
                        shutil.copyfile(destination, backup)
                        os.chmod(backup, 0o755)
                    os.replace(candidate, destination)
                    replaced = True
                    write_json(rooted(INSTALL_PATH), {**meta, "binarySha256": binary_hash,
                               "versionText": version_text, "updatedAt": int(time.time())})
                    if was_running:
                        progress("正在启动服务并验证状态")
                        runtime.restart_and_wait(bundle)
                except (ValueError, OSError) as exc:
                    if replaced:
                        runtime.run([runtime.INIT, "stop"], required=False, timeout=45)
                        if had_previous:
                            os.replace(backup, destination)
                        else:
                            destination.unlink(missing_ok=True)
                        if previous_meta:
                            write_json(rooted(INSTALL_PATH), previous_meta)
                        else:
                            rooted(INSTALL_PATH).unlink(missing_ok=True)
                    recovery = "原内核保持不变" if not replaced else "已恢复原内核"
                    if was_running:
                        try:
                            runtime.restart_and_wait(bundle)
                            recovery += "并恢复服务"
                        except (ValueError, OSError):
                            runtime.run([runtime.INIT, "stop"], required=False, timeout=45)
                            recovery += "；服务恢复失败，已停止接管，请查看日志"
                    raise ValueError("内核更新失败: %s；%s" % (exc, recovery)) from None
            select_channel(channel)
            progress("内核安装完成")
            return {"message": "%s 内核 %s 已安装%s" % (channel.title(), meta["version"], "，服务已恢复" if was_running else ""),
                    "channel": channel, "version": meta["version"], "restarted": was_running}
        finally:
            archive.unlink(missing_ok=True)
            candidate.unlink(missing_ok=True)
