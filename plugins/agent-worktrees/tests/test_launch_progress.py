"""Preparation progress is truthful, stderr-only, and stops with its stage."""

import sys
import threading

import pytest

from agent_worktrees import launch_progress

pytestmark = pytest.mark.guard


def test_heartbeat_is_visible_before_stage_completion(monkeypatch):
    emitted = threading.Event()
    output = []

    class Progress:
        def write(self, text):
            output.append(text)
            if "still working" in text:
                emitted.set()
            return len(text)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stderr", Progress())
    monkeypatch.setattr(launch_progress, "HEARTBEAT_SECONDS", 0.01)
    with launch_progress.step("Projection preparation"):
        assert emitted.wait(2)
        assert "finished" not in "".join(output)
    text = "".join(output)
    assert "starting" in text and "still working" in text and "finished" in text


def test_failure_is_reported_without_swallowing_the_error(capsys):
    with pytest.raises(ValueError, match="invalid declaration"):
        with launch_progress.step("Projection preparation"):
            raise ValueError("invalid declaration")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "starting" in captured.err and "failed" in captured.err
    assert "finished" not in captured.err
