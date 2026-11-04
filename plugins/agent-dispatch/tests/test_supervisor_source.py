from __future__ import annotations

from concurrent.futures import Future
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon


VERSION = "1.2.3+source." + "a" * 40 + "." + "b" * 16 + ".profile." + "c" * 16


def test_preparation_directory_io_is_not_an_absent_candidate(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_source

    directory = tmp_path / "run" / "supervisor-candidates"
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture") is None
    directory.mkdir(parents=True)
    original = os.scandir
    original_iterdir = Path.iterdir

    def denied(path):
        if not isinstance(path, int) and Path(path) == directory:
            raise PermissionError("fixture preparation enumeration denied")
        return original(path)

    def denied_iterdir(path):
        if path == directory:
            raise PermissionError("fixture preparation enumeration denied")
        return original_iterdir(path)

    monkeypatch.setattr(os, "scandir", denied)
    monkeypatch.setattr(Path, "iterdir", denied_iterdir)
    with pytest.raises(PermissionError, match="enumeration denied"):
        supervisor_source.read_prepared(tmp_path, VERSION, "fixture")


def _incumbent(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_source

    (tmp_path / "current-version").write_text(VERSION, encoding="utf-8")
    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=FakeLock(),
        self_update_enabled=True, self_update_argv=["supervise", "serve"],
        self_update_install_dir=lambda: tmp_path,
        self_update_stale_target=lambda *_: Path("incoming-python"),
        self_update_running_version="old",
    )
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    monkeypatch.setattr(supervisor_source, "read_prepared", lambda *_args, **_kwargs: None)
    return daemon, supervisor_source


def test_source_incumbent_does_not_stop_before_independent_preparation(tmp_path, monkeypatch):
    daemon, source = _incumbent(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(source, "spawn_candidate", lambda *_args, **_kwargs: (
        calls.append("spawn") or SimpleNamespace(instance_receipt=object(), poll=lambda: None)
    ))
    monkeypatch.setattr(daemon, "shutdown", lambda: pytest.fail("unready incumbent stop"))
    assert daemon._maybe_self_update() is False
    assert daemon._maybe_self_update() is False
    assert calls == ["spawn"]
    assert not daemon._lock.released


def test_source_incumbent_retains_custody_of_unfinished_native_work(tmp_path, monkeypatch):
    daemon, source = _incumbent(tmp_path, monkeypatch)
    ready = {"instance_id": "d" * 32}
    monkeypatch.setattr(source, "read_prepared", lambda *_args, **_kwargs: ready)
    future = Future()
    daemon._probe_inflight.add(future)
    monkeypatch.setattr(daemon, "shutdown", lambda: pytest.fail("unfinished work stop"))
    assert daemon._maybe_self_update() is False
    assert not daemon._lock.released
    assert daemon._source_handoff["phase"] == "withdrawing"


def test_source_handoff_requires_settled_workers_before_singleton_release(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_retirement

    daemon, source = _incumbent(tmp_path, monkeypatch)
    monkeypatch.setattr(source, "read_prepared", lambda *_args, **_kwargs: {"instance_id": "d" * 32})
    monkeypatch.setattr(daemon, "shutdown", lambda: True)
    monkeypatch.setattr(supervisor_retirement, "_retire_owned_workers", lambda *_: False)
    assert daemon._maybe_self_update() is False
    assert not daemon._lock.released
    monkeypatch.setattr(supervisor_retirement, "_retire_owned_workers", lambda *_: True)
    assert daemon._maybe_self_update() is True
    assert daemon._lock.released


def test_passive_supervisor_never_reconciles_before_original_singleton_admission(tmp_path, monkeypatch):
    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock())
    calls = []
    ownership = iter([False, True])
    monkeypatch.setattr(daemon, "acquire_singleton", lambda: calls.append("acquire") or next(ownership))
    monkeypatch.setattr(daemon, "reconcile_once", lambda: (
        pytest.fail("once not allowed for a passive candidate")
    ))
    admission = SimpleNamespace(
        prepare=lambda _daemon: calls.append("prepared"),
        check=lambda: calls.append("checked"),
        admitted=lambda: calls.append("admitted"),
    )
    with pytest.raises(ValueError, match="singleton"):
        daemon.serve(once=True, source_admission=admission)
    assert calls == []


def test_passive_supervisor_reads_cohort_without_queue_or_unit_mutation(monkeypatch, tmp_path):
    from agent_dispatch import source_start, supervisor_source
    from agent_dispatch.pool_source_snapshot import DeclarationRead

    calls = []
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    daemon = SimpleNamespace(
        machine=None, env="default",
        declared_source=lambda: calls.append("cohort") or DeclarationRead((), True),
        _recheck_governance=lambda _: None,
    )
    monkeypatch.setattr(supervisor_source, "__version__", VERSION)
    admission = supervisor_source.CandidateAdmission(
        tmp_path, VERSION, object(), lambda: None, probe=lambda: None,
    )
    monkeypatch.setattr(admission, "_publish", lambda _: calls.append("prepared"))
    admission.prepare(daemon)
    assert calls == ["cohort", "prepared"]


def test_unknown_cohort_is_not_passive_application_readiness(monkeypatch, tmp_path):
    from agent_dispatch import source_start, supervisor_source
    from agent_dispatch.pool_source_snapshot import DeclarationRead

    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    daemon = SimpleNamespace(
        machine=None, env="default", declared_source=lambda: DeclarationRead((), False, "unknown"),
        _recheck_governance=lambda _: None,
    )
    monkeypatch.setattr(supervisor_source, "__version__", VERSION)
    admission = supervisor_source.CandidateAdmission(
        tmp_path, VERSION, object(), lambda: None, probe=lambda: None,
    )
    monkeypatch.setattr(admission, "_publish", lambda _: pytest.fail("unknown prepared"))
    with pytest.raises(RuntimeError, match="cohort"):
        admission.prepare(daemon)


def test_preparation_requires_native_artifact_custody_not_running_bit(monkeypatch, tmp_path):
    from agent_dispatch import companion, supervisor_source
    from agent_procutil import _daemon_instance_identity, daemon_instances

    identity = "e" * 32
    directory = tmp_path / "run" / "supervisor-candidates"
    directory.mkdir(parents=True)
    path = directory / (identity + ".json")
    document = {
        "runtime_version": VERSION, "scope": "fixture",
        "generation": {"pid": os.getpid(), "start_token": "start"},
        "progress": {"phase": "prepared"}, "instance_id": identity,
        "registry_root": str(tmp_path / "registry"), "birth": "birth",
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    native = {
        "instance_id": identity, "state": "running", "runtime_root": str(tmp_path),
        "pid": str(os.getpid()), "birth": "birth", "artifact_count": "0",
    }
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [native])
    monkeypatch.setattr(companion, "process_start_token", lambda _: "start")
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: "matching")
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture") is None
    device, inode = _daemon_instance_identity.path_identity(path)
    native.update({
        "artifact_count": "1", "artifact_0_path": str(path),
        "artifact_0_device": device, "artifact_0_inode": inode,
    })
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture") == document


def test_source_retirement_does_not_forget_unit_with_unconfirmed_native_exit(tmp_path):
    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock())
    daemon._source_handoff = {"phase": "withdrawing"}

    def wait(**_kwargs):
        raise TimeoutError("native writer remains")

    proc = SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=wait)
    unit = SimpleNamespace(proc=proc, kind="supervised-lane")
    daemon._units["fixture"] = unit
    assert daemon._stop("fixture") is False
    assert daemon._units["fixture"] is unit


def test_passive_admission_waits_for_original_scope_before_any_reconcile(monkeypatch, tmp_path):
    from agent_dispatch import supervisor_source

    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock())
    calls = []
    granted = iter([False, True])
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    monkeypatch.setattr(daemon, "acquire_singleton", lambda: calls.append("acquire") or next(granted))
    monkeypatch.setattr(daemon, "reconcile_once", lambda: pytest.fail("passive reconciliation"))
    daemon.sleep = lambda _: calls.append("wait")
    admission = SimpleNamespace(
        root=tmp_path, version=VERSION,
        prepare=lambda _: calls.append("prepared"), check=lambda: calls.append("check"),
        probe=lambda: calls.append("probe"), admitted=lambda: calls.append("admitted"),
    )
    assert supervisor_source.acquire_candidate(
        daemon, admission, once=False, single_instance=True,
    )
    assert calls == ["prepared", "check", "acquire", "wait", "check", "acquire", "check", "probe", "admitted"]
