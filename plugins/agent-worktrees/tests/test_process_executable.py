"""Tests for attributable process executable discovery."""

from __future__ import annotations

import os
from pathlib import Path

from agent_worktrees import procs


def test_process_executable_path_resolves_current_process() -> None:
    executable = procs.process_executable_path(os.getpid())

    assert executable is not None
    assert Path(executable).is_absolute()
    assert Path(executable).is_file()


def test_process_executable_path_rejects_invalid_pid() -> None:
    assert procs.process_executable_path(0) is None


def test_process_executable_path_uses_proc_link_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(procs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        procs.os,
        "readlink",
        lambda path: (
            "/opt/copilot/bin/copilot"
            if path == Path("/proc/42/exe")
            else None
        ),
    )

    assert procs.process_executable_path(42) == "/opt/copilot/bin/copilot"


def test_process_executable_path_retains_live_deleted_proc_link(
    monkeypatch,
) -> None:
    proc_link = Path("/proc/42/exe")
    monkeypatch.setattr(procs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        procs.os,
        "readlink",
        lambda path: (
            "/opt/copilot/bin/copilot (deleted)"
            if path == proc_link
            else None
        ),
    )
    monkeypatch.setattr(
        procs.os,
        "access",
        lambda path, mode: path == proc_link and mode == os.X_OK,
    )

    assert procs.process_executable_path(42) == str(proc_link)
