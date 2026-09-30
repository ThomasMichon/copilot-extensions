"""Windows installer guards for forwarded venue bridge routes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")


def _extract_function(name: str) -> str:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    start = text.index(f"function {name}")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name!r}")


def _write_forward(tmp_path: Path, active: dict) -> None:
    install_dir = tmp_path / "agent-bridge"
    install_dir.mkdir()
    (install_dir / "active.json").write_text(
        json.dumps({"active": active}), encoding="utf-8"
    )


def _run_harness(tmp_path: Path, functions: list[str], extra: str) -> subprocess.CompletedProcess:
    install_dir = tmp_path / "agent-bridge"
    link_python = tmp_path / "python.exe"
    link_python.write_text("stub", encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        f"$InstallDir = '{install_dir}'\n"
        f"$PidFile = '{install_dir / 'agent-bridge.pid'}'\n"
        f"$LinkPython = '{link_python}'\n"
        "$Port = 9280\n"
        "$TaskName = 'agent-bridge'\n"
        "$ScheduledTaskHasNotRunResult = 267009\n"
        "function Write-Skip { param([string]$m) Write-Host \"SKIP: $m\" }\n"
        "function Write-Fail { param([string]$m) throw $m }\n"
        "function Write-Step { param([string]$m) Write-Host \"STEP: $m\" }\n"
        "function Write-Warn { param([string]$m) Write-Host \"WARN: $m\" }\n"
        "function Write-Ok { param([string]$m) Write-Host \"OK: $m\" }\n"
        + "\n\n".join(_extract_function(name) for name in functions)
        + "\n\n"
        + extra
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


@pytest.mark.parametrize(
    "active",
    [
        {"bind": "127.0.0.1", "port": 62254, "forwarded": True},
        {"port": 62254},
    ],
)
def test_install_ps1_recognizes_forwarded_active_routes(tmp_path: Path, active: dict) -> None:
    _write_forward(tmp_path, active)
    result = _run_harness(
        tmp_path,
        ["Test-ActiveIsForward"],
        "Write-Host \"FORWARD=$(Test-ActiveIsForward)\"",
    )
    assert "FORWARD=True" in result.stdout


def test_get_running_process_never_returns_forward_listener(tmp_path: Path) -> None:
    _write_forward(tmp_path, {"bind": "127.0.0.1", "port": 62254, "forwarded": True})
    result = _run_harness(
        tmp_path,
        ["Test-ActiveIsForward", "Get-ActiveEndpoint", "Get-RunningProcess"],
        """
function Get-Process { return $null }
function Get-NetTCPConnection { throw 'must not inspect the forwarded listener' }
$rp = Get-RunningProcess
if ($null -eq $rp) { Write-Host 'RUNNING=NULL' } else { Write-Host "RUNNING=$($rp.Id)" }
""",
    )
    assert "RUNNING=NULL" in result.stdout


def test_invoke_start_skips_over_forwarded_route(tmp_path: Path) -> None:
    _write_forward(tmp_path, {"bind": "127.0.0.1", "port": 62254, "forwarded": True})
    result = _run_harness(
        tmp_path,
        ["Test-ActiveIsForward", "Invoke-Start"],
        """
function Get-RunningProcess { throw 'must not inspect or stop forwarded route' }
function Test-HealthOnce { throw 'must not probe forwarded route' }
Invoke-Start
""",
    )
    assert "SKIP:" in result.stdout
    assert "not starting a local daemon" in result.stdout
