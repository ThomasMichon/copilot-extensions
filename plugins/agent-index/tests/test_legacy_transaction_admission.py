"""Real cross-process admission shared by native lifecycle and session stamping."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def fixture_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    root = home / ".agent-index"
    root.mkdir(parents=True)
    (root / "operator.txt").write_bytes(b"retained operator state")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_INDEX_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_"))
        and key != "COPILOT_PROJECT_DIR"
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        INDEX_TRANSACTION_LOCK_TIMEOUT_SECONDS="0",
    )
    return root, env


@contextmanager
def held_transaction(root: Path, env: dict[str, str], tmp_path: Path):
    ready = tmp_path / "transaction-ready"
    code = (
        "import pathlib,sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from stamp_paths import install_transaction\n"
        "with install_transaction(pathlib.Path(sys.argv[2])):\n"
        " pathlib.Path(sys.argv[3]).write_text('ready')\n"
        " sys.stdin.readline()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, str(SCRIPTS), str(root), str(ready)],
        env=env, cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "transaction holder did not become ready"
        assert child.poll() is None
        yield child
    finally:
        _, error = child.communicate("\n" if child.poll() is None else None, timeout=15)
        assert child.returncode == 0, error


def test_session_stamp_defers_while_native_transaction_is_held(tmp_path: Path) -> None:
    root, env = fixture_environment(tmp_path)
    command = [
        sys.executable, "-I", "-B", str(SCRIPTS / "session-stamp.py"),
        str(root), str(SCRIPTS.parent), "legacy",
    ]
    with held_transaction(root, env, tmp_path):
        result = subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30, **FLAGS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "session stamp deferred: install transaction is busy" in result.stderr
        assert not (root / "payload-dir").exists()
        assert not (root / "stamped-version").exists()
        assert not (root.parent / ".local" / "bin").exists()
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30, **FLAGS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "payload-dir").read_text().strip() == str(SCRIPTS.parent)
    assert (root / "stamped-version").is_file()
    assert (root / "operator.txt").read_bytes() == b"retained operator state"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh", "bash"])
def test_native_action_waits_before_lifecycle_side_effects(tmp_path: Path, host: str) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    root, env = fixture_environment(tmp_path)
    changed = tmp_path / "lifecycle-called"
    env["INDEX_TEST_CALLED"] = str(changed)
    if os.name == "nt":
        wrapper = tmp_path / "native-stop.ps1"
        wrapper.write_text(
            "param($Installer, $Root)\n$ErrorActionPreference='Stop'\n"
            "function Get-ScheduledTask { param($TaskName,$ErrorAction) "
            "[pscustomobject]@{State='Ready'} }\n"
            "function Stop-ScheduledTask { param($TaskName,$ErrorAction) "
            "[IO.File]::WriteAllText($env:INDEX_TEST_CALLED, 'called') }\n"
            "& $Installer stop -InstallDir $Root\n",
            encoding="utf-8",
        )
        command = [
            shell, "-NoProfile", "-NonInteractive", "-File", str(wrapper),
            str(SCRIPTS / "install.ps1"), str(root),
        ]
    else:
        units = root.parent / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "agent-index.service").write_text("fixture unit")
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        systemctl = fakebin / "systemctl"
        systemctl.write_text('#!/bin/sh\nprintf called > "$INDEX_TEST_CALLED"\nexit 0\n')
        systemctl.chmod(0o755)
        env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
        command = [shell, str(SCRIPTS / "install.sh"), "stop", "--install-dir", str(root)]
    with held_transaction(root, env, tmp_path):
        result = subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=45, **FLAGS,
        )
        assert result.returncode != 0
        assert "Timed out waiting" in result.stdout + result.stderr
        assert not changed.exists()
        assert (root / "operator.txt").read_bytes() == b"retained operator state"
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=45, **FLAGS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert changed.read_text() == "called"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows task-elevation admission")
@pytest.mark.parametrize("host", ("powershell.exe", "pwsh"))
@pytest.mark.parametrize(
    "mode", ("switch", "environment", "already-elevated", "not-opted-in", "refused"),
)
def test_task_elevation_parent_does_not_hold_child_transaction(
    tmp_path: Path, host: str, mode: str,
) -> None:
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    root, env = fixture_environment(tmp_path)
    env["INDEX_TEST_RUNTIME_ROOT"] = str(root)
    env["INDEX_TEST_EVENTS"] = str(tmp_path / "events")
    env["INDEX_TEST_PARENT"] = str(tmp_path / "parent")
    env["INDEX_TEST_HANDOFF"] = str(tmp_path / "handoff")
    env["INDEX_TEST_REFUSE"] = "1" if mode == "refused" else "0"
    env["AGENT_INDEX_ALLOW_TASK_ELEVATION"] = "1" if mode == "environment" else "0"
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8-sig")
    functions = []
    for name in (
        "New-IndexMutex", "Enter-IndexStampLock", "Test-TaskElevationOptIn",
        "Invoke-RegisterTasks",
    ):
        match = re.search(rf"(?ms)^function {name} \{{.*?^\}}$", source)
        assert match is not None, name
        functions.append(match.group())
    admission = source.split("$transactionMutex = $null\ntry {\n", 1)[1].split(
        "$ScriptDir = $PSScriptRoot", 1,
    )[0]
    wrapper = tmp_path / "task-admission.ps1"
    wrapper.write_text(
        "param([switch]$Child,[switch]$AllowTaskElevation,[switch]$AlreadyElevated)\n"
        "$ErrorActionPreference='Stop'\n$Action='register-tasks'\n"
        "$InstallDir=$env:INDEX_TEST_RUNTIME_ROOT\n$DryRun=$false\n"
        "$TaskName='fixture-service';$EngineTaskName='fixture-engine'\n"
        "if (-not $Child) { [IO.File]::WriteAllText($env:INDEX_TEST_PARENT, \"$PID\") }\n"
        "function Test-Elevated { return [bool]($Child -or $AlreadyElevated) }\n"
        "function Write-Warn { param($Message) }\n"
        "function Record-TaskStep {\n"
        " if ($null -eq $transactionMutex) { throw 'Task mutation without transaction' }\n"
        " [IO.File]::AppendAllText($env:INDEX_TEST_EVENTS, \"$PID`n\")\n"
        "}\n"
        "function Remove-LogonAutostart { param($Name);Record-TaskStep }\n"
        "function Register-EngineDaemon { Record-TaskStep }\n"
        "function Install-Service { Record-TaskStep }\n"
        "function Invoke-ScopedElevation {\n"
        " param($ElevAction)\n"
        " if ($ElevAction -ne 'register-tasks') { throw 'Wrong elevated action' }\n"
        " [IO.File]::WriteAllText($env:INDEX_TEST_HANDOFF, \"$PID\")\n"
        " if ($env:INDEX_TEST_REFUSE -eq '1') { return $false }\n"
        " $start=New-Object Diagnostics.ProcessStartInfo\n"
        " $start.FileName=(Get-Process -Id $PID).Path\n"
        " $start.Arguments='-NoProfile -NonInteractive -File \"'+$PSCommandPath+'\" -Child -AllowTaskElevation'\n"
        " $start.UseShellExecute=$false;$start.CreateNoWindow=$true\n"
        " $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true\n"
        " $process=[Diagnostics.Process]::Start($start)\n"
        " $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()\n"
        " $process.WaitForExit()\n"
        " if ($process.ExitCode -ne 0) { throw ($out.Result+$err.Result) }\n"
        " return $true\n"
        "}\n"
        + "\n".join(functions)
        + "\n$transactionMutex=$null\ntry {\n"
        + admission
        + "\nInvoke-RegisterTasks\n} finally {\n"
        " if ($null -ne $transactionMutex) {\n"
        "  [void]$transactionMutex.ReleaseMutex();$transactionMutex.Dispose()\n"
        " }\n}\n",
        encoding="utf-8",
    )
    arguments = ["-AllowTaskElevation"] if mode in ("switch", "refused") else []
    if mode == "already-elevated":
        arguments = ["-AllowTaskElevation", "-AlreadyElevated"]
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(wrapper), *arguments],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30, **FLAGS,
    )
    if mode == "refused":
        assert result.returncode != 0, result.stdout + result.stderr
        assert "Task-scheduling elevation did not complete" in result.stdout + result.stderr
        assert Path(env["INDEX_TEST_HANDOFF"]).exists()
        assert not Path(env["INDEX_TEST_EVENTS"]).exists()
        assert (root / "operator.txt").read_bytes() == b"retained operator state"
        return
    assert result.returncode == 0, result.stdout + result.stderr
    parent = Path(env["INDEX_TEST_PARENT"]).read_text()
    mutations = Path(env["INDEX_TEST_EVENTS"]).read_text().splitlines()
    assert len(mutations) == 4
    if mode in ("switch", "environment"):
        assert Path(env["INDEX_TEST_HANDOFF"]).read_text() == parent
        assert set(mutations) != {parent}
        assert len(set(mutations)) == 1
    else:
        assert not Path(env["INDEX_TEST_HANDOFF"]).exists()
        assert set(mutations) == {parent}
    assert (root / "operator.txt").read_bytes() == b"retained operator state"
