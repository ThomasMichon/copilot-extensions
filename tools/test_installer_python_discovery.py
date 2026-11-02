"""Optional launcher discovery stays quiet without repeating successful work."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "plugins" / "agent-bridge" / "scripts" / "install.ps1"
MCP = ROOT / "plugins" / "agent-mcp" / "scripts" / "init.ps1"
NATIVE = ROOT / "libs" / "versioned-runtime" / "versioned_runtime_native.ps1"


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _bridge_discovery() -> str:
    match = re.search(
        r"^function Get-SignedBasePython \{.*?^\}",
        BRIDGE.read_text(encoding="utf-8"),
        re.M | re.S,
    )
    assert match is not None
    return match.group()


def _mcp_discovery() -> str:
    source = MCP.read_text(encoding="utf-8")
    start = source.index("    $signedBase = $null")
    end = source.index("    if ($signedBase -and (Test-Path $VenvPython))", start)
    return source[start:end]


def _run(
    host: str, script: str, tmp_path: Path, *, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    harness = tmp_path / "discovery.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". {_quote(NATIVE)}\n" + script,
        encoding="utf-8",
    )
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _launcher_fixture(candidate: Path) -> str:
    code = (
        "import sys; "
        "selector,candidate=sys.argv[1:]; "
        "missing=selector!='-3.12'; "
        "sys.stderr.write('No suitable Python runtime found\\n' if missing else ''); "
        "print(candidate) if not missing else None; "
        "sys.exit(103 if missing else 0)"
    )
    return f"""
$env:OS = 'Windows_NT'
$script:Probes = @()
$script:Signatures = 0
$script:SignatureStatus = 'Valid'
function Get-Command {{
    param($Name)
    if ($Name -ne 'py') {{ throw "Unexpected discovery: $Name" }}
    return [pscustomobject]@{{ Source = {_quote(sys.executable)} }}
}}
function Get-AuthenticodeSignature {{
    param($FilePath)
    $script:Signatures++
    return [pscustomobject]@{{ Status = $script:SignatureStatus }}
}}
Set-Item Function:Invoke-DiscoveryChild -Value ${{function:Invoke-Hidden}}
function Invoke-Hidden {{
    param($FilePath, $ArgList, [ref]$ErrorOutput)
    $script:Probes += $ArgList[0]
    $nativeError = ''
    $output = Invoke-DiscoveryChild -FilePath {_quote(sys.executable)} `
        -ArgList @('-I', '-c', {_quote(code)}, $ArgList[0], {_quote(candidate)}) `
        -ErrorOutput ([ref]$nativeError)
    if ($PSBoundParameters.ContainsKey('ErrorOutput')) {{
        $ErrorOutput.Value = $nativeError
    }} elseif ($nativeError) {{
        [Console]::Error.Write($nativeError)
    }}
    return $output
}}
"""


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_native_streaming_preserves_both_streams_and_failure_status(tmp_path, host):
    code = "import sys;sys.stdout.write('native-out');sys.stderr.write('native-error');sys.exit(23)"
    result = _run(host, f"""
Invoke-Hidden -FilePath {_quote(sys.executable)} -StreamOutput `
    -ArgList @('-I', '-c', {_quote(code)})
exit $LASTEXITCODE
""", tmp_path)
    assert result.returncode == 23
    assert result.stdout == "native-out"
    assert result.stderr == "native-error"


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_bridge_reuses_signed_selection_without_more_launcher_children(tmp_path, host):
    candidate = tmp_path / "signed-python.exe"
    candidate.write_bytes(b"signature-fixture")
    script = _launcher_fixture(candidate) + _bridge_discovery() + f"""
Set-StrictMode -Version 2.0
$selected = @(1..4 | ForEach-Object {{ Get-SignedBasePython }})
if ($selected.Count -ne 4 -or @($selected | Where-Object {{ $_ -cne {_quote(candidate)} }}).Count) {{
    throw 'Signed selection changed'
}}
[Console]::Out.Write((@{{ probes = $script:Probes; signatures = $script:Signatures }} | ConvertTo-Json -Compress))
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    evidence = json.loads(result.stdout)
    assert evidence["probes"] == ["-3.13", "-3.12"]
    assert evidence["signatures"] == 4


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
@pytest.mark.parametrize("invalidate", ["remove", "signature"])
def test_bridge_does_not_trust_missing_or_newly_unsigned_cached_candidate(
    tmp_path, host, invalidate,
):
    candidate = tmp_path / "signed-python.exe"
    candidate.write_bytes(b"signature-fixture")
    mutation = (
        f"Remove-Item -LiteralPath {_quote(candidate)}"
        if invalidate == "remove" else "$script:SignatureStatus = 'NotSigned'"
    )
    script = _launcher_fixture(candidate) + _bridge_discovery() + f"""
if ((Get-SignedBasePython) -cne {_quote(candidate)}) {{ throw 'Initial selection failed' }}
{mutation}
if ($null -ne (Get-SignedBasePython)) {{ throw 'Invalid cached candidate accepted' }}
[Console]::Out.Write(($script:Probes | ConvertTo-Json -Compress))
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == [
        "-3.13", "-3.12", "-3.13", "-3.12", "-3.11", "-3.10",
    ]


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_mcp_optional_miss_is_captured_before_signed_fallback(tmp_path, host):
    candidate = tmp_path / "signed-python.exe"
    candidate.write_bytes(b"signature-fixture")
    script = _launcher_fixture(candidate) + _mcp_discovery() + f"""
if ($signedBase -cne {_quote(candidate)}) {{ throw 'Signed fallback changed' }}
[Console]::Out.Write(($script:Probes | ConvertTo-Json -Compress))
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == ["-3.13", "-3.12"]


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_bridge_reconsiders_candidates_after_an_unsuccessful_discovery(tmp_path, host):
    candidate = tmp_path / "signed-python.exe"
    candidate.write_bytes(b"signature-fixture")
    script = _launcher_fixture(candidate) + _bridge_discovery() + f"""
$script:SignatureStatus = 'NotSigned'
if ($null -ne (Get-SignedBasePython)) {{ throw 'Unsigned candidate accepted' }}
$script:SignatureStatus = 'Valid'
if ((Get-SignedBasePython) -cne {_quote(candidate)}) {{ throw 'Unsuccessful discovery was cached' }}
[Console]::Out.Write(($script:Probes | ConvertTo-Json -Compress))
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == [
        "-3.13", "-3.12", "-3.11", "-3.10", "-3.13", "-3.12",
    ]


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_required_native_failure_remains_visible(tmp_path, host):
    code = "import sys; sys.stderr.write('required operation failed'); sys.exit(7)"
    script = f"""
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList @('-I', '-c', {_quote(code)})
if ($LASTEXITCODE -ne 7) {{ throw 'Required failure lost its exit status' }}
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == "required operation failed"


@pytest.mark.skipif(os.name != "nt", reason="native signed Windows interpreter")
@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_native_signed_discovery_builds_a_fresh_copied_venv(tmp_path, host):
    if not shutil.which("py"):
        pytest.skip("Windows Python launcher unavailable")
    venv = tmp_path / "fresh-runtime"
    probe = (
        "import os,sys; "
        "assert os.path.normcase(sys.prefix)==os.path.normcase(sys.argv[1]); "
        "print('fresh-runtime-ready')"
    )
    script = (
        "Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security') "
        "-ErrorAction Stop\n" + _bridge_discovery() + f"""
$base = Get-SignedBasePython
if (-not $base -or (Get-AuthenticodeSignature $base).Status -ne 'Valid') {{
    throw 'No accepted signed interpreter'
}}
if ((Get-SignedBasePython) -cne $base) {{ throw 'Repeated selection changed' }}
$created = Invoke-Hidden -FilePath $base -ArgList @('-m', 'venv', '--copies', {_quote(venv)})
if ($LASTEXITCODE -ne 0) {{ throw 'Fresh signed venv construction failed' }}
$output = Invoke-Hidden -FilePath {_quote(venv / 'Scripts' / 'python.exe')} `
    -ArgList @('-I', '-c', {_quote(probe)}, {_quote(venv)})
if ($LASTEXITCODE -ne 0) {{ throw 'Fresh copied interpreter failed' }}
[Console]::Out.Write($output)
"""
    )
    env = dict(os.environ)
    env.pop("PYLAUNCHER_ALLOW_INSTALL", None)
    result = _run(host, script, tmp_path, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "fresh-runtime-ready"
    assert result.stderr == ""
