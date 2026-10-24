"""All journaled resolver routes require an exact, non-downgradable receipt."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from worktree_manager import engine_client as engine, relocated_launch

ID = "a" * 32


def _plan(action="exec"):
    return {
        "action": action, "worktree_id": "wt-a",
        "launch_attempt_id": ID,
        "launch_attempt_receipt": {
            "version": 1, "operation_id": "b" * 32,
            "phase": "forwarded" if action == "remote" else "plan-ready", "worktree_id": "wt-a",
        },
    }


@pytest.mark.parametrize("mode", ["new", "resume", "base", "bare-resume"])
@pytest.mark.parametrize("remote", [False, True])
def test_all_attempts_send_the_capability_and_require_a_bound_reply(monkeypatch, mode, remote):
    calls = []
    def run(project, args, **kw):
        calls.append(args)
        assert "--require-launch-attempt" in args
        assert args[args.index("--launch-attempt-id") + 1] == ID
        return _plan("remote" if remote else "exec")
    monkeypatch.setattr(engine, "run_json", run)
    result = engine.resolve_launch_plan(
        "example", new=mode == "new", base=mode == "base",
        worktree_id="wt-a" if mode in {"resume", "bare-resume"} else None,
        bare_resume=mode == "bare-resume", target_machine="target" if remote else None,
        launch_attempt_id=ID,
    )
    assert result.raw["launch_attempt_id"] == ID and len(calls) == 1


@pytest.mark.parametrize("damage", [
    "attempt", "missing", "version", "operation", "worktree", "phase", "extra", "wrong-owner",
])
def test_inexact_receipt_never_becomes_an_executable_plan(monkeypatch, damage):
    plan = _plan()
    receipt = plan["launch_attempt_receipt"]
    if damage == "attempt":
        plan["launch_attempt_id"] = "c" * 32
    elif damage == "missing":
        del plan["launch_attempt_receipt"]
    elif damage == "version":
        receipt["version"] = True
    elif damage == "operation":
        receipt["operation_id"] = "not-an-id"
    elif damage == "worktree":
        receipt["worktree_id"] = "wrong"
    elif damage == "phase":
        receipt["phase"] = []
    elif damage == "wrong-owner":
        receipt["phase"] = "forwarded"
    else:
        receipt["text"] = "private fixture"
    calls = []
    monkeypatch.setattr(engine, "run_json", lambda *a, **kw: calls.append(True) or plan)
    with pytest.raises(engine.EngineFeatureUnavailable, match="saved attempt"):
        engine.resolve_launch_plan("example", new=True, launch_attempt_id=ID)
    assert calls == [True]


@pytest.mark.parametrize("flag", ["--launch-attempt-id", "--require-launch-attempt", "--base"])
def test_journaled_base_never_retries_a_weaker_plan(monkeypatch, flag):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        raise engine.EngineError("unrecognized arguments: " + flag)
    monkeypatch.setattr(engine, "run_json", run)
    with pytest.raises(engine.EngineError):
        engine.resolve_launch_plan("example", base=True, launch_attempt_id=ID)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["new", "resume", "base", "bare-resume"])
@pytest.mark.parametrize("new_window", [False, True])
def test_local_second_hop_keeps_attempt_and_never_reissues_new(
    monkeypatch, tmp_path, mode, new_window,
):
    from worktree_manager import new_window_spawn

    monkeypatch.setattr(relocated_launch, "_core", lambda: SimpleNamespace(_is_windows=lambda: True))
    commands = []
    monkeypatch.setattr(
        relocated_launch.subprocess, "Popen",
        lambda argv: commands.append(argv) or SimpleNamespace(wait=lambda: 0),
    )
    monkeypatch.setattr(new_window_spawn, "spawn_detached_new_window",
                        lambda argv, **kw: commands.append(argv))
    req = SimpleNamespace(
        project="example", mode=mode, worktree_id="wt-a", attempt_id=ID,
        no_mux=False, new_window=new_window,
    )
    plan = engine.launch_plan_from_dict(_plan())
    assert relocated_launch._run_relocated_mux_launch(req, plan, tmp_path / "launch-session.ps1") == 0
    command = commands[0]
    assert "--require-launch-attempt" in command
    assert command[command.index("--launch-attempt-id") + 1] == ID
    assert "--new" not in command and "--seed" not in command
