"""MCP's native bootstrap owns broker adoption without a resident prerequisite."""

from pathlib import Path
import os
import shutil
import subprocess

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SOURCE = (SCRIPTS / "init.ps1").read_text(encoding="utf-8")


def _function(name):
    return "function " + name + " {" + SOURCE.split(
        "function " + name + " {", 1,
    )[1].split("\n}", 1)[0] + "\n}"


def test_native_bootstrap_parses_without_execution():
    shell = shutil.which("powershell.exe") if os.name == "nt" else shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    source = str(SCRIPTS / "init.ps1").replace("'", "''")
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command",
         "$tokens=$null;$errors=$null;"
         f"[void][Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$errors);"
         "if ($errors.Count) { throw ($errors | Out-String) }"],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_bootstrap_admits_before_staging_and_adopts_before_selection():
    assert SOURCE.index("-Plugin 'agent-mcp'") < SOURCE.index("# === install-contract:v4 self-stage")
    assert "'broker-remove'" in SOURCE.split("$driverPayload =", 1)[0]
    assert "-RetainHelpers" in _function("Invoke-VersionedMarkComplete")
    activate = SOURCE.split("# === install-contract:v3 versioned-venv activate", 1)[1]
    assert activate.index("Invoke-VersionedMarkComplete") < activate.index("Invoke-RuntimeBrokerAdoption")
    assert activate.index("Invoke-RuntimeBrokerAdoption") < activate.index("'activate', $SrcVersion")
    project = tomllib.loads((SCRIPTS.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-update-driver" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["agent-update-driver"]["path"] == "../../libs/update-driver"
    assert "Dir = 'update-driver'; Pkg = 'agent-update-driver'" in SOURCE
    assert "update-driver:agent-update-driver" in (SCRIPTS / "init.sh").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["posix", "contained", "missing", "ready"])
def test_native_adoption_retains_completion_and_preserves_suppression(tmp_path, mode):
    shell = shutil.which("powershell.exe") if os.name == "nt" else shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    script = tmp_path / "probe.ps1"
    script.write_text(
        "param($Mode)\n$ErrorActionPreference='Stop'\n"
        "$env:OS=if ($Mode -eq 'posix') {'Unix'} else {'Windows_NT'}\n"
        "$env:COPILOT_EXTENSIONS_TEST_CONTAINED=if ($Mode -eq 'contained') {'1'} else {''}\n"
        "$script:RuntimeConstructionSession=if ($Mode -eq 'ready') {\n"
        "  @{Complete=$true;Process=@{HasExited=$false}}\n} else {$null}\n"
        "$VenvPython='candidate-python';$VenvDir='candidate-slot';$InstallDir='consumer'\n"
        "$script:Calls=0\n"
        "function Invoke-Hidden {\n"
        " param($FilePath,$ArgList,$ErrorOutput)\n"
        " if ($FilePath -ne $VenvPython -or $ArgList[2] -ne 'update_driver.windows_broker') {\n"
        "  throw 'wrong adoption command'\n }\n"
        " $script:Calls++;$global:LASTEXITCODE=0;return '{\"adopted\":true}'\n}\n"
        + _function("Invoke-RuntimeBrokerAdoption")
        + "\ntry { Invoke-RuntimeBrokerAdoption } catch {\n"
        " if ($Mode -ne 'missing' -or $_ -notmatch 'complete-slot retention') {throw}\n"
        " if ($script:Calls -ne 0) {throw 'unretained runtime invoked broker'}\n exit 0\n}\n"
        "if ($Mode -eq 'missing') {throw 'missing retention was accepted'}\n"
        "$expected=if ($Mode -eq 'ready') {1} else {0}\n"
        "if ($script:Calls -ne $expected) {throw 'unexpected adoption side effects'}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script), mode],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_broker_removal_precedes_every_runtime_mutation_and_does_not_remove_the_service():
    removal = SOURCE.split("if ($Action -eq 'broker-remove') {", 1)[1].split(
        "# === install-contract:test-persistent-environment", 1,
    )[0]
    assert "--remove-broker" in removal
    assert "Owned broker removal requires an uncontained Windows installer" in removal
    assert "Owned runtime broker removal failed; runtimes preserved" in removal
    assert "Remove-Item" not in removal
    assert removal.rstrip().endswith("return\n}")
