"""Actor-free functional qualification cannot activate a desktop control."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.promotion_unit_tests.test_sdk_desktop_observer import runner, source_request


@pytest.fixture
def functional_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple:
    request = source_request(tmp_path)
    request.pop("visible_controls")
    events: list[str] = []
    fault: dict[str, str] = {"at": ""}

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("functional qualification reached desktop observation or activation")

    class Child:
        def __init__(
            self, api: Any, argv: list[str], child_request: dict, directory: Path,
            name: str, evidence: dict,
        ) -> None:
            assert argv[-1] == "worker"
            self.name, self.evidence = name, evidence
            self.process = SimpleNamespace(returncode=1 if fault["at"] == name + "-exit" else 0)
            evidence["launched"] = True
            events.append(name + "-launch")
            if fault["at"] == name + "-launch":
                evidence["launched"] = False
                raise runner.ObservationBlocked("literal launch failure")

        def admit(self, deadline: float, observer: Any = None) -> None:
            assert observer is None
            events.append(self.name + "-admit")
            if fault["at"] == self.name + "-admit":
                raise runner.ObservationBlocked("literal admission failure")

        def send(self, command: str) -> None:
            assert command == "run"
            events.append(self.name + "-run")

        def settled(self, deadline: float, observer: Any = None) -> None:
            assert observer is None
            events.append(self.name + "-settled")
            if fault["at"] == self.name + "-settled":
                raise runner.ObservationBlocked("literal settlement failure")

        def close(self) -> None:
            events.append(self.name + "-close")
            self.evidence.update(
                exit=self.process.returncode, cleanup_confirmed=True, cleanup_errors=[],
                stdout="retained stdout", stderr="retained stderr", readers_alive=[False, False],
            )
            if fault["at"] == self.name + "-cleanup" or fault.get("cleanup") == self.name:
                self.evidence["cleanup_confirmed"] = False
                raise runner.ObservationBlocked("literal cleanup failure")

    monkeypatch.setattr(runner, "Child", Child)
    monkeypatch.setattr(runner, "new_api", lambda: SimpleNamespace(identity=lambda: {"pid": 7, "birth": 8}))
    monkeypatch.setattr(runner, "DesktopObserver", forbidden)
    monkeypatch.setattr(runner, "activate_foreground", forbidden)
    monkeypatch.setattr(runner, "canary", forbidden)
    monkeypatch.setattr(runner, "require_sdk_sources", lambda *a, **k: None)
    monkeypatch.setattr(runner, "verify_sources", lambda values: list(values))
    monkeypatch.setattr(runner, "verify_native_junit", lambda *a: {"case_count": 44})
    return request, events, fault


def test_functional_native_packet_never_claims_desktop_qualification(functional_fixture: tuple) -> None:
    request, events, _ = functional_fixture
    assert runner.functional(request) == 0
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert report["kind"] == "sdk-functional"
    assert report["passed"] is True and report["desktop_qualified"] is False
    assert report["visible_controls_attempted"] == 0
    assert report["source_before"] == report["source_after"] == request["sources"]
    assert report["native_junit"] == {"case_count": 44}
    assert "quiet" not in report and "canary" not in report and "calibration" not in report
    assert not (Path(request["output"]) / "visibility-result.json").exists()
    assert len(report["workers"]) == 4
    assert events.count("worker-04-close") == 1


@pytest.mark.parametrize("stage", ["launch", "admit", "settled", "exit", "cleanup"])
def test_functional_failure_preserves_evidence_and_stops_before_next_packet(
    functional_fixture: tuple, stage: str,
) -> None:
    request, events, fault = functional_fixture
    fault["at"] = "worker-02-" + stage
    with pytest.raises(runner.ObservationBlocked):
        runner.functional(request)
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert report["passed"] is False and report["desktop_qualified"] is False
    assert report["visible_controls_attempted"] == 0 and report["error"]
    assert len(report["workers"]) == 2
    assert "worker-03-launch" not in events
    if stage != "launch":
        assert events.count("worker-02-close") == 1
        assert report["workers"][1]["stderr"] == "retained stderr"


def test_functional_mode_refuses_visible_control_fields_before_native_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source_request(tmp_path)
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before request refusal"))
    with pytest.raises(runner.ObservationBlocked, match="fields"):
        runner.validate_request("functional", request)
    request.pop("visible_controls")
    assert runner.validate_request("functional", request) is request


def test_functional_mode_hashes_source_before_native_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source_request(tmp_path)
    request.pop("visible_controls")
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before source hashing"))
    with pytest.raises(runner.ObservationBlocked, match="qualification source changed"):
        runner.functional(request)
    assert not Path(request["output"]).exists()


def test_functional_primary_error_survives_secondary_cleanup(functional_fixture: tuple) -> None:
    request, _, fault = functional_fixture
    fault.update(at="worker-02-settled", cleanup="worker-02")
    with pytest.raises(runner.ObservationBlocked, match="literal settlement failure"):
        runner.functional(request)
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert "literal settlement failure" in report["error"]
    assert "literal cleanup failure" in report["workers"][1]["cleanup_error"]
    assert report["workers"][1]["cleanup_confirmed"] is False
    assert report["passed"] is False


def test_functional_primary_error_survives_expired_final_clock(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _, _ = functional_fixture
    request["deadline"] = 110
    clock = {"now": 100}
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def fail(*args: Any) -> None:
        clock["now"] = 111
        raise runner.ObservationBlocked("original worker failure")

    monkeypatch.setattr(runner, "run_workers", fail)
    with pytest.raises(runner.ObservationBlocked, match="original worker failure"):
        runner.functional(request)
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert "original worker failure" in report["error"]
    assert report["deadline_error"] == "immutable functional deadline exceeded"
    assert report["passed"] is False and report["desktop_qualified"] is False


def test_functional_request_expired_by_hashing_cannot_reach_native_binding(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, events, _ = functional_fixture
    clock = {"now": 100}
    request["deadline"] = 110
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def hashing(values: list[dict]) -> list[dict]:
        clock["now"] = 111
        return values

    monkeypatch.setattr(runner, "verify_sources", hashing)
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding after expired hashing"))
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.functional(request)
    assert events == []
    assert not Path(request["output"]).exists()


def test_functional_request_expired_by_parent_identity_cannot_launch(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, events, _ = functional_fixture
    clock = {"now": 100}
    request["deadline"] = 110
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def identity() -> dict[str, int]:
        clock["now"] = 111
        return {"pid": 7, "birth": 8}

    monkeypatch.setattr(runner, "new_api", lambda: SimpleNamespace(identity=identity))
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.functional(request)
    assert events == []


def test_functional_request_expired_between_packets_cannot_launch_next(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, events, _ = functional_fixture
    clock = {"now": 100}
    request["deadline"] = 110
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    original_close = runner.Child.close

    def close(child: Any) -> None:
        original_close(child)
        if child.name == "worker-01":
            clock["now"] = 111

    monkeypatch.setattr(runner.Child, "close", close)
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.functional(request)
    assert "worker-01-close" in events and "worker-02-launch" not in events


def test_owned_child_request_expired_during_request_write_cannot_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100}
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def write(*args: Any) -> None:
        clock["now"] = 111

    monkeypatch.setattr(runner, "write_new", write)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("expired child spawn"))
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    evidence: dict[str, Any] = {}
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.Child(SimpleNamespace(), ["python"], {"deadline": 110}, tmp_path, "late", evidence=evidence)
    assert evidence["launched"] is False
