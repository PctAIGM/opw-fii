"""Scheduled restart, boot delay, core environment and br_netfilter handling."""
import io
import json
import os
import secrets
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / "build/test-tmp"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class TestDirectory:
    """Avoid Python 3.13's Windows-only 0700 ACL excluding sandbox identity."""

    def __init__(self):
        self.path = TEMP_ROOT / ("test-" + secrets.token_hex(8))
        self.path.mkdir(mode=0o777)
        self.name = str(self.path)

    def __enter__(self):
        return self.name

    def __exit__(self, *args):
        self.cleanup()

    def cleanup(self):
        resolved = self.path.resolve()
        if resolved.parent != TEMP_ROOT.resolve():
            raise ValueError("Refusing cleanup outside test directory")
        shutil.rmtree(resolved)


sys.path.insert(0, str(ROOT / "fii/files/usr/lib"))

from fii import cli, model, runtime
from fii.store import Store, lock, rooted

YAML = """proxies:
  - {name: HK, type: ss, server: hk.example.com, port: 443, cipher: aes-128-gcm, password: test}
"""


def sample():
    data = model.defaults()
    data["sources"] = [{"id": "s1", "name": "Airport", "url": "https://example.com/sub", "cachedYaml": YAML}]
    data["outputs"] = [{"id": "p1", "name": "Home", "sourceIds": ["s1"], "overrideIds": []}]
    return data


class MaintenanceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TestDirectory()
        self.env = patch.dict(os.environ, {"FII_ROOT": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_old_settings_gain_new_defaults(self):
        data = sample()
        for key in ("bootDelay", "scheduledRestart", "restartTime", "goMaxProcs",
                    "goMemLimit", "disableQuicGso", "disableQuicEcn", "skipCoreIpv6Check"):
            data["settings"].pop(key, None)
        model.validate(data)
        defaults = model.defaults()["settings"]
        for key in ("bootDelay", "goMaxProcs", "goMemLimit"):
            self.assertEqual(data["settings"][key], defaults[key])
        self.assertEqual(data["settings"]["restartTime"], "03:00")
        for key in ("scheduledRestart", "disableQuicGso", "disableQuicEcn", "skipCoreIpv6Check"):
            selfIsFalse = data["settings"][key]
            self.assertIs(selfIsFalse, False)

    def test_out_of_range_values_rejected(self):
        cases = [
            ({"bootDelay": 601}, "开机延迟"),
            ({"bootDelay": -1}, "开机延迟"),
            ({"restartTime": "3:00"}, "重启时间"),
            ({"restartTime": "24:00"}, "重启时间"),
            ({"restartTime": "03:60"}, "重启时间"),
            ({"goMaxProcs": 65}, "GOMAXPROCS"),
            ({"goMemLimit": -1}, "内存上限"),
            ({"scheduledRestart": "yes"}, "scheduledRestart"),
            ({"disableQuicGso": 1}, "disableQuicGso"),
        ]
        for override, fragment in cases:
            data = sample()
            data["settings"].update(override)
            with self.assertRaises(ValueError, msg=str(override)) as caught:
                model.validate(data)
            self.assertIn(fragment, str(caught.exception))

    def test_valid_values_round_trip_through_store(self):
        data = sample()
        data["settings"].update({"bootDelay": 45, "scheduledRestart": True, "restartTime": "23:59",
                                 "goMaxProcs": 4, "goMemLimit": 256, "disableQuicGso": True})
        with lock():
            Store().save(data, 0)
        read = Store().read()
        self.assertEqual(read["settings"]["bootDelay"], 45)
        self.assertTrue(read["settings"]["scheduledRestart"])
        self.assertEqual(read["settings"]["restartTime"], "23:59")


class CoreEnvironmentTests(unittest.TestCase):
    def test_environment_mapping(self):
        env = runtime.core_environment({"goMaxProcs": 0, "goMemLimit": 0, "disableQuicGso": False,
                                        "disableQuicEcn": False, "skipCoreIpv6Check": False})
        self.assertEqual(env, {})
        env = runtime.core_environment({"goMaxProcs": 4, "goMemLimit": 256, "disableQuicGso": True,
                                        "disableQuicEcn": True, "skipCoreIpv6Check": True})
        self.assertEqual(env, {"GOMAXPROCS": "4", "GOMEMLIMIT": "256MiB", "QUIC_GO_DISABLE_GSO": "1",
                               "QUIC_GO_DISABLE_ECN": "1", "SKIP_SYSTEM_IPV6_CHECK": "1"})

    def test_prepare_writes_sourceable_env_file(self):
        bundle = {"outputId": "p1", "name": "Home", "settings": {"goMaxProcs": 4, "goMemLimit": 128},
                  "yaml": YAML, "appliedAt": 1, "revision": 1}
        with patch.object(runtime, "active", return_value=bundle), \
                patch.object(runtime, "core_test"), \
                patch.object(runtime, "nftables", return_value=""):
            runtime.prepare()
        content = rooted("/tmp/fii/core-env").read_text(encoding="utf-8")
        self.assertIn("export GOMAXPROCS=4\n", content)
        self.assertIn("export GOMEMLIMIT=128MiB\n", content)
        self.assertNotIn("QUIC_GO", content)

    def test_prepare_without_tuning_writes_empty_file(self):
        bundle = {"outputId": "p1", "name": "Home", "settings": {}, "yaml": YAML, "appliedAt": 1, "revision": 1}
        with patch.object(runtime, "active", return_value=bundle), \
                patch.object(runtime, "core_test"), \
                patch.object(runtime, "nftables", return_value=""):
            runtime.prepare()
        self.assertEqual(rooted("/tmp/fii/core-env").read_text(encoding="utf-8"), "")


class ScheduledRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TestDirectory()
        self.env = patch.dict(os.environ, {"FII_ROOT": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def restart_due(self, settings, bundle={"outputId": "p1"}, strftime="03:00", now=1_800_000_000,
                    running=True, marker=None):
        if marker is not None:
            rooted("/etc/fii").mkdir(parents=True, exist_ok=True)
            rooted("/etc/fii/scheduler.json").write_text(json.dumps(marker), encoding="utf-8")
        with patch("time.strftime", return_value=strftime), \
                patch("time.time", return_value=now), \
                patch.object(runtime, "service_active", return_value=running), \
                patch.object(cli, "run") as run:
            fired = cli.restart_due(settings, bundle)
        return fired, run

    def test_fires_at_configured_time_when_running(self):
        fired, run = self.restart_due({"scheduledRestart": True, "restartTime": "03:00"})
        self.assertTrue(fired)
        self.assertEqual(run.call_args.args[0], [runtime.INIT, "restart"])
        marker = json.loads(rooted("/etc/fii/scheduler.json").read_text(encoding="utf-8"))
        self.assertEqual(marker, {"restartAt": 1_800_000_000})

    def test_disabled_or_missing_bundle_never_restarts(self):
        fired, run = self.restart_due({"scheduledRestart": False, "restartTime": "03:00"})
        self.assertFalse(fired)
        run.assert_not_called()
        fired, run = self.restart_due({"scheduledRestart": True, "restartTime": "03:00"}, bundle=None)
        self.assertFalse(fired)
        run.assert_not_called()

    def test_other_times_do_not_restart(self):
        fired, run = self.restart_due({"scheduledRestart": True, "restartTime": "04:00"})
        self.assertFalse(fired)
        run.assert_not_called()

    def test_recent_marker_suppresses_duplicate_fires(self):
        fired, run = self.restart_due({"scheduledRestart": True, "restartTime": "03:00"},
                                      marker={"restartAt": 1_800_000_000 - 3600})
        self.assertFalse(fired)
        run.assert_not_called()

    def test_stopped_service_is_left_alone(self):
        fired, run = self.restart_due({"scheduledRestart": True, "restartTime": "03:00"}, running=False)
        self.assertFalse(fired)
        run.assert_not_called()
        self.assertFalse(rooted("/etc/fii/scheduler.json").exists())

    def test_marker_written_before_restart_command(self):
        # If the restart itself fails the marker still counts the attempt.
        fired, run = self.restart_due({"scheduledRestart": True, "restartTime": "03:00"})
        self.assertTrue(fired)
        self.assertTrue(rooted("/etc/fii/scheduler.json").exists())

    def test_boot_delay_cli_reports_store_value(self):
        data = sample()
        data["settings"]["bootDelay"] = 42
        with lock():
            Store().save(data, 0)
        output = io.StringIO()
        with patch.object(sys, "argv", ["fii", "boot-delay"]), redirect_stdout(output):
            cli.main()
        self.assertEqual(output.getvalue().strip(), "42")

    def test_boot_delay_cli_defaults_to_zero_without_setting(self):
        data = sample()
        data["settings"].pop("bootDelay", None)
        with lock():
            Store().save(data, 0)
        output = io.StringIO()
        with patch.object(sys, "argv", ["fii", "boot-delay"]), redirect_stdout(output):
            cli.main()
        self.assertEqual(output.getvalue().strip(), "0")


class BridgeNetfilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TestDirectory()
        self.env = patch.dict(os.environ, {"FII_ROOT": self.tmp.name})
        self.env.start()
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fake_run(self, args, timeout=30, required=True):
        self.calls.append(list(args))
        if args[:3] == ["/sbin/sysctl", "-n", runtime.BRIDGE_SYSCTL["ipv4"]]:
            return "1"
        if args[:3] == ["/sbin/sysctl", "-n", runtime.BRIDGE_SYSCTL["ipv6"]]:
            return "0"
        return ""

    def test_quiet_flips_only_enabled_families_reported_active(self):
        settings = {"ipv4Proxy": True, "ipv6": True}
        with patch.object(runtime, "run", side_effect=self.fake_run):
            flipped = runtime.bridge_netfilter_quiet(settings)
        self.assertEqual(flipped, [runtime.BRIDGE_SYSCTL["ipv4"]])
        self.assertIn(["/sbin/sysctl", "-q", "-w", runtime.BRIDGE_SYSCTL["ipv4"] + "=0"], self.calls)
        self.assertNotIn(["/sbin/sysctl", "-q", "-w", runtime.BRIDGE_SYSCTL["ipv6"] + "=0"], self.calls)
        self.assertTrue(rooted("/tmp/fii/bridge-nf-ipv4").exists())
        self.assertFalse(rooted("/tmp/fii/bridge-nf-ipv6").exists())

    def test_quiet_skips_disabled_family_even_when_sysctl_on(self):
        settings = {"ipv4Proxy": False, "ipv6": False}
        with patch.object(runtime, "run", side_effect=self.fake_run):
            flipped = runtime.bridge_netfilter_quiet(settings)
        self.assertEqual(flipped, [])
        self.assertEqual(self.calls, [])

    def test_quiet_without_module_is_a_noop(self):
        def no_key(args, timeout=30, required=True):
            self.calls.append(list(args))
            return ""
        settings = {"ipv4Proxy": True, "ipv6": True}
        with patch.object(runtime, "run", side_effect=no_key):
            flipped = runtime.bridge_netfilter_quiet(settings)
        self.assertEqual(flipped, [])
        self.assertEqual([c for c in self.calls if "-w" in c], [])
        self.assertFalse(rooted("/tmp/fii/bridge-nf-ipv4").exists())

    def test_restore_reverts_flagged_flips_only(self):
        flags = rooted("/tmp/fii")
        flags.mkdir(parents=True, exist_ok=True)
        (flags / "bridge-nf-ipv4").write_text("", encoding="utf-8")
        with patch.object(runtime, "run", side_effect=self.fake_run):
            runtime.bridge_netfilter_restore()
        self.assertIn(["/sbin/sysctl", "-q", "-w", runtime.BRIDGE_SYSCTL["ipv4"] + "=1"], self.calls)
        self.assertNotIn(["/sbin/sysctl", "-q", "-w", runtime.BRIDGE_SYSCTL["ipv6"] + "=1"], self.calls)
        self.assertFalse((flags / "bridge-nf-ipv4").exists())

    def test_restore_without_flags_touches_nothing(self):
        with patch.object(runtime, "run", side_effect=self.fake_run):
            runtime.bridge_netfilter_restore()
        self.assertEqual(self.calls, [])

    def test_idempotent_quiet_keeps_existing_flag(self):
        settings = {"ipv4Proxy": True, "ipv6": False}
        flags = rooted("/tmp/fii")
        flags.mkdir(parents=True, exist_ok=True)
        (flags / "bridge-nf-ipv4").write_text("", encoding="utf-8")

        def already_off(args, timeout=30, required=True):
            self.calls.append(list(args))
            if args[:3] == ["/sbin/sysctl", "-n", runtime.BRIDGE_SYSCTL["ipv4"]]:
                return "0"
            return ""
        with patch.object(runtime, "run", side_effect=already_off):
            flipped = runtime.bridge_netfilter_quiet(settings)
        self.assertEqual(flipped, [])
        # No second write happened; the flag from the first flip stays for restore.
        self.assertEqual([c for c in self.calls if "-w" in c], [])
        self.assertTrue((flags / "bridge-nf-ipv4").exists())


if __name__ == "__main__":
    unittest.main()
