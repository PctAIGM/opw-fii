import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

from test_fii import TestDirectory
from fii import core, runtime, cli
from fii.store import rooted, write_json, lock


def executable():
    header = bytearray(120)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = b"\x3e\x00"
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HH", header, 54, 56, 1)
    struct.pack_into("<I", header, 64, 1)
    return bytes(header) + b"sample-core" * 100


def metadata(channel="release", content=None):
    content = content if content is not None else gzip.compress(executable())
    version = "v1.19.32" if channel == "release" else "alpha-123abcd"
    tag = version if channel == "release" else "Prerelease-Alpha"
    name = "mihomo-linux-amd64-compatible-" + version + ".gz"
    return {"channel": channel, "version": version, "tag": tag, "asset": name, "assetId": 1,
            "size": len(content), "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "url": "https://github.com/MetaCubeX/mihomo/releases/download/" + tag + "/" + name}


def release(channel="release"):
    meta = metadata(channel)
    return {"tag_name": meta["tag"], "prerelease": channel == "alpha", "draft": False,
            "published_at": "2024-08-12T00:00:00Z", "assets": [
                {"name": meta["asset"], "size": meta["size"], "digest": meta["digest"], "id": 1,
                 "updated_at": "2026-10-19T00:00:00Z", "browser_download_url": meta["url"]}]}


class MetadataTests(unittest.TestCase):
    def test_release_selects_compatible_asset_from_latest(self):
        document = release()
        document["assets"].append({"name": "mihomo-linux-amd64-v3-v1.19.32.gz"})
        with patch.object(core, "open_url", return_value=io.BytesIO(json.dumps(document).encode())) as open_url:
            result = core.release_metadata("release")
        open_url.assert_called_once_with(core.API + "latest")
        self.assertEqual(result["version"], "v1.19.32")

    def test_alpha_uses_asset_commit_and_update_time(self):
        with patch.object(core, "open_url", return_value=io.BytesIO(json.dumps(release("alpha")).encode())) as open_url:
            result = core.release_metadata("alpha")
        open_url.assert_called_once_with(core.API + "tags/Prerelease-Alpha")
        self.assertEqual(result["version"], "alpha-123abcd")
        self.assertEqual(result["publishedAt"], "2026-10-19T00:00:00Z")

    def test_alpha_reused_tag_does_not_hide_new_build(self):
        with patch.object(core, "release_metadata", return_value=metadata("alpha")), \
                patch.object(core, "local_info", return_value={"installed": True, "channel": "alpha", "digest": "sha256:old"}):
            self.assertTrue(core.check_update("alpha")["updateAvailable"])

    def test_invalid_channels_never_make_network_requests(self):
        with patch.object(core, "open_url") as open_url:
            for channel in ("../alpha", "https://evil.example", "beta", None):
                with self.assertRaises(ValueError):
                    core.release_metadata(channel)
        open_url.assert_not_called()

    def test_no_digest_offsite_url_and_prerelease_rejected(self):
        cases = []
        missing = release(); missing["assets"][0]["digest"] = None; cases.append(missing)
        offsite = release(); offsite["assets"][0]["browser_download_url"] = "https://evil.example/core.gz"; cases.append(offsite)
        unstable = release(); unstable["prerelease"] = True; cases.append(unstable)
        for document in cases:
            with patch.object(core, "open_url", return_value=io.BytesIO(json.dumps(document).encode())), self.assertRaises(ValueError):
                core.release_metadata("release")


class CoreInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TestDirectory()
        self.env = patch.dict(os.environ, {"FII_ROOT": self.tmp.name})
        self.env.start()
        self.os_type = patch.object(core.platform, "system", return_value="Linux")
        self.machine = patch.object(core.platform, "machine", return_value="x86_64")
        self.os_type.start(); self.machine.start()
        self.content = gzip.compress(executable())
        self.meta = metadata(content=self.content)

    def tearDown(self):
        self.env.stop(); self.os_type.stop(); self.machine.stop(); self.tmp.cleanup()

    def old_core(self):
        target = rooted(core.CORE_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old core")
        old = {"channel": "alpha", "version": "alpha-old", "digest": "sha256:old"}
        write_json(rooted(core.INSTALL_PATH), old)
        return old

    def install(self, running=False, bundle=None):
        with patch.object(core, "release_metadata", return_value=self.meta), \
                patch.object(core, "open_url", return_value=io.BytesIO(self.content)), \
                patch.object(core, "validate_candidate", return_value="Mihomo Meta v1.19.32"), \
                patch.object(core, "managed_running", return_value=running), \
                patch.object(runtime, "active", return_value=bundle):
            return core.install_core("release")

    def test_first_install_does_not_start_service_without_output(self):
        with patch.object(runtime, "run") as run, patch.object(runtime, "restart_and_wait") as restart:
            result = self.install()
        self.assertFalse(result["restarted"])
        self.assertEqual(rooted(core.CORE_PATH).read_bytes(), executable())
        self.assertEqual(core.read_json(core.INSTALL_PATH)["channel"], "release")
        run.assert_not_called(); restart.assert_not_called()

    def test_running_update_validates_before_stopping_and_restarts(self):
        self.old_core()
        events = []
        with patch.object(core, "release_metadata", return_value=self.meta), \
                patch.object(core, "open_url", return_value=io.BytesIO(self.content)), \
                patch.object(core, "validate_candidate", side_effect=lambda *args: events.append("validate") or "Mihomo v1.19.32"), \
                patch.object(core, "managed_running", return_value=True), \
                patch.object(runtime, "active", return_value={"yaml": "example"}), \
                patch.object(runtime, "run", side_effect=lambda *args, **kwargs: events.append("stop")), \
                patch.object(runtime, "restart_and_wait", side_effect=lambda *args: events.append("restart")):
            result = core.install_core("release")
        self.assertEqual(events, ["validate", "stop", "restart"])
        self.assertTrue(result["restarted"])
        self.assertEqual(rooted("/etc/fii/bin/mihomo.previous").read_bytes(), b"old core")

    def test_sha_failure_never_stops_or_changes_current_core(self):
        old = self.old_core()
        bad = dict(self.meta, digest="sha256:" + "0" * 64)
        with patch.object(core, "release_metadata", return_value=bad), \
                patch.object(core, "open_url", return_value=io.BytesIO(self.content)), patch.object(runtime, "run") as run:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                core.install_core("release")
        run.assert_not_called()
        self.assertEqual(core.read_json(core.INSTALL_PATH), old)
        self.assertEqual(rooted(core.CORE_PATH).read_bytes(), b"old core")
        self.assertEqual([p.name for p in rooted("/etc/fii/bin").iterdir()], ["mihomo"])

    def test_failed_config_validation_keeps_running_core(self):
        self.old_core()
        with patch.object(core, "release_metadata", return_value=self.meta), \
                patch.object(core, "open_url", return_value=io.BytesIO(self.content)), \
                patch.object(core, "validate_candidate", side_effect=ValueError("incompatible config")), \
                patch.object(runtime, "active", return_value={"yaml": "old"}), patch.object(runtime, "run") as run:
            with self.assertRaisesRegex(ValueError, "incompatible"):
                core.install_core("release")
        run.assert_not_called()
        self.assertEqual(rooted(core.CORE_PATH).read_bytes(), b"old core")

    def test_failed_start_restores_binary_metadata_and_service(self):
        old = self.old_core()
        with patch.object(runtime, "run"), patch.object(runtime, "restart_and_wait", side_effect=[ValueError("port busy"), None]) as restart:
            with self.assertRaisesRegex(ValueError, "恢复原内核并恢复服务"):
                self.install(True, {"yaml": "old"})
        self.assertEqual(restart.call_count, 2)
        self.assertEqual(rooted(core.CORE_PATH).read_bytes(), b"old core")
        self.assertEqual(core.read_json(core.INSTALL_PATH), old)

    def test_stopped_service_stays_stopped_after_update(self):
        self.old_core()
        with patch.object(runtime, "run") as run, patch.object(runtime, "restart_and_wait") as restart:
            self.install(False, {"yaml": "saved"})
        run.assert_not_called(); restart.assert_not_called()

    def test_backup_disk_error_restores_service_without_swapping(self):
        self.old_core()
        with patch.object(core.shutil, "copyfile", side_effect=OSError("no space")), \
                patch.object(runtime, "run"), patch.object(runtime, "restart_and_wait") as restart:
            with self.assertRaisesRegex(ValueError, "原内核保持不变"):
                self.install(True, {"yaml": "old"})
        self.assertEqual(rooted(core.CORE_PATH).read_bytes(), b"old core")
        restart.assert_called_once()

    def test_changed_upstream_version_requires_another_check(self):
        self.old_core()
        with patch.object(core, "release_metadata", return_value=self.meta), patch.object(core, "download_archive") as download:
            with self.assertRaisesRegex(ValueError, "版本已变化"):
                core.install_core("release", "sha256:previously-checked")
        download.assert_not_called()

    def test_channel_selection_persists_without_creating_config_library(self):
        core.select_channel("alpha")
        self.assertEqual(core.local_info()["selectedChannel"], "alpha")
        self.assertFalse(rooted("/etc/fii/state.json").exists())

    def test_unsupported_platform_rejected_before_network(self):
        with patch.object(core.platform, "machine", return_value="aarch64"), \
                patch.object(core, "release_metadata") as remote, self.assertRaises(ValueError):
            core.install_core("release")
        remote.assert_not_called()

    def test_truncated_download_rejected(self):
        path = rooted("/download.gz")
        with patch.object(core, "open_url", return_value=io.BytesIO(self.content[:-2])), self.assertRaisesRegex(ValueError, "不完整"):
            core.download_archive(self.meta, path, lambda *args: None)

    def test_non_elf_and_wrong_machine_rejected(self):
        for content in (b"not an ELF", executable()[:18] + b"\xb7\x00" + executable()[20:]):
            archive, target = rooted("/core.gz"), rooted("/candidate")
            archive.write_bytes(gzip.compress(content))
            with self.assertRaisesRegex(ValueError, "ELF64"):
                core.unpack_archive(archive, target)

    def test_native_probe_uses_candidate_and_saved_config(self):
        candidate = rooted("/candidate")
        with patch.object(runtime, "run", return_value="Mihomo Meta v1.19.32") as run:
            core.validate_candidate(candidate, self.meta, {"yaml": "mode: rule"})
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(run.call_args_list[0].args[0], [str(candidate), "-v"])
        self.assertIn("-t", run.call_args_list[1].args[0])
        self.assertFalse(rooted("/tmp/fii/core-update-test.yaml").exists())

    def test_update_rpc_job_reports_progress_and_has_longer_deadline(self):
        ident = "b" * 32
        def install(channel, digest, progress):
            progress("正在下载内核", 50, 100)
            live = cli.dispatch("job", {"id": ident})
            self.assertEqual(live["progress"]["received"], 50)
            return {"message": "installed"}
        with patch.object(core, "install_core", side_effect=install):
            cli.run_job("core_update", ident, {"channel": "alpha"})
        self.assertTrue(cli.dispatch("job", {"id": ident})["ok"])

    def test_rpc_payload_accepts_flat_rpcd_params(self):
        # rpcd exec plugins pass the ubus call params as flat JSON on stdin.
        self.assertEqual(cli.rpc_payload('{"channel":"release"}'), {"channel": "release"})

    def test_rpc_payload_accepts_envelope_payload_string(self):
        self.assertEqual(cli.rpc_payload('{"payload": "{\\"id\\": \\"p1\\"}"}'), {"id": "p1"})

    def test_rpc_payload_accepts_envelope_payload_object(self):
        self.assertEqual(cli.rpc_payload('{"payload": {"id": "p1"}}'), {"id": "p1"})

    def test_rpc_payload_empty_input_yields_empty_params(self):
        self.assertEqual(cli.rpc_payload(""), {})


if __name__ == "__main__":
    unittest.main()
