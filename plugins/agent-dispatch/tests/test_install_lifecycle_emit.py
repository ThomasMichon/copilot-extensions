"""Execute the shipped lifecycle helper and dispatch boundary in isolation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _function(text: str, name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}\(\) \{{.*?^\}}$", text, re.MULTILINE | re.DOTALL
    )
    assert match, name
    return match.group(0)


def _ps_function(text: str, name: str) -> str:
    match = re.search(
        rf"^function {re.escape(name)} \{{.*?^\}}$", text, re.MULTILINE | re.DOTALL
    )
    assert match, name
    return match.group(0)


def _run(tmp_path: Path, action: str, failure: str = "", *, blocked: bool = False,
         powershell: bool = False, discovery: str = "") -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    shell = shutil.which("pwsh" if powershell else "bash")
    if not shell or (not powershell and os.name == "nt"):
        pytest.skip("native shell unavailable")
    config = tmp_path / "state with spaces"
    if blocked:
        config.write_text("preserve", encoding="utf-8")
    runtime = tmp_path / "runtime"
    if discovery == "runtime":
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin" / "python").symlink_to(sys.executable)
    text = (PLUGIN / "scripts" / ("install.ps1" if powershell else "install.sh")).read_text(
        encoding="utf-8"
    )
    if powershell:
        boundary = text[text.index("function Write-InstallerLifecycle {"):]
        mocks = "\n".join(
            f"function {name} {{ "
            + ("throw 'synthetic refusal'" if failure == name else
               "if ($global:LASTEXITCODE -ne 47) { throw 'recorder changed native status' }; return")
            + " }"
            for name in (
                "Initialize-SourceRuntime", "Assert-SourceRuntime", "Invoke-Install",
                "Invoke-Provision", "Invoke-Update", "Invoke-Start", "Invoke-Stop",
                "Invoke-Status", "Invoke-Stamp", "Invoke-Uninstall",
                "Stop-RuntimeConstruction", "Exit-InstallLock",
            )
        )
        script = (
            "Set-StrictMode -Version 2.0\n$ErrorActionPreference = 'Stop'\n"
            "$PluginDir = $env:TEST_PLUGIN\n$InstallDir = $env:TEST_CONFIG\n"
            "$VenvDir = $env:TEST_RUNTIME\n$LinkDir = $env:TEST_RUNTIME\n"
            "$SrcVersion = '1.2.3'\n$Action = $env:TEST_ACTION\n"
            "$global:LASTEXITCODE = 47\n"
            + ". (Join-Path $PluginDir 'scripts/versioned_runtime_native.ps1')\n"
            + ". (Join-Path $PluginDir 'scripts/base-python.ps1')\n"
            + _ps_function(text, "Get-BootstrapPython") + "\n"
            + (_ps_function(text, "Get-SignedBasePython") + "\n"
               if "function Get-SignedBasePython {" in text else "")
            + (_launcher_fixture() if discovery == "launcher" else "")
            + ("function Get-Command { param($Name, $CommandType, $ErrorAction) }\n"
               if discovery == "missing" else "")
            + (_store_alias_fixture() if discovery == "store-alias" else "")
            + mocks + "\n" + boundary
        )
        args = [shell, "-NoProfile", "-NonInteractive", "-File"]
        suffix = ".ps1"
    else:
        boundary = text[text.index('_LIFECYCLE_ACTION=""\ncase "$ACTION" in'):]
        mocks = "\n".join(
            f"{name}() {{ " + ("exit 23" if failure == name else ":") + "; }"
            for name in (
                "_initialize_source_runtime", "_assert_source_runtime", "do_install",
                "do_update", "do_start", "do_stop", "do_status", "do_stamp",
                "do_uninstall",
            )
        )
        script = (
            "set -euo pipefail\nPLUGIN_DIR=\"$TEST_PLUGIN\"\nINSTALL_DIR=\"$TEST_CONFIG\"\n"
            "SRC_VERSION=1.2.3\nACTION=\"$TEST_ACTION\"\nLINK_DIR=\"$TEST_RUNTIME\"\n"
            "_warn() { printf '%s\\n' \"$*\" >&2; }\n"
            + _function(text, "_bootstrap_python") + "\n"
            + _function(text, "_lifecycle_emit") + "\n"
            + _function(text, "_lifecycle_finish") + "\n" + mocks + "\n" + boundary
        )
        args = [shell]
        suffix = ".sh"
    harness = tmp_path / ("boundary" + suffix)
    harness.write_text(script, encoding="utf-8")
    environment = {
        **os.environ, "TEST_PLUGIN": str(PLUGIN), "TEST_CONFIG": str(config),
        "TEST_ACTION": action, "AGENT_DISPATCH_ROUTING_DIR": "",
        "TEST_RUNTIME": str(runtime), "TEST_PYTHON": sys.executable,
        "TEST_LAUNCHER_USED": str(tmp_path / "launcher-used"),
    }
    if not powershell and discovery in {"runtime", "missing"}:
        environment["PATH"] = str(tmp_path / "empty-path")
    result = subprocess.run(
        [*args, str(harness)], env=environment, capture_output=True, text=True,
        timeout=25, check=False,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    log = config / "lifecycle.log"
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] \
        if log.is_file() else []
    return result, events


def _launcher_fixture() -> str:
    # Native launcher/signature observations are synthetic; the recorder still runs.
    return r"""
$env:OS = 'Windows_NT'
$script:RealHidden = ${function:Invoke-Hidden}
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($Name -eq 'py') { return [pscustomobject]@{ Source = 'fixture-launcher' } }
}
function Get-AuthenticodeSignature {
    param($Path)
    return [pscustomobject]@{ Status = 'Valid' }
}
function Invoke-Hidden {
    param([string]$FilePath, [string[]]$ArgList, [ref]$ErrorOutput)
    if ($FilePath -eq 'fixture-launcher') {
        if ($ArgList[0] -notlike '-3*') { throw 'unexpected launcher request' }
        [IO.File]::WriteAllText($env:TEST_LAUNCHER_USED, $ArgList[0])
        $global:LASTEXITCODE = 0
        return $env:TEST_PYTHON + "`n"
    }
    return & $script:RealHidden -FilePath $FilePath -ArgList $ArgList -ErrorOutput $ErrorOutput
}
"""


def test_runtime_only_bash_discovery_records_events(tmp_path: Path) -> None:
    result, events = _run(tmp_path, "update", discovery="runtime")
    assert result.returncode == 0, result.stderr
    assert [event["outcome"] for event in events] == ["begin", "ok"]
    assert not result.stderr


def test_powershell_canonical_launcher_discovery_records_events(tmp_path: Path) -> None:
    result, events = _run(tmp_path, "update", powershell=True, discovery="launcher")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "launcher-used").is_file()
    assert [event["outcome"] for event in events] == ["begin", "ok"]
    assert not result.stderr


def _store_alias_fixture() -> str:
    return r"""
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($Name -eq 'python3') {
        return [pscustomobject]@{ Source = 'C:\WindowsApps\python.exe' }
    }
    if ($Name -eq 'python') {
        return [pscustomobject]@{ Source = $env:TEST_PYTHON }
    }
}
"""


def test_powershell_store_alias_is_not_launched(tmp_path: Path) -> None:
    result, events = _run(tmp_path, "update", powershell=True, discovery="store-alias")
    assert result.returncode == 0, result.stderr
    assert [event["outcome"] for event in events] == ["begin", "ok"]
    assert not result.stderr


@pytest.mark.parametrize("powershell", [False, True], ids=["bash", "powershell"])
def test_missing_interpreter_is_advisory(tmp_path: Path, powershell: bool) -> None:
    result, events = _run(tmp_path, "update", powershell=powershell, discovery="missing")
    assert result.returncode == 0, result.stderr
    assert not events
    assert "Lifecycle record unavailable" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("powershell", [False, True], ids=["bash", "powershell"])
@pytest.mark.parametrize("action", ["install", "provision", "update", "start", "stop"])
def test_installer_lifecycle_records_real_command_boundary(
    tmp_path: Path, action: str, powershell: bool
) -> None:
    result, events = _run(tmp_path, action, powershell=powershell)
    assert result.returncode == 0, result.stderr
    expected = "install" if action == "provision" else action
    assert [(event["action"], event["outcome"]) for event in events] == [
        (expected, "begin"), (expected, "ok"),
    ]
    assert {event["service"] for event in events} == {PLUGIN.name}
    assert len({event["pid"] for event in events}) == 1
    assert all(event["version"] == "1.2.3" for event in events)
    assert result.stdout == ""


@pytest.mark.parametrize("powershell", [False, True], ids=["bash", "powershell"])
@pytest.mark.parametrize("failure", ["initialize", "action"])
def test_installer_lifecycle_failure_is_not_success(
    tmp_path: Path, powershell: bool, failure: str
) -> None:
    name = {
        (False, "initialize"): "_initialize_source_runtime",
        (False, "action"): "do_update",
        (True, "initialize"): "Initialize-SourceRuntime",
        (True, "action"): "Invoke-Update",
    }[powershell, failure]
    result, events = _run(tmp_path, "update", name, powershell=powershell)
    assert result.returncode == (1 if powershell else 23), result.stderr
    assert [(event["action"], event["outcome"]) for event in events] == [
        ("update", "begin"), ("update", "fail"),
    ]


@pytest.mark.parametrize("powershell", [False, True], ids=["bash", "powershell"])
def test_installer_lifecycle_logging_failure_is_advisory(
    tmp_path: Path, powershell: bool
) -> None:
    result, events = _run(tmp_path, "install", blocked=True, powershell=powershell)
    assert result.returncode == 0, result.stderr
    assert not events
    assert "could not append install/begin" in result.stderr
    assert "could not append install/ok" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("powershell", [False, True], ids=["bash", "powershell"])
def test_inspection_does_not_create_lifecycle_records(
    tmp_path: Path, powershell: bool
) -> None:
    result, events = _run(tmp_path, "status", powershell=powershell)
    assert result.returncode == 0, result.stderr
    assert not events


def test_existing_exit_traps_keep_marker_and_lock_cleanup() -> None:
    text = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    traps = re.findall(r"^\s+trap (.*) EXIT$", text, re.MULTILINE)
    assert traps
    assert all("_lifecycle_finish" in trap for trap in traps)


def test_powershell_cleanup_failure_is_recorded(tmp_path: Path) -> None:
    result, events = _run(
        tmp_path, "update", "Stop-RuntimeConstruction", powershell=True
    )
    assert result.returncode != 0, result.stderr
    assert [event["outcome"] for event in events] == ["begin", "fail"]


@pytest.mark.parametrize("exit_code", [0, 23])
def test_shipped_exit_traps_preserve_status_and_cleanup(tmp_path: Path, exit_code: int) -> None:
    shell = shutil.which("bash")
    if not shell or os.name == "nt":
        pytest.skip("POSIX bash unavailable")
    text = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    traps = sorted(set(re.findall(r"^\s+trap (.*) EXIT$", text, re.MULTILINE)))
    for index, trap in enumerate(traps):
        root = tmp_path / str(index)
        root.mkdir()
        script = (
            "set -euo pipefail\n"
            "PLUGIN_DIR=\"$TEST_PLUGIN\"\nINSTALL_DIR=\"$TEST_CONFIG\"\n"
            "SRC_VERSION=1.2.3\n_LIFECYCLE_ACTION=update\nLINK_DIR=\"$INSTALL_DIR/runtime\"\n"
            "_warn() { printf '%s\\n' \"$*\" >&2; }\n"
            "_clear_update_marker() { echo marker; }\n"
            "_exit_install_lock() { echo lock; }\n"
            + _function(text, "_bootstrap_python") + "\n"
            + _function(text, "_lifecycle_emit") + "\n"
            + _function(text, "_lifecycle_finish") + "\n"
            + f"trap {trap} EXIT\n_lifecycle_emit begin\nexit {exit_code}\n"
        )
        result = subprocess.run(
            [shell, "-c", script], capture_output=True, text=True, timeout=15, check=False,
            env={**os.environ, "TEST_PLUGIN": str(PLUGIN), "TEST_CONFIG": str(root),
                 "AGENT_DISPATCH_ROUTING_DIR": ""},
        )
        assert result.returncode == exit_code, result.stderr
        assert ("marker\n" in result.stdout) == ("_clear_update_marker" in trap)
        assert ("lock\n" in result.stdout) == ("_exit_install_lock" in trap)
        events = [json.loads(line) for line in (root / "lifecycle.log").read_text().splitlines()]
        assert [event["outcome"] for event in events] == [
            "begin", "ok" if exit_code == 0 else "fail",
        ]
