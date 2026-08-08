"""Tests for the loop driver -- the executable work/suspend/resolve rhythm."""

from __future__ import annotations

import json

import pytest

from agent_dispatch import recipes
from agent_dispatch.recipes import driver
from agent_dispatch.__main__ import _cmd_recipes_drive, build_parser


def _args(argv):
    return build_parser().parse_args(argv)


def _reviewer():
    return recipes.get_recipe("reviewer")


# -- resolution_outcome ------------------------------------------------------


@pytest.mark.parametrize("sig", ["merged", "landed", "goal-met", "resolved:landed", "DONE"])
def test_landed_signals(sig):
    assert driver.resolution_outcome(sig) == "landed"


@pytest.mark.parametrize("sig", ["abandoned", "closed", "goal-abandoned", "resolved:abandoned"])
def test_abandoned_signals(sig):
    assert driver.resolution_outcome(sig) == "abandoned"


@pytest.mark.parametrize("sig", ["start", "change-updated", "idle", "whatever"])
def test_non_terminal_signals(sig):
    assert driver.resolution_outcome(sig) is None


# -- decide ------------------------------------------------------------------


def test_start_signals_work():
    a = driver.decide(_reviewer(), "start")
    assert a.kind == driver.WORK
    assert a.wait_for == _reviewer().suspend_on


def test_suspend_on_event_signals_work():
    # reviewer suspends on 'change-updated' -> a fresh update means: do a pass
    a = driver.decide(_reviewer(), "change-updated")
    assert a.kind == driver.WORK


def test_work_done_signals_suspend_until_next_event():
    a = driver.decide(_reviewer(), "work-done")
    assert a.kind == driver.SUSPEND
    assert set(a.wait_for) == set(_reviewer().suspend_on)


def test_idle_and_unknown_signal_suspends_conservatively():
    assert driver.decide(_reviewer(), "idle").kind == driver.SUSPEND
    assert driver.decide(_reviewer(), "nonsense").kind == driver.SUSPEND


def test_merged_signals_resolve_landed():
    a = driver.decide(_reviewer(), "merged")
    assert a.kind == driver.RESOLVE
    assert a.outcome == "landed"


def test_abandoned_signals_resolve_abandoned():
    a = driver.decide(_reviewer(), "closed")
    assert a.kind == driver.RESOLVE
    assert a.outcome == "abandoned"


def test_goal_driven_recipe_uses_its_own_suspend_on():
    goal = recipes.get_recipe("goal-driven")
    a = driver.decide(goal, "review-posted")  # a goal-driven suspend_on event
    assert a.kind == driver.WORK
    assert driver.decide(goal, "goal-met").outcome == "landed"


# -- CLI: parsing + plan-only ------------------------------------------------


def test_cli_parses_drive():
    a = _args(["recipes", "drive", "reviewer", "--signal", "start"])
    assert a.func is _cmd_recipes_drive


def test_drive_plan_only_emits_action(capsys):
    rc = _cmd_recipes_drive(_args(["recipes", "drive", "reviewer", "--signal", "merged"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"]["kind"] == "resolve"
    assert out["action"]["outcome"] == "landed"


def test_drive_unknown_recipe_errors(capsys):
    rc = _cmd_recipes_drive(_args(["recipes", "drive", "nope", "--signal", "start"]))
    assert rc == 2
    assert "unknown recipe" in capsys.readouterr().err


# -- CLI: execute legs -------------------------------------------------------


def test_drive_execute_suspend_spawns_waiter(capsys, monkeypatch):
    captured = {}

    def fake_spawn(spec):
        captured["spec"] = spec
        return {"pid": 555}

    monkeypatch.setattr("agent_dispatch.__main__._spawn_detached_waiter", fake_spawn)
    rc = _cmd_recipes_drive(
        _args([
            "recipes", "drive", "reviewer", "--signal", "work-done",
            "--resume", "m/wt-1", "--execute", "--", "agent-worktrees", "pr-watch", "42",
        ])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"]["kind"] == "suspend"
    assert out["executed"] is True
    assert out["waiter"]["pid"] == 555
    assert captured["spec"].command == ("agent-worktrees", "pr-watch", "42")
    assert captured["spec"].resume_worktree == "m/wt-1"


def test_drive_execute_suspend_needs_resume_and_command(capsys):
    rc = _cmd_recipes_drive(
        _args(["recipes", "drive", "reviewer", "--signal", "idle", "--execute"])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is False
    assert "needs --resume" in out["note"]


def test_drive_execute_resolve_runs_unwind(capsys, monkeypatch):
    calls = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **k):
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", fake_run)
    rc = _cmd_recipes_drive(
        _args([
            "recipes", "drive", "reviewer", "--signal", "abandoned",
            "--base", "main", "--source", "o/n#42", "--execute",
        ])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"]["kind"] == "resolve"
    assert out["resolution"]["outcome"] == "abandoned"
    assert ["git", "reset", "--hard", "origin/main"] in calls


def test_drive_execute_work_reports_agent_owns_it(capsys):
    rc = _cmd_recipes_drive(
        _args(["recipes", "drive", "reviewer", "--signal", "start", "--execute"])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"]["kind"] == "work"
    assert out["executed"] is False
