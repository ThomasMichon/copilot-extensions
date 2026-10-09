"""One installation-wide bootstrap writer for independent runtime consumers."""
from __future__ import annotations

import os
import base64
import shlex
import shutil
import subprocess
import sys

import pytest

from test_install_engine_adoption import environment, function, run_ps
from test_installer_publication_and_health import publication_fixture


@pytest.mark.parametrize("fallback", [False, True])
def test_posix_runtime_and_engine_acquisition_have_one_complete_writer(tmp_path, fallback):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    started, ready = (tmp_path / name for name in ("started", "ready"))
    root = tmp_path / "runtime"
    exe = root / "tool/uv"
    complete = "#!/bin/sh\nprintf 'uv-fixture-complete\\n'\n"
    definitions = "\n".join(function(name, "sh") for name in (
        "_with_index_advisory_lock", "_with_index_build_lock", "_with_index_publication_lock",
        "_bootstrap_python", "_find_python", "_ensure_uv",
    ))
    script = definitions + f"""
set -uo pipefail
INSTALL_DIR='{root}'
LINK_DIR='{tmp_path / "missing"}'
_warn() {{ echo "$*" >&2; }}
export COPILOT_EXT_NO_FLOCK={1 if fallback else 0}
ensure_uv() {{
    local path="$1/tool/uv"
    if [[ -x "$path" ]] && [[ "$("$path" --version)" == uv-fixture-complete ]]; then
        printf '%s\\n' "$path"; return 0
    fi
    echo "$Actor" >> '{tmp_path / "writers"}'
    mkdir -p "$1/tool"
    printf '#!/bin/sh\\nexit 47\\n' > "$path"; chmod +x "$path"
    echo started > '{started}'
    deadline=$((SECONDS + 10))
    while [[ ! -f '{ready}' ]]; do ((SECONDS < deadline)) || return 97; sleep 0.02; done
    # Acquisition uses fd7 and cannot clobber or hold build8/publication9.
    free() {{ :; }}
    INDEX_BUILD_LOCK_TIMEOUT_SECONDS=0 _with_index_build_lock "$1/versions/1.0.0" free || return 97
    _with_index_advisory_lock "$1/publication" 9 0 free || return 97
    printf '%s' {shlex.quote(complete)} > "$path"
    chmod +x "$path"
    [[ "$("$path" --version)" == uv-fixture-complete ]] || return 97
    printf '%s\\n' "$path"
}}
"""
    worker = tmp_path / "engine.sh"
    worker.write_text(script + f"""
Actor=engine
deadline=$((SECONDS + 10))
while [[ ! -f '{started}' ]]; do ((SECONDS < deadline)) || exit 97; sleep 0.02; done
echo ready > '{ready}'
_ensure_uv || exit $?
[[ "$("$UV_COMMAND" --version)" == uv-fixture-complete ]] || exit 97
echo "$UV_COMMAND"
""")
    with subprocess.Popen([bash, str(worker)], env=environment(tmp_path),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        result = subprocess.run([bash, "-c", script + """
Actor=runtime
_ensure_uv || exit $?
[[ "$("$UV_COMMAND" --version)" == uv-fixture-complete ]] || exit 97
echo "$UV_COMMAND"
"""], env=environment(tmp_path), capture_output=True, text=True, timeout=25)
        stdout, stderr = child.communicate(timeout=25)
    assert result.returncode == 0, result.stderr
    assert child.returncode == 0, stderr
    assert result.stdout.strip() == stdout.strip() == str(exe)
    assert (tmp_path / "writers").read_text().splitlines() == ["runtime"]
    assert exe.read_text() == complete


def test_windows_runtime_and_engine_share_uv_bootstrap_before_build(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    definitions, root = publication_fixture(tmp_path)
    started, ready = (tmp_path / name for name in ("started", "ready"))
    attempting = tmp_path / "attempting"
    package = tmp_path / "package"
    (package / "src").mkdir(parents=True)
    exe = root / "tool" / ("uv.cmd" if os.name == "nt" else "uv")
    complete = "@echo off\r\necho uv-fixture-complete\r\nexit /b 0\r\n" if os.name == "nt" else (
        "#!/bin/sh\nprintf 'uv-fixture-complete\\n'\n"
    )
    encoded = base64.b64encode(complete.encode()).decode()
    script = (definitions + function("Install-Runtime", "ps1") +
              function("Install-ServerVenv", "ps1") + function("Install-Engine", "ps1")) + f"""
$BasePython='{sys.executable}'
$PkgSrcDir='{package / "src"}'; $PluginDir='{package}'
$LocalBin='{tmp_path / "bin"}'
$VersionedRuntime=$false
$LinkDir=$VenvDir; $VenvPython='fixture_python'; $LinkPython=$VenvPython
$EngineHome='{tmp_path / "engine"}'; $EngineVenv=Join-Path $EngineHome 'venv'
$EngineVenvPython='fixture_python'
function Write-Warn {{ param($Msg) [Console]::Error.WriteLine($Msg) }}
function Get-Command {{
    [CmdletBinding()]param($Name,$CommandType)
    if ($Name -in @('python','python3','py')) {{ return [pscustomobject]@{{ Source=$BasePython }} }}
    throw "forbidden executable rediscovery: $Name"
}}
function Ensure-Uv {{
    param($InstallRoot)
    $path='{exe}'
    if (Test-Path $path) {{
        $version=& $path --version
        if ($LASTEXITCODE -eq 0 -and $version -eq 'uv-fixture-complete') {{ return $path }}
    }}
    Add-Content '{tmp_path / "writers"}' $Actor
    New-Item -ItemType Directory -Path (Split-Path $path) -Force | Out-Null
    [IO.File]::WriteAllText($path, 'partial')
    [IO.File]::WriteAllText('{started}', 'started')
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while (-not ((Test-Path '{ready}') -and (Test-Path '{attempting}'))) {{
        if ([DateTime]::UtcNow -gt $deadline) {{ throw 'peer acquisition never started' }}
        Start-Sleep -Milliseconds 20
    }}
    # A fixture probe in another process must be able to enter the engine build
    # while its caller waits for Uv; the caller performs acquisition before build.
    $freeEngine=Enter-IndexBuildLock -VenvPath $EngineVenv -TimeoutSeconds 0
    [void]$freeEngine.ReleaseMutex();$freeEngine.Dispose()
    [IO.File]::WriteAllText($path, ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))
    if ($env:OS -ne 'Windows_NT') {{ & chmod +x $path }}
    $version=& $path --version
    if ($LASTEXITCODE -ne 0 -or $version -ne 'uv-fixture-complete') {{ throw 'bootstrap incomplete' }}
    return $path
}}
function New-IndexVenv {{ param($Dir,$Python,$PythonCmd,$PreferSignedPython) return $true }}
function Test-IndexVenv {{ param($Dir,$Python) return $false }}
function Get-SignedBasePython {{ return $null }}
function Get-ActivationRole {{ return 'client' }}
function Resolve-Zdd {{ return '{package}' }}
function Resolve-VendoredLib {{ param($LibName) return $null }}
function Invoke-VersionedSlotClean {{ }}
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
function Invoke-IndexUvPipInstall {{
    $version=& $script:UvCommand --version
    if ($LASTEXITCODE -ne 0 -or $version -ne 'uv-fixture-complete') {{ throw 'consumer received partial uv' }}
    $global:LASTEXITCODE=0
}}
function fixture_python {{ $global:LASTEXITCODE=0 }}
"""
    worker = tmp_path / "engine.ps1"
    worker.write_text(script + f"""
$Actor='engine'
$deadline=[DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path '{started}')) {{
    if ([DateTime]::UtcNow -gt $deadline) {{ throw 'runtime writer did not start' }}
    Start-Sleep -Milliseconds 20
}}
$free=Enter-IndexBuildLock -VenvPath $EngineVenv -TimeoutSeconds 0
try {{ [IO.File]::WriteAllText('{ready}', 'ready') }} finally {{ [void]$free.ReleaseMutex();$free.Dispose() }}
$freeRuntime=Enter-IndexBuildLock -VenvPath $VenvDir -TimeoutSeconds 0
[void]$freeRuntime.ReleaseMutex();$freeRuntime.Dispose()
$freePublication=Enter-IndexStampLock -Scope Publish -TimeoutSeconds 0
[void]$freePublication.ReleaseMutex();$freePublication.Dispose()
$enter=${{function:Enter-IndexStampLock}}
function Enter-IndexStampLock {{
    param($Scope,$TimeoutSeconds=20,$LockRoot=$InstallDir)
    if ($Scope -eq 'Uv') {{ [IO.File]::WriteAllText('{attempting}', 'attempting') }}
    return (& $enter -Scope $Scope -TimeoutSeconds $TimeoutSeconds -LockRoot $LockRoot)
}}
if (-not (Install-Engine -Upgrade)) {{ throw 'engine fixture failed' }}
""")
    with subprocess.Popen([pwsh, "-NoProfile", "-File", str(worker)], env=environment(tmp_path),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        result = run_ps(tmp_path, script + "$Actor='runtime'\nInstall-Runtime\n")
        stdout, stderr = child.communicate(timeout=25)
    assert result.returncode == 0, result.stderr
    assert child.returncode == 0, stdout + stderr
    assert (tmp_path / "writers").read_text().splitlines() == ["runtime"]
    assert exe.read_bytes() == complete.encode()
