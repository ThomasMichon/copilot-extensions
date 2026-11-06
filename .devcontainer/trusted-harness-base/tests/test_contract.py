from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import ast
import hashlib
import importlib.util
from importlib.abc import Loader
from importlib.machinery import ModuleSpec
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
from typing import Any, Callable, cast
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = cast(ModuleSpec, importlib.util.spec_from_file_location("trusted_base_build", ROOT / "build.py"))
builder = importlib.util.module_from_spec(spec)
cast(Loader, spec.loader).exec_module(builder)
previous_build = sys.modules.get("build")
sys.modules["build"] = builder
install_spec = cast(ModuleSpec, importlib.util.spec_from_file_location("trusted_base_install", ROOT / "install.py"))
installer = importlib.util.module_from_spec(install_spec)
cast(Loader, install_spec.loader).exec_module(installer)
if previous_build is None:
    del sys.modules["build"]
else:
    sys.modules["build"] = previous_build
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def archive(path: Path, *, architecture: str = "amd64", corrupt: bool = False) -> None:
    entries: dict[str, bytes] = {}

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
    def setUp(self) -> None:
        self.work = ROOT.parents[1] / ".trusted-base-test-runs" / uuid.uuid4().hex
        self.work.mkdir(parents=True)
        if os.name == "nt":
            # The real builder requires Linux's directory durability primitive.
            patch = mock.patch.object(builder, "fsync_directory")
            patch.start()
            self.addCleanup(patch.stop)
        self.files: dict[str, bytes] = builder.context_files()
        self.inputs: dict[str, Any] = {"schema_version": 1, "source_revision": "a" * 40,
                       "context_sha256": builder.context_digest(self.files),
                       "timeout_seconds": 60,
                       "builder": {"name": "image-proof", "container_id": "b" * 64,
                                   "max_memory_bytes": 1073741824, "max_cpus": 2}}
        self.lock: dict[str, Any] = json.loads(self.files["dependencies.lock.json"])
        self.docker_config = self.work / "ambient-docker"
        self.buildx_config = self.work / "ambient-buildx"
        (self.buildx_config / "instances").mkdir(parents=True)
        self.metadata: dict[str, Any] = {"Name": "image-proof", "Driver": "docker-container", "Dynamic": False,
                         "Nodes": [{"Name": "image-proof0", "Endpoint": "default", "Files": {},
                                    "Platforms": None,
                                    "Flags": ["--allow-insecure-entitlement=network.host"],
                                    "DriverOpts": {
                                        "image": "moby/buildkit@sha256:"
                                                 "2be0a5be7ae3cd9268f6836782e9690cce7bc4124142f7700f1641af5ccca8de",
                                        "memory": "1g", "memory-swap": "1g",
                                        "cpu-period": "100000", "cpu-quota": "200000",
                                    }}]}
        self.instance = self.buildx_config / "instances" / "image-proof"
        self.instance.write_text(json.dumps(self.metadata))
        environment = mock.patch.dict(os.environ, {
            "DOCKER_CONFIG": str(self.docker_config), "BUILDX_CONFIG": str(self.buildx_config),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def inspect(self, argv: list[str]) -> str:
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
                "Config": {"Image": builder.BUILDKIT_IMAGE},
                "HostConfig": {"Memory": 1073741824, "MemorySwap": 1073741824,
                               "NanoCpus": 2000000000},
            }])
        self.fail("Unexpected inspection")

    def tearDown(self) -> None:
        shutil.rmtree(self.work)
        try:
            self.work.parent.rmdir()
        except OSError:
            pass

    def test_locked_recipe_is_seasoned(self) -> None:
        builder.validate_age(self.lock, NOW)
        self.assertIn(self.lock["base"]["image"], self.files["Dockerfile"].decode())
        self.assertNotIn("ARG ", self.files["Dockerfile"].decode())
        self.assertEqual(self.lock["archives"]["copilot"]["version"], "1.0.98")

    def test_age_boundary_future_and_snapshot(self) -> None:
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

    def test_unknown_inputs_bool_schema_and_changed_context_refused(self) -> None:
        for patch in ({"token": "not-a-credential"}, {"platform": "linux/arm64"},
                      {"schema_version": True}, {"source_revision": "main"},
                      {"context_sha256": "0" * 64}):
            inputs = dict(self.inputs, **patch)
            with self.assertRaises(ValueError):
                builder.validate_inputs(inputs, self.inputs["context_sha256"])

    def test_context_is_allowlisted_and_line_ending_stable(self) -> None:
        self.assertEqual(set(self.files), set(builder.CONTEXT))
        self.assertEqual(builder.context_digest(self.files),
                         builder.context_digest({k: v for k, v in reversed(list(self.files.items()))}))
        changed = dict(self.files, **{"entrypoint.sh": b"changed"})
        self.assertNotEqual(builder.context_digest(changed), self.inputs["context_sha256"])

    def test_build_command_is_fixed_and_preserves_path_boundaries(self) -> None:
        argv = builder.build_command("docker", "image-proof", self.work / "context with spaces",
                                     self.work / "image with spaces.oci.tar")
        self.assertEqual(argv[:9], ["docker", "--context", "default",
                                   "buildx", "build", "--builder", "image-proof",
                                   "--platform", "linux/amd64"])
        self.assertEqual(argv[-1], str(self.work / "context with spaces"))
        for forbidden in ("--push", "--load", "--build-arg", "--secret", "--ssh"):
            self.assertNotIn(forbidden, argv)

    def test_real_subprocess_preserves_metacharacters(self) -> None:
        marker = str(self.work / "must-not-exist")
        payload = "space ; & | $() " + marker
        builder.execute([sys.executable, "-B", "-c",
                         "import sys; assert sys.argv[1] == " + repr(payload), payload])
        self.assertFalse(Path(marker).exists())

    def test_real_cli_describe(self) -> None:
        flags: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")} if os.name == "nt" else {}
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "build.py"), "--describe"],
            capture_output=True, text=True, check=True, **flags,
        )
        self.assertEqual(json.loads(result.stdout)["context_sha256"], self.inputs["context_sha256"])

    def test_actual_subprocess_timeout_is_bounded(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            builder.execute([sys.executable, "-B", "-c", "import time; time.sleep(60)"], timeout=0.1)

    def test_actual_descendant_pipe_timeout_is_bounded_and_contained(self) -> None:
        pid_file = self.work / "descendant.pid"
        descendant = (
            "import os,pathlib,signal,sys,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
        )
        parent = (
            "import os,pathlib,subprocess,sys,time; "
            "flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}; "
            "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]], "
            "stdin=subprocess.DEVNULL, **flags); "
            "path=pathlib.Path(sys.argv[2]); "
            "exec('while not path.exists(): time.sleep(0.01)')"
        )
        execute_contained = builder.execute_contained
        started = 0.0

        def after_startup(
            command: list[str],
            argv: list[str],
            timeout: float,
            assign: Callable[[subprocess.Popen[str]], None],
            terminate: Callable[[], None] | None,
            **flags: Any,
        ) -> str:
            def assign_and_wait(process: subprocess.Popen[str]) -> None:
                nonlocal started
                assign(process)
                # Release the existing Windows gate only after Job assignment.
                if process.stdin is not None:
                    process.stdin.write("1")
                    process.stdin.flush()
                    process.stdin.close()
                deadline = time.monotonic() + 30
                while not pid_file.exists():
                    if time.monotonic() >= deadline:
                        self.fail(
                            "Descendant fixture startup did not reach its PID marker"
                        )
                    time.sleep(0.01)
                started = time.monotonic()

            return cast(
                str,
                execute_contained(
                    command, argv, timeout, assign_and_wait, terminate, **flags
                ),
            )

        # Interpreter startup is not the descendant-pipe timeout under test.
        with mock.patch.object(builder, "execute_contained", side_effect=after_startup):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                builder.execute(
                    [sys.executable, "-B", "-c", parent, descendant, str(pid_file)],
                    timeout=1,
                )
        self.assertEqual(caught.exception.timeout, 1)
        self.assertLess(time.monotonic() - started, 5)
        pid = int(pid_file.read_text())
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
                finally:
                    kernel.CloseHandle(handle)
        else:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                # An orphan awaiting its system reaper is no longer executing.
                stat = Path("/proc") / str(pid) / "stat"
                if stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z "):
                    break
                time.sleep(0.01)
            else:
                self.fail("Invocation-owned descendant survived cleanup")

    def test_cleanup_bounds_final_pipe_drain_and_child_wait(self) -> None:
        process = mock.Mock()
        process.pid = 123
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["fixture"], 0.1),
            subprocess.TimeoutExpired(["fixture"], builder.CLEANUP_TIMEOUT),
            subprocess.TimeoutExpired(["fixture"], builder.CLEANUP_TIMEOUT),
        ]
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired(["fixture"], builder.CLEANUP_TIMEOUT)
        with mock.patch.object(builder.subprocess, "Popen", return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                builder.execute_contained(["fixture"], ["fixture"], 0.1, mock.Mock(), mock.Mock())
        self.assertEqual(caught.exception.timeout, 0.1)
        self.assertEqual([call.kwargs["timeout"] for call in process.communicate.call_args_list],
                         [0.1, builder.CLEANUP_TIMEOUT, builder.CLEANUP_TIMEOUT])
        process.wait.assert_called_once_with(timeout=builder.CLEANUP_TIMEOUT)

    def test_lost_or_reused_posix_custody_never_signals(self) -> None:
        for disposition, returncode, status in (
            (signal.SIG_DFL, None, ChildProcessError()),
            (signal.SIG_DFL, None, SimpleNamespace(si_pid=456)),
            (signal.SIG_DFL, 0, None),
            (signal.SIG_IGN, None, None),
        ):
            with self.subTest(disposition=disposition, returncode=returncode, status=status):
                process = mock.Mock(pid=123, returncode=returncode)
                waitid = mock.Mock()
                if isinstance(status, BaseException):
                    waitid.side_effect = status
                else:
                    waitid.return_value = status
                with (mock.patch.multiple(builder.os, P_PID=1, WEXITED=4, WNOHANG=1,
                                          WNOWAIT=0x1000000, waitid=waitid,
                                          killpg=mock.Mock(), create=True),
                      mock.patch.multiple(builder.signal, SIGCHLD=17, SIGKILL=9,
                                          getsignal=mock.Mock(return_value=disposition), create=True),
                      mock.patch.object(builder.subprocess, "Popen", return_value=process),
                      mock.patch.object(builder, "communicate_unreaped",
                                        side_effect=subprocess.TimeoutExpired(["fixture"], 0.1))):
                    with self.assertRaisesRegex(builder.ChildCustodyError, "custody|identity"):
                        builder.execute_contained(["fixture"], ["fixture"], 0.1, mock.Mock(), None)
                    builder.os.killpg.assert_not_called()
                process.communicate.assert_not_called()
                process.poll.assert_not_called()
                process.kill.assert_not_called()
                process.wait.assert_not_called()
                process.stdout.close.assert_called_once()
                process.stderr.close.assert_called_once()

    def test_posix_leader_stays_unreaped_until_group_retirement(self) -> None:
        process = mock.Mock(pid=123, returncode=None)
        events: list[str] = []

        def drain(*args: Any) -> tuple[str, str]:
            self.assertIsNone(process.returncode)
            process.wait.assert_not_called()
            events.append("drain")
            return "captured", ""

        def observe(*args: Any) -> SimpleNamespace:
            events.append("observe")
            self.assertIsNone(process.returncode)
            return SimpleNamespace(si_pid=123)

        def retire(pid: int, sig: int) -> None:
            self.assertEqual((pid, sig), (123, 9))
            self.assertIsNone(process.returncode)
            process.wait.assert_not_called()
            events.append("retire")

        def wait(*, timeout: float) -> None:
            self.assertEqual(timeout, builder.CLEANUP_TIMEOUT)
            events.append("wait")
            process.returncode = 0

        process.wait.side_effect = wait
        with (mock.patch.multiple(builder.os, P_PID=1, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000,
                                  waitid=observe, killpg=retire, create=True),
              mock.patch.multiple(builder.signal, SIGCHLD=17, SIGKILL=9,
                                  getsignal=mock.Mock(return_value=signal.SIG_DFL), create=True),
              mock.patch.object(builder.subprocess, "Popen", return_value=process),
              mock.patch.object(builder, "communicate_unreaped", side_effect=drain)):
            self.assertEqual(
                builder.execute_contained(["fixture"], ["fixture"], 1, mock.Mock(), None),
                "captured",
            )
        self.assertEqual(events, ["drain", "observe", "retire", "wait"])
        process.communicate.assert_not_called()
        process.poll.assert_not_called()
        process.kill.assert_not_called()

    def test_unconfirmed_cleanup_cannot_return_success(self) -> None:
        process = mock.Mock(returncode=0)
        process.communicate.return_value = ("completed", "")
        process.wait.side_effect = subprocess.TimeoutExpired(["fixture"], builder.CLEANUP_TIMEOUT)
        with mock.patch.object(builder.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(builder.ChildCustodyError, "cleanup is unconfirmed"):
                builder.execute_contained(["fixture"], ["fixture"], 1, mock.Mock(), mock.Mock())

    def test_job_docker_config_snapshots_only_selected_local_builder(self) -> None:
        self.docker_config.mkdir()
        (self.docker_config / "config.json").write_text(json.dumps({
            "auths": {"registry.invalid": {"auth": "synthetic-credential"}},
            "credsStore": "synthetic-helper", "credHelpers": {"registry.invalid": "helper"},
            "proxies": {"default": {"httpProxy": "http://synthetic-proxy.invalid"}},
            "currentContext": "foreign",
        }))
        output = self.work / "isolated"
        output.mkdir()
        ambient = {"HOME": str(self.work / "ambient-home"),
                   "USERPROFILE": str(self.work / "ambient-home"),
                   "HTTP_PROXY": "http://synthetic-proxy.invalid",
                   "https_proxy": "http://synthetic-proxy.invalid",
                   "ALL_PROXY": "http://synthetic-proxy.invalid", "NO_PROXY": "private.invalid",
                   "DOCKER_HOST": "tcp://foreign.invalid", "DOCKER_CONTEXT": "foreign",
                   "DOCKER_AUTH_CONFIG": "synthetic-credential",
                   "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": "synthetic-certs",
                   "BUILDX_BUILDER": "foreign", "BUILDX_BAKE_GIT_AUTH_TOKEN": "synthetic-credential",
                   "XDG_CONFIG_HOME": str(self.work / "ambient-xdg")}
        with mock.patch.dict(os.environ, ambient):
            environment = builder.docker_environment(self.inputs["builder"], output)
        client = output / "docker-client"
        self.assertEqual(json.loads((client / "config.json").read_text()), {"auths": {"": {}}})
        snapshot = json.loads((client / "buildx" / "instances" / "image-proof").read_text())
        self.assertEqual(snapshot, dict(self.metadata, Dynamic=False))
        self.instance.write_text("mutated after snapshot")
        self.assertEqual(json.loads((client / "buildx" / "instances" / "image-proof").read_text()),
                         snapshot)
        self.assertEqual({p.relative_to(client).as_posix() for p in client.rglob("*") if p.is_file()},
                         {"config.json", "buildx/instances/image-proof"})
        for name in ambient:
            if name not in ("HOME", "USERPROFILE"):
                self.assertNotIn(name, environment)
        self.assertEqual(environment["HOME"], str(client))
        self.assertEqual(environment["USERPROFILE"], str(client))
        self.assertEqual(environment["DOCKER_CONFIG"], str(client))
        self.assertEqual(environment["BUILDX_CONFIG"], str(client / "buildx"))
        self.assertNotIn("synthetic", json.dumps(environment))

    def test_job_docker_metadata_fails_closed_on_unknown_or_foreign_builder(self) -> None:
        patch: dict[str, Any]
        for patch in ({"Name": "foreign"}, {"Driver": "remote"}, {"Dynamic": True},
                      {"Nodes": []}, {"Nodes": [{"Name": "node", "Endpoint": "ssh://foreign.invalid"}]},
                      {"Nodes": [{"Name": "node", "Endpoint": "default", "Files": {"key": "value"}}]},
                      {"Nodes": [{"Name": "node", "Endpoint": "default", "DriverOpts": {"key": "v"}}]},
                      {"Nodes": [{"Name": "node", "Endpoint": "default", "Flags": ["--debug"]}]}):
            with self.subTest(patch=patch):
                self.instance.write_text(json.dumps(dict(self.metadata, **patch)))
                with self.assertRaises(ValueError):
                    builder.docker_environment(self.inputs["builder"], self.work)
                self.assertFalse((self.work / "docker-client").exists())

    def test_bounded_buildx_metadata_preserves_admitted_real_shape(self) -> None:
        inputs = deepcopy(self.inputs)
        inputs["builder"].update(max_memory_bytes=8 * 1024**3, max_cpus=4)
        metadata = deepcopy(self.metadata)
        metadata["Nodes"][0]["Endpoint"] = "unix:///var/run/docker.sock"
        metadata["Nodes"][0]["DriverOpts"].update(
            memory="8g", **{"memory-swap": "8g", "cpu-quota": "400000"},
        )
        self.instance.write_text(json.dumps(metadata))
        output = self.work / "bounded-build"
        calls: list[list[str]] = []

        def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
            calls.append(argv)
            client = Path(env["DOCKER_CONFIG"])
            copied = json.loads((Path(env["BUILDX_CONFIG"]) / "instances" / "image-proof").read_text())
            self.assertEqual(copied, metadata)
            self.assertEqual(json.loads((client / "config.json").read_text()), {"auths": {"": {}}})
            if argv[3:5] == ["buildx", "build"]:
                archive(output / "image.oci.tar")
                return ""
            inspected = json.loads(self.inspect(argv))
            if argv[3] == "inspect":
                inspected[0]["HostConfig"].update(
                    Memory=8 * 1024**3, MemorySwap=8 * 1024**3, NanoCpus=4_000_000_000,
                )
            return json.dumps(inspected)

        receipt = builder.build(inputs, output, runner=runner, now=NOW)
        self.assertEqual(receipt["resource_bounds"],
                         {"memory_bytes": 8 * 1024**3, "cpus": 4, "swap_disabled": True})
        self.assertEqual(receipt["status"], "built-not-runtime-validated")
        self.assertEqual(len(calls), 9)
        self.assertEqual(sum(argv[3] == "inspect" for argv in calls), 2)

    def test_unsafe_bounded_metadata_refused_before_any_cli_command(self) -> None:
        cases: list[dict[str, Any]] = []
        patch: dict[str, Any]
        for patch in (
            {"env.HTTP_PROXY": "http://synthetic-proxy.invalid"},
            {"env.HTTPS_PROXY": "http://synthetic-proxy.invalid"},
            {"unknown-option": "value"}, {"network": "host"},
            {"image": "moby/buildkit:latest"},
            {"image": "moby/buildkit@sha256:" + "0" * 64},
            {"image": "synthetic-user:synthetic-password@registry.invalid/buildkit"},
            {"memory": "2g"}, {"memory": "0"}, {"memory": "1g "}, {"memory": True},
            {"memory-swap": "-1"}, {"memory-swap": "2g"}, {"memory-swap": "512m"},
            {"cpu-period": "0"}, {"cpu-period": "1000001"}, {"cpu-period": "1000.0"},
            {"cpu-quota": "200001"}, {"cpu-quota": "-1"}, {"cpu-quota": "unbounded"},
        ):
            metadata = deepcopy(self.metadata)
            metadata["Nodes"][0]["DriverOpts"].update(patch)
            cases.append(metadata)
        for patch in (
            {"Flags": ["--debug"]},
            {"Flags": ["--allow-insecure-entitlement=security.insecure"]},
            {"Flags": ["--allow-insecure-entitlement=network.host"] * 2},
            {"Flags": "--allow-insecure-entitlement=network.host"},
            {"Files": {"buildkitd.toml": "synthetic-sensitive"}},
            {"Files": []}, {"Endpoint": "ssh://foreign.invalid"},
            {"Platforms": ["linux/amd64"]}, {"DriverOpts": {}},
            {"DriverOpts": []}, {"Unknown": "synthetic-sensitive"},
        ):
            metadata = deepcopy(self.metadata)
            metadata["Nodes"][0].update(patch)
            cases.append(metadata)
        missing_bound = deepcopy(self.metadata)
        del missing_bound["Nodes"][0]["DriverOpts"]["cpu-quota"]
        cases.append(missing_bound)
        cases.append(dict(self.metadata, Unknown="synthetic-sensitive"))
        for index, metadata in enumerate(cases):
            with self.subTest(index=index):
                self.instance.write_text(json.dumps(metadata))
                output = self.work / ("refused-" + str(index))
                with mock.patch.object(builder, "execute") as cli:
                    with self.assertRaises(ValueError):
                        builder.build(self.inputs, output, now=NOW)
                    cli.assert_not_called()
                self.assertFalse((output / "build-receipt.json").exists())

    def test_actual_job_docker_environment_isolated_at_cli_boundary(self) -> None:
        self.docker_config.mkdir()
        (self.docker_config / "config.json").write_text(json.dumps({
            "auths": {"registry.invalid": {"auth": "synthetic-sensitive"}},
            "credsStore": "synthetic-helper", "credHelpers": {"registry.invalid": "synthetic-helper"},
            "proxies": {"default": {"httpProxy": "http://synthetic-sensitive.invalid"}},
        }))
        output = self.work / "artifact"
        fixture = (
            "import json,os,pathlib; "
            "assert 'synthetic-sensitive' not in json.dumps(dict(os.environ)); "
            "client=pathlib.Path(os.environ['DOCKER_CONFIG']); "
            "assert os.environ['HOME']==os.environ['USERPROFILE']==str(client); "
            "assert json.loads((client/'config.json').read_text())=={'auths':{'':{}}}; "
            "node=json.loads((pathlib.Path(os.environ['BUILDX_CONFIG'])/'instances'/'image-proof')"
            ".read_text()); "
            "assert node['Name']=='image-proof' and node['Nodes'][0]['Endpoint']=='default'; "
            "assert not any(k in os.environ for k in "
            "['HTTP_PROXY','https_proxy','DOCKER_HOST','DOCKER_CONTEXT','DOCKER_AUTH_CONFIG']); "
            "print('isolated')"
        )
        calls: list[list[str]] = []
        real_execute = builder.execute

        def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
            # A later ambient mutation cannot alter the already-selected snapshot.
            self.instance.write_text("unsafe later replacement")
            self.assertEqual(real_execute([sys.executable, "-B", "-c", fixture],
                                          timeout=timeout, env=env).strip(), "isolated")
            calls.append(argv)
            if argv[3:5] != ["buildx", "build"]:
                return self.inspect(argv)
            archive(output / "image.oci.tar")
            return ""

        ambient = {"HTTP_PROXY": "http://synthetic-sensitive.invalid",
                   "https_proxy": "http://synthetic-sensitive.invalid",
                   "DOCKER_HOST": "tcp://synthetic-sensitive.invalid", "DOCKER_CONTEXT": "foreign",
                   "DOCKER_AUTH_CONFIG": "synthetic-sensitive"}
        with (mock.patch.dict(os.environ, ambient),
              mock.patch.object(builder, "execute", side_effect=runner) as default_runner):
            receipt = builder.build(self.inputs, output, now=NOW)
        self.assertEqual(receipt["status"], "built-not-runtime-validated")
        self.assertEqual(default_runner.call_count, 9)
        self.assertEqual(len(calls), 9)

    def test_output_and_receipt_directory_entries_sync_before_buildx(self) -> None:
        output = self.work / "new-parent" / "new-child" / "artifact"
        events: list[str | Path] = []
        real_fsync = os.fsync

        def sync_file(descriptor: int) -> None:
            events.append("file")
            real_fsync(descriptor)

        def sync_directory(path: Path) -> None:
            events.append(path)

        def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
            if argv[3:5] != ["buildx", "build"]:
                return self.inspect(argv)
            self.assertEqual(events, [self.work, output.parent.parent, output.parent,
                                      output, output / "docker-client",
                                      output / "docker-client" / "buildx", "file", output])
            self.assertEqual(json.loads((output / "build-receipt.json").read_text())["status"],
                             "build-started-outcome-unconfirmed")
            archive(output / "image.oci.tar")
            return ""

        with mock.patch.object(builder.os, "fsync", side_effect=sync_file):
            with mock.patch.object(builder, "fsync_directory", side_effect=sync_directory):
                builder.build(self.inputs, output, runner=runner, now=NOW)
        self.assertEqual(events[-2:], ["file", output])

    def test_failed_directory_sync_never_launches_buildx(self) -> None:
        for failure in ("ancestor", "receipt"):
            with self.subTest(failure=failure):
                output = self.work / failure / "artifact"
                build_calls: list[list[str]] = []

                def sync_directory(path: Path) -> None:
                    if ((failure == "ancestor" and path == self.work)
                            or path == output and (output / "build-receipt.json").exists()):
                        raise OSError("directory durability unavailable")

                def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
                    if argv[3:5] == ["buildx", "build"]:
                        build_calls.append(argv)
                        self.fail("Buildx launched without a durable initial receipt")
                    return self.inspect(argv)

                with mock.patch.object(builder, "fsync_directory", side_effect=sync_directory):
                    with self.assertRaisesRegex(OSError, "directory durability unavailable"):
                        builder.build(self.inputs, output, runner=runner, now=NOW)
                self.assertEqual(build_calls, [])
                if failure == "receipt":
                    receipt = json.loads((output / "build-receipt.json").read_text())
                    self.assertEqual(receipt["status"], "build-started-outcome-unconfirmed")
                else:
                    self.assertFalse((output / "build-receipt.json").exists())

    def test_final_receipt_sync_failure_does_not_claim_durable_success(self) -> None:
        output = self.work / "final-sync-failure"
        syncs = 0

        def sync_directory(path: Path) -> None:
            nonlocal syncs
            if path == output and (output / "build-receipt.json").exists():
                syncs += 1
                if syncs == 2:
                    raise OSError("final receipt sync failed")

        def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
            if argv[3:5] != ["buildx", "build"]:
                return self.inspect(argv)
            archive(output / "image.oci.tar")
            return ""

        with mock.patch.object(builder, "fsync_directory", side_effect=sync_directory):
            with self.assertRaisesRegex(OSError, "final receipt sync failed"):
                builder.build(self.inputs, output, runner=runner, now=NOW)

    @unittest.skipIf(os.name == "nt", "Directory fsync requires the Linux build host")
    def test_actual_directory_fsync(self) -> None:
        builder.fsync_directory(self.work)

    def test_build_stages_only_approved_bytes_and_inspects_archive(self) -> None:
        output = self.work / "artifact directory"

        def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
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

    def test_failed_engine_keeps_failure_receipt_without_success_claim(self) -> None:
        output = self.work / "failed"

        def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
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

    def test_wrong_platform_and_corrupt_layer_refused(self) -> None:
        for name, options in (("wrong", {"architecture": "arm64"}),
                              ("corrupt", {"corrupt": True})):
            path = self.work / name
            archive(path, **cast(dict[str, Any], options))
            with self.assertRaises(ValueError):
                builder.oci_identity(path)

    def test_output_injection_and_own_recipe_refused_before_engine(self) -> None:
        for output in (ROOT / "output", self.work / "name,extra=value"):
            with self.assertRaises(ValueError):
                builder.build(self.inputs, output, runner=lambda _: self.fail("engine called"), now=NOW)

    def test_default_or_unbounded_builder_refused(self) -> None:
        for bounds in ({"Memory": 0}, {"NanoCpus": 0, "CpuQuota": -1},
                       {"MemorySwap": -1}, {"Memory": 2147483648},
                       {"NanoCpus": 4000000000}):
            def runner(argv: list[str], *, timeout: float) -> str:
                result = json.loads(self.inspect(argv))
                if argv[3] == "inspect":
                    result[0]["HostConfig"].update(bounds)
                return json.dumps(result)
            with self.assertRaises(ValueError):
                builder.verify_builder("docker", self.inputs["builder"], runner)
        def wrong_driver(argv: list[str], *, timeout: float) -> str:
            result = json.loads(self.inspect(argv))
            if argv[3:5] == ["buildx", "ls"]:
                result["Driver"] = "docker"
            return json.dumps(result)
        with self.assertRaises(ValueError):
            builder.verify_builder("docker", self.inputs["builder"], wrong_driver)

    def test_wrong_builder_instance_and_timeout_inputs_refused(self) -> None:
        def runner(argv: list[str], *, timeout: float) -> str:
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

    def test_running_builder_image_checked_before_and_after_build(self) -> None:
        for stage in ("preflight", "postbuild"):
            for index, image in enumerate((None, "moby/buildkit:latest",
                                           "moby/buildkit@sha256:" + "0" * 64)):
                with self.subTest(stage=stage, image=image):
                    output = self.work / (stage + "-" + str(index))
                    inspections = 0
                    builds: list[list[str]] = []

                    def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
                        nonlocal inspections
                        if argv[3:5] == ["buildx", "build"]:
                            builds.append(argv)
                            archive(output / "image.oci.tar")
                            return ""
                        result = json.loads(self.inspect(argv))
                        if argv[3] == "inspect":
                            inspections += 1
                            if inspections == (1 if stage == "preflight" else 2):
                                result[0]["Config"] = {} if image is None else {"Image": image}
                        return json.dumps(result)

                    with self.assertRaisesRegex(ValueError, "Running builder image"):
                        builder.build(self.inputs, output, runner=runner, now=NOW)
                    self.assertEqual(len(builds), 0 if stage == "preflight" else 1)
                    if stage == "postbuild":
                        receipt = json.loads((output / "build-receipt.json").read_text())
                        self.assertEqual(receipt["status"], "build-failed")
                        self.assertNotIn("manifest_digest", receipt)
                    else:
                        self.assertFalse((output / "build-receipt.json").exists())

    def test_cancelled_build_retains_unknown_outcome(self) -> None:
        for error in (subprocess.TimeoutExpired(["fixture"], 60), KeyboardInterrupt(),
                      builder.ChildCustodyError("CLI child identity changed")):
            with self.subTest(error=type(error).__name__):
                output = self.work / type(error).__name__
                def runner(argv: list[str], *, timeout: float, env: dict[str, str]) -> str:
                    if argv[3:5] != ["buildx", "build"]:
                        return self.inspect(argv)
                    raise error
                with self.assertRaises(type(error)):
                    builder.build(self.inputs, output, runner=runner, now=NOW)
                receipt = json.loads((output / "build-receipt.json").read_text())
                self.assertEqual(receipt["status"],
                                 "cancellation-requested-builder-outcome-unconfirmed")
                self.assertNotIn("manifest_digest", receipt)
                self.assertEqual(receipt["builder_container_id"], "b" * 64)
                self.assertEqual(len(receipt["approval_sha256"]), 64)

    def test_foreign_node_endpoint_cannot_borrow_local_container_limits(self) -> None:
        for endpoint in ("other-context", "ssh://remote.invalid", "unix:///other/socket"):
            def runner(argv: list[str], *, timeout: float) -> str:
                self.assertNotEqual(argv[3], "inspect", "must refuse before inspecting local container")
                result = json.loads(self.inspect(argv))
                if argv[3:5] == ["buildx", "ls"]:
                    result["Nodes"][0]["Endpoint"] = endpoint
                return json.dumps(result)
            with self.assertRaises(ValueError):
                builder.verify_builder("docker", self.inputs["builder"], runner)

    def test_foreign_default_context_and_changed_context_refused(self) -> None:
        calls = 0
        def runner(argv: list[str], *, timeout: float) -> str:
            nonlocal calls
            result = json.loads(self.inspect(argv))
            if argv[3:5] == ["context", "inspect"]:
                calls += 1
                if calls == 2:
                    result["Host"] = "tcp://remote.invalid:2376"
            return json.dumps(result)
        with self.assertRaises(ValueError):
            builder.verify_builder("docker", self.inputs["builder"], runner)

    def test_buildx_json_lines_deduplicate_only_identical_selected_records(self) -> None:
        selected = json.loads(self.inspect(["docker", "--context", "default", "buildx", "ls"]))
        selected["Nodes"][0]["Endpoint"] = "unix:///var/run/docker.sock"
        unrelated = {"Name": "default", "Driver": "docker", "Nodes": []}
        for conflicting in (False, True):
            def runner(argv: list[str], *, timeout: float) -> str:
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

    def test_runtime_identity_and_auth_boundary(self) -> None:
        entry = self.files["entrypoint.sh"].decode()
        self.assertIn("mountpoint -q", entry)
        self.assertIn('[ "$$" != 1 ]', entry)
        self.assertIn('findmnt -n -o PROPAGATION /', entry)
        self.assertIn('!= private', entry)
        self.assertIn("python3 /opt/trusted-harness/recipe/install.py --runtime", entry)
        stages = [
            "if ! mountpoint -q /run || [ -L /run ] || [ -L /run/systemd ]; then",
            'for path in /home/vscode /workspaces; do',
            "python3 /opt/trusted-harness/recipe/install.py --runtime",
            "exec /lib/systemd/systemd",
        ]
        for stage in stages:
            self.assertIn(stage, entry)
        self.assertEqual([entry.index(stage) for stage in stages],
                         sorted(entry.index(stage) for stage in stages))
        self.assertNotIn("chown -R", entry)
        self.assertNotIn("rm -rf", entry)
        self.assertIn(
            "COPY --chmod=0644 profile.sh /etc/profile.d/trusted-harness.sh",
            self.files["Dockerfile"].decode(),
        )
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
            ("/usr/bin/systemctl", "--root=/", "enable", "ssh.service"),
            calls,
        )
        self.assertIn(
            ("/usr/bin/systemctl", "--root=/", "mask", "apt-daily.service", "apt-daily.timer",
             "apt-daily-upgrade.service", "apt-daily-upgrade.timer"),
            calls,
        )

    def test_entrypoint_uses_ordinary_run_without_mount_pin(self) -> None:
        bash = shutil.which("bash")
        if os.name == "nt":
            git = shutil.which("git")
            candidate = Path(git).parents[1] / "bin" / "bash.exe" if git else None
            bash = str(candidate) if candidate and candidate.is_file() else None
        if not bash:
            self.skipTest("A native Bash interpreter is required")
        script = r"""
id() { if [[ "$*" == "-g vscode" || "$*" == "-u vscode" ]]; then echo 1000; else echo 0; fi; }
findmnt() { echo private; }
mountpoint() {
    printf 'mountpoint %s\n' "$*"
    [[ "$2" != "$MISSING" && ( "$2" == /run || "$2" == /home/vscode || "$2" == /workspaces ) ]]
}
[() {
    if [[ "$1" == "$$" && "$2" == '!=' && "$3" == 1 ]]; then return 1; fi
    if [[ "$1" == -L ]]; then [[ "$2" == "$LINK" ]]; return; fi
    builtin [ "$@"
}
install() { printf 'install %s\n' "$*"; }
mount() { printf 'mount %s\n' "$*"; }
python3() { printf 'python3 %s\n' "$*"; }
function /usr/sbin/sshd() { printf 'sshd %s\n' "$*"; }
exec() { printf 'exec %s\n' "$*"; }
source "$1" /lib/systemd/systemd
"""
        flags: dict[str, Any] = (
            {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")}
            if os.name == "nt"
            else {}
        )
        for missing, link in (
            ("", ""),
            ("/run", ""),
            ("", "/run"),
            ("", "/run/systemd"),
            ("/home/vscode", ""),
            ("/workspaces", ""),
        ):
            with self.subTest(missing=missing, link=link):
                result = subprocess.run(
                    [
                        bash,
                        "--noprofile",
                        "--norc",
                        "-c",
                        script,
                        "entrypoint-test",
                        (ROOT / "entrypoint.sh").as_posix(),
                    ],
                    env={**os.environ, "MISSING": missing, "LINK": link},
                    capture_output=True,
                    text=True,
                    timeout=10,
                    **flags,
                )
                self.assertEqual(
                    result.returncode, 65 if missing or link else 0, result.stderr
                )
                self.assertNotIn("mount --bind", result.stdout)
                self.assertNotIn("mountpoint -q /run/systemd", result.stdout)
                if missing or link:
                    self.assertNotIn("python3 ", result.stdout)
                    self.assertNotIn("exec ", result.stdout)
                else:
                    self.assertIn(
                        "python3 /opt/trusted-harness/recipe/install.py --runtime",
                        result.stdout,
                    )
                    self.assertIn("sshd -t", result.stdout)
                    self.assertIn("mount -o remount,rw /sys/fs/cgroup", result.stdout)
                    self.assertIn("exec /lib/systemd/systemd", result.stdout)


class RuntimePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = ROOT.parents[1] / ".trusted-base-test-runs" / uuid.uuid4().hex
        self.home = self.work / "home"
        self.workspace = self.work / "workspaces"
        self.etc = self.work / "etc"
        for path in (self.home, self.workspace, self.etc):
            path.mkdir(parents=True)
        (self.etc / "machine-id").write_text("")
        self.owners: dict[Path, tuple[int, int]] = {}
        self.modes: dict[Path, int] = {}
        self.public = "ssh-ed25519 AAAAtestkey fixture\n"
        self.commands: list[tuple[str, ...]] = []
        self.patches = mock.patch.multiple(
            installer, ownership=lambda p: self.owners.get(p, (0, 0)),
            permissions=lambda p: self.modes.get(p, 0o700 if p.is_dir() else 0o644),
            set_owner=lambda p, u, g: self.owners.__setitem__(p, (u, g)),
            set_permissions=lambda p, m: self.modes.__setitem__(p, m),
            run=self.keygen,
        )
        self.patches.start()

    def tearDown(self) -> None:
        self.patches.stop()
        shutil.rmtree(self.work)
        try:
            self.work.parent.rmdir()
        except OSError:
            pass

    def keygen(self, *args: str) -> str:
        self.commands.append(args)
        if "-y" in args:
            return self.public
        private = Path(args[-1])
        private.write_bytes(b"existing-key-bytes")
        private.with_suffix(".pub").write_text(self.public)
        return ""

    def dbus_reference(self) -> None:
        reference = self.work / "var/lib/dbus/machine-id"
        reference.parent.mkdir(parents=True)
        try:
            reference.symlink_to(self.etc / "machine-id")
        except OSError as error:
            self.skipTest("Host cannot create symlinks: " + str(error))

    def initialize(self) -> Path:
        self.dbus_reference()
        installer.prepare_runtime(self.home, self.workspace, self.etc)
        return self.home / ".local/state/trusted-harness/identity"

    def test_public_regular_projection_with_private_home_and_identity(self) -> None:
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

    def test_foreign_and_nonempty_root_mounts_refused_without_adoption(self) -> None:
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

    def test_partial_identity_never_regenerates_host_key(self) -> None:
        identity = self.initialize()
        original_pub = (identity / "ssh_host_ed25519_key.pub").read_bytes()
        (identity / "ssh_host_ed25519_key").unlink()
        commands = len(self.commands)
        with self.assertRaises(ValueError):
            installer.prepare_runtime(self.home, self.workspace, self.etc)
        self.assertEqual(len(self.commands), commands)
        self.assertEqual((identity / "ssh_host_ed25519_key.pub").read_bytes(), original_pub)
        self.assertFalse((identity / "ssh_host_ed25519_key").exists())

    def test_missing_public_key_or_machine_id_and_empty_identity_refused(self) -> None:
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

    def test_malformed_machine_and_keypair_refused_without_rewriting(self) -> None:
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

    def test_foreign_or_wrong_mode_identity_refused(self) -> None:
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

    def test_linked_mount_home_subdirectory_and_identity_file_refused(self) -> None:
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

    def test_linked_projection_refused_without_changing_canonical_id(self) -> None:
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
