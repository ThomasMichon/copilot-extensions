"""MCP's native bootstrap retains completion without legacy broker adoption."""

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


def test_bootstrap_admits_before_staging_and_completes_before_selection():
    assert SOURCE.index("-Plugin 'agent-mcp'") < SOURCE.index("# === install-contract:v4 self-stage")
    assert "'broker-remove'" in SOURCE.split("$driverPayload =", 1)[0]
    assert "-RetainHelpers" in _function("Invoke-VersionedMarkComplete")
    activate = SOURCE.split("# === install-contract:v3 versioned-venv activate", 1)[1]
    assert activate.index("Invoke-VersionedMarkComplete") < activate.index("'activate', $SrcVersion")
    assert "BrokerAdoption" not in SOURCE
    project = tomllib.loads((SCRIPTS.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-update-driver" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["agent-update-driver"]["path"] == "../../libs/update-driver"
    assert "Dir = 'update-driver'; Pkg = 'agent-update-driver'" in SOURCE
    assert "update-driver:agent-update-driver" in (SCRIPTS / "init.sh").read_text(encoding="utf-8")


def test_broker_removal_precedes_every_runtime_mutation_and_does_not_remove_the_service():
    removal = SOURCE.split("if ($Action -eq 'broker-remove') {", 1)[1].split(
        "# === install-contract:test-persistent-environment", 1,
    )[0]
    assert "--remove-broker" in removal
    assert "Owned broker removal requires an uncontained Windows installer" in removal
    assert "Owned runtime broker removal failed; runtimes preserved" in removal
    assert "Remove-Item" not in removal
    assert removal.rstrip().endswith("return\n}")
