"""Creation refusal, uncertain custody and exact execution provenance."""

import os
import io
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
    diagnostic = (spec.runtime_root / "log").read_text()
    assert "instance-reserved" in diagnostic and "creating-suspended" in diagnostic
    assert "created-suspended" not in diagnostic and "spawn_daemon: resumed" not in diagnostic
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


@pytest.mark.parametrize("operation", ["write", "flush"])
def test_native_precreation_diagnostic_failure_cancels_only_its_reservation(
    launch, monkeypatch, operation,
):
    start, spec = launch
    failure = OSError("fixture diagnostic storage unavailable")
    popen = Mock()
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", popen)

    class Output:
        reserved = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, data):
            self.reserved = b"instance-reserved" in data
            if self.reserved and operation == "write":
                raise failure

        def flush(self):
            if self.reserved and operation == "flush":
                raise failure

    monkeypatch.setattr(pu, "_open_daemon_log", lambda _path: Output())
    with pytest.raises(OSError) as caught:
        start()
    assert caught.value is failure
    assert inspect_instances(spec.registry_root) == []
    assert list(spec.registry_root.glob("*.lock"))
    popen.assert_not_called()


@pytest.mark.parametrize("stage", [
    "creating-suspended", "created-suspended", "caller-jobs-verified", "resumed", "instance-bound",
])
def test_native_child_is_returned_even_when_stage_diagnostic_storage_fails(launch, monkeypatch, stage):
    from agent_procutil import _daemon_instance_identity, _job_scopes

    start, spec = launch
    process = Mock(pid=4321)
    seen = []
    original = pu._open_daemon_log

    @contextmanager
    def output(path):
        with original(path) as stream:
            class Output:
                def write(self, data):
                    if ("spawn_daemon: " + stage + "\n").encode() in data:
                        seen.append(stage)
                        raise OSError("fixture final diagnostic unavailable")
                    return stream.write(data)

                def flush(self):
                    stream.flush()

            yield Output()

    @contextmanager
    def scopes(_environment):
        yield lambda _handle: None

    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_resume_suspended_process", lambda _pid: True)
    monkeypatch.setattr(pu, "_open_daemon_log", output)
    monkeypatch.setattr(_job_scopes, "query_scopes", scopes)
    monkeypatch.setattr(_daemon_instance_identity, "created_process_identity", lambda _process: (4321, "fixture"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    assert start() is process
    assert seen == [stage]
    record = inspect_instances(spec.registry_root)[0]
    assert record["state"] == "bound" and record["pid"] == "4321"
    assert process.instance_receipt.instance_id == record["instance_id"]
    process.kill.assert_not_called()


def test_failed_native_diagnostics_preserve_unconfirmed_child_custody(launch, monkeypatch):
    from agent_procutil import _daemon_instance_identity, _job_scopes

    start, spec = launch
    process = Mock(pid=4321)
    process.kill.side_effect = OSError("fixture termination unconfirmed")
    original = pu._open_daemon_log

    @contextmanager
    def output(path):
        with original(path) as stream:
            class Output:
                def write(self, data):
                    if b"instance-reserved" not in data and b"spawn_daemon:" in data:
                        raise OSError("fixture diagnostic storage unavailable")
                    return stream.write(data)

                def flush(self):
                    stream.flush()

            yield Output()

    @contextmanager
    def scopes(_environment):
        yield lambda _handle: None

    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_resume_suspended_process", lambda _pid: False)
    monkeypatch.setattr(pu, "_open_daemon_log", output)
    monkeypatch.setattr(_job_scopes, "query_scopes", scopes)
    monkeypatch.setattr(_daemon_instance_identity, "created_process_identity", lambda _process: (4321, "fixture"))
    monkeypatch.setattr(store, "finish_failed_launch", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    with pytest.raises(pu.DaemonSpawnError) as caught:
        start()
    assert caught.value.process is process
    assert caught.value.cleanup_confirmed is False and caught.value.process_created is True
    assert caught.value.process_identity == (4321, "fixture")
    assert caught.value.instance_receipt.record_path.is_file()
    assert "failed to resume" in str(caught.value.__cause__)


@pytest.mark.parametrize("cleanup_unconfirmed", [False, True])
def test_persistent_buffered_flush_and_close_failures_preserve_child_outcome(
    launch, monkeypatch, cleanup_unconfirmed,
):
    from agent_procutil import _daemon_instance_identity, _job_scopes

    start, spec = launch
    process = Mock(pid=4321)

    class Storage(io.RawIOBase):
        attempts = 0

        def writable(self):
            return True

        def write(self, data):
            self.attempts += 1
            if self.attempts > 2:
                raise OSError("fixture persistent diagnostic storage failure")
            return len(data)

    raw = Storage()
    stream = io.BufferedWriter(raw)

    @contextmanager
    def scopes(_environment):
        yield lambda _handle: None

    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_resume_suspended_process", lambda _pid: not cleanup_unconfirmed)
    monkeypatch.setattr(pu, "_open_daemon_log", lambda _path: stream)
    monkeypatch.setattr(_job_scopes, "query_scopes", scopes)
    monkeypatch.setattr(_daemon_instance_identity, "created_process_identity", lambda _process: (4321, "fixture"))
    monkeypatch.setattr(store, "finish_failed_launch", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    if cleanup_unconfirmed:
        process.kill.side_effect = OSError("fixture termination unconfirmed")
        with pytest.raises(pu.DaemonSpawnError) as caught:
            start()
        assert caught.value.process is process and caught.value.cleanup_confirmed is False
        assert caught.value.instance_receipt.record_path.is_file()
    else:
        assert start() is process
        record = inspect_instances(spec.registry_root)[0]
        assert record["state"] == "bound" and process.instance_receipt.instance_id == record["instance_id"]
        process.kill.assert_not_called()
    assert stream.closed and raw.attempts > 2


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
