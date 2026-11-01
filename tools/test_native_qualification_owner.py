"""Fake Windows owner boundaries; never native qualification."""

from __future__ import annotations

from contextlib import contextmanager
import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import native_qualification_contract as contract
from tools import plugin_test_containment as owner

UTC = "2026-10-31T08:00:00+00:00"


def fake_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, assignment_at: float = 20,
    work_completed: bool = True, held_lifecycle: bool = False,
) -> tuple[contract.Qualification, list[str], list[float]]:
    (tmp_path / "evidence").mkdir()
    now = [20.0]
    events: list[str] = []
    result = contract.Qualification(contract.Boundary(10, UTC), 7, "a" * 64, tmp_path)
    terminal = {
        "execution_scope": 7, "monotonic_start": 10, "evidence_phase": contract.DESCENDANT_PHASE,
        "original_job_drained": False, "original_owner_settlement_required": True,
        "functional_positive": True,
    }
    raw = b"FUNCTIONAL START: scope 7; functional only\n" + json.dumps(terminal).encode()
    report = {
        "kind": "native-qualification-receiver", "passed": True,
        "evidence_phase": contract.DESCENDANT_PHASE, "original_job_drained": False,
        "original_owner_settlement_required": True,
        "execution_scope": 7, "monotonic_start": 10, "started_utc": UTC,
        "manifest_sha256": "a" * 64, "runtime_root": str(tmp_path),
        "desktop_qualified": False, "visible_controls_attempted": 0, "errors": [],
        "native_exit": 0, "native_exited": True, "streams_closed": True,
        "readers_settled": True, "process_handle_closed": True,
        "preparation_sources_unchanged_after": True,
        "post_capture_monotonic": 20, "post_artifact_verification_monotonic": 20,
        "terminal": terminal, "captured_streams": {
            name: {
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "base64": base64.b64encode(data).decode(), "complete": True,
            } for name, data in (("stdout", raw), ("stderr", b""))
        },
    }

    class Reader:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

        def join(self, timeout):
            assert 0 <= timeout <= 170

        def is_alive(self):
            return False

    class Process:
        pid = 99

        def __init__(self, argv, **kwargs):
            assert argv[2] == "_worker-qualification"
            assert argv[3:5] == ["10", UTC]
            assert kwargs["bufsize"] == 0 and kwargs["creationflags"] == 0x08000000
            assert not (tmp_path / ".containment-ready").exists()
            events.append("worker-created")
            self.stdout = io.BytesIO(json.dumps(report).encode("ascii"))
            self.stderr = io.BytesIO()
            self._handle = SimpleNamespace(Close=lambda: events.append("worker-handle-closed"))

        def poll(self):
            assert (tmp_path / ".containment-ready").read_text() == "assigned\n"
            if work_completed:
                contract.publish_work_completion(
                    result, candidate_exit=0, workers_exited=True, clock=lambda: now[0],
                )
            events.append("worker-exited")
            return 0

        def wait(self, *, timeout):
            assert 0 <= timeout <= min(5, 190 - now[0])
            events.append("worker-reaped")
            return 0

        def kill(self):
            pytest.fail("fake exited worker must not be killed")

    class Job:
        def __init__(self, limits):
            assert limits == owner.Limits(wall_seconds=165)
            events.append("original-job-created")

        def assign(self, pid):
            assert pid == 99
            events.append("worker-assigned")
            now[0] = assignment_at

        def close(self, *, qualification):
            events.append("job-zero-before-close")
            qualification.boundary.require(lambda: now[0])
            qualification.job_zero_observed = qualification.job_handle_closed = True
            events.append("original-job-closed")

    @contextmanager
    def lifecycle(**kwargs):
        assert kwargs["deadline"] == 190
        assert not (tmp_path / ".containment-ready").exists()
        events.append("lifecycle-entered")
        yield {}
        events.append("lifecycle-exited")
        if held_lifecycle:
            kwargs["cleanup_results"].append(SimpleNamespace(status="held", reason="unconfirmed"))

    monkeypatch.setattr(owner, "os", SimpleNamespace(name="nt", getpid=lambda: 42))
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=lambda _seconds: None))
    monkeypatch.setattr(owner, "_WindowsJob", Job)
    monkeypatch.setattr(owner.subprocess, "Popen", Process)
    monkeypatch.setattr(owner, "no_window_kwargs", lambda: {"creationflags": 0x08000000})
    monkeypatch.setattr(owner, "_read_registry_environment", lambda: events.append("registry-before"))
    monkeypatch.setattr(owner, "_registry_environment_drift", lambda _snapshot: events.append("registry-after") or [])
    monkeypatch.setattr(owner, "daemon_test_lifecycle", lifecycle)
    monkeypatch.setattr(contract, "threading", SimpleNamespace(Thread=Reader))
    return result, events, now


def run(result: contract.Qualification, **kwargs: Any) -> int:
    return owner.run_contained(
        ["source-qualified-receiver"], cwd=result.runtime_root,
        sandbox=result.runtime_root, env={}, limits=owner.Limits(wall_seconds=165),
        qualification=result, **kwargs,
    )


def test_one_original_job_owns_receiver_and_settles_before_lifecycle_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, events, _now = fake_owner(tmp_path, monkeypatch)
    assert run(result) == 0
    result.require_settled(lambda: 20)
    assert events == [
        "registry-before", "lifecycle-entered", "worker-created", "original-job-created",
        "worker-assigned", "worker-exited", "job-zero-before-close", "original-job-closed",
        "worker-reaped", "worker-handle-closed", "lifecycle-exited", "registry-after",
    ]
    assert events.count("original-job-created") == 1
    assert not (tmp_path / ".containment-ready").exists()


def test_assignment_setup_crossing_work_deadline_cannot_release_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, events, _now = fake_owner(tmp_path, monkeypatch, assignment_at=176)
    with pytest.raises(contract.QualificationError, match="deadline"):
        run(result)
    assert events.count("original-job-created") == 1 and "worker-assigned" in events
    assert "worker-exited" not in events
    assert not (tmp_path / ".containment-ready").exists()


def test_receiver_zero_exit_without_work_phase_is_not_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _events, _now = fake_owner(tmp_path, monkeypatch, work_completed=False)
    with pytest.raises(contract.QualificationError, match="work-completion"):
        run(result)
    assert not result.work_completed and not result.lifecycle_settled


def test_held_lifecycle_outcome_blocks_an_otherwise_settled_native_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _events, _now = fake_owner(tmp_path, monkeypatch, held_lifecycle=True)
    with pytest.raises(contract.QualificationError, match="settlement"):
        run(result)
    assert result.job_zero_observed and not result.lifecycle_settled


def test_qualification_sink_is_one_shot_even_after_a_failed_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, events, now = fake_owner(tmp_path, monkeypatch, assignment_at=176)
    with pytest.raises(contract.QualificationError):
        run(result)
    now[0] = 20
    before = list(events)
    with pytest.raises(contract.QualificationError, match="already used"):
        run(result)
    assert events == before


def test_original_job_forced_drain_remains_nonpositive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = contract.Qualification(contract.Boundary(10, UTC), 7, "a" * 64, tmp_path)
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: 20, sleep=lambda _seconds: None))
    job = object.__new__(owner._WindowsJob)
    active = iter([2, 0])
    job.active_processes = lambda: next(active)
    job._handle = 7
    job._kernel32 = SimpleNamespace(TerminateJobObject=lambda *_args: 1, CloseHandle=lambda _handle: 1)
    job.close(qualification=result)
    assert result.job_zero_observed and result.job_handle_closed and result.forced_termination


def test_reap_timeout_uses_only_remaining_original_whole_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = contract.Qualification(contract.Boundary(10, UTC), 7, "a" * 64, tmp_path)
    now = [189.0]
    waits = []
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def wait(*, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise owner.subprocess.TimeoutExpired("fake", timeout)
        return 0

    process = SimpleNamespace(
        wait=wait, kill=lambda: now.__setitem__(0, 190),
        _handle=SimpleNamespace(Close=lambda: None),
    )
    with pytest.raises(contract.QualificationError, match="deadline"):
        owner._reap_controller(process, qualification=result)
    assert waits == [1, 0] and result.forced_termination
