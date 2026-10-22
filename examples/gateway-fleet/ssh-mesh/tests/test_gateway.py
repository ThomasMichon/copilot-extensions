import contextlib
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch
import uuid


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("gateway", HERE.parent / "gateway.py")
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)
FP = "A" * 43 + "="


class FakeDocker:
    def __init__(self):
        self.containers = {}
        self.images = {}
        self.calls = []
        self.activation_fail = False
        self.last_image = None
        self.run_counter = 0
        self.logs = {}

    def next_start(self):
        self.run_counter += 1
        return f"2026-10-14T00:00:{self.run_counter:02d}.000000000Z"

    def startup_logs(self, name):
        started = self.containers[name]["State"]["StartedAt"]
        address = "127.0.0.1:18445" if name == gateway.SERVICE + "-candidate" else "0.0.0.0:18443"
        self.logs.setdefault(name, []).extend([
            (started, "2026/10/14 00:00:00 server: Fingerprint " + FP + "\n"),
            (started, "2026/10/14 00:00:00 server: Listening on http://" + address + "\n"),
        ])

    def inspect(self, name):
        return self.containers.get(name)

    def call(self, *args):
        self.calls.append(args)
        if args[0] == "build":
            tag = args[args.index("-t") + 1]
            self.last_image = tag
            self.images[tag] = "sha256:" + gateway.digest(tag.encode())
            return ""
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": self.images[args[2]]}])
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            image_id = next(value for value in args if value.startswith("sha256:"))
            index = args.index(image_id)
            labels = dict(value.split("=", 1) for i, value in enumerate(args)
                          if i and args[i - 1] == "--label")
            source = args[args.index("--mount") + 1].split("source=", 1)[1].split(",target=", 1)[0]
            started = self.next_start()
            self.containers[name] = {
                "Id": gateway.digest((name + started).encode()),
                "Image": image_id,
                "State": {"Running": not (
                    self.activation_fail and name == gateway.SERVICE and
                    image_id == self.images[self.last_image]), "StartedAt": started},
                "Config": {"Image": image_id, "Labels": labels,
                           "Cmd": list(args[index + 1:]),
                           "User": args[args.index("--user") + 1]},
                "HostConfig": {
                    "NetworkMode": "host", "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "RestartPolicy": {"Name": args[args.index("--restart") + 1]},
                    "LogConfig": {"Type": "json-file",
                                  "Config": {"max-size": "5m", "max-file": "3"}},
                },
                "Mounts": [{"Type": "bind", "Source": source,
                            "Destination": "/config", "RW": False}],
            }
            self.logs[name] = []
            self.startup_logs(name)
            return "container-id"
        if args[0] == "exec":
            return gateway.VERSION + "\n"
        if args[0] == "logs":
            entries = self.logs[args[-1]]
            if "--since" in args:
                since = args[args.index("--since") + 1]
                entries = [(stamp, line) for stamp, line in entries if stamp >= since]
            if "--tail" in args:
                entries = entries[-int(args[args.index("--tail") + 1]):]
            return "".join(line for _, line in entries)
        if args[0] == "rm":
            del self.containers[args[-1]]
            return ""
        if args[0] in ("start", "stop"):
            self.containers[args[-1]]["State"]["Running"] = args[0] == "start"
            if args[0] == "start":
                self.containers[args[-1]]["State"]["StartedAt"] = self.next_start()
                self.startup_logs(args[-1])
            return ""
        raise AssertionError(args)

    def probe(self, port):
        name = gateway.SERVICE + "-candidate" if port == 18445 else gateway.SERVICE
        return bool(self.containers.get(name, {}).get("State", {}).get("Running"))


class GatewayTests(unittest.TestCase):
    def setUp(self):

        self.workspace = HERE / (".test-work-" + uuid.uuid4().hex)
        self.workspace.mkdir(mode=0o700)
        self.root = self.workspace / "runtime"
        self.source = self.workspace / "gateway.py"
        self.source.write_bytes((HERE.parent / "gateway.py").read_bytes())
        self.config = HERE.parent / "gateway.yaml"
        self.archive = gzip.compress(b"verified fake Chisel binary")
        self.docker = FakeDocker()
        self.service = gateway.Gateway(self.root, self.docker,
                                       fetch=lambda url: self.archive, probe=self.docker.probe)
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(gateway.platform, "machine", return_value="x86_64"))
        self.stack.enter_context(patch.object(gateway, "ASSETS", {
            "x86_64": ("fake.gz", gateway.digest(self.archive))}))
        self.stack.enter_context(patch.object(gateway.os, "getuid", return_value=1000, create=True))
        self.stack.enter_context(patch.object(gateway.os, "getgid", return_value=1000, create=True))
        if os.name != "nt":

            self.stack.enter_context(patch.object(gateway.os, "getuid",
                                                 return_value=self.workspace.stat().st_uid))
            self.stack.enter_context(patch.object(gateway.os, "getgid",
                                                 return_value=self.workspace.stat().st_gid))
        self.stack.enter_context(patch.object(gateway, "run", side_effect=self.native))
        self.stack.enter_context(patch.object(gateway.time, "sleep"))

    def tearDown(self):
        self.stack.close()
        shutil.rmtree(self.workspace)

    def native(self, argv, **kwargs):
        if "--keygen" in argv:
            gateway.write_file(Path(argv[-1]), b"fake stable private key")
            return ""
        return gateway.VERSION + "\n"

    def install(self):
        return self.service.install(self.source, self.config)

    def change_source(self):
        self.source.write_bytes(self.source.read_bytes() + b"\n# update test\n")

    def test_anchored_allowlist_and_unauthorized_routes(self):
        for machine, port in gateway.PEERS.items():
            rules = gateway.route_rules(machine)
            self.assertEqual(rules[0], rf"^R:127\.0\.0\.1:{port}$")
            for address in ("socks", "R:socks", "R:0.0.0.0:" + str(port),
                            "127.0.0.1:8787", "127.0.0.1:13001",
                            "127.0.0.1:13005", "127x0x0x1:" + str(port),
                            "127.0.0.1:230010", "R:127.0.0.1:23006",
                            "R:127.0.0.1:" + str(port) + ":22"):
                self.assertFalse(any(re.search(rule, address) for rule in rules), address)
            for other in gateway.PEERS.values():
                self.assertTrue(re.search(rules[1], "127.0.0.1:" + str(other)))
                self.assertEqual(bool(re.search(rules[0], "R:127.0.0.1:" + str(other))),
                                 other == port)

    def test_archive_digest_refused_before_decompress(self):
        with patch.object(gateway.gzip, "decompress") as decompress:
            with self.assertRaisesRegex(gateway.GatewayError, "before decompression"):
                gateway.unpack_verified(b"bad archive", "f" * 64)
            decompress.assert_not_called()

    def test_actual_release_pin_and_no_unreviewed_config(self):
        pins = SPEC.loader.get_source("gateway")
        self.assertIn("f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6", pins)
        self.assertIn("2ec6152cd2c74fe0146d4d79e4e7aa174521368c56e433d55e023a92ea404ec3", pins)
        bad = dict(gateway.DEFAULT, server_port=8787)
        with self.assertRaises(gateway.GatewayError):
            gateway.validate_config(bad)

    def test_install_idempotent_credentials_stable_and_source_replaceable(self):
        self.assertTrue(self.install()["ready"])
        before = {p.name: p.read_bytes() for p in self.service.config.iterdir()}
        state = self.service.state()
        calls = len(self.docker.calls)
        self.install()
        self.assertFalse(any(call[0] in ("run", "rm", "start", "stop", "build")
                             for call in self.docker.calls[calls:]))
        self.change_source()
        self.install()
        self.assertNotEqual(self.service.state()["slot"], state["slot"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.service.config.iterdir()})
        self.assertEqual(gateway.read_json(self.service.state_dir / "previous.json"), state)
        installed_script = self.service.versions / self.service.state()["slot"] / "gateway.py"
        self.source.unlink()
        self.assertTrue(installed_script.is_file())
        self.assertTrue(self.service.status()["ready"])
        self.assertNotIn(str(self.workspace), (self.root / gateway.SERVICE).read_text().split("SLOT=")[-1])
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        completed = subprocess.run([gateway.sys.executable, str(installed_script), "--help"],
                                   capture_output=True, text=True, check=True, **options)
        self.assertIn("export-client", completed.stdout)

    def test_status_has_no_secrets_and_export_is_explicit(self):
        receipt = self.install()
        status = self.service.status()
        self.assertEqual(status["host_enrollment"], "unknown")
        self.assertEqual(status["client_routes"], "unknown")
        secrets_ = [(self.service.config / (m + ".secret")).read_text() for m in gateway.PEERS]
        self.assertEqual(len(set(secrets_)), 5)
        self.assertTrue(all(len(secret) == 43 for secret in secrets_))
        for secret in secrets_:
            self.assertNotIn(secret, json.dumps(receipt))
            self.assertNotIn(secret, json.dumps(status))
            self.assertNotIn(secret, (self.root / "deploy-manifest.json").read_text())
            self.assertNotIn(secret, (self.service.state_dir / "startup-verification.json").read_text())
            self.assertNotIn(secret, repr(self.docker.calls))
        with self.assertRaisesRegex(gateway.GatewayError, "--secret-stdout"):
            self.service.export_client("operator-node-e", False)
        enrollment = self.service.export_client("operator-node-e", True)
        self.assertEqual(enrollment["auth"], "operator-node-e:" + secrets_[-1])
        self.assertEqual(enrollment["fingerprint"], FP)
        self.assertEqual(enrollment["relay_port"], 23005)

    def test_foreign_root_and_container_refused(self):
        self.root.mkdir()
        (self.root / "foreign").write_text("not ours")
        with self.assertRaisesRegex(gateway.GatewayError, "foreign"):
            self.install()
        shutil.rmtree(self.root)
        self.docker.containers[gateway.SERVICE] = {"Config": {"Labels": {}}}
        with self.assertRaisesRegex(gateway.GatewayError, "Foreign"):
            self.install()
        self.assertFalse(any(call[0] == "rm" for call in self.docker.calls))

    def test_labels_and_security_drift_block_stop_uninstall(self):
        self.install()
        container = self.docker.containers[gateway.SERVICE]
        container["Config"]["Labels"][gateway.PREFIX + "configdigest"] = "foreign"
        for action in (self.service.stop, self.service.uninstall):
            with self.assertRaisesRegex(gateway.GatewayError, "drift"):
                action()
        container["Config"]["Labels"] = self.service.labels(self.service.state())
        container["HostConfig"]["ReadonlyRootfs"] = False
        with self.assertRaisesRegex(gateway.GatewayError, "security"):
            self.service.stop()
        self.assertTrue(container["State"]["Running"])

    def test_credential_key_and_config_drift_refused(self):
        self.install()
        key = self.service.config / "server-key"
        key.write_bytes(b"unexpected replacement")
        with self.assertRaisesRegex(gateway.GatewayError, "drift"):
            self.install()
        with self.assertRaisesRegex(gateway.GatewayError, "drift"):
            self.service.stop()

    def test_candidate_failure_keeps_active_and_marker(self):
        self.install()
        old = self.service.state()
        self.change_source()
        original = self.service.observed

        def observed(name, state, port):
            result = original(name, state, port)
            if port == 18445:
                result["ready"] = False
            return result

        with patch.object(self.service, "observed", side_effect=observed):
            with self.assertRaisesRegex(gateway.GatewayError, "Readiness"):
                self.install()
        self.assertEqual(self.service.state(), old)
        self.assertTrue(self.service.status()["ready"])
        self.assertNotIn(gateway.SERVICE + "-candidate", self.docker.containers)

    def test_activation_failure_rolls_back_previous_image_and_marker(self):
        self.install()
        old = self.service.state()
        self.change_source()
        self.docker.activation_fail = True
        with self.assertRaisesRegex(gateway.GatewayError, "prior deployment restored"):
            self.install()
        self.assertEqual(self.service.state(), old)
        self.assertTrue(self.service.status()["ready"])
        self.assertEqual(self.docker.containers[gateway.SERVICE]["Image"], old["image_id"])

    def test_metadata_write_failure_restores_container_and_selection(self):
        self.install()
        old = self.service.state()
        self.change_source()
        original = gateway.write_file
        failed = False

        def write(path, data, mode=0o600):
            nonlocal failed
            if path.name == "current-version" and not failed:
                failed = True
                raise OSError("simulated disk failure")
            return original(path, data, mode)

        with patch.object(gateway, "write_file", side_effect=write):
            with self.assertRaisesRegex(gateway.GatewayError, "Selection commit failed; prior"):
                self.install()
        self.assertEqual(self.service.state(), old)
        self.assertTrue(self.service.status()["ready"])

    def test_http_or_tcp_only_does_not_prove_chisel_listener(self):
        self.install()
        (self.service.state_dir / "startup-verification.json").unlink()
        original = self.docker.call

        def call(*args):
            if args[0] == "logs":
                return "2026/10/14 00:00:00 server: Fingerprint " + FP + "\n"
            return original(*args)

        with patch.object(self.docker, "call", side_effect=call):
            self.assertFalse(self.service.status()["ready"])

    def noisy_logs(self, count=500, replace=False):
        name = gateway.SERVICE
        stamp = self.docker.containers[name]["State"]["StartedAt"]
        messages = [(stamp, f"2026/10/14 00:00:00 server: session#{i} Open/Close\n")
                    for i in range(count)]
        if replace:
            self.docker.logs[name] = messages
        else:
            self.docker.logs[name].extend(messages)

    def test_cached_run_remains_ready_when_startup_logs_are_rotated_away(self):
        self.install()
        self.noisy_logs(replace=True)
        self.assertNotIn("Fingerprint", self.docker.call("logs", "--tail", "100", gateway.SERVICE))
        reopened = gateway.Gateway(self.root, self.docker, probe=self.docker.probe)
        before = len(self.docker.calls)
        status = reopened.status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["fingerprint"], FP)
        self.assertEqual(status["startup_identity"], "verified-cache")
        self.assertEqual(reopened.export_client("operator-node-e", True)["fingerprint"], FP)
        self.assertTrue(reopened.start()["ready"])
        self.assertTrue(self.install()["ready"])
        self.assertFalse(any(call[0] in ("logs", "run", "rm", "start", "stop")
                             for call in self.docker.calls[before:]))

    def test_uncached_run_finds_startup_beyond_old_100_line_tail(self):
        self.install()
        (self.service.state_dir / "startup-verification.json").unlink()
        self.noisy_logs()
        self.assertNotIn("Fingerprint", self.docker.call("logs", "--tail", "100", gateway.SERVICE))
        status = self.service.status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["startup_identity"], "verified-startup-logs")
        self.assertIn(("logs", "--since", status["started_at"], gateway.SERVICE), self.docker.calls)

    def test_cached_identity_never_replaces_current_version_or_network_health(self):
        self.install()
        self.noisy_logs(replace=True)
        with patch.object(self.service, "probe", return_value=False):
            status = self.service.status()
        self.assertFalse(status["ready"])
        self.assertFalse(status["endpoint_reachable"])
        self.assertEqual(status["startup_identity"], "verified-cache")
        original = self.docker.call

        def call(*args):
            return "unexpected-version\n" if args[0] == "exec" else original(*args)

        with patch.object(self.docker, "call", side_effect=call):
            status = self.service.status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["version"], "unexpected-version")
        self.assertTrue(self.service.status()["ready"])

    def test_restart_same_container_new_timestamp_requires_new_startup_evidence(self):
        self.install()
        name = gateway.SERVICE
        original_id = self.docker.containers[name]["Id"]
        cache_path = self.service.state_dir / "startup-verification.json"
        prior = gateway.read_json(cache_path)["records"][name]
        self.docker.containers[name]["State"]["StartedAt"] = self.docker.next_start()

        status = self.service.status()
        self.assertEqual(status["container_id"], original_id)
        self.assertFalse(status["ready"])
        self.assertIsNone(status["fingerprint"])
        self.assertFalse(status["listener_reported"])
        with self.assertRaisesRegex(gateway.GatewayError, "Readiness failed"):
            self.service.export_client("operator-node-e", True)
        self.docker.startup_logs(name)
        self.assertTrue(self.service.start()["ready"])
        renewed = gateway.read_json(cache_path)["records"][name]
        self.assertNotEqual(prior["provenance"]["started_at"], renewed["provenance"]["started_at"])
        self.assertEqual(renewed["provenance"]["container_id"], original_id)
        self.assertEqual(renewed["source"], "docker-startup-logs")

    def test_new_container_id_cannot_reuse_verified_cache(self):
        self.install()
        name = gateway.SERVICE
        self.docker.containers[name]["Id"] = "b" * 64
        self.noisy_logs(replace=True)
        self.assertFalse(self.service.status()["ready"])
        self.docker.startup_logs(name)
        self.assertTrue(self.service.status()["ready"])
        record = gateway.read_json(self.service.state_dir / "startup-verification.json")["records"][name]
        self.assertEqual(record["provenance"]["container_id"], "b" * 64)

    def test_restart_changed_fingerprint_is_rejected(self):
        self.install()
        name = gateway.SERVICE
        self.docker.containers[name]["State"]["StartedAt"] = self.docker.next_start()
        self.docker.startup_logs(name)
        stamp, line = self.docker.logs[name][-2]
        self.docker.logs[name][-2] = (stamp, line.replace(FP, "B" * 43 + "="))
        with self.assertRaisesRegex(gateway.GatewayError, "fingerprint drift"):
            self.service.status()

    def test_cache_provenance_drift_is_explicit(self):
        self.install()
        path = self.service.state_dir / "startup-verification.json"
        cache = gateway.read_json(path)
        cache["records"][gateway.SERVICE]["provenance"]["root"] = "foreign root"
        gateway.write_file(path, gateway.encoded(cache))
        with self.assertRaisesRegex(gateway.GatewayError, "cache record is invalid"):
            self.service.status()

    def test_restart_during_health_check_never_certifies_old_run(self):
        self.install()
        original = self.docker.call

        def call(*args):
            result = original(*args)
            if args[0] == "exec":
                self.docker.containers[gateway.SERVICE]["State"]["StartedAt"] = self.docker.next_start()
            return result

        with patch.object(self.docker, "call", side_effect=call):
            status = self.service.status()
        self.assertFalse(status["ready"])
        self.assertIsNone(status["fingerprint"])
        self.assertEqual(status["startup_identity"], "unverified")

    def test_foreign_candidate_and_fingerprint_drift_refuse_without_active_stop(self):
        self.install()
        old = self.service.state()
        self.change_source()
        self.docker.containers[gateway.SERVICE + "-candidate"] = {"Config": {"Labels": {}}}
        with self.assertRaisesRegex(gateway.GatewayError, "candidate"):
            self.install()
        self.assertEqual(self.service.state(), old)
        del self.docker.containers[gateway.SERVICE + "-candidate"]
        original = self.docker.call

        def call(*args):
            result = original(*args)
            if args[0] == "logs":
                return result.replace(FP, "B" * 43 + "=")
            return result

        with patch.object(self.docker, "call", side_effect=call):
            with self.assertRaisesRegex(gateway.GatewayError, "fingerprint drift"):
                self.install()
        self.assertEqual(self.service.state(), old)
        self.assertTrue(self.service.status()["ready"])

    def test_rollback_failure_is_explicit_not_success_shaped(self):
        self.install()
        old = self.service.state()
        self.change_source()
        original = self.service.wait_ready

        def ready(name, state, port):
            if name == gateway.SERVICE:
                raise gateway.GatewayError("simulated unavailable")
            return original(name, state, port)

        with patch.object(self.service, "wait_ready", side_effect=ready):
            with self.assertRaisesRegex(gateway.GatewayError, "rollback readiness also failed"):
                self.install()
        self.assertEqual(self.service.state(), old)

    def test_stop_start_uninstall_reinstall_keeps_identity(self):
        self.install()
        material = self.service.material_digest()
        self.assertFalse(self.service.stop()["running"])
        self.assertTrue(self.service.start()["ready"])
        self.assertTrue(self.service.uninstall()["configuration_retained"])
        self.assertFalse(self.service.status()["installed"])
        self.install()
        self.assertEqual(self.service.material_digest(), material)
        self.service.uninstall(remove_config=True)
        self.assertEqual(list(self.service.config.iterdir()), [])

    def test_container_command_preserves_production_ports_and_hardening(self):
        self.install()
        slot = self.service.versions / self.service.state()["slot"]
        self.assertIn("COPY --chmod=0555 chisel /chisel", (slot / "Dockerfile").read_text())
        for call in self.docker.calls:
            if call[0] == "run":
                self.assertIn("--read-only", call)
                self.assertIn("no-new-privileges", call)
                self.assertNotIn("--socks5", call)
                self.assertIn("15s", call)
                self.assertNotIn("8787", call)
                self.assertNotIn("13001", call)
                self.assertNotIn("13005", call)
        self.assertTrue(all(call[-1] in (gateway.SERVICE, gateway.SERVICE + "-candidate")
                            for call in self.docker.calls if call[0] in ("rm", "start", "stop")))

    def test_invalid_json_and_image_tag_drift_refused(self):
        self.install()
        self.docker.images[self.service.state()["image"]] = "sha256:foreign"
        with self.assertRaisesRegex(gateway.GatewayError, "tag drift"):
            self.service.status()
        with self.assertRaises(gateway.GatewayError):
            gateway.read_json(self.source)

    def test_docker_logs_captures_stderr_without_exposing_command_error(self):
        self.stack.close()
        completed = subprocess.CompletedProcess(["docker"], 0, "", "server: Fingerprint " + FP + "\n")
        with patch.object(gateway.subprocess, "run", return_value=completed):
            self.assertIn(FP, gateway.Docker().call("logs", "--tail", "100", "owned"))
        failed = subprocess.CompletedProcess(["docker"], 1, "", "private secret contents")
        with patch.object(gateway.subprocess, "run", return_value=failed):
            with self.assertRaises(gateway.GatewayError) as error:
                gateway.Docker().call("exec", "owned", "/chisel")
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
