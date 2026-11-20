"""Real finite shell transport with explicit setup-owned Python selection."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

HELPER = Path(__file__).resolve().parents[1] / "src/agent_procutil/daemon_instances.ps1"
pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="native Windows helper transport"),
    pytest.mark.parametrize("host", ["powershell.exe", "pwsh"]),
]


def _literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(body: str, host: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
    return subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=20,
        creationflags=flags,
    )


def test_explicit_bootstrap_python_runs_transient_transport_without_path(
    tmp_path: Path, host: str,
) -> None:
    result = _run(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f"$env:PATH={_literal(tmp_path)}; "
        f"$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON={_literal(sys.executable)}; "
        f". {_literal(HELPER)}; "
        "Rename-Item Function:\\Start-DaemonInstanceWorker Start-OriginalWorker; "
        "function Start-DaemonInstanceWorker { param([hashtable]$State) "
        "Start-OriginalWorker $State; "
        "$script:selected=$State.selectedExecutable }; "
        "$reply=Invoke-DaemonInstanceRequest @{command='reconcile-contract'} -Transient; "
        "@{selected=$script:selected;reply=$reply;"
        "hint=$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON;"
        "cached=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))} "
        "| ConvertTo-Json -Compress",
        host,
    )
    assert result.returncode == 0, result.stderr
    details = json.loads(result.stdout)
    assert details == {
        "selected": sys.executable, "hint": sys.executable, "cached": False,
        "reply": {"version": 1, "termination_policy": True, "runtime_scope": True},
    }


def test_changed_bootstrap_hint_refuses_without_replacing_live_helper(
    tmp_path: Path, host: str,
) -> None:
    result = _run(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f"$env:PATH={_literal(tmp_path)}; "
        f"$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON={_literal(sys.executable)}; "
        f". {_literal(HELPER)}; "
        "$gate=[string]::Intern('copilot-daemon-instance-shell-v1'); "
        "try { "
        "$null=Invoke-DaemonInstanceRequest @{command='reconcile-contract'}; "
        "$state=[AppDomain]::CurrentDomain.GetData($gate); "
        "$sequence=$state.sequence; $original=$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON; "
        f"$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON={_literal(tmp_path / 'different.exe')}; "
        "$failure=$null; "
        "try { $null=Invoke-DaemonInstanceRequest @{command='reconcile-contract'} } "
        "catch { $failure=$_.Exception.Message }; "
        "$unchanged=($state.sequence -eq $sequence -and -not $state.broken -and "
        "[object]::ReferenceEquals($state,[AppDomain]::CurrentDomain.GetData($gate))); "
        "$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON=$original; "
        "$last=Invoke-DaemonInstanceRequest @{command='reconcile-contract'}; "
        "@{failure=$failure;unchanged=$unchanged;version=$last.version} | ConvertTo-Json -Compress "
        "} finally { "
        "$state=[AppDomain]::CurrentDomain.GetData($gate); "
        "if ($null -ne $state) { Close-DaemonInstanceWorker $state }; "
        "[AppDomain]::CurrentDomain.SetData($gate,$null) }",
        host,
    )
    assert result.returncode == 0, result.stderr
    details = json.loads(result.stdout)
    assert "existing custody was not changed" in details["failure"]
    assert details["unchanged"] is True and details["version"] == 1


@pytest.mark.parametrize("selection", [
    "relative", "drive-relative", "current-drive", "missing", "alias", "broken",
])
def test_invalid_explicit_bootstrap_python_never_falls_back_to_path(
    tmp_path: Path, host: str, selection: str,
) -> None:
    alias = tmp_path / "WindowsApps" / "python.exe"
    alias.parent.mkdir()
    alias.touch()
    broken = tmp_path / "broken.exe"
    broken.touch()
    selected = {
        "relative": "python.exe", "drive-relative": "C:python.exe",
        "current-drive": r"\python.exe", "missing": str(tmp_path / "missing.exe"),
        "alias": str(alias), "broken": str(broken),
    }[selection]
    result = _run(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f"$env:PATH={_literal(Path(sys.executable).parent)}; "
        f"$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON={_literal(selected)}; "
        f". {_literal(HELPER)}; "
        "$failure=$null; "
        "try { $null=Invoke-DaemonInstanceRequest @{command='reconcile-contract'} -Transient } "
        "catch { $failure=$_.Exception.Message }; "
        "@{failure=$failure;hint=$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON;"
        "cached=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))} "
        "| ConvertTo-Json -Compress",
        host,
    )
    assert result.returncode == 0, result.stderr
    details = json.loads(result.stdout)
    assert details["failure"], "a valid PATH interpreter must not replace the explicit selection"
    if selection != "broken":
        assert "supplied bootstrap Python" in details["failure"]
    assert details["hint"] == selected and details["cached"] is False
