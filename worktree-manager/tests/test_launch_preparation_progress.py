"""Slow foreground preparation remains visible and preserves the JSON seam."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest

from worktree_manager import engine_client as ec
from worktree_manager import engine_process


PLAN = {"action": "exec", "work_dir": "/checkout", "cmd": ["copilot"], "env": {}}


@pytest.mark.parametrize("selection", [{"new": True}, {"worktree_id": "wt-a"}, {"base": True}])
def test_foreground_plan_waits_without_aggregate_deadline(monkeypatch, selection):
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["engine"])
    observed = []

    def run(argv, **kwargs):
        observed.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps(PLAN), "")

    monkeypatch.setattr(engine_process, "run", run)
    assert ec.resolve_launch_plan("demo", **selection).is_exec
    assert observed[0]["timeout"] is None
    assert observed[0]["stdout"] is subprocess.PIPE
    assert isinstance(observed[0]["stderr"], int)


def test_progress_arrives_before_slow_real_engine_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    result = []
    failures = []

    class Progress:
        def write(self, text):
            if "projection starting" in text:
                started.set()
            return len(text)

        def flush(self):
            pass

    gate = tmp_path / "continue"
    script = (
        "import json,sys,time\nfrom pathlib import Path\n"
        "print('projection starting',file=sys.stderr,flush=True)\n"
        "deadline=time.monotonic()+15\n"
        f"while not Path({str(gate)!r}).exists() and time.monotonic()<deadline:\n"
        "    time.sleep(0.05)\n"
        f"print(json.dumps({PLAN!r}))\n"
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", script])
    monkeypatch.setattr(sys, "stderr", Progress())

    def resolve():
        try:
            result.append(ec.resolve_launch_plan("demo", new=True))
        except BaseException as exc:
            failures.append(exc)
        finally:
            release.set()

    thread = threading.Thread(target=resolve)
    thread.start()
    try:
        assert started.wait(10), "preparation progress was hidden until completion"
        assert not release.is_set()
    finally:
        gate.write_text("continue", encoding="utf-8")
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert failures == []
    assert result[0].is_exec


def test_streamed_error_retains_version_skew_detail(monkeypatch, capsys):
    script = (
        "import sys;"
        "print('unrecognized arguments: --stage-launch-seed',file=sys.stderr,flush=True);"
        "sys.exit(2)"
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", script])
    with pytest.raises(ec.EngineFeatureUnavailable, match="predates staged"):
        ec.resolve_launch_plan("demo", new=True, seed="task")
    assert "unrecognized arguments" in capsys.readouterr().err
