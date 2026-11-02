"""Actor-free actual stage/receiver wiring, never native qualification."""

from __future__ import annotations

import io
import json
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import native_process_stage as stage
from tools import native_qualification_contract as contract
from tools import native_qualification_receiver as receiver
from tools.promotion_unit_tests.test_native_qualification_contract import receiver_report

UTC = "2026-10-31T08:00:00+00:00"


@pytest.fixture
def prepared_stage(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    (root / "evidence").mkdir(parents=True)
    args = dict(
        scope=7, acceptance="accepted", manifest_sha256="a" * 64, runtime_root=root,
        original_start_monotonic=10, original_start_utc=UTC,
    )
    stage.artifacts.write_new(root / "evidence/owner-admission.json", {
        "scope": 7, "acceptance": "accepted", "manifest_sha256": "a" * 64,
        "monotonic_start": 10, "started_utc": UTC, "runtime_root": str(root),
    })
    now, events, fault = [20.0], [], {"at": ""}
    rows = [{"path": str(tmp_path / "source"), "bytes": 1, "sha256": "b" * 64}]
    junit = {"case_count": 44}

    def load(launcher, digest, boundary, clock):
        events.append("sources")
        boundary.require(clock)
        return tmp_path, {"nonce": "c" * 32, "case_packets": [16, 16, 8, 4]}, rows

    def candidate(argv, output, boundary):
        events.append("candidate")
        assert argv[1:5] == ["-I", "-B", "-X", "utf8"] and argv[-2] == "process-safety"
        request = json.loads(Path(argv[-1]).read_bytes())
        assert request["deadline"] == 175 and request["original_start_monotonic"] == 10
        assert not any(key in request for key in ("visible_controls", "original"))
        path = Path(request["output"])
        path.mkdir()
        report = {
            "kind": "sdk-process-safety", "passed": True, "desktop_observed": False,
            "desktop_qualified": False, "visible_controls_attempted": 0,
            "source_before": rows, "source_after": rows, "native_junit": junit,
            "process_owner": {"inherited_job_membership": True, "elevated": False},
            "workers": [{
                "packet": i, "succeeded": True, "cleanup_confirmed": True, "exit": 0,
                "cleanup_errors": [], "reader_error": None, "readers_alive": [False, False],
            } for i in range(4)],
        }
        if fault["at"] == "worker":
            report["workers"][3]["cleanup_confirmed"] = False
        elif fault["at"] == "desktop":
            report["desktop_observed"] = True
        elif fault["at"] == "functional":
            report["kind"] = "sdk-functional"
        elif fault["at"] == "source":
            report["source_after"] = []
        elif fault["at"] == "late":
            now[0] = 175
        stage.artifacts.write_new(path / "process-safety-result.json", report)
        return 1 if fault["at"] == "exit" else 0

    monkeypatch.setattr(stage, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(stage, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(stage.sources, "load", load)
    monkeypatch.setattr(stage.sources, "sdk", lambda *a: (SimpleNamespace(
        __file__="fixture-runner", verify_native_junit=lambda *a: junit,
    ), rows))
    monkeypatch.setattr(stage.receiver, "_require_existing_job", lambda: events.append("inherited-job"))
    monkeypatch.setattr(stage, "candidate", candidate)
    return args, now, events, fault


def test_real_nonowner_stage_publishes_original_work_and_distinct_index(prepared_stage, capsys):
    args, now, events, _ = prepared_stage
    terminal = stage.run(**args)
    assert events == ["sources", "inherited-job", "candidate", "sources"]
    assert terminal["qualification"] == contract.PROCESS_MODE
    assert terminal["original_job_drained"] is False
    assert terminal["original_owner_settlement_required"] is True
    qualification = contract.Qualification(
        contract.Boundary(10, UTC), 7, "a" * 64, args["runtime_root"], mode=contract.PROCESS_MODE,
    )
    assert qualification.observe_work(lambda: now[0])
    raw = capsys.readouterr().out.encode() + json.dumps(terminal).encode()
    assert receiver.terminal(
        raw, boundary=qualification.boundary, scope=7, native_exit=0,
        captured_at=20, mode=contract.PROCESS_MODE,
    ) == terminal
    receiver.verify_artifacts(
        terminal, parent=args["runtime_root"], manifest_sha256="a" * 64,
        boundary=qualification.boundary, clock=lambda: 20,
    )
    report = json.loads(receiver_report(qualification))
    report.update(qualification=contract.PROCESS_MODE, desktop_observed=False, terminal=terminal)
    report["captured_streams"]["stdout"] = {
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": base64.b64encode(raw).decode(), "complete": True,
    }
    qualification.capture.streams[0].data.extend(json.dumps(report).encode())
    qualification.observe_receiver(lambda: 20)
    assert qualification.receiver == report
    with pytest.raises(contract.QualificationError, match="settlement"):
        qualification.require_settled(lambda: 20)
    with pytest.raises(contract.QualificationError):
        receiver.terminal(raw, boundary=qualification.boundary, scope=7, native_exit=0, captured_at=20)


@pytest.mark.parametrize("failure", ["worker", "desktop", "functional", "source", "late", "exit"])
def test_stage_failure_never_publishes_retirement_permission(prepared_stage, failure):
    args, _, events, fault = prepared_stage
    fault["at"] = failure
    with pytest.raises(contract.QualificationError):
        stage.run(**args)
    assert not (args["runtime_root"] / "evidence/work-completed.json").exists()
    assert not (args["runtime_root"] / "artifacts/sha256.json").exists()
    assert events.count("candidate") == 1


def test_stage_refuses_foreign_admission_before_candidate_or_native_membership(prepared_stage):
    args, _, events, _ = prepared_stage
    args["acceptance"] = "another acceptance"
    with pytest.raises(contract.QualificationError, match="admission"):
        stage.run(**args)
    assert events == ["sources"]


@pytest.mark.parametrize("field,value", [
    ("qualification", "functional-only"), ("desktop_observed", True),
    ("process_safety_positive", False), ("functional_positive", True),
])
def test_process_terminal_never_borrows_a_functional_result(prepared_stage, capsys, field, value):
    args, _, _, _ = prepared_stage
    terminal = stage.run(**args)
    prefix = capsys.readouterr().out.encode()
    terminal[field] = value
    with pytest.raises(contract.QualificationError):
        receiver.terminal(
            prefix + json.dumps(terminal).encode(), boundary=contract.Boundary(10, UTC),
            scope=7, native_exit=0, captured_at=20, mode=contract.PROCESS_MODE,
        )


def test_candidate_wait_capture_exit_and_handle_use_original_bound(tmp_path, monkeypatch):
    events = []
    boundary = contract.Boundary(10, UTC)
    monkeypatch.setattr(stage, "time", SimpleNamespace(monotonic=lambda: 20))
    class Reader:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

        def join(self, timeout):
            assert timeout >= 0

        def is_alive(self):
            return False

    monkeypatch.setattr(contract, "threading", SimpleNamespace(Thread=Reader))

    def process(argv, **kwargs):
        assert kwargs["creationflags"] == 0x08000000 and kwargs["stdin"] == stage.subprocess.DEVNULL
        assert kwargs["stdout"] == kwargs["stderr"] == stage.subprocess.PIPE
        def wait(*, timeout):
            events.append(("wait", timeout))
            return 0
        return SimpleNamespace(
            stdout=io.BytesIO(b"out"), stderr=io.BytesIO(b"err"), wait=wait, poll=lambda: 0,
            _handle=SimpleNamespace(Close=lambda: events.append(("closed", True))),
        )

    monkeypatch.setattr(stage.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(stage.subprocess, "Popen", process)
    assert stage.candidate(["source-qualified-candidate"], tmp_path, boundary) == 0
    assert events == [("wait", 155), ("closed", True)]
    assert (tmp_path / "candidate-stdout.bin").read_bytes() == b"out"
    assert (tmp_path / "candidate-stderr.bin").read_bytes() == b"err"
