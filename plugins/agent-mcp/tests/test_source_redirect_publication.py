"""Source-only redirect publication through real native stamp callers."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "cli_redirect_native_fixture", Path(__file__).with_name("test_source_install_transactions.py"),
)
assert SPEC and SPEC.loader
native = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = native
SPEC.loader.exec_module(native)

pytestmark = pytest.mark.guard
ADOPTERS = (
    "agent-codespaces", "agent-containers", "agent-mcp", "agent-ssh",
    "agent-machines", "agent-pull-requests", "budget-guidance",
)


def _function(plugin: str, name: str) -> str:
    entry = "init" if plugin in {
        "agent-containers", "agent-mcp", "agent-machines",
    } else "install"
    text = (
        native.ROOT / "plugins" / plugin / "scripts" / f"{entry}.ps1"
    ).read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}", text)
    assert match, name
    return match.group().replace("$PSScriptRoot", "$ScriptDir")


def _stamp_attempt(
    tmp_path: Path, payload: Path, plugin: str, language: str, *,
    locked: bool = False, succeeds: bool = False,
) -> str:
    script = native._setup(tmp_path, payload, plugin, language)
    if language == "sh":
        return script + """
_initialize_source_runtime
if _stamp_source_snapshot; then exit 0; fi
exit 23
"""
    return script + f"""
$ServiceName = 'fixture'
$LocalBin = Join-Path $InstallDir 'bin'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-ServiceHeader($value) {{ }}
function Write-ServiceOk($value) {{ }}
function Write-Ok($value) {{ }}
function Deploy-SelfProvisioningBinstub {{
    if (-not ${str(locked or succeeds or plugin == 'agent-codespaces').lower()}) {{
        throw 'Unexpected binstub publication'
    }}
}}
function Materialize-SnapshotDependencies {{ throw 'Source snapshot was rematerialized' }}
{native._function(plugin, 'Invoke-Stamp', language)}
Initialize-SourceRuntime
$lock = $null
try {{
    if (${str(locked).lower()}) {{
        $lock = [IO.File]::Open((Join-Path $InstallDir 'stamped-version'),
            [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    }}
    Invoke-Stamp
    exit 0
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    if (${str(locked).lower()}) {{
        foreach ($marker in @('payload-dir', 'stamped-version', 'current-version')) {{
            if ([IO.File]::ReadAllText((Join-Path $InstallDir $marker)) -cne 'previous') {{
                throw "Marker changed while second marker was held: $marker"
            }}
        }}
        [IO.File]::WriteAllText({native._quote(tmp_path / 'locked-preserved', 'ps1')}, 'verified')
        $lock.Dispose()
        $lock = $null
        Invoke-Stamp
    }}
    exit 23
}} finally {{
    if ($lock) {{ $lock.Dispose() }}
}}
"""


def _instrument_runtime_cli(payload: Path, injection: str) -> str:
    """Seal the fixture CLI's injected bytes into its actual source receipt."""
    helper = payload / "scripts" / "versioned_runtime.py"
    text = helper.read_text(encoding="utf-8")
    future = "from __future__ import annotations"
    assert text.count(future) == 1
    helper.write_text(text.replace(future, future + "\n" + injection), encoding="utf-8")
    receipt = payload / "runtime-source.json"
    identity = json.loads(receipt.read_text(encoding="utf-8"))
    identity["assembly_hash"] = native.IDENTITY.assembly_hash(payload)
    identity["runtime_version"] = native.IDENTITY.runtime_version(
        native.VERSION, identity["source_commit"], identity["assembly_hash"],
    )
    receipt.write_text(json.dumps(identity), encoding="utf-8")
    return identity["runtime_version"]


def _fail_marker_replace(payload: Path, marker: str) -> None:
    """Fail a real replace in the fixture CLI, leaving rollback operational."""
    injection = f"""
import os as _fixture_os
import sys as _fixture_sys
_fixture_replace = _fixture_os.replace
_fixture_failed = False
def _fail_redirect_replace(source, destination, *args, **kwargs):
    global _fixture_failed
    if ('publish-redirects' in _fixture_sys.argv and not _fixture_failed
            and _fixture_os.path.basename(destination) == {marker!r}):
        _fixture_failed = True
        raise OSError('injected redirect replacement failure: {marker}')
    return _fixture_replace(source, destination, *args, **kwargs)
_fixture_os.replace = _fail_redirect_replace
"""
    _instrument_runtime_cli(payload, injection)


@pytest.mark.parametrize("plugin", native.PLUGINS)
@pytest.mark.parametrize("language", ["ps1", "sh"])
@pytest.mark.parametrize("marker", ["payload-dir", "stamped-version"])
def test_source_stamp_replace_failure_restores_prior_pair(
    tmp_path: Path, plugin: str, language: str, marker: str,
) -> None:
    payload, _ = native._payload(tmp_path, plugin)
    _fail_marker_replace(payload, marker)
    script = _stamp_attempt(tmp_path, payload, plugin, language)
    result = native._run(tmp_path, language, script)
    assert result.returncode == 23, result.stdout + result.stderr
    assert f"injected redirect replacement failure: {marker}" in result.stderr
    for name in ("payload-dir", "stamped-version", "current-version"):
        assert (tmp_path / "install" / name).read_bytes() == b"previous"


@pytest.mark.parametrize("plugin", native.PLUGINS)
@pytest.mark.parametrize("language", ["ps1", "sh"])
def test_original_source_change_after_redirect_commit_does_not_fail_stamp(
    tmp_path: Path, plugin: str, language: str,
) -> None:
    payload, _ = native._payload(tmp_path, plugin)
    original = payload / "src" / "hashed.txt"
    previous_bytes = original.read_bytes()
    selected = _instrument_runtime_cli(payload, f"""
import atexit as _fixture_atexit
import sys as _fixture_sys
from pathlib import Path as _fixture_Path
if 'publish-redirects' in _fixture_sys.argv:
    _fixture_atexit.register(
        lambda: _fixture_Path({str(original)!r}).write_bytes(b'changed after commit\\n')
    )
""")
    script = _stamp_attempt(tmp_path, payload, plugin, language, succeeds=True)
    result = native._run(tmp_path, language, script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert original.read_bytes() == b"changed after commit\n"
    root = tmp_path / "install"
    snapshot = root / "snapshots" / selected
    assert (snapshot / "src" / "hashed.txt").read_bytes() == previous_bytes
    assert native.IDENTITY.read_source_identity(
        snapshot, native.VERSION, plugin=plugin,
    ).runtime_version == selected
    assert (root / "payload-dir").read_text().strip() == str(snapshot)
    assert (root / "stamped-version").read_text().strip() == selected
    assert (root / "current-version").read_bytes() == b"previous"


@pytest.mark.skipif(os.name != "nt", reason="Real Windows destination sharing lock")
@pytest.mark.parametrize("plugin", native.PLUGINS)
def test_source_stamp_locked_second_marker_restores_prior_pair(
    tmp_path: Path, plugin: str,
) -> None:
    payload, selected = native._payload(tmp_path, plugin)
    script = _stamp_attempt(tmp_path, payload, plugin, "ps1", locked=True)
    result = native._run(tmp_path, "ps1", script)
    assert result.returncode == 23, result.stdout + result.stderr
    assert re.search(r"WinError (?:5|32)\]", result.stderr), result.stdout + result.stderr
    root = tmp_path / "install"
    assert repr(str(root / "stamped-version")) in result.stderr
    assert (tmp_path / "locked-preserved").read_text() == "verified"
    assert (root / "current-version").read_bytes() == b"previous"
    assert (root / "stamped-version").read_text().strip() == selected
    assert (root / "payload-dir").read_text().strip() == str(root / "snapshots" / selected)


@pytest.mark.parametrize("plugin", native.PLUGINS)
def test_legacy_stamp_does_not_acquire_bootstrap_python(
    tmp_path: Path, plugin: str,
) -> None:
    payload, _ = native._payload(tmp_path, plugin, source=False)
    script = native._setup(tmp_path, payload, plugin, "ps1") + f"""
$ServiceName = 'fixture'
$LocalBin = Join-Path $InstallDir 'bin'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Get-BootstrapPython {{ throw 'Legacy stamp acquired bootstrap Python' }}
function Write-ServiceHeader($value) {{ }}
function Write-ServiceOk($value) {{ }}
function Write-Ok($value) {{ }}
function Deploy-SelfProvisioningBinstub {{ }}
function Materialize-SnapshotDependencies {{ }}
{_function(plugin, 'Invoke-Stamp')}
Initialize-SourceRuntime
Invoke-Stamp
"""
    result = native._run(tmp_path, "ps1", script)
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "install"
    assert (root / "stamped-version").read_text() == native.VERSION
    assert (root / "payload-dir").read_text().strip() == str(
        root / "snapshots" / native.VERSION,
    )


@pytest.mark.parametrize("plugin", ADOPTERS)
def test_native_adopters_wire_source_redirects_without_replacing_legacy(
    plugin: str,
) -> None:
    scripts = native.ROOT / "plugins" / plugin / "scripts"
    entry = "init" if plugin in {
        "agent-containers", "agent-mcp", "agent-machines",
    } else "install"
    powershell = _function(plugin, "Invoke-Stamp")
    assert "'publish-redirects', '--payload-dir', $snapDir" in powershell
    assert "'--version', $SrcVersion" in powershell
    assert "'--expected-source-identity'," in powershell
    if plugin == "agent-ssh":
        assert '"stamped-version.$PID.tmp"' in powershell
    else:
        assert "WriteAllText((Join-Path $InstallDir 'stamped-version')" in powershell
    assert "Source redirect publication failed" in powershell
    shell = (
        scripts / ("source-runtime.sh" if plugin in native.PLUGINS else f"{entry}.sh")
    ).read_text(encoding="utf-8")
    assert 'publish-redirects --payload-dir "' in shell
    assert '--version "$SRC_VERSION"' in shell
    assert '--expected-source-identity "$SOURCE_RUNTIME_IDENTITY" || ' in shell
    if plugin == "agent-machines":
        assert "'--version', $SrcVersion, '--payload-origin', $probePayload" in powershell


def test_machines_fast_source_stamp_serializes_decision_and_omits_version() -> None:
    function = _function("agent-machines", "Invoke-StampBinstubOnly")
    lock = "if ($SourceRuntimeIdentity) { $stampMutex = Enter-StampLock }"
    assert function.index(lock) < function.index("$payloadDirAlreadyValid = $false")
    assert function.index("$existingIdentity = Get-SourceRuntimeIdentity") < function.index(
        "'publish-redirects'",
    )
    assert "$stampPayload = if ($payloadDirAlreadyValid) { $existingPayloadDir }" in function
    assert "'--payload-origin', $probePayload" in function
    assert "'--expected-source-identity', $SourceRuntimeIdentity" in function
    assert "'--version'" not in function
    assert "if ($stampMutex)" in function


@pytest.mark.parametrize("peer_changed", [False, True])
def test_machines_fast_source_waits_for_full_stamp_and_keeps_snapshot(
    tmp_path: Path, peer_changed: bool,
) -> None:
    host = native._host("ps1")
    payload, _ = native._payload(tmp_path, "agent-mcp")
    (payload / "scripts" / "init.ps1").write_text("# fallback\n", encoding="utf-8")
    receipt = payload / "runtime-source.json"
    identity = json.loads(receipt.read_text(encoding="utf-8"))
    identity["assembly_hash"] = native.IDENTITY.assembly_hash(payload)
    selected = native.IDENTITY.runtime_version(
        native.VERSION, identity["source_commit"], identity["assembly_hash"],
    )
    identity["runtime_version"] = selected
    receipt.write_text(json.dumps(identity), encoding="utf-8")
    source_pin = json.dumps(native.IDENTITY.manifest_fields(
        native.IDENTITY.read_source_identity(payload, native.VERSION, plugin="agent-mcp"),
    ), sort_keys=True)
    assert "b" * 40 in source_pin
    peer_pin = source_pin.replace("b" * 40, "c" * 40)
    root = tmp_path / "install"
    snapshot = root / "snapshots" / selected
    shutil.copytree(payload, snapshot)
    for marker in ("payload-dir", "payload-origin", "stamped-version"):
        (root / marker).write_text("previous", encoding="utf-8")

    def q(value: Path | str) -> str:
        return native._quote(value, "ps1")

    setup = f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$InstallDir = {q(root)}
$DefaultInstallDir = $InstallDir
$PluginDir = {q(payload)}
$probePayload = $PluginDir
$ScriptDir = Join-Path $PluginDir 'scripts'
$PSScriptRoot = $ScriptDir
$LocalBin = Join-Path $InstallDir 'bin'
$SrcVersion = {q(selected)}
$SourceRuntimeIdentity = {q(source_pin)}
$ObservedSourceIdentity = $SourceRuntimeIdentity
$DecisionReadPath = ''
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Assert-SourceRuntimeIdentity {{ param($Payload) }}
function Get-SourceRuntimeIdentity {{
    param($Payload, $ExpectedVersion)
    if ($DecisionReadPath) {{ [IO.File]::WriteAllText($DecisionReadPath, 'read') }}
    return $ObservedSourceIdentity
}}
function Get-BootstrapPython {{ return {q(sys.executable)} }}
function Write-Fail($value) {{ throw $value }}
function Write-Ok($value) {{ }}
function Write-Step($value) {{ }}
function Deploy-SelfProvisioningBinstub {{ }}
. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
{_function('agent-machines', 'Enter-StampLock')}
"""
    winner_script = tmp_path / "full.ps1"
    winner_script.write_text(setup + f"""
{_function('agent-machines', 'Invoke-Stamp')}
$outer = Enter-StampLock
try {{
    Invoke-Stamp
    [IO.File]::WriteAllText({q(tmp_path / 'published')}, 'ready')
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath {q(tmp_path / 'release')})) {{
        if ([DateTime]::UtcNow -ge $deadline) {{ throw 'Fixture release timed out' }}
        Start-Sleep -Milliseconds 20
    }}
}} finally {{
    [void]$outer.ReleaseMutex()
    $outer.Dispose()
}}
""", encoding="utf-8")
    fast_script = tmp_path / "fast.ps1"
    fast_script.write_text(setup + f"""
{_function('agent-machines', 'Invoke-StampBinstubOnly')}
$DecisionReadPath = {q(tmp_path / 'decision-read')}
$ObservedSourceIdentity = {q(peer_pin if peer_changed else source_pin)}
$actualEnterStampLock = ${{function:Enter-StampLock}}
function Enter-StampLock {{
    [IO.File]::WriteAllText({q(tmp_path / 'attempting')}, 'ready')
    & $actualEnterStampLock
}}
try {{ Invoke-StampBinstubOnly }} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 23
}}
""", encoding="utf-8")

    def spawn(script: Path) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [host, "-NoProfile", "-File", str(script)],
            cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def wait_for(path: Path, process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 10
        while not path.is_file() and time.monotonic() < deadline:
            assert process.poll() is None, process.communicate(timeout=5)
            time.sleep(0.02)
        assert path.is_file(), f"Missing fixture signal: {path.name}"

    winner = spawn(winner_script)
    fast = None
    try:
        wait_for(tmp_path / "published", winner)
        fast = spawn(fast_script)
        wait_for(tmp_path / "attempting", fast)
        assert fast.poll() is None
        assert not (tmp_path / "decision-read").exists()
        (tmp_path / "release").write_text("release", encoding="utf-8")
        winner_out = winner.communicate(timeout=20)
        fast_out = fast.communicate(timeout=20)
        assert winner.returncode == 0, winner_out
        if peer_changed:
            assert fast.returncode != 0, fast_out
            assert "refusing fast-stamp replacement" in "".join(fast_out)
        else:
            assert fast.returncode == 0, fast_out
    finally:
        (tmp_path / "release").write_text("release", encoding="utf-8")
        for process in (winner, fast):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert (root / "payload-dir").read_text().strip() == str(snapshot)
    assert (root / "payload-origin").read_text().strip() == str(payload)
    assert (root / "stamped-version").read_text().strip() == selected
