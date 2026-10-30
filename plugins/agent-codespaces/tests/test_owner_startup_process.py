"""Exercise readiness against a cold, isolated child without opening a connection."""

from __future__ import annotations

import subprocess
import sys
import types
import json
import hashlib
import importlib.util
import os
import shutil
import time
from pathlib import Path

import pytest
from agent_codespaces import connection_owner as owner


def _owner_slot(root: Path, version: str) -> Path:
    slot = root / "versions" / version
    package = slot / "packages" / "agent_codespaces"
    shutil.copytree(Path(owner.__file__).parent, package, ignore=shutil.ignore_patterns("__pycache__"))
    library = Path(__file__).resolve().parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location("_owner_test_runtime", library / "versioned_runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    runtime.retain_runtime_helpers(slot, complete=False)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-01-01T00:00:00Z",
        "pid": os.getpid(), "payload_hash": hashlib.sha256(version.encode()).hexdigest(),
    }), encoding="utf-8")
    return slot


def _wait(predicate, children=(), timeout: float = 12) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if predicate():
                return
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            # Atomic publication can briefly deny a Windows pathname open.
            pass
        assert all(child.poll() is None for child in children), "owner exited before its witness"
        assert time.monotonic() < deadline, "owner did not reach its witness"
        time.sleep(.02)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("old_beacon", ("current", "legacy", "unknown"))
def test_selected_owner_starts_before_incumbent_settles_without_overlapping_work(tmp_path, old_beacon):
    """Two real owners share HOME; incoming readiness cannot steal admitted work."""
    from agent_procutil import no_window_kwargs
    from agent_procutil.daemon_instances import daemon_test_lifecycle

    root = tmp_path / "home"
    root.mkdir()
    first, second = (_owner_slot(root, version) for version in ("9.0.0", "1.0.0"))
    selection = root / "current-version"
    selection.write_text(first.name, encoding="utf-8")
    script = """
import asyncio, json, os, sys
from pathlib import Path
slot, root = Path(sys.argv[1]), Path(sys.argv[2])
sys.prefix = str(slot)
sys.path.insert(0, str(slot / "packages"))
from agent_codespaces import connection_owner as owner
label = sys.argv[3]
print(label, owner.__file__, owner.RUNTIME_DIR, owner._PROCESS_SELECTION, flush=True)
pid_file = root / (label + ".pid")
pid_pending = pid_file.with_suffix(".pending")
pid_pending.write_text(str(os.getpid()), encoding="ascii")
os.replace(pid_pending, pid_file)
if label == "v1" and sys.argv[4] != "current":
    original_write = owner._write_liveness
    def legacy_write(*args, **kwargs):
        original_write(*args, **kwargs)
        raw = json.loads(owner.LIVE_FILE.read_text())
        for key in ("runtime_identity", "work_lease_version", "owner_control_version"):
            raw.pop(key, None)
        if sys.argv[4] == "unknown":
            raw.pop("process_identity", None)
            raw["owner_control_version"] = 99
        owner.LIVE_FILE.write_text(json.dumps(raw))
    owner._write_liveness = legacy_write
class Channels:
    def active_codespaces(self):
        return {"offline"} if (root / (label + ".active")).exists() else set()
    async def reconcile(self):
        other = "v2" if label == "v1" else "v1"
        if (root / (other + ".active")).exists():
            (root / "overlap").touch()
        assert not (root / (other + ".active")).exists(), "simultaneous work owners"
        (root / (label + ".active")).touch()
    async def shutdown(self):
        (root / (label + ".draining")).touch()
        if label == "v1":
            while not (root / "release-v1").exists():
                await asyncio.sleep(.02)
        (root / (label + ".active")).unlink(missing_ok=True)
async def run():
    stop = asyncio.Event()
    async def watch():
        while not (root / ("stop-" + label)).exists():
            await asyncio.sleep(.02)
        stop.set()
    watcher = asyncio.create_task(watch())
    try:
        await owner.run_owner_daemon(Channels(), interval=.05, stop_event=stop)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
asyncio.run(run())
"""
    children = []
    with daemon_test_lifecycle(owner="offline-incoming-owner", registry_root=tmp_path / "instances") as env:
        environment = {
            **os.environ, **env, "AGENT_CODESPACES_HOME": str(root),
            "HOME": str(root), "USERPROFILE": str(root),
            "PYTHONPATH": os.pathsep.join(sys.path),
        }
        try:
            for label, slot in (("v1", first), ("v2", second)):
                if slot == second:
                    selection.write_text(second.name, encoding="utf-8")
                    _wait(lambda: (root / "v1.draining").exists(), children)
                child = subprocess.Popen(
                    [sys.executable, "-c", script, str(slot), str(root), label, old_beacon],
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    **no_window_kwargs(),
                )
                children.append(child)
                if slot == first:
                    _wait(lambda: (root / "v1.active").exists(), children)
            beacon = root / "connection-owner.live.json"
            _wait(lambda: (root / "v2.pid").exists(), children)
            incoming_pid = int((root / "v2.pid").read_text(encoding="ascii"))
            _wait(lambda: json.loads(beacon.read_text()).get("pid") == incoming_pid, children)
            _wait(lambda: (root / "v1.draining").exists(), children)
            assert children[0].poll() is None
            assert (root / "v1.active").exists()
            assert not (root / "v2.active").exists()
            live = json.loads(beacon.read_text())
            assert live["state"] == "passive"
            assert live["active"] == []
            (root / "release-v1").touch()
            assert children[0].wait(timeout=8) == 0
            _wait(lambda: (root / "v2.active").exists(), (children[1],))
            _wait(lambda: json.loads(beacon.read_text()).get("state") == "active", (children[1],))
            assert json.loads(beacon.read_text())["pid"] == incoming_pid
            assert not (root / "overlap").exists()
        finally:
            failure = sys.exc_info()[1]
            for label in ("v1", "v2"):
                (root / ("stop-" + label)).touch()
            (root / "release-v1").touch()
            for child in children:
                try:
                    stdout, stderr = child.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    stdout, stderr = child.communicate(timeout=5)
                if failure is not None:
                    failure.add_note(repr((stdout, stderr)))
                assert child.returncode == 0, (stdout, stderr)


def test_selected_owner_proof_is_frozen_and_ambiguous_selection_does_not_retire(tmp_path, monkeypatch):
    from agent_codespaces import source_build

    root = tmp_path / "home"
    first, second = (_owner_slot(root, version) for version in ("9.0.0", "1.0.0"))
    monkeypatch.setattr(sys, "prefix", str(first))
    monkeypatch.setattr(source_build, "__file__", str(first / "packages" / "agent_codespaces" / "source_build.py"))
    selection = source_build.capture_owner_runtime(root)
    assert selection is not None
    selector = root / "current-version"
    selector.write_text(first.name, encoding="utf-8")
    assert not selection.superseded()
    selector.write_text(second.name, encoding="utf-8")
    assert selection.superseded(), "rollback is selection inequality, not version ordering"
    selector.write_text("../foreign", encoding="utf-8")
    assert not selection.superseded()
    selector.write_text(second.name, encoding="utf-8")
    marker = second / ".install-complete.json"
    os.link(marker, second / "untrusted-alias")
    assert not selection.superseded(), "linked completion is not authenticated slot provenance"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("replacement", ("supersede", "restart", "passive-shutdown"))
def test_lease_less_legacy_exclusion_survives_passive_owner_replacement(tmp_path, replacement):
    """A genuinely lease-less native writer outlives two current owner processes."""
    from agent_procutil import no_window_kwargs
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from single_instance_lease import OperationLock

    root = tmp_path / "home"
    root.mkdir()
    second, third = (_owner_slot(root, version) for version in ("2.0.0", "3.0.0"))
    selection = root / "current-version"
    selection.write_text(second.name, encoding="utf-8")
    legacy_script = """
import json, os, platform, sys, time
from pathlib import Path
from ssh_manager.locks import process_identity
root = Path(sys.argv[1])
(root / "legacy.active").touch()
pending = root / "legacy.pending"
pending.write_text(json.dumps({
    "pid": os.getpid(), "host": platform.node(), "process_identity": process_identity(os.getpid()),
    "heartbeat_at": time.time(), "interval": 15, "active": ["offline"],
}), encoding="utf-8")
os.replace(pending, root / "connection-owner.live.json")
while not (root / "release-legacy").exists():
    time.sleep(.02)
(root / "legacy.active").unlink()
"""
    incoming_script = """
import asyncio, json, os, sys
from pathlib import Path
slot, root, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.prefix = str(slot)
sys.path.insert(0, str(slot / "packages"))
from agent_codespaces import connection_owner as owner
pending = root / (label + ".pending")
pending.write_text(str(os.getpid()), encoding="ascii")
os.replace(pending, root / (label + ".pid"))
class Channels:
    def active_codespaces(self):
        return set()
    async def reconcile(self):
        if (root / "legacy.active").exists():
            (root / "overlap").touch()
        (root / (label + ".active")).touch()
    async def shutdown(self):
        (root / (label + ".cleaned")).touch()
async def run():
    stop = asyncio.Event()
    async def watch():
        while not (root / ("stop-" + label)).exists():
            await asyncio.sleep(.02)
        stop.set()
    watcher = asyncio.create_task(watch())
    try:
        await owner.run_owner_daemon(Channels(), interval=.05, stop_event=stop)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
asyncio.run(run())
"""
    children = []
    with daemon_test_lifecycle(owner="offline-legacy-barrier", registry_root=tmp_path / "instances") as env:
        environment = {
            **os.environ, **env, "AGENT_CODESPACES_HOME": str(root),
            "HOME": str(root), "USERPROFILE": str(root), "PYTHONPATH": os.pathsep.join(sys.path),
        }

        def start(script, *arguments):
            child = subprocess.Popen(
                [sys.executable, "-c", script, *(str(value) for value in arguments)],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **no_window_kwargs(),
            )
            children.append(child)
            return child

        def ready(label):
            pid_path = root / (label + ".pid")
            return pid_path.exists() and json.loads(
                (root / "connection-owner.live.json").read_text(),
            ).get("pid") == int(pid_path.read_text())

        try:
            legacy = start(legacy_script, root)
            _wait(lambda: (root / "connection-owner.live.json").exists(), (legacy,))
            incoming = start(incoming_script, second, root, "second")
            _wait(lambda: ready("second"), children)
            assert json.loads((root / "connection-owner.live.json").read_text())["state"] == "passive"
            probe = OperationLock(root / "connection-owner.work.lock")
            probe.acquire()  # V1 and passive V2 genuinely hold no work lease.
            probe.release()
            if replacement != "supersede":
                (root / "stop-second").touch()
                assert incoming.wait(timeout=8) == 0
                assert (root / "second.cleaned").exists()
                assert not (root / "connection-owner.live.json").exists()
            if replacement != "restart":
                selection.write_text(third.name, encoding="utf-8")
            successor = start(
                incoming_script, second if replacement == "restart" else third, root, "third",
            )
            _wait(lambda: ready("third"), (legacy, successor))
            time.sleep(.25)  # several real reconcile/admission attempts
            assert legacy.poll() is None
            assert not (root / "overlap").exists(), "successor lost the lease-less native exclusion"
            assert not (root / "third.active").exists()
            assert json.loads((root / "connection-owner.live.json").read_text())["state"] == "passive"
            (root / "release-legacy").touch()
            assert legacy.wait(timeout=8) == 0
            _wait(lambda: (root / "third.active").exists(), (successor,))
        finally:
            failure = sys.exc_info()[1]
            (root / "release-legacy").touch()
            for label in ("second", "third"):
                (root / ("stop-" + label)).touch()
            for child in children:
                try:
                    stdout, stderr = child.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    stdout, stderr = child.communicate(timeout=5)
                if failure is not None:
                    failure.add_note(repr((stdout, stderr)))
                assert child.returncode == 0, (stdout, stderr)


def test_legacy_barrier_retires_native_birth_mismatch_without_retargeting(tmp_path, monkeypatch):
    from agent_codespaces import owner_identity as identity

    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "registry.lock")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    birth = identity.owner_process_identity(os.getpid())
    assert birth
    with owner._owner_lock():
        identity._write_exclusions([identity.LegacyExclusion(os.getpid(), birth + "-old")])
        assert not identity.legacy_work_blocked()
        assert identity._read_exclusions() == []
    assert identity.owner_process_identity(os.getpid()) == birth


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_unknown_legacy_birth_remains_excluded_until_native_exit(tmp_path, monkeypatch):
    from agent_procutil import no_window_kwargs
    from agent_codespaces import owner_identity as identity

    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "registry.lock")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    try:
        with owner._owner_lock():
            identity._write_exclusions([identity.LegacyExclusion(child.pid, None)])
            assert identity.owner_process_identity(child.pid)
            assert identity.legacy_work_blocked(), "later birth observation must not rebind an unknown original"
        child.stdin.close()
        assert child.wait(timeout=8) == 0
        with owner._owner_lock():
            assert not identity.legacy_work_blocked()
            assert identity._read_exclusions() == []
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize("evidence", ("corrupt", "unknown-version", "linked"))
def test_uncertain_durable_barrier_blocks_only_work_and_survives_beacon_clear(
    tmp_path, monkeypatch, evidence,
):
    from agent_codespaces import owner_identity as identity

    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "connection-owner.live.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "registry.lock")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(owner, "_PROCESS_SELECTION", types.SimpleNamespace(
        identity="selected", superseded=lambda: False,
    ))
    path = tmp_path / "connection-owner.predecessors.json"
    with owner._owner_lock():
        identity._write_exclusions([])
    if evidence == "corrupt":
        path.write_bytes(b"{")
    elif evidence == "unknown-version":
        raw = json.loads(path.read_text())
        raw["version"] = 99
        path.write_text(json.dumps(raw))
    else:
        os.link(path, tmp_path / "linked-evidence")
    before = path.read_bytes()
    with owner._owner_lock():
        identity.retain_legacy_exclusion(types.SimpleNamespace(
            pid=123, process_identity=None, host=owner._this_host(), work_lease_version=0,
        ))
        assert identity.legacy_work_blocked()
    assert owner.claim_owner_singleton(.1), "unknown evidence cannot veto passive startup"
    assert owner.read_liveness().state == "passive"
    owner._clear_liveness()
    assert path.read_bytes() == before
    with owner._owner_lock():
        assert identity.legacy_work_blocked()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_default_budget_observes_delayed_child_beacon(monkeypatch, tmp_path):
    import agent_procutil

    live_file = tmp_path / "connection-owner.live.json"
    stop_file = tmp_path / "stop"
    monkeypatch.setattr(owner, "LIVE_FILE", live_file)
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    real_spawn = agent_procutil.spawn_daemon
    children = []
    script = """
import os, sys, time
from pathlib import Path
from agent_codespaces import connection_owner as owner
root = Path(sys.argv[1])
owner.RUNTIME_DIR = root
owner.LIVE_FILE = root / "connection-owner.live.json"
owner.ensure_runtime_dir = lambda: None
time.sleep(6.0)
root.joinpath("writer-pid").write_text(str(os.getpid()), encoding="ascii")
owner._write_liveness(15.0)
deadline = time.monotonic() + 15.0
while not (root / "stop").exists() and time.monotonic() < deadline:
    time.sleep(0.05)
"""

    def spawn_fixture(argv, **kwargs):
        if argv != [sys.executable, "-I", "-m", "agent_codespaces", "owner"]:
            return real_spawn(argv, **kwargs)
        child = real_spawn([sys.executable, "-c", script, str(tmp_path)], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn_fixture)
    try:
        assert owner.ensure_owner_running(types.SimpleNamespace()) is True
        assert len(children) == 1
        live = owner.read_liveness()
        assert live is not None
        assert live.pid == int((tmp_path / "writer-pid").read_text(encoding="ascii"))
        assert live.process_identity
        assert children[0].poll() is None
        assert not (tmp_path / "connection-owner.json").exists()
    finally:
        stop_file.touch()
        for child in children:
            try:
                child.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5.0)
