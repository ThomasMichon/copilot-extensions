"""Creation refusal, uncertain custody and exact execution provenance."""

import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

import agent_procutil as pu
from agent_procutil import daemon, daemon_broker, _daemon_instance_store as store
from agent_procutil.daemon_instances import DaemonInstanceSpec, inspect_instances


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    spec = DaemonInstanceSpec(
        "failure-fixture", tmp_path, Path(__file__).resolve(),
        registry_root=tmp_path / "inventory",
    )

    def start(**kwargs):
        return daemon.spawn_daemon(
            ["fixture-executable"], cwd=tmp_path, log_path=tmp_path / "log",
            instance=spec, **kwargs,
        )
    return start, spec


@pytest.mark.parametrize("error", [FileNotFoundError("missing"), PermissionError("refused")])
def test_direct_positive_creation_refusal_removes_only_record(launch, monkeypatch, error):
    start, spec = launch
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=error))
    with pytest.raises(type(error)):
        start(breakaway=False)
    assert inspect_instances(spec.registry_root) == []
    assert (spec.runtime_root / "log").exists()
    assert list(spec.registry_root.glob("*.lock")), "canonical locks are not deleted"


def test_positive_broker_refusal_removes_record(launch, monkeypatch):
    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", Mock(side_effect=pu.DaemonSpawnError(
        "no daemon requested", process_created=False, admission_sent=False,
    )))
    with pytest.raises(pu.DaemonSpawnError):
        start()
    assert inspect_instances(spec.registry_root) == []


def test_refused_contained_fallback_also_removes_its_new_reservation(launch, monkeypatch):
    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", Mock(side_effect=pu.DaemonSpawnError(
        "no daemon requested", process_created=False, admission_sent=False,
    )))
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=FileNotFoundError("fallback missing")))
    with pytest.raises(FileNotFoundError):
        start(contained_fallback=True)
    assert inspect_instances(spec.registry_root) == []


@pytest.mark.parametrize("confirmed", [False, True])
def test_unknown_broker_creation_never_cancels_or_replays(launch, monkeypatch, confirmed):
    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", Mock(side_effect=pu.DaemonSpawnError(
        "unknown creation", cleanup_confirmed=confirmed,
    )))
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(pu.DaemonSpawnError) as failure:
        start(contained_fallback=True)
    popen.assert_not_called()
    rows = inspect_instances(spec.registry_root)
    assert len(rows) == 1
    assert failure.value.instance_receipt.instance_id == rows[0]["instance_id"]


@pytest.mark.parametrize("admitted", [False, True])
def test_known_dead_broker_child_settles_only_pre_admission_tracking(launch, monkeypatch, admitted):
    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    proof = spec.runtime_root / "retained-proof"
    proof.write_text("keep")

    def broker(*_args, instance_receipt, **_kwargs):
        with store._locked(instance_receipt) as path:
            row = store._read(path)
            row.update(state="bound", pid="4321", birth="fixture-birth")
            store._write(path, row)
        raise pu.DaemonSpawnError(
            "created child reaped", process_created=True, admission_sent=admitted,
            process_identity=(4321, "fixture-birth"),
        )
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", broker)
    with pytest.raises(pu.DaemonSpawnError):
        start()
    rows = inspect_instances(spec.registry_root)
    assert len(rows) == int(admitted)
    if admitted:
        assert rows[0]["spawn_failure"] == "exit-confirmed-effects-held"
    assert proof.read_text() == "keep"


@pytest.mark.parametrize("exit_confirmed", [False, True])
def test_bind_failure_retains_original_receipt_and_never_deletes_artifacts(
    launch, monkeypatch, exit_confirmed,
):
    start, spec = launch
    child = Mock(pid=4321)
    if not exit_confirmed:
        child.wait.side_effect = subprocess.TimeoutExpired("fixture", 5)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=child))
    from agent_procutil import _daemon_instance_identity as identities
    monkeypatch.setattr(identities, "created_process_identity", lambda _: (4321, "fixture-birth"))
    monkeypatch.setattr(store, "bind_process", Mock(side_effect=OSError("commit refused")))
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead" if exit_confirmed else "matching")
    with pytest.raises(pu.DaemonSpawnError) as failure:
        start(breakaway=False)
    assert failure.value.cleanup_confirmed is exit_confirmed
    assert failure.value.instance_receipt.record_path.is_file()
    row = inspect_instances(spec.registry_root)[0]
    assert row["pid"] == "4321"
    assert row["spawn_failure"] == (
        "exit-confirmed-effects-held" if exit_confirmed else "exit-unconfirmed"
    )
    child.kill.assert_called_once()


def test_posix_lookup_uses_child_path_and_relative_entries_from_child_cwd(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    for folder in (parent, child):
        target = folder / "worker"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
    monkeypatch.setenv("PATH", str(parent))
    assert daemon._resolve_posix_executable("worker", str(tmp_path), {"PATH": "child"}) == (
        child / "worker"
    ).resolve()


def test_missing_automatic_executable_has_no_library_source_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", Mock())
    missing = tmp_path / "definitely-missing.exe"
    with pytest.raises(FileNotFoundError):
        daemon.spawn_daemon([str(missing)], cwd=tmp_path, log_path=tmp_path / "log")
    subprocess.Popen.assert_not_called()
    assert inspect_instances() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess lookup contract")
def test_windows_automatic_bare_search_requires_explicit_instance(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    for folder in (parent, child):
        (folder / "worker.exe").write_bytes(b"not-a-real-executable")
    monkeypatch.setenv("PATH", str(parent))
    with pytest.raises(ValueError, match="absolute executable"):
        daemon.spawn_daemon(
            ["worker.exe"], cwd=tmp_path, log_path=tmp_path / "log", env={"PATH": str(child)},
        )
    assert inspect_instances() == []
