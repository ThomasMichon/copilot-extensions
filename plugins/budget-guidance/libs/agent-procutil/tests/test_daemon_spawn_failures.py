"""Creation refusal, uncertain custody and exact execution provenance."""

import os
from pathlib import Path
import subprocess
from contextlib import contextmanager
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


def test_positive_native_breakaway_refusal_removes_record_without_activation(launch, monkeypatch):
    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    activate = Mock(side_effect=AssertionError("unexpected activation"))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", activate)
    error = PermissionError("breakaway refused")
    error.winerror = 5
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=error))
    with pytest.raises(pu.DaemonSpawnError):
        start()
    assert inspect_instances(spec.registry_root) == []
    activate.assert_not_called()


@pytest.mark.parametrize("mode", ["durable-windows", "contained-windows", "posix"])
@pytest.mark.parametrize("error", [KeyboardInterrupt(), RuntimeError("creation handoff lost")])
def test_interrupted_creation_retains_unknown_reservation_without_replay(launch, monkeypatch, mode, error):
    from agent_procutil import _job_scopes

    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: mode != "posix")

    @contextmanager
    def scopes(environment):
        yield Mock()

    monkeypatch.setattr(_job_scopes, "query_scopes", scopes)
    popen = Mock(side_effect=error)
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(type(error)) as caught:
        start(breakaway=mode == "durable-windows", contained_fallback=True)
    assert caught.value is error
    assert len(inspect_instances(spec.registry_root)) == 1
    assert popen.call_count == 1
    assert "creation-outcome-unknown" in (spec.runtime_root / "log").read_text()


def test_query_cleanup_cannot_reclassify_interrupted_creation_as_refusal(launch, monkeypatch):
    from agent_procutil import _job_scopes

    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)

    @contextmanager
    def scopes(environment):
        try:
            yield Mock()
        finally:
            raise OSError("query handle close failed")

    monkeypatch.setattr(_job_scopes, "query_scopes", scopes)
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=KeyboardInterrupt()))
    with pytest.raises(OSError, match="query handle close failed"):
        start(contained_fallback=True)
    assert len(inspect_instances(spec.registry_root)) == 1
    assert subprocess.Popen.call_count == 1
    assert "creation-outcome-unknown" in (spec.runtime_root / "log").read_text()


def test_native_reservation_failure_is_visible_without_exception_payload(launch, monkeypatch):
    from agent_procutil import daemon_instances

    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    failure = OSError(5, "synthetic-private-exception-payload")
    monkeypatch.setattr(daemon_instances, "reserve_instance", Mock(side_effect=failure))
    broker = Mock(side_effect=AssertionError("unreserved broker launch"))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", broker)
    with pytest.raises(OSError) as caught:
        start()
    assert caught.value is failure
    text = (spec.runtime_root / "log").read_text()
    assert "instance-reservation-failed" in text and "errno=5" in text
    assert "synthetic-private-exception-payload" not in text
    broker.assert_not_called()


@pytest.mark.parametrize("error,retained", [(FileNotFoundError("fallback missing"), False), (KeyboardInterrupt(), True)])
def test_contained_fallback_settles_only_positive_creation_refusal(launch, monkeypatch, error, retained):
    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", Mock(side_effect=pu.DaemonSpawnError(
        "no daemon requested", process_created=False, admission_sent=False,
    )))
    denied = PermissionError("breakaway refused")
    denied.winerror = 5
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=[denied, error]))
    with pytest.raises(type(error)):
        start(contained_fallback=True)
    assert len(inspect_instances(spec.registry_root)) == int(retained)


@pytest.mark.parametrize("kill_fails", [False, True])
def test_unconfirmed_native_cleanup_never_cancels_or_replays(launch, monkeypatch, kill_fails):
    from agent_procutil import _job_scopes, _daemon_instance_identity

    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    child = Mock(pid=4321, _handle=456)
    if kill_fails:
        child.kill.side_effect = OSError("kill refused")
    child.wait.side_effect = subprocess.TimeoutExpired("fixture", 5)
    monkeypatch.setattr(_daemon_instance_identity, "created_process_identity", lambda _: (4321, "fixture-birth"))
    monkeypatch.setattr(store, "identity_state", lambda *_: "matching")

    @contextmanager
    def scopes(environment):
        yield Mock(side_effect=OSError("cleanup Job retained"))

    monkeypatch.setattr(_job_scopes, "query_scopes", scopes)
    popen = Mock(return_value=child)
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(pu.DaemonSpawnError) as failure:
        start(contained_fallback=True)
    assert popen.call_count == 1
    assert failure.value.process is child
    assert not failure.value.cleanup_confirmed
    rows = inspect_instances(spec.registry_root)
    assert len(rows) == 1
    assert failure.value.instance_receipt.instance_id == rows[0]["instance_id"]


@pytest.mark.parametrize("admitted", [False, True])
def test_native_child_settles_only_before_resume_attempt(launch, monkeypatch, admitted):
    from agent_procutil import _job_scopes, _daemon_instance_identity

    start, spec = launch
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: True)
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    proof = spec.runtime_root / "retained-proof"
    proof.write_text("keep")

    child = Mock(pid=4321, _handle=456)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(_daemon_instance_identity, "created_process_identity", lambda _: (4321, "fixture-birth"))
    monkeypatch.setattr(pu, "_resume_suspended_process", lambda pid: False)

    @contextmanager
    def scopes(environment):
        yield Mock() if admitted else Mock(side_effect=OSError("cleanup Job retained"))

    monkeypatch.setattr(_job_scopes, "query_scopes", scopes)
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
