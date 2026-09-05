"""Tests for attributable process executable discovery."""

from __future__ import annotations

import os
import subprocess
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


def test_copilot_relaunch_path_accepts_identified_executable(
    monkeypatch, tmp_path,
) -> None:
    executable = tmp_path / "copilot"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(procs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        procs.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "GitHub Copilot CLI 1.2.3\n", "",
        ),
    )

    assert procs.copilot_relaunch_path(str(executable)) == str(executable)


def test_copilot_relaunch_path_prefers_canonical_windows_replacement(
    monkeypatch, tmp_path,
) -> None:
    retained = tmp_path / "copilot.exe.old-123"
    canonical = tmp_path / "copilot.exe"
    retained.write_text("", encoding="utf-8")
    canonical.write_text("", encoding="utf-8")
    called = []
    monkeypatch.setattr(procs.platform, "system", lambda: "Windows")

    def _run(argv, **kwargs):
        called.append(argv[0])
        return subprocess.CompletedProcess(
            argv, 0, "GitHub Copilot CLI 1.2.4\n", "",
        )

    monkeypatch.setattr(procs.subprocess, "run", _run)

    assert procs.copilot_relaunch_path(str(retained)) == str(canonical)
    assert called == [str(canonical)]


def test_copilot_relaunch_path_rejects_silent_success(
    monkeypatch, tmp_path,
) -> None:
    retained = tmp_path / "copilot.exe.old-123"
    retained.write_text("", encoding="utf-8")
    monkeypatch.setattr(procs.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        procs.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "", "",
        ),
    )

    assert procs.copilot_relaunch_path(str(retained)) is None


def test_copilot_relaunch_path_rejects_probe_timeout(
    monkeypatch, tmp_path,
) -> None:
    executable = tmp_path / "copilot"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(procs.platform, "system", lambda: "Linux")

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(procs.subprocess, "run", _timeout)

    assert procs.copilot_relaunch_path(str(executable)) is None
