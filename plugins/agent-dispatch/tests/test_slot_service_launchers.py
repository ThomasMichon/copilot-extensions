"""Use generated stable entrypoints while slot-local execution owns the call."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows service entrypoints")


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("role", ["coordinator", "supervisor"])
def test_stable_entrypoint_releases_file_before_slot_execution(tmp_path, host, role):
    shell = shutil.which(host)
    assert shell, f"required Windows shell unavailable: {host}"
    root = tmp_path / "application's directory"
    slot = root / "versions" / "fixture"
    python = slot / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (root / "current-version").write_text("fixture")
    filename = "serve-service.ps1" if role == "coordinator" else "supervise-service.ps1"
    owner = "Install-CoordinatorTask" if role == "coordinator" else "Write-SupervisorLauncher"
    resource = slot / "Lib" / "site-packages" / "agent_dispatch" / "service_launchers"
    resource.mkdir(parents=True)
    evidence = root / "execution.json"
    profile = root / "a profile.env"
    profile.write_text("# preserved profile\n")
    (resource / f"{role}.ps1").write_text(
        "param($InstallDir,$RuntimePython,$RuntimeSlot,$EnvFile,[switch]$DescribeLaunch)\n"
        "$ErrorActionPreference='Stop'\n"
        f"$stable=Join-Path $InstallDir {quote(filename)}\n"
        "$handle=[IO.File]::Open($stable,'Open','ReadWrite','None')\n"
        "try {\n"
        "  @{script=$PSCommandPath;root=$InstallDir;python=$RuntimePython;"
        "slot=$RuntimeSlot;profile=$EnvFile;describe=[bool]$DescribeLaunch}"
        f" | ConvertTo-Json | Set-Content -LiteralPath {quote(evidence)}\n"
        "  Start-Sleep -Milliseconds 100\n"
        "} finally {$handle.Dispose()}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    publisher = root / "publish.ps1"
    publisher.write_text(f"""
$ErrorActionPreference='Stop'
$InstallDir={quote(root)};$LinkDir={quote(root / '.venv')};$LinkPython={quote(python)}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(INSTALLER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Installer parse failed'}}
$owner=@($ast.FindAll({{param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq '{owner}'
}},$true))[0]
$assignment=@($owner.FindAll({{param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left.Extent.Text -eq '$launcherBody'
}},$true))
if($assignment.Count -ne 1){{throw 'Launcher assignment not unique'}}
. ([scriptblock]::Create($assignment[0].Extent.Text))
[IO.File]::WriteAllText((Join-Path $InstallDir {quote(filename)}),$launcherBody)
""", encoding="utf-8")
    common = [shell, "-NoProfile", "-NonInteractive", "-File"]
    published = subprocess.run(
        [*common, str(publisher)], capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert published.returncode == 0, published.stdout + published.stderr
    arguments = ["-EnvFile", str(profile), "-DescribeLaunch"] if role == "supervisor" else []
    executed = subprocess.run(
        [*common, str(root / filename), *arguments], capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    observed = json.loads(evidence.read_text(encoding="utf-8-sig"))
    assert Path(observed["script"]).samefile(resource / f"{role}.ps1")
    assert Path(observed["root"]).samefile(root)
    assert Path(observed["python"]).samefile(python)
    assert Path(observed["slot"]).samefile(slot)
    if role == "supervisor":
        assert Path(observed["profile"]).samefile(profile)
        assert observed["describe"] is True
    assert profile.read_text() == "# preserved profile\n"
    # An old runtime cannot silently borrow a different generation's launcher.
    (resource / f"{role}.ps1").unlink()
    resolved = subprocess.run(
        [*common, str(root / filename), "-ResolveOnly"],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    assert str(python) in resolved.stdout
    missing = subprocess.run(
        [*common, str(root / filename), *arguments],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert missing.returncode != 0
    assert f"no slot-local {role} launcher" in missing.stderr
