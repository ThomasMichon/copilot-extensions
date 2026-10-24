from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trusted_base_build", ROOT / "build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
previous_build = sys.modules.get("build")
sys.modules["build"] = builder
install_spec = importlib.util.spec_from_file_location("trusted_base_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(install_spec)
install_spec.loader.exec_module(installer)
if previous_build is None:
    del sys.modules["build"]
else:
    sys.modules["build"] = previous_build
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def archive(path: Path, *, architecture="amd64", corrupt=False) -> None:
    entries = {}

    def blob(value: bytes) -> str:
        digest = "sha256:" + hashlib.sha256(value).hexdigest()
        entries["blobs/sha256/" + digest[7:]] = value
        return digest

    config = blob(json.dumps({"os": "linux", "architecture": architecture}).encode())
    layer = blob(b"layer-bytes")
    manifest = blob(json.dumps({
        "schemaVersion": 2, "config": {"digest": config},
        "layers": [{"digest": layer, "size": len(b"layer-bytes")}],
    }).encode())
    entries["index.json"] = json.dumps({
        "schemaVersion": 2, "manifests": [{"digest": manifest}],
    }).encode()
    if corrupt:
        entries["blobs/sha256/" + layer[7:]] = b"wrong-bytes"
    with tarfile.open(path, "w") as output:
        for name, content in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            output.addfile(entry, io.BytesIO(content))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT.parents[1] / ".trusted-base-test-runs" / uuid.uuid4().hex
        self.work.mkdir(parents=True)
        self.files = builder.context_files()
        self.inputs = {"schema_version": 1, "source_revision": "a" * 40,
                       "context_sha256": builder.context_digest(self.files),
                       "timeout_seconds": 60,
                       "builder": {"name": "image-proof", "container_id": "b" * 64,
                                   "max_memory_bytes": 1073741824, "max_cpus": 2}}
        self.lock = json.loads(self.files["dependencies.lock.json"])

    def inspect(self, argv):
        self.assertEqual(argv[1:3], ["--context", "default"])
        if argv[3:5] == ["context", "inspect"]:
            return json.dumps({"Host": "unix:///var/run/docker.sock", "SkipTLSVerify": False})
        if argv[3:5] == ["buildx", "ls"]:
            return json.dumps({"Name": "image-proof", "Driver": "docker-container",
                               "Nodes": [{"Name": "image-proof0", "Status": "running",
                                          "Endpoint": "default"}]})
        if argv[3] == "inspect":
            return json.dumps([{
                "Id": "b" * 64, "Name": "/buildx_buildkit_image-proof0",
                "State": {"Running": True},
                "HostConfig": {"Memory": 1073741824, "MemorySwap": 1073741824,
                               "NanoCpus": 2000000000},
            }])
        self.fail("Unexpected inspection")

    def tearDown(self):
        shutil.rmtree(self.work)
        try:
            self.work.parent.rmdir()
        except OSError:
            pass

    def test_locked_recipe_is_seasoned(self):
        builder.validate_age(self.lock, NOW)
        self.assertIn(self.lock["base"]["image"], self.files["Dockerfile"].decode())
        self.assertNotIn("ARG ", self.files["Dockerfile"].decode())
        self.assertEqual(self.lock["archives"]["copilot"]["version"], "1.0.98")

    def test_age_boundary_future_and_snapshot(self):
        for section in ("base", "apt"):
            lock = deepcopy(self.lock)
            lock[section]["published_at"] = "2026-10-18T00:00:01Z"
            with self.assertRaises(ValueError):
                builder.validate_age(lock, NOW)
        lock = deepcopy(self.lock)
        lock["archives"]["uv"]["published_at"] = "2027-01-01T00:00:00Z"
        with self.assertRaises(ValueError):
            builder.validate_age(lock, NOW)
        lock = deepcopy(self.lock)
        lock["apt"]["snapshot"] = "https://snapshot.debian.org/archive/debian/20261023T000000Z/"
        with self.assertRaises(ValueError):
            builder.validate_age(lock, NOW)

    def test_unknown_inputs_bool_schema_and_changed_context_refused(self):
        for patch in ({"token": "not-a-credential"}, {"platform": "linux/arm64"},
                      {"schema_version": True}, {"source_revision": "main"},
                      {"context_sha256": "0" * 64}):
            inputs = dict(self.inputs, **patch)
            with self.assertRaises(ValueError):
                builder.validate_inputs(inputs, self.inputs["context_sha256"])

    def test_context_is_allowlisted_and_line_ending_stable(self):
        self.assertEqual(set(self.files), set(builder.CONTEXT))
        self.assertEqual(builder.context_digest(self.files),
                         builder.context_digest({k: v for k, v in reversed(list(self.files.items()))}))
        changed = dict(self.files, **{"entrypoint.sh": b"changed"})
        self.assertNotEqual(builder.context_digest(changed), self.inputs["context_sha256"])

    def test_build_command_is_fixed_and_preserves_path_boundaries(self):
        argv = builder.build_command("docker", "image-proof", self.work / "context with spaces",
                                     self.work / "image with spaces.oci.tar")
        self.assertEqual(argv[:9], ["docker", "--context", "default",
                                   "buildx", "build", "--builder", "image-proof",
                                   "--platform", "linux/amd64"])
        self.assertEqual(argv[-1], str(self.work / "context with spaces"))
        for forbidden in ("--push", "--load", "--build-arg", "--secret", "--ssh"):
            self.assertNotIn(forbidden, argv)

    def test_real_subprocess_preserves_metacharacters(self):
        marker = str(self.work / "must-not-exist")
        payload = "space ; & | $() " + marker
        builder.execute([sys.executable, "-B", "-c",
                         "import sys; assert sys.argv[1] == " + repr(payload), payload])
        self.assertFalse(Path(marker).exists())

    def test_real_cli_describe(self):
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "build.py"), "--describe"],
            capture_output=True, text=True, check=True, **flags,
        )
        self.assertEqual(json.loads(result.stdout)["context_sha256"], self.inputs["context_sha256"])

    def test_actual_subprocess_timeout_is_bounded(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            builder.execute([sys.executable, "-B", "-c", "import time; time.sleep(60)"], timeout=0.1)

    def test_build_stages_only_approved_bytes_and_inspects_archive(self):
        output = self.work / "artifact directory"

        def runner(argv, *, timeout):
            if argv[3:5] != ["buildx", "build"]:
                return self.inspect(argv)
            self.assertEqual(timeout, 60)
            self.assertEqual(argv[-1], str(output / "context"))
            started = json.loads((output / "build-receipt.json").read_text())
            self.assertEqual(started["status"], "build-started-outcome-unconfirmed")
            self.assertEqual(started["builder_container_id"], "b" * 64)
            archive(output / "image.oci.tar")
            return ""

        receipt = builder.build(self.inputs, output, runner=runner, now=NOW)
        self.assertEqual(receipt["status"], "built-not-runtime-validated")
        self.assertEqual(set(p.name for p in (output / "context").iterdir()), set(builder.CONTEXT))
        self.assertEqual(receipt["archive_sha256"], builder.file_sha256(output / "image.oci.tar"))
        self.assertEqual(receipt["source_revision_authority"], "caller-supplied")
        expected_approval = hashlib.sha256(
            json.dumps(self.inputs, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(receipt["approval_sha256"], expected_approval)
        self.assertNotIn(str(self.work), json.dumps(receipt))
        with self.assertRaises(FileExistsError):
            builder.build(self.inputs, output, runner=runner, now=NOW)

    def test_failed_engine_keeps_failure_receipt_without_success_claim(self):
        output = self.work / "failed"

        def runner(argv, *, timeout):
            if argv[3:5] != ["buildx", "build"]:
                return self.inspect(argv)
            raise subprocess.CalledProcessError(1, argv)

        with self.assertRaises(subprocess.CalledProcessError):
            builder.build(self.inputs, output, runner=runner, now=NOW)
        receipt = json.loads((output / "build-receipt.json").read_text())
        self.assertEqual(receipt["status"], "build-failed")
        self.assertNotIn("manifest_digest", receipt)
        self.assertEqual(receipt["builder_container_id"], "b" * 64)
        self.assertEqual(len(receipt["approval_sha256"]), 64)

    def test_wrong_platform_and_corrupt_layer_refused(self):
        for name, options in (("wrong", {"architecture": "arm64"}),
                              ("corrupt", {"corrupt": True})):
            path = self.work / name
            archive(path, **options)
            with self.assertRaises(ValueError):
                builder.oci_identity(path)

    def test_output_injection_and_own_recipe_refused_before_engine(self):
        for output in (ROOT / "output", self.work / "name,extra=value"):
            with self.assertRaises(ValueError):
                builder.build(self.inputs, output, runner=lambda _: self.fail("engine called"), now=NOW)

    def test_default_or_unbounded_builder_refused(self):
        for bounds in ({"Memory": 0}, {"NanoCpus": 0, "CpuQuota": -1},
                       {"MemorySwap": -1}, {"Memory": 2147483648},
                       {"NanoCpus": 4000000000}):
            def runner(argv, *, timeout):
                result = json.loads(self.inspect(argv))
                if argv[3] == "inspect":
                    result[0]["HostConfig"].update(bounds)
                return json.dumps(result)
            with self.assertRaises(ValueError):
                builder.verify_builder("docker", self.inputs["builder"], runner)
        def wrong_driver(argv, *, timeout):
            result = json.loads(self.inspect(argv))
            if argv[3:5] == ["buildx", "ls"]:
                result["Driver"] = "docker"
            return json.dumps(result)
        with self.assertRaises(ValueError):
            builder.verify_builder("docker", self.inputs["builder"], wrong_driver)

    def test_wrong_builder_instance_and_timeout_inputs_refused(self):
        def runner(argv, *, timeout):
            result = json.loads(self.inspect(argv))
            if argv[3] == "inspect":
                result[0]["Name"] = "/unrelated-service"
            return json.dumps(result)
        with self.assertRaises(ValueError):
            builder.verify_builder("docker", self.inputs["builder"], runner)
        for timeout in (0, True, 86401):
            with self.assertRaises(ValueError):
                builder.validate_inputs(dict(self.inputs, timeout_seconds=timeout),
                                        self.inputs["context_sha256"])

    def test_cancelled_build_retains_unknown_outcome(self):
        output = self.work / "cancelled"
        def runner(argv, *, timeout):
            if argv[3:5] != ["buildx", "build"]:
                return self.inspect(argv)
            raise subprocess.TimeoutExpired(argv, timeout)
        with self.assertRaises(subprocess.TimeoutExpired):
            builder.build(self.inputs, output, runner=runner, now=NOW)
        receipt = json.loads((output / "build-receipt.json").read_text())
        self.assertEqual(receipt["status"], "cancellation-requested-builder-outcome-unconfirmed")
        self.assertNotIn("manifest_digest", receipt)
        self.assertEqual(receipt["builder_container_id"], "b" * 64)
        self.assertEqual(len(receipt["approval_sha256"]), 64)

    def test_foreign_node_endpoint_cannot_borrow_local_container_limits(self):
        for endpoint in ("other-context", "ssh://remote.invalid", "unix:///other/socket"):
            def runner(argv, *, timeout):
                self.assertNotEqual(argv[3], "inspect", "must refuse before inspecting local container")
                result = json.loads(self.inspect(argv))
                if argv[3:5] == ["buildx", "ls"]:
                    result["Nodes"][0]["Endpoint"] = endpoint
                return json.dumps(result)
            with self.assertRaises(ValueError):
                builder.verify_builder("docker", self.inputs["builder"], runner)

    def test_foreign_default_context_and_changed_context_refused(self):
        calls = 0
        def runner(argv, *, timeout):
            nonlocal calls
            result = json.loads(self.inspect(argv))
            if argv[3:5] == ["context", "inspect"]:
                calls += 1
                if calls == 2:
                    result["Host"] = "tcp://remote.invalid:2376"
            return json.dumps(result)
        with self.assertRaises(ValueError):
            builder.verify_builder("docker", self.inputs["builder"], runner)

    def test_buildx_json_lines_deduplicate_only_identical_selected_records(self):
        selected = json.loads(self.inspect(["docker", "--context", "default", "buildx", "ls"]))
        selected["Nodes"][0]["Endpoint"] = "unix:///var/run/docker.sock"
        unrelated = {"Name": "default", "Driver": "docker", "Nodes": []}
        for conflicting in (False, True):
            def runner(argv, *, timeout):
                if argv[3:5] == ["buildx", "ls"]:
                    second = deepcopy(selected)
                    if conflicting:
                        second["Nodes"][0]["Name"] = "different-worker"
                    return "\n".join(json.dumps(x) for x in (unrelated, selected, unrelated, second))
                return self.inspect(argv)
            if conflicting:
                with self.assertRaises(ValueError):
                    builder.verify_builder("docker", self.inputs["builder"], runner)
            else:
                self.assertEqual(
                    builder.verify_builder("docker", self.inputs["builder"], runner)["cpus"], 2,
                )

    def test_runtime_identity_and_auth_boundary(self):
        entry = self.files["entrypoint.sh"].decode()
        self.assertIn("mountpoint -q", entry)
        self.assertIn('[ "$$" != 1 ]', entry)
        self.assertIn('findmnt -n -o PROPAGATION /', entry)
        self.assertIn('!= private', entry)
        self.assertIn("python3 /opt/trusted-harness/recipe/install.py --runtime", entry)
        self.assertNotIn("chown -R", entry)
        self.assertNotIn("rm -rf", entry)
        ssh = self.files["sshd.conf"].decode()
        for setting in ("PermitRootLogin no", "PasswordAuthentication no", "AllowUsers vscode"):
            self.assertIn(setting, ssh)
        self.assertIn("from build import validate_age", self.files["install.py"].decode())
        calls = [
            tuple(arg.value for arg in node.args if isinstance(arg, ast.Constant))
            for node in ast.walk(ast.parse(self.files["install.py"]))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run"
        ]
        self.assertIn(
            ("systemctl", "mask", "apt-daily.service", "apt-daily.timer",
             "apt-daily-upgrade.service", "apt-daily-upgrade.timer"),
            calls,
        )


class RuntimePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT.parents[1] / ".trusted-base-test-runs" / uuid.uuid4().hex
        self.home = self.work / "home"
        self.workspace = self.work / "workspaces"
        self.etc = self.work / "etc"
        for path in (self.home, self.workspace, self.etc):
            path.mkdir(parents=True)
        (self.etc / "machine-id").write_text("")
        self.owners = {}
        self.modes = {}
        self.public = "ssh-ed25519 AAAAtestkey fixture\n"
        self.commands = []
        self.patches = mock.patch.multiple(
            installer, ownership=lambda p: self.owners.get(p, (0, 0)),
            permissions=lambda p: self.modes.get(p, 0o700 if p.is_dir() else 0o644),
            set_owner=lambda p, u, g: self.owners.__setitem__(p, (u, g)),
            set_permissions=lambda p, m: self.modes.__setitem__(p, m),
            run=self.keygen,
        )
        self.patches.start()

    def tearDown(self):
        self.patches.stop()
        shutil.rmtree(self.work)
        try:
            self.work.parent.rmdir()
        except OSError:
            pass

    def keygen(self, *args):
        self.commands.append(args)
        if "-y" in args:
            return self.public
        private = Path(args[-1])
        private.write_bytes(b"existing-key-bytes")
        private.with_suffix(".pub").write_text(self.public)
        return ""

    def dbus_reference(self):
        reference = self.work / "var/lib/dbus/machine-id"
        reference.parent.mkdir(parents=True)
        try:
            reference.symlink_to(self.etc / "machine-id")
        except OSError as error:
            self.skipTest("Host cannot create symlinks: " + str(error))

    def initialize(self):
        self.dbus_reference()
        installer.prepare_runtime(self.home, self.workspace, self.etc)
        return self.home / ".local/state/trusted-harness/identity"

    def test_public_regular_projection_with_private_home_and_identity(self):
        identity = self.initialize()
        self.modes[self.home] = 0o700
        original = {p.name: p.read_bytes() for p in identity.iterdir()}
        installer.prepare_runtime(self.home, self.workspace, self.etc)
        projected = self.etc / "machine-id"
        self.assertFalse(projected.is_symlink())
        self.assertEqual(projected.read_bytes(), original["machine-id"])
        self.assertEqual(self.modes[projected], 0o444)
        self.assertEqual(self.modes[identity], 0o700)
        self.assertEqual(self.modes[self.home], 0o700)
        self.assertEqual({p.name: p.read_bytes() for p in identity.iterdir()}, original)
        self.assertEqual(len([args for args in self.commands if "-q" in args]), 1)

    def test_foreign_and_nonempty_root_mounts_refused_without_adoption(self):
        for owner, content in (((1234, 1234), False), ((0, 1000), False), ((0, 0), True)):
            self.owners[self.home] = owner
            if content:
                (self.home / "existing").write_text("preserve")
            with self.assertRaises(ValueError):
                installer.prepare_runtime(self.home, self.workspace, self.etc)
            self.assertEqual(self.owners[self.home], owner)
            self.assertFalse((self.home / ".local").exists())
        self.owners[self.home] = (1000, 1000)
        self.owners[self.workspace] = (1234, 1234)
        with self.assertRaises(ValueError):
            installer.prepare_runtime(self.home, self.workspace, self.etc)

    def test_partial_identity_never_regenerates_host_key(self):
        identity = self.initialize()
        original_pub = (identity / "ssh_host_ed25519_key.pub").read_bytes()
        (identity / "ssh_host_ed25519_key").unlink()
        commands = len(self.commands)
        with self.assertRaises(ValueError):
            installer.prepare_runtime(self.home, self.workspace, self.etc)
        self.assertEqual(len(self.commands), commands)
        self.assertEqual((identity / "ssh_host_ed25519_key.pub").read_bytes(), original_pub)
        self.assertFalse((identity / "ssh_host_ed25519_key").exists())

    def test_missing_public_key_or_machine_id_and_empty_identity_refused(self):
        identity = self.initialize()
        for name in ("ssh_host_ed25519_key.pub", "machine-id"):
            path = identity / name
            content = path.read_bytes()
            path.unlink()
            commands = len(self.commands)
            with self.assertRaises(ValueError):
                installer.prepare_runtime(self.home, self.workspace, self.etc)
            self.assertEqual(len(self.commands), commands)
            path.write_bytes(content)
        for path in identity.iterdir():
            path.unlink()
        with self.assertRaises(ValueError):
            installer.prepare_runtime(self.home, self.workspace, self.etc)
        self.assertFalse(any(identity.iterdir()))

    def test_malformed_machine_and_keypair_refused_without_rewriting(self):
        identity = self.initialize()
        machine = identity / "machine-id"
        machine.write_text("invalid\n")
        with self.assertRaises(ValueError):
            installer.prepare_runtime(self.home, self.workspace, self.etc)
        self.assertEqual(machine.read_text(), "invalid\n")
        machine.write_text("1" * 32 + "\n")
        public = identity / "ssh_host_ed25519_key.pub"
        public.write_text("ssh-ed25519 AAAAdifferent\n")
        private_before = (identity / "ssh_host_ed25519_key").read_bytes()
        with self.assertRaises(ValueError):
            installer.prepare_runtime(self.home, self.workspace, self.etc)
        self.assertEqual((identity / "ssh_host_ed25519_key").read_bytes(), private_before)

    def test_foreign_or_wrong_mode_identity_refused(self):
        identity = self.initialize()
        for target, owner, mode in (
            (identity, (1000, 1000), 0o700),
            (identity, (0, 0), 0o755),
            (identity / "machine-id", (1000, 1000), 0o444),
            (identity / "ssh_host_ed25519_key", (0, 0), 0o644),
        ):
            old_owner = self.owners[target]
            old_mode = self.modes[target]
            self.owners[target], self.modes[target] = owner, mode
            with self.assertRaises(ValueError):
                installer.prepare_runtime(self.home, self.workspace, self.etc)
            self.owners[target], self.modes[target] = old_owner, old_mode

    def test_linked_mount_home_subdirectory_and_identity_file_refused(self):
        identity = self.initialize()
        for original in (identity / "machine-id", identity / "ssh_host_ed25519_key",
                         identity / "ssh_host_ed25519_key.pub", identity,
                         self.home / ".local", self.workspace, self.home):
            saved = original.with_name(original.name + "-saved")
            original.rename(saved)
            try:
                original.symlink_to(saved, target_is_directory=saved.is_dir())
                with self.assertRaises(ValueError):
                    installer.prepare_runtime(self.home, self.workspace, self.etc)
            finally:
                original.unlink(missing_ok=True)
                saved.rename(original)

    def test_linked_projection_refused_without_changing_canonical_id(self):
        identity = self.initialize()
        original = (identity / "machine-id").read_bytes()
        projected = self.etc / "machine-id"
        projected.unlink()
        projected.symlink_to(identity / "machine-id")
        with self.assertRaises(ValueError):
            installer.prepare_runtime(self.home, self.workspace, self.etc)
        self.assertEqual((identity / "machine-id").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
