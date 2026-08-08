"""Tests for the drive-the-worktree-to-resolution planner and the ``resolve`` CLI."""

from __future__ import annotations

import json

import pytest

from agent_dispatch import resolution
from agent_dispatch.__main__ import _cmd_abandon, _cmd_resolve, build_parser


def _args(argv):
    return build_parser().parse_args(argv)


# -- base_ref ----------------------------------------------------------------


def test_base_ref_defaults_to_tracked_upstream():
    assert resolution.base_ref(None) == "@{upstream}"
    assert resolution.base_ref("") == "@{upstream}"
    assert resolution.base_ref("   ") == "@{upstream}"


def test_base_ref_qualifies_a_bare_branch_against_origin():
    assert resolution.base_ref("main") == "origin/main"
    assert resolution.base_ref("release/1.x") == "origin/release/1.x"


def test_base_ref_passes_qualified_refs_through():
    assert resolution.base_ref("origin/main") == "origin/main"
    assert resolution.base_ref("refs/heads/main") == "refs/heads/main"
    assert resolution.base_ref("@{upstream}") == "@{upstream}"


# -- plan_resolution ---------------------------------------------------------


def test_landed_plan_only_verifies_clean():
    plan = resolution.plan_resolution("landed")
    assert plan.outcome == "landed"
    assert not plan.has_destructive_steps
    kinds = [s.kind for s in plan.steps]
    assert kinds == ["verify-clean"]
    assert plan.steps[0].argv == ("git", "status", "--porcelain")


def test_abandoned_plan_unwinds_then_reconciles():
    plan = resolution.plan_resolution("abandoned", base="main", source_ref="o/n#42")
    kinds = [s.kind for s in plan.steps]
    assert kinds == ["unwind-to-base", "drop-untracked", "reconcile-source"]
    assert plan.has_destructive_steps
    unwind = plan.steps[0]
    assert unwind.destructive
    assert unwind.argv == ("git", "reset", "--hard", "origin/main")
    # reconcile-source is advisory (agent-dispatch coordinates, does not perform)
    reconcile = plan.steps[-1]
    assert reconcile.advisory
    assert reconcile.argv is None
    assert "o/n#42" in reconcile.description


def test_abandoned_plan_defaults_base_to_upstream():
    plan = resolution.plan_resolution("abandoned")
    assert plan.base_ref == "@{upstream}"
    assert plan.steps[0].argv == ("git", "reset", "--hard", "@{upstream}")


def test_abandoned_reason_rides_into_reconcile():
    plan = resolution.plan_resolution("abandoned", reason="upstream withdrew the change")
    assert "upstream withdrew the change" in plan.steps[-1].description
    assert plan.reason == "upstream withdrew the change"


def test_unknown_outcome_raises():
    with pytest.raises(resolution.ResolutionError):
        resolution.plan_resolution("sideways")


def test_plan_to_dict_round_trips_shape():
    d = resolution.plan_resolution("abandoned", base="main").to_dict()
    assert d["outcome"] == "abandoned"
    assert d["base_ref"] == "origin/main"
    assert d["has_destructive_steps"] is True
    assert d["steps"][0]["destructive"] is True
    assert d["steps"][-1]["advisory"] is True


# -- CLI: parsing ------------------------------------------------------------


def test_cli_parses_resolve():
    a = _args(["resolve", "--outcome", "abandoned"])
    assert a.func is _cmd_resolve
    assert a.outcome == "abandoned"
    assert a.execute is False


def test_cli_resolve_requires_valid_outcome():
    with pytest.raises(SystemExit):
        _args(["resolve", "--outcome", "bogus"])


# -- CLI: plan-only (default) ------------------------------------------------


def test_resolve_plan_only_runs_nothing(capsys, monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("plan-only must not shell out")

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", _boom)
    rc = _cmd_resolve(_args(["resolve", "--outcome", "abandoned", "--base", "main"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is False
    assert out["base_ref"] == "origin/main"
    assert "--execute" in out["note"]


# -- CLI: execute ------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_resolve_execute_runs_steps_and_reports(capsys, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _FakeProc(returncode=0)

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", fake_run)
    rc = _cmd_resolve(
        _args(["resolve", "--outcome", "abandoned", "--base", "main", "--execute"])
    )
    assert rc == 0
    # both destructive git steps ran; the advisory step did not shell out
    assert ["git", "reset", "--hard", "origin/main"] in calls
    assert ["git", "clean", "-fd"] in calls
    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is True
    assert any(r.get("advisory") for r in out["results"])
    assert out["instructions"]  # the reconcile-source instruction is surfaced


def test_resolve_execute_stops_on_failed_unwind(capsys, monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:3] == ["git", "reset", "--hard"]:
            return _FakeProc(returncode=1, stderr="reset failed")
        raise AssertionError("must stop after the failed destructive unwind")

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", fake_run)
    rc = _cmd_resolve(_args(["resolve", "--outcome", "abandoned", "--execute"]))
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["results"][0]["ok"] is False


# -- abandon --resolve surfaces the plan -------------------------------------


class _FakeClient:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def abandon(self, task_id, **kwargs):
        self._sink.update(task_id=task_id, **kwargs)
        return {"id": task_id, "status": "abandoned"}


def test_abandon_with_resolve_surfaces_plan(capsys, monkeypatch):
    sink: dict = {}
    monkeypatch.setattr(
        "agent_dispatch.__main__._client", lambda args, **k: _FakeClient(sink)
    )
    rc = _cmd_abandon(
        _args(["abandon", "task-1", "--permit", "--reason", "no longer needed", "--resolve"])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["abandon"]["status"] == "abandoned"
    assert out["resolution"]["outcome"] == "abandoned"
    assert out["resolution"]["has_destructive_steps"] is True


def test_abandon_without_resolve_is_unchanged(capsys, monkeypatch):
    sink: dict = {}
    monkeypatch.setattr(
        "agent_dispatch.__main__._client", lambda args, **k: _FakeClient(sink)
    )
    rc = _cmd_abandon(_args(["abandon", "task-1", "--permit"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "abandoned"
    assert "resolution" not in out
