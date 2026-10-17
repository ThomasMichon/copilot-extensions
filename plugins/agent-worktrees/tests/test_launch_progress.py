"""Preparation progress is truthful, stderr-only, and stops with its stage."""

import sys
import threading
import json

import pytest

from agent_worktrees import launch_progress

pytestmark = pytest.mark.guard


def test_heartbeat_is_visible_before_stage_completion(monkeypatch):
    emitted = threading.Event()
    output = []

    class Progress:
        def write(self, text):
            output.append(text)
            if " running " in text:
                emitted.set()
            return len(text)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stderr", Progress())
    monkeypatch.setattr(launch_progress, "HEARTBEAT_SECONDS", 0.01)
    with launch_progress.step("Projection preparation"):
        assert emitted.wait(2)
        assert " done " not in "".join(output)
    text = "".join(output)
    assert "starting" in text and " running " in text and " done " in text
    assert "[elapsed " in text and "[duration " in text


def test_failure_is_reported_without_swallowing_the_error(capsys):
    with pytest.raises(ValueError, match="invalid declaration"):
        with launch_progress.step("Projection preparation"):
            raise ValueError("invalid declaration")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "starting" in captured.err and "failed" in captured.err
    assert "finished" not in captured.err


def test_quiet_stage_persists_timing_without_sensitive_error_text(monkeypatch, tmp_path, capsys):
    path = tmp_path / "activity.jsonl"
    monkeypatch.setattr(launch_progress.activity, "log_path", lambda: path)
    with pytest.raises(ValueError):
        with launch_progress.step("Preparing launch prompt", enabled=False, worktree_id="wt-a"):
            raise ValueError("private prompt and credentials must not enter the trace")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["state"] for event in events] == ["started", "failed"]
    assert events[0]["step_id"] == events[1]["step_id"]
    assert all(event["worktree_id"] == "wt-a" for event in events)
    assert all(event["elapsed_ms"] >= 0 for event in events)
    assert events[1]["error_type"] == "ValueError"
    assert "private prompt" not in path.read_text(encoding="utf-8")
    assert capsys.readouterr() == ("", "")


def test_durable_heartbeat_precedes_completion(monkeypatch):
    events = []
    waiting = threading.Event()

    def log_event(event, **fields):
        events.append(fields)
        if fields["state"] == "waiting":
            waiting.set()

    monkeypatch.setattr(launch_progress.activity, "log_event", log_event)
    monkeypatch.setattr(launch_progress, "HEARTBEAT_SECONDS", 0.01)
    with launch_progress.step("Verifying worktree liveness"):
        assert waiting.wait(2)
        assert events[0]["state"] == "started"
        assert all(event["state"] != "finished" for event in events)
    assert events[-1]["state"] == "finished"
    assert len({event["step_id"] for event in events}) == 1


def test_legacy_adapter_keeps_start_elapsed_and_completion(monkeypatch):
    emitted = threading.Event()
    text = []

    class Sink:
        def write(self, value):
            text.append(value)
            if "still working" in value:
                emitted.set()
            return len(value)

        def flush(self):
            pass

    monkeypatch.setattr(launch_progress.output, "HumanOperation", None)
    monkeypatch.setattr(launch_progress.activity, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(launch_progress, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(sys, "stderr", Sink())
    with launch_progress.step("Fixture preparation"):
        assert emitted.wait(2)
    captured = "".join(text)
    assert "[launch] Fixture preparation: starting" in captured
    assert "still working (" in captured and "s elapsed)" in captured
    assert "finished (" in captured


def test_failed_output_start_keeps_activity_and_restores_context(monkeypatch):
    from update_telemetry.human import current_operation

    events = []

    class Broken:
        def write(self, value):
            raise OSError("closed diagnostic stream")

        def flush(self):
            pass

    monkeypatch.setattr(launch_progress.activity, "log_event",
                        lambda event, **fields: events.append(fields))
    monkeypatch.setattr(sys, "stderr", Broken())
    with pytest.raises(OSError, match="closed diagnostic stream"):
        with launch_progress.step("Fixture preparation"):
            pytest.fail("stage body must not run after output startup fails")
    assert events[0]["state"] == "started"
    assert current_operation() is None


def test_failed_heartbeat_start_records_failure_and_restores_context(monkeypatch):
    from update_telemetry.human import current_operation

    events = []

    def refuse(thread):
        raise RuntimeError("heartbeat startup refused")

    monkeypatch.setattr(launch_progress.activity, "log_event",
                        lambda event, **fields: events.append(fields))
    monkeypatch.setattr(threading.Thread, "start", refuse)
    with pytest.raises(RuntimeError, match="heartbeat startup refused"):
        with launch_progress.step("Fixture preparation"):
            pytest.fail("stage body must not run after heartbeat startup fails")
    assert [event["state"] for event in events] == ["started", "failed"]
    assert events[-1]["error_type"] == "RuntimeError"
    assert current_operation() is None
