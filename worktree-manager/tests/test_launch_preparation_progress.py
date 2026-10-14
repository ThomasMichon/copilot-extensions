"""Slow foreground preparation remains visible and preserves the JSON seam."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize("route", ["shell", "shell-resume", "direct", "ahp", "persisted-ahp", "remote"])
def test_manager_defers_only_shell_delegated_preparation(monkeypatch, route):
    from worktree_manager import __main__ as entrypoint
    from worktree_manager import ahp_provider, launcher

    events = []
    plan = dict(PLAN, worktree_id="wt-a")
    if route == "remote":
        plan["action"] = "remote"
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["engine"])
    monkeypatch.setattr(
        entrypoint, "_relocated_launch_script",
        lambda: None if route == "direct" else Path("launch-session.sh"),
    )

    def execution_leg(project, worktree_id):
        events.append("inspect-leg")
        return {"execution_leg": {"state": "active", "provider": "ahp"} if route == "persisted-ahp" else None}

    monkeypatch.setattr(ec, "execution_leg_get", execution_leg)

    def run(argv, **kwargs):
        deferred = kwargs["env"].get("AGENT_WORKTREES_DEFER_PROJECTIONS") == "1"
        assert "--defer-projections" not in argv
        events.append("resolve-deferred" if deferred else "resolve-prepared")
        return subprocess.CompletedProcess(argv, 0, json.dumps(plan), "")

    monkeypatch.setattr(engine_process, "run", run)
    monkeypatch.setattr(
        entrypoint, "_run_relocated_mux_launch",
        lambda *a: events.extend(["maintenance", "final-publication", "shell-handoff"]) or 0,
    )
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: events.append("direct-handoff") or 0)
    monkeypatch.setattr(ahp_provider, "ensure_session", lambda *a: events.append("ahp-ensure"))
    monkeypatch.setattr(ahp_provider, "attach_plan", lambda plan, attachment: plan)
    resumed = route in {"shell-resume", "persisted-ahp"}
    request = SimpleNamespace(
        project="demo", worktree_id="wt-a" if resumed else None,
        mode="resume" if resumed else "new", ahp=route == "ahp",
        machine="remote-host" if route == "remote" else None,
    )
    monkeypatch.delenv("AGENT_WORKTREES_DEFER_PROJECTIONS", raising=False)
    assert entrypoint._run_launch(request) == 0
    if route in {"shell", "shell-resume"}:
        assert events == (
            (["inspect-leg"] if resumed else [])
            + ["resolve-deferred", "maintenance", "final-publication", "shell-handoff"]
        )
    else:
        assert events.count("resolve-prepared") == 1
        assert "resolve-deferred" not in events
        assert "final-publication" not in events
        assert events[-1] == "direct-handoff"
        assert events.count("inspect-leg") == (1 if resumed else 0)
    assert "AGENT_WORKTREES_DEFER_PROJECTIONS" not in os.environ


@pytest.mark.parametrize("deferred", [False, True])
def test_projection_deferral_reaches_only_the_selected_engine_call(monkeypatch, deferred):
    script = (
        "import json,os;"
        f"plan={PLAN!r};"
        "plan['deferred']=os.environ.get('AGENT_WORKTREES_DEFER_PROJECTIONS');"
        "print(json.dumps(plan))"
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", script])
    monkeypatch.delenv("AGENT_WORKTREES_DEFER_PROJECTIONS", raising=False)
    plan = ec.resolve_launch_plan("demo", new=True, defer_projections=deferred)
    assert plan.raw["deferred"] == ("1" if deferred else None)
    assert "AGENT_WORKTREES_DEFER_PROJECTIONS" not in os.environ


def test_projection_deferral_survives_bare_resume_compatibility_retry(monkeypatch):
    calls = []

    def old_runtime(project, args, **kwargs):
        calls.append(kwargs)
        if "--bare-resume" in args:
            raise ec.EngineError("unrecognized arguments: --bare-resume")
        return PLAN

    monkeypatch.setattr(ec, "run_json", old_runtime)
    assert ec.resolve_launch_plan(
        "demo", worktree_id="wt-a", bare_resume=True, defer_projections=True,
    ).is_exec
    assert len(calls) == 2
    assert all(call["environment"] == {"AGENT_WORKTREES_DEFER_PROJECTIONS": "1"} for call in calls)


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
