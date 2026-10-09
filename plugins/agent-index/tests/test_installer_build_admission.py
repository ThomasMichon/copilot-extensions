"""Direct build admission and non-elevated mutex creation contracts."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
import pytest

if os.name != "nt":
    import fcntl

from test_install_engine_adoption import environment, function, run_ps
from test_installer_publication_and_health import publication_fixture


@pytest.mark.parametrize("denied", [False, True])
def test_windows_mutex_global_or_explicit_local_fallback_is_reentrant(tmp_path, denied):
    result = run_ps(tmp_path, f"""
$env:OS = 'Windows_NT'
$InstallDir = '{tmp_path / "runtime"}'
$factory = ${{function:New-IndexMutex}}
function Write-Warn {{ param($Msg) [Console]::Error.WriteLine($Msg) }}
function New-IndexMutex {{
    param($Name)
    Add-Content '{tmp_path / "names"}' $Name
    if (${str(denied).lower()} -and $Name.StartsWith('Global\\')) {{
        throw [UnauthorizedAccessException]::new('fixture Global creation denied')
    }}
    return (& $factory -Name $Name)
}}
$first = Enter-IndexStampLock -Scope Publish
try {{
    $InstallDir += [IO.Path]::DirectorySeparatorChar
    $second = Enter-IndexStampLock -Scope Publish -TimeoutSeconds 0
    try {{ 'reentrant' }} finally {{ [void]$second.ReleaseMutex(); $second.Dispose() }}
}} finally {{ [void]$first.ReleaseMutex(); $first.Dispose() }}
""")
    assert result.returncode == 0, result.stderr
    names = (tmp_path / "names").read_text().splitlines()
    assert all(name.startswith("Global\\") for name in names) if not denied else (
        names[0].startswith("Global\\") and names[1].startswith("Local\\")
        and names[2:] == names[:2]
    )
    assert "cross-session serialization is unavailable" in result.stderr if denied else not result.stderr


def test_unrelated_mutex_creation_error_does_not_fall_back(tmp_path):
    result = run_ps(tmp_path, f"""
$env:OS = 'Windows_NT'
$InstallDir = '{tmp_path / "runtime"}'
function New-IndexMutex {{
    param($Name)
    if ($Name.StartsWith('Local\\')) {{ throw 'unexpected Local fallback' }}
    throw [IO.IOException]::new('fixture unrelated creation failure')
}}
try {{ Enter-IndexStampLock -Scope Publish; throw 'unexpected admission' }}
catch [IO.IOException] {{ $_.Exception.Message }}
""")
    assert result.returncode == 0, result.stderr
    assert "fixture unrelated creation failure" in result.stdout


@pytest.mark.parametrize("outcome", ["published", "failed", "superseded"])
def test_direct_same_version_installs_serialize_cleanup_build_and_publication(tmp_path, outcome):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    definitions, root = publication_fixture(tmp_path)
    started = tmp_path / "started"
    ready = tmp_path / "worker-ready"
    blocked = tmp_path / "blocked"
    attempted = tmp_path / "attempted"
    finished = tmp_path / "finished"
    owner = root / "versions/2.0.0/owner"
    pkg = tmp_path / "package"
    (pkg / "src").mkdir(parents=True)
    definitions += function("Install-Runtime", "ps1") + function("Install-ServerVenv", "ps1") + f"""
$env:AGENT_INDEX_REBUILD_CURRENT = '1'
$BasePython = '{sys.executable}'
$PkgSrcDir = '{pkg / "src"}'
$PluginDir = '{pkg}'
$LocalBin = '{tmp_path / "bin"}'
$LinkDir = $VenvDir
$LinkPython = $VenvPython
function Ensure-Uv {{ param($InstallRoot) return 'fixture-uv' }}
function Write-Warn {{ param($Msg) [Console]::Error.WriteLine($Msg) }}
function uv {{ throw 'forbidden raw uv' }}
function Get-Command {{
    [CmdletBinding()]param([string]$Name)
    if ($Name -in @('python','python3','py')) {{ return [pscustomobject]@{{ Source=$BasePython }} }}
    if ($Name -eq 'uv') {{ return [pscustomobject]@{{ Source='fixture-uv' }} }}
    throw "unexpected executable lookup: $Name"
}}
function Get-SignedBasePython {{ return $null }}
function Get-ActivationRole {{ return 'client' }}
function Resolve-Zdd {{ return '{pkg}' }}
function Resolve-VendoredLib {{ param($LibName) return $null }}
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
function Get-VersionedCurrent {{ return '' }}
function Invoke-VersionedSlotClean {{ }}
function Invoke-VersionedGc {{ param($KeepPrev) }}
function Invoke-VersionedMarkComplete {{ Add-Content '{tmp_path / "events"}' "health-$Actor" }}
function New-IndexVenv {{
    param($Dir, $Python, $PythonCmd)
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    [IO.File]::WriteAllText('{owner}', $Actor)
    Add-Content '{tmp_path / "events"}' "repair-$Actor"
    if ($Actor -eq 'first') {{
        [IO.File]::WriteAllText('{started}', 'ready')
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while (-not ((Test-Path '{blocked}') -and (Test-Path '{attempted}'))) {{
            if ([DateTime]::UtcNow -gt $deadline) {{ throw 'second installer never attempted admission' }}
            Start-Sleep -Milliseconds 20
        }}
        if ([IO.File]::ReadAllText('{blocked}') -ne 'blocked') {{ throw 'same-version build overlapped' }}
        if ([IO.File]::ReadAllText('{owner}') -ne 'first') {{ throw 'second cleanup corrupted the admitted build' }}
        if ('{outcome}' -eq 'failed') {{ throw 'fixture build failure' }}
    }}
    return $true
}}
function Invoke-IndexUvPipInstall {{
    Add-Content '{tmp_path / "events"}' "package-$Actor"
    if ([IO.File]::ReadAllText('{owner}') -ne $Actor) {{ throw 'build ownership lost during packages' }}
    $global:LASTEXITCODE = 0
    return ''
}}
"""
    worker = tmp_path / "second.ps1"
    worker.write_text(definitions + f"""
$Actor = 'second'
[IO.File]::WriteAllText('{ready}', 'ready')
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while (-not (Test-Path '{started}')) {{
    if ([DateTime]::UtcNow -gt $deadline) {{ throw 'first installer did not start' }}
    Start-Sleep -Milliseconds 20
}}
try {{
    $probe = Enter-IndexBuildLock -VenvPath $VenvDir -TimeoutSeconds 0
    [void]$probe.ReleaseMutex(); $probe.Dispose()
    [IO.File]::WriteAllText('{blocked}', 'unserialized')
}} catch {{
    if ($_.Exception.Message -notlike '*Timed out waiting*') {{ throw }}
    [IO.File]::WriteAllText('{blocked}', 'blocked')
}}
$otherVersion = Enter-IndexBuildLock -VenvPath (Join-Path (Split-Path $VenvDir) '3.0.0') -TimeoutSeconds 0
try {{ 'independent-version' }} finally {{ [void]$otherVersion.ReleaseMutex(); $otherVersion.Dispose() }}
if ('{outcome}' -eq 'superseded') {{
    $publication = Enter-IndexStampLock -Scope Publish
    try {{ Publish-FileAtomically -Path (Join-Path $InstallDir 'stamped-version') -Content '3.0.0' }}
    finally {{ [void]$publication.ReleaseMutex(); $publication.Dispose() }}
}}
$admit = ${{function:Enter-IndexBuildLock}}
function Enter-IndexBuildLock {{
    param($VenvPath)
    [IO.File]::WriteAllText('{attempted}', 'attempted')
    return (& $admit -VenvPath $VenvPath)
}}
Install-Runtime
[IO.File]::WriteAllText('{finished}', 'finished')
""", encoding="utf-8")
    with subprocess.Popen(
        [pwsh, "-NoProfile", "-File", str(worker)], env=environment(tmp_path),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as child:
        deadline = time.monotonic() + 30
        while not ready.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate(timeout=5)
                pytest.fail(f"admission worker exited before readiness:\n{stdout}\n{stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("admission worker did not finish startup within 30 seconds")
            time.sleep(0.02)
        result = run_ps(tmp_path, definitions + f"""
$Actor = 'first'
$expectedFailure = $false
try {{ Install-Runtime }} catch {{
    if ('{outcome}' -ne 'failed' -or $_.Exception.Message -notlike '*fixture build failure*') {{ throw }}
    $expectedFailure = $true
}}
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path '{finished}')) {{
    if ([DateTime]::UtcNow -gt $deadline) {{ throw 'build guard leaked until process exit' }}
    Start-Sleep -Milliseconds 20
}}
if ($expectedFailure) {{ exit 1 }}
""")
        stdout, stderr = child.communicate(timeout=25)
    assert result.returncode == (1 if outcome == "failed" else 0), (
        f"first process:\n{result.stdout}\n{result.stderr}\n"
        f"second process ({child.returncode}):\n{stdout}\n{stderr}"
    )
    assert child.returncode == 0, stdout + stderr
    events = (tmp_path / "events").read_text().splitlines()
    if outcome == "superseded":
        assert "repair-second" not in events
        assert (root / "stamped-version").read_text() == "3.0.0"
        assert not (root / "launcher-version").exists()
        assert owner.read_text() == "first"
    else:
        assert events.index("repair-first") < events.index("repair-second")
        if outcome == "published":
            assert events.index("health-first") < events.index("repair-second")
        assert (root / "current-version").read_text() == "2.0.0"
    # A return, failed build, or superseded admission must not leak the guard.
    result = run_ps(tmp_path, definitions + """
$lock = Enter-IndexBuildLock -VenvPath $VenvDir -TimeoutSeconds 0
try { 'released' } finally { [void]$lock.ReleaseMutex(); $lock.Dispose() }
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fallback", [False, True])
def test_posix_build_timeout_and_failure_release(tmp_path, fallback):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    script = f"""
{function("_with_index_build_lock", "sh")}
{function("_with_index_advisory_lock", "sh")}
{function("_bootstrap_python", "sh")}
{function("_find_python", "sh")}
LINK_DIR='{tmp_path / "missing-bootstrap-slot"}'
_warn() {{ echo "$*" >&2; }}
export COPILOT_EXT_NO_FLOCK={1 if fallback else 0}
target='{tmp_path / "versions/1.0.0"}'
fail_build() {{ return 37; }}
_with_index_build_lock "$target" fail_build
[[ $? == 37 ]] || exit 97
[[ ! -L '{tmp_path / "versions/.1.0.0.build.lock.pid"}' ]] || exit 97
success_build() {{ echo released; }}
_with_index_build_lock "$target" success_build
"""
    result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "released" in result.stdout
    parent = tmp_path / "versions"
    # Legacy PID evidence is ignored; the inode is never replaced or unlinked.
    legacy = parent / ".1.0.0.build.lock.pid"
    legacy.symlink_to("2147483647")
    lock = parent / ".1.0.0.build.lock"
    inode = lock.stat().st_ino
    with lock.open("r+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        result = subprocess.run([bash, "-c", script.split("fail_build()", 1)[0] + """
INDEX_BUILD_LOCK_TIMEOUT_SECONDS=0
never() { echo forbidden; }
_with_index_build_lock "$target" never
"""], env=environment(tmp_path), capture_output=True, text=True, timeout=20)
        assert result.returncode == 1
        assert "Timed out waiting" in result.stderr
        assert "forbidden" not in result.stdout
    result = subprocess.run([bash, "-c", script.replace(
        f"[[ ! -L '{legacy}' ]] || exit 97", ":",
    )], env=environment(tmp_path), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert legacy.is_symlink() and os.readlink(legacy) == "2147483647"
    assert lock.stat().st_ino == inode


@pytest.mark.parametrize("fallback", [False, True])
def test_admission_unlocks_shared_descriptor_while_descendant_is_alive(tmp_path, fallback):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    ready, stop, done = (tmp_path / name for name in ("ready", "stop", "done"))
    child_code = (
        "import os, pathlib, time\n"
        "os.fstat(8)\n"
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 10\n"
        f"while not pathlib.Path({str(stop)!r}).exists() and time.monotonic() < deadline:\n"
        "    os.fstat(8)\n"
        "    time.sleep(0.02)\n"
        f"pathlib.Path({str(done)!r}).write_text('done')\n"
    )
    prelude = f"""
{function("_with_index_build_lock", "sh")}
{function("_with_index_advisory_lock", "sh")}
{function("_bootstrap_python", "sh")}
{function("_find_python", "sh")}
LINK_DIR='{tmp_path / "missing-bootstrap-slot"}'
_warn() {{ echo "$*" >&2; }}
export COPILOT_EXT_NO_FLOCK={1 if fallback else 0}
target='{tmp_path / "versions/1.0.0"}'
"""
    try:
        result = subprocess.run([bash, "-c", prelude + f"""
spawn_holder() {{
    '{sys.executable}' -I -c {shlex.quote(child_code)} >'{tmp_path / "child.log"}' 2>&1 &
    deadline=$((SECONDS + 5))
    while [[ ! -f '{ready}' ]]; do
        ((SECONDS < deadline)) || return 97
        sleep 0.02
    done
}}
_with_index_build_lock "$target" spawn_holder
"""], env=environment(tmp_path), capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert ready.exists(), (tmp_path / "child.log").read_text()
        os.kill(int(ready.read_text()), 0)
        assert not done.exists()
        result = subprocess.run([bash, "-c", prelude + """
INDEX_BUILD_LOCK_TIMEOUT_SECONDS=0
admitted() { echo reacquired; }
_with_index_build_lock "$target" admitted
"""], env=environment(tmp_path), capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert "reacquired" in result.stdout
        assert not done.exists()
    finally:
        stop.write_text("stop")
        deadline = time.monotonic() + 10
        while ready.exists() and not done.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    assert not ready.exists() or done.exists(), "harmless descendant did not finish"
