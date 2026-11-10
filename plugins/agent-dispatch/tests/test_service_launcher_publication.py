"""Publish generated Windows launchers without starting any service."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows file sharing")


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("owner", ["Install-CoordinatorTask", "Write-SupervisorLauncher"])
def test_generated_launcher_publication(tmp_path: Path, host: str, owner: str) -> None:
    shell = shutil.which(host)
    assert shell, f"required Windows shell unavailable: {host}"

    def quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = f"""
$ErrorActionPreference='Stop'
$InstallDir={quote(tmp_path)}
$LinkDir=Join-Path $InstallDir '.venv'
$LinkPython=Join-Path $LinkDir 'Scripts\\python.exe'
$utf8NoBom=[Text.UTF8Encoding]::new($false)
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(INSTALLER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Installer parse failed'}}
$functions=@($ast.FindAll({{param($n)
    $n -is [Management.Automation.Language.FunctionDefinitionAst]
}},$true))
foreach($name in @('Enter-PluginSnapshotLock','Publish-FileAtomically','Publish-ServiceLauncher')){{
    $found=@($functions | Where-Object Name -eq $name)
    if($found.Count -eq 1){{ . ([scriptblock]::Create($found[0].Extent.Text)) }}
}}
$owner=@($functions | Where-Object Name -eq '{owner}')[0]
# Exercise the original generation/publication statements, not service enrollment.
$body=$owner.Body.EndBlock.Statements
$start=@($body | Where-Object {{$_.Extent.Text -match '^\\$launcher ='}})[0]
$end=@($body | Where-Object {{
    $_.Extent.Text -match '^\\[System.IO.File\\]::WriteAllText\\(\\$launcher,' -or
    $_.Extent.Text -match '^Publish-ServiceLauncher '
}})[0]
if(-not $start -or -not $end){{throw 'Launcher publication boundary missing'}}
$publish=[scriptblock]::Create($owner.Extent.Text.Substring(
    $start.Extent.StartOffset-$owner.Extent.StartOffset,
    $end.Extent.EndOffset-$start.Extent.StartOffset))
. $publish
$original=[IO.File]::ReadAllBytes($launcher)
$before=[IO.File]::GetLastWriteTimeUtc($launcher)
$reader=[IO.File]::Open($launcher,'Open','Read','Read')
try {{
    . $publish
    if([IO.File]::GetLastWriteTimeUtc($launcher) -ne $before){{throw 'Identical launcher rewritten'}}
    # A changed generated body must fail, leaving the held script intact.
    $LinkPython=Join-Path $InstallDir 'different-python.exe'
    $refused=$false
    try {{ . $publish }} catch {{ $refused=$true }}
    if(-not $refused){{throw 'Blocked changed content reported success'}}
    if([Convert]::ToBase64String([IO.File]::ReadAllBytes($launcher)) -cne
       [Convert]::ToBase64String($original)){{throw 'Blocked publication damaged launcher'}}
}} finally {{ $reader.Dispose() }}
# A live reader that permits replacement retains a complete old script.
$reader=[IO.File]::Open($launcher,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
try {{
    . $publish
    $held=New-Object byte[] $original.Length
    if($reader.Read($held,0,$held.Length) -ne $held.Length){{throw 'Short old script read'}}
    if([Convert]::ToBase64String($held) -cne [Convert]::ToBase64String($original)){{
        throw 'Reader observed a torn script'
    }}
    if([IO.File]::ReadAllText($launcher) -cne $launcherBody){{throw 'Changed content not published'}}
}} finally {{ $reader.Dispose() }}
Write-Output 'identical-preserved changed-refused reader-preserved changed-published'
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "identical-preserved changed-refused reader-preserved changed-published" in result.stdout
    )
