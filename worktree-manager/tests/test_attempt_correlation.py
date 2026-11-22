"""All journaled resolver routes require an exact, non-downgradable receipt."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from worktree_manager import engine_client as engine, relocated_launch

ID = "a" * 32


@pytest.fixture
def admitted_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> Callable[[Callable[..., dict[str, Any]]], None]:
    def install(resolve: Callable[..., dict[str, Any]]) -> None:
        def request(project: str | None, args: list[str], **kwargs: Any) -> dict[str, Any]:
            if args[:2] == ["agent-policy", "check"]:
                return {
                    "version": 1, "repository": str(tmp_path), "expose_agent": True,
                    "sources": [], "denied_by": [], "allowed": True,
                }
            return resolve(project, args, **kwargs)
        monkeypatch.setattr(engine, "run_json", request)
    return install


def _plan(action: str = "exec", worktree_id: str | None = "wt-a") -> dict[str, Any]:
    return {
        "action": action, "worktree_id": worktree_id,
        **({
            "remote_terminal_handoff_version": 1,
            "ssh_alias": "target", "remote_command": "example --new",
        } if action == "remote" else {}),
        "launch_attempt_id": ID,
        "launch_attempt_receipt": {
            "version": 1, "operation_id": "b" * 32,
            "phase": "forwarded" if action == "remote" else "plan-ready",
            **({"worktree_id": worktree_id} if worktree_id else {}),
        },
    }


@pytest.mark.parametrize("mode", ["new", "resume", "base", "bare-resume"])
@pytest.mark.parametrize("remote", [False, True])
def test_all_attempts_send_the_capability_and_require_a_bound_reply(
    admitted_engine: Callable[[Callable[..., dict[str, Any]]], None], mode: str, remote: bool,
) -> None:
    calls: list[list[str]] = []
    def run(project: str | None, args: list[str], **kw: Any) -> dict[str, Any]:
        calls.append(args)
        assert "--require-launch-attempt" in args
        assert args[args.index("--launch-attempt-id") + 1] == ID
        return _plan(
            "remote" if remote else "exec",
            None if remote and mode in {"new", "base"} else "wt-a",
        )
    admitted_engine(run)
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
def test_inexact_receipt_never_becomes_an_executable_plan(
    admitted_engine: Callable[[Callable[..., dict[str, Any]]], None], damage: str,
) -> None:
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
    calls: list[bool] = []
    def run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(True)
        return plan
    admitted_engine(run)
    with pytest.raises(engine.EngineFeatureUnavailable, match="saved attempt"):
        engine.resolve_launch_plan("example", new=True, launch_attempt_id=ID)
    assert calls == [True]


@pytest.mark.parametrize("flag", ["--launch-attempt-id", "--require-launch-attempt", "--base"])
def test_journaled_base_never_retries_a_weaker_plan(
    admitted_engine: Callable[[Callable[..., dict[str, Any]]], None], flag: str,
) -> None:
    calls: list[tuple[Any, ...]] = []
    def run(*args: Any, **kwargs: Any) -> NoReturn:
        calls.append(args)
        raise engine.EngineError("unrecognized arguments: " + flag)
    admitted_engine(run)
    with pytest.raises(engine.EngineError):
        engine.resolve_launch_plan("example", base=True, launch_attempt_id=ID)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["new", "resume", "base", "bare-resume"])
@pytest.mark.parametrize("new_window", [False, True])
def test_local_second_hop_keeps_attempt_and_never_reissues_new(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, new_window: bool,
) -> None:
    from worktree_manager import new_window_spawn

    monkeypatch.setattr(relocated_launch, "_core", lambda: SimpleNamespace(_is_windows=lambda: True))
    commands: list[list[str]] = []
    def spawn(argv: list[str]) -> SimpleNamespace:
        commands.append(argv)
        return SimpleNamespace(wait=lambda: 0)
    monkeypatch.setattr(
        subprocess, "Popen", spawn,
    )
    monkeypatch.setattr(new_window_spawn, "spawn_detached_new_window",
                        lambda argv, **kw: commands.append(argv))
    req = SimpleNamespace(
        project="example", mode=mode, worktree_id="wt-a", attempt_id=ID,
        no_mux=False, new_window=new_window,
    )
    plan = engine.launch_plan_from_dict(_plan())
    assert relocated_launch._run_relocated_mux_launch(
        req, plan, tmp_path / "launch-session.ps1",
    ) == 0
    command = commands[0]
    assert "--require-launch-attempt" in command
    assert command[command.index("--launch-attempt-id") + 1] == ID
    assert "--new" not in command and "--seed" not in command
