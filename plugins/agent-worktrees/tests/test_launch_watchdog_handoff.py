"""Observe the resolution watchdog inside the real direct-fallback wait."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import config as cfg, manager_launch_cli, registry_paths, terminal_launch


class _BlockedWait(BaseException):
    pass


class _Timer:
    def __init__(self) -> None:
        self.started = False
        self.cancelled = False
        self.daemon = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


@pytest.fixture
def resolution_timer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> _Timer:
    timer = _Timer()

    def make_timer(interval: float, callback: Callable[[], None]) -> _Timer:
        assert interval == main._LAUNCH_RESOLUTION_TIMEOUT_SECS
        assert callback is main._fire_launch_resolution_watchdog
        return timer

    monkeypatch.setattr(main.threading, "Timer", make_timer)
    monkeypatch.setattr(terminal_launch, "validate_or_exit", lambda: None)
    monkeypatch.setattr(cfg, "active_project", lambda: "fixture")
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(main, "_usable_worktree_manager_launcher_dir", lambda: None)
    return timer


@pytest.mark.parametrize("action", ["exec", "remote"])
@pytest.mark.parametrize("blocking", [False, True], ids=["returns", "blocks"])
def test_direct_handoff_disarms_before_child_wait(
    monkeypatch: pytest.MonkeyPatch, resolution_timer: _Timer,
    action: str, blocking: bool,
) -> None:
    events: list[str] = []

    def resolve(
        project: str | None, passthrough: list[str],
    ) -> tuple[dict[str, object], str | None]:
        assert project == "fixture"
        assert passthrough == ["--resume=fixture"]
        assert resolution_timer.started and not resolution_timer.cancelled
        events.append("resolve")
        return {
            "action": action,
            "work_dir": "fixture",
            "cmd": ["fixture-child"],
            "ssh_alias": "fixture-host",
            "remote_command": "fixture-command",
        }, project

    class Child:
        def wait(self, timeout: float | None = None) -> int:
            assert resolution_timer.cancelled, "resolution timer reached the child wait"
            events.append("wait")
            if blocking:
                raise _BlockedWait
            return 17

    def spawn(*args: object, **kwargs: object) -> Child:
        assert events == ["resolve"]
        assert not resolution_timer.cancelled, "resolution ended before handoff"
        events.append("spawn")
        return Child()

    monkeypatch.setattr(manager_launch_cli, "_resolve_direct_launch_plan", resolve)
    monkeypatch.setattr(manager_launch_cli.subprocess, "Popen", spawn)
    if action == "remote":
        # Windows retains the child/wait path; POSIX replacement is tested separately.
        monkeypatch.setattr(
            manager_launch_cli, "os", SimpleNamespace(**{**vars(os), "name": "nt"}),
        )
    if blocking:
        with pytest.raises(_BlockedWait):
            main.cmd_launch(["--resume=fixture"])
    else:
        assert main.cmd_launch(["--resume=fixture"]) == 17
    assert events == ["resolve", "spawn", "wait"]
    assert resolution_timer.cancelled


@pytest.mark.parametrize("failure", [RuntimeError, SystemExit])
def test_resolution_error_always_cancels(
    monkeypatch: pytest.MonkeyPatch, resolution_timer: _Timer,
    failure: type[BaseException],
) -> None:
    def resolve(
        project: str | None, passthrough: list[str],
    ) -> tuple[dict[str, object], str | None]:
        assert resolution_timer.started and not resolution_timer.cancelled
        raise failure("fixture resolution failure")

    monkeypatch.setattr(manager_launch_cli, "_resolve_direct_launch_plan", resolve)
    with pytest.raises(failure, match="fixture resolution failure"):
        main.cmd_launch([])
    assert resolution_timer.cancelled


def test_refresh_keeps_next_resolution_bounded(
    monkeypatch: pytest.MonkeyPatch, resolution_timer: _Timer,
) -> None:
    calls = 0

    def resolve(
        project: str | None, passthrough: list[str],
    ) -> tuple[dict[str, object], str | None]:
        nonlocal calls
        assert resolution_timer.started and not resolution_timer.cancelled
        calls += 1
        return {"action": "refresh" if calls == 1 else "none"}, project

    monkeypatch.setenv("WORKTREE_NO_UPDATE", "1")
    monkeypatch.setattr(manager_launch_cli, "_resolve_direct_launch_plan", resolve)
    assert main.cmd_launch([]) == 0
    assert calls == 2
    assert resolution_timer.cancelled
