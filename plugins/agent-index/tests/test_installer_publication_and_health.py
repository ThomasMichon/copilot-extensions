"""Publication ordering and runtime repair boundaries in disposable fixtures."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from test_install_engine_adoption import FAKE_PYTHON, environment, function, run_ps


@pytest.mark.parametrize("failure", ["filesystem", "probe"])
def test_optional_server_repair_exception_warns_and_restores_preference(tmp_path, failure):
    (tmp_path / "slot/server").mkdir(parents=True)
    injection = (
        "function Test-IndexVenv { param($Dir,$Python) throw 'probe fixture failure' }"
        if failure == "probe" else
        "function Test-IndexVenv { param($Dir,$Python) return $false }\n"
        "function Remove-Item { param($LiteralPath) throw 'filesystem fixture failure' }"
    )
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") +
                    function("Install-ServerVenv", "ps1") + f"""
$env:OS = 'Installer_Test'
$VenvDir = '{tmp_path / "slot"}'
$ErrorActionPreference = 'Stop'
function Write-Warn {{ param($Msg) [Console]::Error.WriteLine($Msg) }}
{injection}
function Resolve-Zdd {{ throw 'package install must not follow failed repair' }}
Install-ServerVenv -InstallRole host -PythonCmd forbidden
if ($ErrorActionPreference -ne 'Stop') {{ throw 'preference was not restored' }}
'primary-continues'
""")
    assert result.returncode == 0, result.stderr
    assert "primary-continues" in result.stdout
    assert f"{failure} fixture failure" in result.stderr
    assert "falls back to the shared venv" in result.stderr


def publication_fixture(tmp_path: Path) -> tuple[str, Path]:
    root = tmp_path / "runtime"
    (root / "versions/2.0.0").mkdir(parents=True)
    scripts = tmp_path
    (scripts / "versioned_runtime.py").write_text(
        "import pathlib, sys\n"
        "a = sys.argv\n"
        "if 'activate' in a:\n"
        "    root = pathlib.Path(a[a.index('--root') + 1])\n"
        "    (root / 'current-version').write_text(a[a.index('activate') + 1])\n",
        encoding="utf-8",
    )
    definitions = "\n".join(function(name, "ps1") for name in (
        "Get-VerTuple", "Test-VersionLt", "New-IndexMutex", "Enter-IndexStampLock", "Enter-IndexBuildLock", "Get-IndexUv",
        "Test-IndexPublicationFresh", "Publish-FileAtomically",
        "Invoke-VersionedActivate", "Publish-IndexRuntime",
    ))
    script = definitions + f"""
$ErrorActionPreference = 'Stop'
$InstallDir = '{root}'
$PSScriptRoot = '{scripts}'
$SrcVersion = '2.0.0'
$VersionedRuntime = $true
$Force = $false
$LegacyVenvDir = '{tmp_path / "absent-legacy"}'
$VenvDir = '{root / "versions/2.0.0"}'
$VenvPython = '{sys.executable}'
function Write-Ok {{ param($Msg) }}
function Write-Skip {{ param($Msg) }}
function Write-Fail {{ param($Msg) throw $Msg }}
function Test-RuntimeOrigin {{ param($Python, $Slot) return $true }}
function Invoke-Stop {{ throw 'forbidden lifecycle mutation' }}
function Deploy-SetupGatedBinstub {{
    param($PayloadRoot)
    [IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $SrcVersion)
    [IO.File]::WriteAllText((Join-Path $InstallDir 'launcher-version'), $SrcVersion)
}}
function Write-Manifest {{
    [IO.File]::WriteAllText((Join-Path $InstallDir 'manifest-version'), $SrcVersion)
}}
"""
    return script, root


def test_new_activation_cannot_enter_between_stamp_check_and_publication(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    definitions, root = publication_fixture(tmp_path)
    (root / "current-version").write_text("0.9.0", encoding="utf-8")
    old_snapshot = root / "snapshots/1.0.0-identity"
    old_snapshot.mkdir(parents=True)
    (root / "stamp-candidate-1.0.0").write_text(str(old_snapshot), encoding="utf-8")
    checked = tmp_path / "checked"
    outcome = tmp_path / "lock-outcome"
    worker = tmp_path / "worker.ps1"
    worker.write_text(definitions + f"""
$InstallDir = $InstallDir + [IO.Path]::DirectorySeparatorChar
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path '{checked}')) {{
    if ([DateTime]::UtcNow -gt $deadline) {{ throw 'stamp never reached freshness check' }}
    Start-Sleep -Milliseconds 20
}}
try {{
    $probe = Enter-IndexStampLock -Scope Publish -TimeoutSeconds 0
    [void]$probe.ReleaseMutex(); $probe.Dispose()
    [IO.File]::WriteAllText('{outcome}', 'unserialized')
}} catch {{
    if ($_.Exception.Message -notlike '*Timed out waiting*') {{ throw }}
    [IO.File]::WriteAllText('{outcome}', 'blocked')
}}
if (-not (Publish-IndexRuntime)) {{ throw 'new activation failed' }}
""", encoding="utf-8")
    with subprocess.Popen(
        [pwsh, "-NoProfile", "-File", str(worker)], env=environment(tmp_path),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as child:
        result = run_ps(tmp_path, definitions + function("Invoke-Stamp", "ps1") + f"""
$SrcVersion = '1.0.0'
$LocalBin = '{tmp_path / "bin"}'
$probePayload = 'old-origin'
function New-IndexSnapshot {{ return '{old_snapshot}' }}
$compare = ${{function:Test-VersionLt}}
function Test-VersionLt {{
    param($A, $B)
    if (-not $script:Signalled -and $A -eq '1.0.0' -and $B -eq '0.9.0') {{
        $script:Signalled = $true
        [IO.File]::WriteAllText('{checked}', 'checked')
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while (-not (Test-Path '{outcome}')) {{
            if ([DateTime]::UtcNow -gt $deadline) {{ throw 'activation did not attempt publication' }}
            Start-Sleep -Milliseconds 20
        }}
        if ([IO.File]::ReadAllText('{outcome}') -ne 'blocked') {{
            throw 'activation entered between the stamp check and publication'
        }}
    }}
    return (& $compare -A $A -B $B)
}}
Invoke-Stamp
""")
        stdout, stderr = child.communicate(timeout=25)
    assert result.returncode == 0, result.stderr
    assert child.returncode == 0, stdout + stderr
    for marker in ("current-version", "payload-dir", "launcher-version", "manifest-version"):
        assert (root / marker).read_text() == "2.0.0"


@pytest.mark.parametrize("authority", ["current-version", "stamped-version"])
def test_runtime_publication_rechecks_freshness_before_all_writes(tmp_path, authority):
    definitions, root = publication_fixture(tmp_path)
    (root / authority).write_text("3.0.0", encoding="utf-8")
    for name in ("payload-dir", "launcher-version", "manifest-version"):
        (root / name).write_text("newer", encoding="utf-8")
    result = run_ps(tmp_path, definitions + """
if (Publish-IndexRuntime) { throw 'older runtime was published' }
if (-not $script:RuntimePublicationSuperseded) { throw 'supersession not reported' }
""")
    assert result.returncode == 0, result.stderr
    assert (root / authority).read_text() == "3.0.0"
    for name in ("payload-dir", "launcher-version", "manifest-version"):
        assert (root / name).read_text() == "newer"

def test_direct_launcher_writer_rejects_superseded_source(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("3.0.0")
    (root / "payload-dir").write_text("newer-payload")
    result = run_ps(tmp_path, function("Deploy-SetupGatedBinstub", "ps1") + f"""
$InstallDir = '{root}'
$SrcVersion = '2.0.0'
$LocalBin = '{tmp_path / "bin"}'
Deploy-SetupGatedBinstub -PayloadRoot older-payload
""")
    assert result.returncode == 0, result.stderr
    assert (root / "payload-dir").read_text() == "newer-payload"
    assert not (tmp_path / "bin").exists()


@pytest.mark.parametrize("mode", ["healthy", "corrupt", "base", "wrong-prefix", "probe-failure"])
def test_posix_actual_interpreter_health_and_repair(tmp_path, mode):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    directory = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(directory)
    python = directory / "bin/python"
    if mode != "healthy":
        python.unlink()
        if mode == "base":
            python.write_text(f"#!/bin/sh\nexec '{sys._base_executable}' \"$@\"\n")
            python.chmod(0o755)
        elif mode == "wrong-prefix":
            other = tmp_path / "other"
            venv.EnvBuilder(with_pip=False, symlinks=True).create(other)
            python.write_text(f"#!/bin/sh\nexec '{other / 'bin/python'}' \"$@\"\n")
            python.chmod(0o755)
        else:
            python.write_text("not an interpreter\n" if mode == "corrupt" else "#!/bin/sh\nexit 13\n")
            python.chmod(0o755)
    log = tmp_path / "uv-calls"
    uv = tmp_path / "uv-fixture"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        '[[ "$1" == venv && "$3" == --allow-existing && "$#" == 3 ]] || exit 97\n'
        'mkdir -p "$2/bin"; echo fixture > "$2/pyvenv.cfg"\n'
        f"printf '%s' {shlex.quote(FAKE_PYTHON)} > \"$2/bin/python\"\n"
        'chmod +x "$2/bin/python"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    script = f"""
set -euo pipefail
{function("_test_index_venv", "sh")}
{function("_new_index_venv", "sh")}
. '{Path(__file__).resolve().parents[3] / "libs/installer-engine/installer-engine.sh"}'
_warn() {{ echo "$*" >&2; }}
UV_COMMAND='{uv}'
if _test_index_venv '{directory}' '{python}'; then initial=healthy; else initial=invalid; fi
echo "$initial"
_new_index_venv '{directory}' '{python}' forbidden-base 1
_test_index_venv '{directory}' '{python}'
"""
    result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("healthy" if mode == "healthy" else "invalid")
    assert log.exists() == (mode != "healthy")
    if log.exists():
        assert len(log.read_text().splitlines()) == 1


def test_posix_bad_created_venv_fails_explicitly(tmp_path):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    directory = tmp_path / "venv"
    script = f"""
{function("_test_index_venv", "sh")}
{function("_new_index_venv", "sh")}
_warn() {{ echo "$*" >&2; }}
invoke_uv_venv_resilient() {{
    mkdir -p "$2/bin"; echo fixture > "$2/pyvenv.cfg"
    printf '#!/bin/sh\\nexec "%s" "$@"\\n' '{sys._base_executable}' > "$2/bin/python"
    chmod +x "$2/bin/python"
    return 0
}}
fallback() {{ echo forbidden-fallback >&2; return 37; }}
_new_index_venv '{directory}' '{directory / "bin/python"}' fallback 1
"""
    result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "uv venv failed health validation" in result.stderr
    assert "Python venv fallback failed" in result.stderr
