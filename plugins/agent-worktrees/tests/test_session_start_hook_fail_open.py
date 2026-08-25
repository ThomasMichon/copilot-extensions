"""Session-start wrappers tolerate partial deployment without masking producers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


_PLUGIN = Path(__file__).resolve().parents[1]


def _hooks(event: str) -> list[dict[str, object]]:
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    return hooks["hooks"][event]


def _run(command: str, shell: str, home: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-Command", command]
        if Path(shell).name.startswith("pwsh")
        else [shell, "-c", command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _stage_stub(command: str, home: Path, cwd: Path, body: str) -> None:
    match = re.search(r"([a-z-]+\.sh)", command)
    assert match is not None
    for directory in (home / ".agent-worktrees" / "bin", cwd / "scripts"):
        directory.mkdir(parents=True, exist_ok=True)
        script = directory / match.group(1)
        script.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")


def _powershell_wrapper(command: str, script: Path) -> str:
    wrapper_start = command.rfind("if (Test-Path $s)")
    assert wrapper_start >= 0
    escaped = str(script).replace("'", "''")
    return f"$s = '{escaped}'; {command[wrapper_start:]}"


def test_bash_hooks_emit_empty_context_when_runtime_scripts_are_absent(tmp_path: Path):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()

    for hook in _hooks("sessionStart"):
        result = _run(str(hook["bash"]), "bash", home, cwd)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}"


def test_bash_session_end_hook_succeeds_when_runtime_script_is_absent(tmp_path: Path):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()

    result = _run(str(_hooks("sessionEnd")[0]["bash"]), "bash", home, cwd)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_bash_hooks_do_not_mask_runtime_script_failures(tmp_path: Path):
    for index, hook in enumerate(_hooks("sessionStart") + _hooks("sessionEnd")):
        home = tmp_path / str(index) / "home"
        cwd = tmp_path / str(index) / "cwd"
        home.mkdir(parents=True)
        cwd.mkdir()
        command = str(hook["bash"])
        _stage_stub(command, home, cwd, "exit 23")

        result = _run(command, "bash", home, cwd)
        if "provision-check.sh" in command:
            assert result.returncode == 0
        else:
            assert result.returncode == 23


def test_bash_hook_forwards_runtime_script_output(tmp_path: Path):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    command = str(_hooks("sessionStart")[0]["bash"])
    expected = '{"additionalContext":"runtime guidance"}'
    _stage_stub(command, home, cwd, f"printf '%s' '{expected}'")

    result = _run(command, "bash", home, cwd)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_powershell_hooks_fail_open_when_runtime_scripts_are_absent(tmp_path: Path):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is not available")
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()

    for hook in _hooks("sessionStart"):
        result = _run(str(hook["powershell"]), pwsh, home, cwd)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}"

    result = _run(str(_hooks("sessionEnd")[0]["powershell"]), pwsh, home, cwd)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_powershell_hooks_do_not_mask_runtime_script_failures(tmp_path: Path):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is not available")

    for index, hook in enumerate(_hooks("sessionStart") + _hooks("sessionEnd")):
        home = tmp_path / str(index) / "home"
        cwd = tmp_path / str(index) / "cwd"
        home.mkdir(parents=True)
        cwd.mkdir()
        script = tmp_path / str(index) / "stub.ps1"
        script.write_text("exit 23\n", encoding="utf-8")
        command = _powershell_wrapper(str(hook["powershell"]), script)

        result = _run(command, pwsh, home, cwd)
        assert result.returncode != 0


def test_powershell_hook_forwards_runtime_script_output(tmp_path: Path):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is not available")
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    script = tmp_path / "stub.ps1"
    expected = '{"additionalContext":"runtime guidance"}'
    script.write_text(f"[Console]::Out.Write('{expected}')\n", encoding="utf-8")
    command = _powershell_wrapper(
        str(_hooks("sessionStart")[0]["powershell"]),
        script,
    )

    result = _run(command, pwsh, home, cwd)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
