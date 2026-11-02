"""Mechanical boundaries only; these mocks never qualify a real container run."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROOF = ROOT / "tools/clean-room/gateway-role-tunnels"


def load() -> tuple:
    loaded = {}
    with patch.dict(sys.modules):
        for name in ("source", "architecture", "engine", "witness", "run"):
            spec = importlib.util.spec_from_file_location(name, PROOF / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    return tuple(loaded[name] for name in ("source", "engine", "witness", "run"))


SOURCE, ENGINE, WITNESS, RUN = load()
CID_A, CID_B, NETWORK = "a" * 64, "b" * 64, "c" * 64
IMAGE = "sha256:" + "d" * 64


def test_fixed_package_allowlist_covers_current_committed_sdk_modules() -> None:
    names = SOURCE.git(
        ROOT, "ls-tree", "-r", "--name-only", "HEAD", "fleet-manager/src/fleet_manager",
    ).decode().splitlines()
    expected = {name for name in SOURCE.REQUIRED if name.startswith("fleet-manager/src/")}
    assert set(names) == expected


def test_hardened_argv_has_no_host_privilege_or_secret_material(tmp_path: Path) -> None:
    with patch.object(ENGINE.os, "getuid", return_value=1001):
        argv = ENGINE.container_args(
            IMAGE, tmp_path / "cid", "proof-gateway", NETWORK,
            [(tmp_path, "/config", False)], True, None)
    for flag, value in {
        "--network": NETWORK, "--memory": "256m", "--memory-swap": "256m",
        "--cpus": "1", "--pids-limit": "64", "--cap-drop": "ALL",
        "--security-opt": "no-new-privileges:true", "--network-alias": "gateway",
    }.items():
        assert argv[argv.index(flag) + 1] == value
    assert "--read-only" in argv
    assert argv[argv.index("--user") + 1].startswith("1001:")
    assert argv[argv.index("--mount") + 1].endswith(",readonly")
    assert not set(argv) & {"--privileged", "--publish", "-p", "--pid", "--env", "-e",
                           "--net=host", "--network=host", "--device", "--volume", "-v"}
    assert "docker.sock" not in " ".join(argv)
    assert "TOKEN" not in " ".join(argv)
    assert "--rm" not in argv  # Creation receipts survive until finally.


def test_preparation_is_networkless_and_only_explicit_output_is_writable(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    with patch.object(ENGINE.os, "getuid", return_value=1001):
        argv = ENGINE.container_args(
            IMAGE, tmp_path / "cid", "fixture-prep", "none",
            [(tmp_path / "fixtures", "/output", True)], False,
            ["/proof/fixture_auth.py", "/output"])
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[-3:] == [IMAGE, "/proof/fixture_auth.py", "/output"]
    with pytest.raises(SOURCE.ProofError, match="foreign-or-writable"):
        engine.create(IMAGE, name="bad", mounts=[(tmp_path.parent, "/output", True)])


@pytest.mark.parametrize("network", [None, "host", "bridge", "foreign"])
def test_only_receipted_network_or_none_is_admitted(tmp_path: Path, network: str) -> None:
    with pytest.raises(SOURCE.ProofError, match="owned-network"):
        ENGINE.container_args(IMAGE, tmp_path / "cid", "test", network, [], False, None)


def test_symlink_and_public_results_roots_refuse(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(private, target_is_directory=True)
    with pytest.raises(SOURCE.ProofError, match="indirection"):
        SOURCE.private_root(linked, ROOT)
    private.chmod(0o755)
    with pytest.raises(SOURCE.ProofError, match="not-private"):
        SOURCE.private_root(private, ROOT)
    with pytest.raises(SOURCE.ProofError, match="not-absolute"):
        SOURCE.private_root(Path("relative"), ROOT)


def test_repository_and_session_results_roots_refuse(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(SOURCE.ProofError, match="in-repository"):
        SOURCE.private_root(repo, ROOT)
    state = tmp_path / "session-state"
    state.mkdir(mode=0o700)
    with pytest.raises(SOURCE.ProofError, match="not-private"):
        SOURCE.private_root(state, ROOT)


def test_fresh_run_directory_is_unique_private_and_below_supplied_root(tmp_path: Path) -> None:
    first, second = SOURCE.fresh_run(tmp_path), SOURCE.fresh_run(tmp_path)
    assert first != second and first.parent == second.parent == tmp_path
    assert first.stat().st_mode & 0o077 == 0
    assert first.name.startswith("gateway-role-")


def tree(override: tuple[str, str] | None = None, extra: str | None = None) -> bytes:
    entries = []
    for name in sorted(SOURCE.REQUIRED):
        mode = override[1] if override and name == override[0] else "100644"
        entries.append(f"{mode} blob {'a' * 40}\t{name}".encode())
    if extra:
        entries.append(f"100644 blob {'b' * 40}\t{extra}".encode())
    return b"\0".join(entries) + b"\0"


@pytest.mark.parametrize("listing", [
    b"", tree(("fleet-manager/setup.py", "120000")),
    tree(extra="fleet-manager/src/fleet_manager/unknown.py"),
])
def test_incomplete_linked_or_unknown_source_closure_refuses(
    tmp_path: Path, listing: bytes,
) -> None:
    def git(repository: Path, *args: str) -> bytes:
        return b"a" * 40 if args[0] == "rev-parse" else listing
    with patch.object(SOURCE, "git", git), pytest.raises(SOURCE.ProofError):
        SOURCE.materialize(ROOT, "a" * 40, tmp_path)
    assert not (tmp_path / "context").exists()


@pytest.mark.parametrize("ref", ["", "--help", "HEAD:secret", "HEAD^{tree}", "x\nx"])
def test_unsafe_source_ref_never_invokes_git(tmp_path: Path, ref: str) -> None:
    with patch.object(SOURCE, "git") as git, pytest.raises(SOURCE.ProofError):
        SOURCE.materialize(ROOT, ref, tmp_path)
    git.assert_not_called()


def test_materialization_never_copies_live_checkout_or_unallowlisted_blobs(tmp_path: Path) -> None:
    blobs = {}
    for index, name in enumerate(sorted(SOURCE.REQUIRED)):
        oid = f"{index:040x}"
        blobs[oid] = ((PROOF / Path(name).name).read_bytes()
                      if name.startswith(SOURCE.PROOF) else b"canonical-git-blob\n")
    listing = b"\0".join(
        f"100644 blob {index:040x}\t{name}".encode()
        for index, name in enumerate(sorted(SOURCE.REQUIRED))) + b"\0"

    def git(repository: Path, *args: str) -> bytes:
        if args[0] == "rev-parse":
            return b"a" * 40
        if args[0] == "ls-tree":
            return listing + b"100644 blob ffffffffffffffffffffffffffffffffffffffff\tprivate.key\0"
        assert args[0:2] == ("cat-file", "blob")
        return blobs[args[2]]

    with patch.object(SOURCE, "git", git):
        facts = SOURCE.materialize(ROOT, "a" * 40, tmp_path)
    assert facts["source_commit"] == "a" * 40
    assert not (tmp_path / "context/private.key").exists()
    assert (tmp_path / "context/fleet-manager/setup.py").read_bytes() == b"canonical-git-blob\n"
    manifest = json.loads((tmp_path / "source-manifest.json").read_text())
    assert set(manifest["blobs"]) == SOURCE.REQUIRED
    assert len(facts["source_manifest_sha256"]) == 64


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only host runner")
@pytest.mark.parametrize("legacy", [False, True], ids=["no-lazy-fetch", "transport-fallback"])
def test_missing_promisor_blob_never_invokes_remote_transport(tmp_path: Path, legacy: bool) -> None:
    repo, helper, marker = tmp_path / "repo", tmp_path / "remote.py", tmp_path / "attempts"
    helper.write_text(
        "import pathlib,sys\n"
        "with pathlib.Path(sys.argv[1]).open('a') as out: out.write('attempt\\n')\n"
        "sys.exit(1)\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    for key, value in {
        "remote.origin.url": f"ext::{sys.executable} {helper} {marker}",
        "remote.origin.promisor": "true", "remote.origin.partialclonefilter": "blob:none",
        "protocol.ext.allow": "always",
    }.items():
        subprocess.run(["git", "-C", str(repo), "config", key, value],
                       check=True, capture_output=True)
    missing = "1" * 40
    control = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", missing], capture_output=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        timeout=5)
    attempts = marker.read_bytes()
    assert control.returncode != 0 and attempts
    launch = subprocess.run

    def bounded(*args, **kwargs):
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == ""
        if legacy:
            kwargs["env"].pop("GIT_NO_LAZY_FETCH")
        return launch(*args, **kwargs)

    with patch.object(SOURCE.subprocess, "run", side_effect=bounded):
        with pytest.raises(SOURCE.ProofError, match="git-source-unavailable"):
            SOURCE.git(repo, "cat-file", "blob", missing)
    assert marker.read_bytes() == attempts


def test_commands_have_finite_timeouts_and_do_not_inherit_credentials(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    with patch.dict(os.environ, {"GH_TOKEN": "must-not-propagate",
                                "DOCKER_HOST": "tcp://must-not-connect"}):
        with patch.object(ENGINE.subprocess, "run") as launch:
            launch.return_value = SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")
            assert engine.call("info") == "ok"
            kwargs = launch.call_args.kwargs
    assert 0 < kwargs["timeout"] <= 20
    assert "GH_TOKEN" not in kwargs["env"] and "DOCKER_HOST" not in kwargs["env"]
    assert kwargs["env"]["DOCKER_CONFIG"] == str(tmp_path / "docker-config")
    engine.deadline = time.monotonic() - 1
    with pytest.raises(SOURCE.ProofError, match="deadline"):
        engine.call("info")


def test_cleanup_only_uses_exact_creation_receipts_including_partial_create(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    engine.containers = [CID_A]
    cidfile = tmp_path / "partial.cid"
    cidfile.write_text(CID_B)
    engine.pending = [cidfile]
    engine.network = NETWORK
    calls = []
    engine.call = lambda *args, **kwargs: calls.append(args) or ""
    assert engine.cleanup() == []
    assert ("stop", "--time", "5", CID_A) in calls
    assert ("stop", "--time", "5", CID_B) in calls
    assert ("rm", CID_A) in calls and ("rm", CID_B) in calls
    assert ("network", "rm", NETWORK) in calls
    assert not any("prune" in call or "--force" in call or "-f" in call for call in calls)
    with pytest.raises(SOURCE.ProofError, match="foreign-container"):
        engine.execute("e" * 64, "python", "--version")


def test_cleanup_failure_is_explicit_and_does_not_skip_network_cleanup(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    engine.containers = [CID_A]
    engine.network = NETWORK
    calls = []

    def fail(*args: str, **kwargs: object) -> str:
        calls.append(args)
        if args[0] == "stop":
            raise SOURCE.ProofError("synthetic-stop-failed")
        return ""

    engine.call = fail
    assert engine.cleanup() == ["owned-container-cleanup-failed"]
    assert ("rm", CID_A) in calls
    assert ("network", "rm", NETWORK) in calls


@pytest.mark.parametrize("failure", [
    None, FileNotFoundError(), OSError(), subprocess.TimeoutExpired("docker", 1),
], ids=["completed", "missing-command", "launch-error", "timeout"])
def test_log_write_failure_cannot_abort_owned_resource_cleanup(
    tmp_path: Path, failure: Exception | None,
) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    engine.containers = [CID_A, CID_B]
    engine.network = NETWORK
    calls = []

    def launch(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(tuple(argv[1:]))
        if failure is not None:
            raise failure
        return SimpleNamespace(returncode=0, stdout=b"synthetic-output", stderr=b"")

    with patch.object(ENGINE.subprocess, "run", side_effect=launch), \
            patch.object(Path, "write_bytes", side_effect=OSError("synthetic log failure")):
        errors = engine.cleanup()
    for cid in (CID_A, CID_B):
        assert ("logs", "--tail", "160", cid) in calls
        assert ("stop", "--time", "5", cid) in calls
        assert ("rm", cid) in calls
    assert ("network", "rm", NETWORK) in calls
    assert errors == [
        "container-log-capture-failed", "owned-container-cleanup-failed",
        "container-log-capture-failed", "owned-container-cleanup-failed",
        "owned-network-cleanup-failed",
    ]
    assert json.loads((tmp_path / "receipt.json").read_text())["cleanup_errors"] == errors


def test_network_creation_retains_exact_id_before_fallible_logging(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    calls = []
    write = Path.write_bytes
    writes = 0

    def log(path: Path, data: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("synthetic creation log failure")
        return write(path, data)

    def launch(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(tuple(argv[1:]))
        return SimpleNamespace(returncode=0, stdout=NETWORK.encode(), stderr=b"")

    with patch.object(ENGINE.subprocess, "run", side_effect=launch), \
            patch.object(Path, "write_bytes", log):
        with pytest.raises(SOURCE.ProofError, match="docker-log-write-failed"):
            engine.create_network()
        assert engine.network == NETWORK and engine.ambiguous_network is False
        assert json.loads((tmp_path / "receipt.json").read_text())["network_id"] == NETWORK
        assert engine.cleanup() == []
    assert ("network", "rm", NETWORK) in calls


def test_uncertain_creation_without_receipt_is_not_clean_success(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    engine.pending = [tmp_path / "missing.cid"]
    engine.ambiguous_network = True
    with patch.object(engine, "call") as call:
        assert engine.cleanup() == [
            "container-create-outcome-unknown-no-id", "network-create-outcome-unknown-no-id"]
    call.assert_not_called()


def test_exec_refuses_arbitrary_commands_or_secret_arguments(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    engine.containers = [CID_A]
    with patch.object(engine, "call") as call:
        with pytest.raises(SOURCE.ProofError, match="unsafe-exec"):
            engine.execute(CID_A, "python", "/proof/probe.py", "synthetic-secret")
        with pytest.raises(SOURCE.ProofError, match="unsafe-exec"):
            engine.execute(CID_A, "sh", "-c", "unapproved")
    call.assert_not_called()


def test_missing_docker_is_reported_not_skipped_or_installed(tmp_path: Path) -> None:
    previous = {number: signal.getsignal(number) for number in RUN.TERMINATION_SIGNALS}
    with patch.object(RUN, "materialize", return_value={"source_commit": "a" * 40}), \
            patch.object(RUN.shutil, "which", return_value=None), \
            patch.object(RUN, "Engine") as engine:
        receipt = RUN.run(ROOT, "a" * 40, tmp_path)
    engine.assert_not_called()
    assert receipt["result"] == "fail" and receipt["cleanup"] == "complete"
    report = json.loads((tmp_path / "cr-report.json").read_text())
    assert report["failed"] == 1 and report["jams"][0]["hint"] == "docker-unavailable"
    assert {number: signal.getsignal(number) for number in previous} == previous


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only host runner")
@pytest.mark.parametrize("first,second", [
    (signal.SIGTERM, signal.SIGTERM), (signal.SIGINT, signal.SIGINT),
    (signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM),
], ids=["term-term", "int-int", "term-int", "int-term"])
def test_termination_enters_cleanup_and_repeated_signals_cannot_interrupt_it(
    tmp_path: Path, first: int, second: int,
) -> None:
    script = """
import json, signal, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import run as subject
root = Path(sys.argv[2])
eventually = subject.Witness.eventually
class Engine:
    def __init__(self, root, receipt, architecture="amd64"):
        self.deadline = 0
    def journal(self):
        pass
    def preflight(self):
        pass
    def build(self, sha, timeout):
        return "synthetic-image"
    def open_runner_lifetime(self):
        pass
    def cleanup(self):
        (root / "cleanup-started").touch()
        deadline = time.monotonic() + 5
        while not (root / "release-cleanup").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("cleanup-release-timeout")
            time.sleep(0.01)
        (root / "cleanup-finished").touch()
        return []
class Witness:
    def __init__(self, *args):
        self.engine = args[0]
    def passed(self, *args):
        pass
    def run(self):
        def action():
            (root / "witness-ready").touch()
            while True:
                time.sleep(0.01)
        eventually(self, action)
subject.Engine, subject.Witness = Engine, Witness
subject.shutil.which = lambda name: "/synthetic/docker"
subject.materialize = lambda *args: {
    "source_commit": "a" * 40, "source_manifest_sha256": "b" * 64,
    "image_id": "synthetic-image", "architecture": {},
}
previous = {number: signal.getsignal(number) for number in subject.TERMINATION_SIGNALS}
receipt = subject.run(root, "a" * 40, root)
assert {number: signal.getsignal(number) for number in previous} == previous
print(json.dumps(receipt))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(PROOF), str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(name: str) -> None:
        deadline = time.monotonic() + 5
        while not (tmp_path / name).exists():
            assert process.poll() is None, "proof child exited before " + name
            assert time.monotonic() < deadline, "proof child did not reach " + name
            time.sleep(0.01)

    try:
        wait_for("witness-ready")
        process.send_signal(first)
        wait_for("cleanup-started")
        process.send_signal(second)
        (tmp_path / "release-cleanup").touch()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    receipt = json.loads(stdout)
    assert receipt["result"] == "fail" and receipt["error"] == "runner-terminated"
    assert receipt["cleanup"] == "complete"
    assert (tmp_path / "cleanup-finished").exists()
    report = json.loads((tmp_path / "cr-report.json").read_text())
    assert report["failed"] == 1 and report["jams"][0]["hint"] == "runner-terminated"


def test_docker_timeout_is_classified_without_echoing_args(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    with patch.object(ENGINE.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
        with pytest.raises(SOURCE.ProofError, match="command-timeout"):
            engine.call("info")


def test_real_header_construction_preserves_values_without_importing_optional_dependencies() -> None:
    module = ast.parse((PROOF / "probe.py").read_text())
    node = next(item for item in module.body if isinstance(item, ast.FunctionDef)
                and item.name == "auth_headers")
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "auth_headers", "exec"), namespace)
    assert namespace["auth_headers"]("synthetic-access", "synthetic-proof") == {
        "Authorization": "DP" + "oP " + "synthetic-access", "DPoP": "synthetic-proof"}


def test_new_sources_remain_bounded_and_production_image_has_no_fixture_dependency() -> None:
    for name in SOURCE.RUNNER_FILES:
        assert len((PROOF / name).read_text().splitlines()) < 500
    assert len(Path(__file__).read_text().splitlines()) < 500
    dockerfile = (ROOT / SOURCE.IMAGE / "Dockerfile").read_text()
    assert "python:3.13-slim" in dockerfile and "uv:0.11.7" in dockerfile
    assert dockerfile.count('--exclude-newer "7 days"') == 3
    assert "USER ${GATEWAY_UID}:${GATEWAY_GID}" in dockerfile and "[service-gateway]" in dockerfile
    assert "HEALTHCHECK NONE" in dockerfile
    assert '"doctor"' not in dockerfile
    assert "COPY . " not in dockerfile and "fixture" not in dockerfile.lower()
    assert "pip install" not in dockerfile.replace("uv pip install", "")
    assert "PYTHONPATH" not in dockerfile


def test_dockerignore_recloses_each_reopened_parent_before_file_exceptions() -> None:
    lines = (ROOT / SOURCE.IMAGE / "Dockerfile.dockerignore").read_text().splitlines()
    assert lines[0] == "**"
    for index, line in enumerate(lines):
        if line.startswith("!") and line.endswith("/"):
            assert lines[index + 1] == line[1:] + "**"
