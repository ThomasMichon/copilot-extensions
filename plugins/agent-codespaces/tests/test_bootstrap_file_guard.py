"""Private stock-framework custody, not complete interpreter admission."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "libs/installer-engine/bootstrap-file-guard.ps1"
_CAPTURE_IDENTITY_HANDLE = r"""
$script:observedHandle=$null
$script:identityReader=${function:Get-BootstrapStockFileIdentity}
function Get-BootstrapStockFileIdentity {
    param($Handle, $Bindings)
    $script:observedHandle=$Handle
    & $script:identityReader -Handle $Handle -Bindings $Bindings
}
"""


def test_guard_is_not_an_enabled_bootstrap_or_compilation_path():
    text = HELPER.read_text(encoding="utf-8")
    for forbidden in ("Add-Type", "Reflection.Emit", "GetDelegateForFunctionPointer", "Process.Start"):
        assert forbidden not in text
    assert "Resolve-EvaluatorBootstrapPython" not in text
    for plugin in ("agent-codespaces", "agent-containers"):
        caller = ROOT / "plugins" / plugin / "scripts/runtime-gate.ps1"
        assert HELPER.name not in caller.read_text(encoding="utf-8")


@pytest.fixture(params=("powershell", "pwsh"))
def host(request: pytest.FixtureRequest) -> str:
    if os.name != "nt":
        pytest.skip("native Windows stock-framework guard")
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


def _run(host, tmp_path, body, *, success=True, environment=None):
    script = tmp_path / "guard-probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        ". $env:GUARD_HELPER\n" + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        env={**os.environ, "GUARD_HELPER": str(HELPER), **(environment or {})},
        **no_window_kwargs(),
    )
    if success:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        return json.loads(result.stdout)
    return result


@pytest.mark.parametrize("directory", (False, True))
def test_stock_guard_pins_identity_and_releases_the_same_handle(host, tmp_path, directory):
    path = tmp_path / "guard's \u00e9"
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"guard fixture")
    result = _run(host, tmp_path, r"""
$guard=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH -Directory:($env:IS_DIRECTORY -eq '1')
$handle=$guard.Handle
try {
    $same=Get-BootstrapStockFileIdentity -Handle $handle -Bindings (Get-BootstrapStockFileBindings)
    $writerBlocked=$false
    if ($env:IS_DIRECTORY -ne '1') {
        try {
            $writer=[IO.File]::Open($guard.Path, [IO.FileMode]::Open,
                [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
            $writer.Dispose()
        } catch [IO.IOException] {
            if (($_.Exception.HResult -band 0xffff) -ne 32) { throw }
            $writerBlocked=$true
        }
    }
    $renameBlocked=$false
    try {
        if ($env:IS_DIRECTORY -eq '1') {
            [IO.Directory]::Move($guard.Path, $guard.Path + '-moved')
        } else {
            [IO.File]::Move($guard.Path, $guard.Path + '-moved')
        }
    } catch [IO.IOException] {
        if (($_.Exception.HResult -band 0xffff) -ne 32) { throw }
        $renameBlocked=$true
    }
    $deleteBlocked=$false
    try {
        if ($env:IS_DIRECTORY -eq '1') {
            [IO.Directory]::Delete($guard.Path)
        } else {
            [IO.File]::Delete($guard.Path)
        }
    } catch [IO.IOException] {
        if (($_.Exception.HResult -band 0xffff) -ne 32) { throw }
        $deleteBlocked=$true
    }
    [pscustomobject]@{
        first=$guard.Identity
        second=$same
        closed=$handle.IsClosed
        invalid=$handle.IsInvalid
        writerBlocked=$writerBlocked
        renameBlocked=$renameBlocked
        deleteBlocked=$deleteBlocked
    } | ConvertTo-Json -Compress
} finally { $handle.Dispose() }
if (-not $handle.IsClosed) { throw 'Guard handle did not close.' }
if ($env:IS_DIRECTORY -eq '1') {
    [IO.Directory]::Move($env:FIXTURE_PATH, $env:FIXTURE_PATH + '-released')
    [IO.Directory]::Move($env:FIXTURE_PATH + '-released', $env:FIXTURE_PATH)
} else {
    $writer=[IO.File]::Open($env:FIXTURE_PATH, [IO.FileMode]::Open,
        [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    $writer.Dispose()
    [IO.File]::Move($env:FIXTURE_PATH, $env:FIXTURE_PATH + '-released')
    [IO.File]::Move($env:FIXTURE_PATH + '-released', $env:FIXTURE_PATH)
}
""", environment={"FIXTURE_PATH": str(path), "IS_DIRECTORY": str(int(directory))})
    assert result["first"] == result["second"]
    assert bool(result["first"]["Attributes"] & 0x10) is directory
    assert not result["first"]["Attributes"] & 0x400
    assert result["closed"] is False
    assert result["invalid"] is False
    assert result["renameBlocked"] is True
    assert result["deleteBlocked"] is True
    if not directory:
        assert result["writerBlocked"] is True
        with path.open("r+b"):
            pass


@pytest.mark.parametrize("directory", (False, True))
def test_stock_guard_refuses_wrong_kind_and_releases_failed_open(host, tmp_path, directory):
    path = tmp_path / "wrong-kind"
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"unchanged")
    result = _run(host, tmp_path, _CAPTURE_IDENTITY_HANDLE + r"""
$message=$null
try {
    $null=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH -Directory:($env:EXPECT_DIRECTORY -eq '1')
} catch { $message=$_.Exception.Message }
if ($message -cne 'Bootstrap file guard object kind does not match the requested kind.') {
    throw 'Wrong-kind refusal was lost.'
}
if ($null -eq $script:observedHandle -or -not $script:observedHandle.IsClosed) {
    throw 'Wrong-kind admission did not close its handle before returning.'
}
if ($env:EXPECT_DIRECTORY -eq '1') {
    [IO.File]::Move($env:FIXTURE_PATH, $env:FIXTURE_PATH + '-released')
    [IO.File]::Move($env:FIXTURE_PATH + '-released', $env:FIXTURE_PATH)
} else {
    [IO.Directory]::Move($env:FIXTURE_PATH, $env:FIXTURE_PATH + '-released')
    [IO.Directory]::Move($env:FIXTURE_PATH + '-released', $env:FIXTURE_PATH)
}
[pscustomobject]@{closed=$script:observedHandle.IsClosed} | ConvertTo-Json -Compress
""", environment={
        "FIXTURE_PATH": str(path), "EXPECT_DIRECTORY": str(int(not directory)),
    })
    assert result == {"closed": True}


@pytest.mark.parametrize("path", (
    "relative", r"\\server\share\file", r"C:\base\..\file", r"C:\base\file:stream",
    r"C:\base\file.", "C:\\base\\file ", "C:/base/file",
))
def test_stock_guard_refuses_nonordinary_path_before_native_open(host, tmp_path, path):
    result = _run(host, tmp_path, r"""
function Get-BootstrapStockFileBindings { throw 'Unfit path reached native bindings.' }
Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH
""", success=False, environment={"FIXTURE_PATH": path})
    assert result.returncode != 0
    assert "Unfit path reached native bindings" not in result.stderr
    assert "Bootstrap file guard" in result.stderr


def test_stock_guard_missing_file_is_error_not_unavailable_selection(host, tmp_path):
    result = _run(host, tmp_path, r"""
Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH
""", success=False, environment={"FIXTURE_PATH": str(tmp_path / "missing")})
    assert result.returncode != 0
    assert "Bootstrap file guard open failed" in result.stderr
    assert "unavailable" not in result.stdout


def test_stock_guard_refuses_existing_writer_without_retry(host, tmp_path):
    path = tmp_path / "existing-writer"
    path.write_bytes(b"untouched")
    result = _run(host, tmp_path, r"""
$writer=[IO.File]::Open($env:FIXTURE_PATH, [IO.FileMode]::Open,
    [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
try {
    Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH
} finally { $writer.Dispose() }
""", success=False, environment={"FIXTURE_PATH": str(path)})
    assert result.returncode != 0
    assert "Bootstrap file guard open failed" in result.stderr
    assert path.read_bytes() == b"untouched"


def test_stock_guard_releases_handle_when_identity_query_throws(host, tmp_path):
    path = tmp_path / "query-refusal"
    path.write_bytes(b"untouched")
    result = _run(host, tmp_path, r"""
$script:observedHandle=$null
function Get-BootstrapStockFileIdentity {
    param($Handle, $Bindings)
    $script:observedHandle=$Handle
    throw 'synthetic identity inspection refusal'
}
$message=$null
try { $null=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH }
catch { $message=$_.Exception.Message }
if ($message -cne 'synthetic identity inspection refusal') { throw 'Identity refusal was lost.' }
if ($null -eq $script:observedHandle) { throw 'Open did not reach identity inspection.' }
[pscustomobject]@{closed=$script:observedHandle.IsClosed} | ConvertTo-Json -Compress
""", environment={"FIXTURE_PATH": str(path)})
    assert result == {"closed": True}
    with path.open("r+b"):
        pass


def test_stock_guard_closed_handle_is_not_queried_or_reopened(host, tmp_path):
    path = tmp_path / "closed-guard"
    path.write_bytes(b"untouched")
    result = _run(host, tmp_path, r"""
$guard=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH
$guard.Handle.Dispose()
Get-BootstrapStockFileIdentity -Handle $guard.Handle -Bindings (Get-BootstrapStockFileBindings)
""", success=False, environment={"FIXTURE_PATH": str(path)})
    assert result.returncode != 0
    assert "DangerousAddRef" in result.stderr


def test_stock_guard_refuses_final_junction_without_opening_its_target(host, tmp_path):
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = tmp_path / "junction"
    creation = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert creation.returncode == 0, creation.stderr
    result = _run(host, tmp_path, _CAPTURE_IDENTITY_HANDLE + r"""
$message=$null
try { $null=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH -Directory }
catch { $message=$_.Exception.Message }
if ($message -cne 'Bootstrap file guard refuses a final reparse point.') {
    throw 'Reparse-point refusal was lost.'
}
if ($null -eq $script:observedHandle -or -not $script:observedHandle.IsClosed) {
    throw 'Reparse-point admission did not close its handle before returning.'
}
[pscustomobject]@{closed=$script:observedHandle.IsClosed} | ConvertTo-Json -Compress
""", environment={"FIXTURE_PATH": str(junction)})
    assert result == {"closed": True}
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_attribute_only_directory_handle_does_not_establish_custody(host, tmp_path):
    path = tmp_path / "metadata-only"
    path.mkdir()
    result = _run(host, tmp_path, r"""
$bindings=Get-BootstrapStockFileBindings
$arguments=if ($bindings.Core) {
    [object[]]@($env:FIXTURE_PATH, [int]0x80, [IO.FileShare]::Read, [IO.FileMode]::Open, [int]0x02200000)
} else {
    [object[]]@(
        $env:FIXTURE_PATH, [int]0x80, [int]1, [IntPtr]::Zero, [int]3, [int]0x02200000, [IntPtr]::Zero
    )
}
$handle=$bindings.Open.Invoke($null, $arguments)
try {
    if ($handle.IsInvalid) { throw 'Metadata-only control did not open.' }
    $before=Get-BootstrapStockFileIdentity -Handle $handle -Bindings $bindings
    [IO.Directory]::Move($env:FIXTURE_PATH, $env:FIXTURE_PATH + '-moved')
    if ([IO.Directory]::Exists($env:FIXTURE_PATH) -or
        -not [IO.Directory]::Exists($env:FIXTURE_PATH + '-moved')) {
        throw 'Metadata-only control did not observe the rename.'
    }
    $after=Get-BootstrapStockFileIdentity -Handle $handle -Bindings $bindings
    [IO.Directory]::Move($env:FIXTURE_PATH + '-moved', $env:FIXTURE_PATH)
    [pscustomobject]@{before=$before; after=$after; closed=$handle.IsClosed} | ConvertTo-Json -Compress
} finally { $handle.Dispose() }
""", environment={"FIXTURE_PATH": str(path)})
    assert result["before"] == result["after"]
    assert result["closed"] is False
    assert path.is_dir()


def test_stock_guard_dot_source_and_bindings_do_not_emit_or_compile(host, tmp_path):
    result = _run(host, tmp_path, r"""
function Add-Type { throw 'Compilation is forbidden.' }
$tokens=$null
$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:GUARD_HELPER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Guard helper must parse cleanly.' }
$forbidden=@($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst] -and
    ($node.GetCommandName() -in @('Add-Type', 'Invoke-Expression', 'Start-Process', 'Get-Command') -or
     $node.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown)
}, $true))
$bindings=Get-BootstrapStockFileBindings
[pscustomobject]@{forbidden=$forbidden.Count; open=$bindings.Open.Name} | ConvertTo-Json -Compress
""")
    assert result == {"forbidden": 0, "open": "CreateFile"}


@pytest.fixture
def core() -> str:
    if os.name != "nt":
        pytest.skip("native Windows Core file bindings")
    core = shutil.which("pwsh")
    if not core:
        pytest.skip("PowerShell Core is unavailable; parity remains unqualified")
    return core


@pytest.mark.parametrize("directory", (False, True))
def test_core_final_path_uses_same_guard_handle_and_releases_reference(
    core: str, tmp_path: Path, directory: bool,
) -> None:
    path = tmp_path / "final's \u00e9"
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"untouched")
    result = _run(core, tmp_path, r"""
$guard=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH -Directory:($env:IS_DIRECTORY -eq '1')
$bindings=Get-BootstrapStockFileBindings
$script:final=$bindings.FinalPath
$script:info=$bindings.Information
$script:expected=$guard.Handle
$script:calls=0
$proxy=[pscustomobject]@{}
$proxy | Add-Member ScriptMethod Invoke {
    param($target, $arguments)
    if (-not [object]::ReferenceEquals($script:expected, $arguments[0]) -or $arguments[0].IsClosed) {
        throw 'Final path did not receive the exact live guard handle.'
    }
    if ($arguments[1].Length -ne 32768) { throw 'Final path buffer is not bounded.' }
    $script:calls++
    return $script:final.Invoke($target,$arguments)
}
$bindings.FinalPath=$proxy
$infoProxy=[pscustomobject]@{}
$infoProxy | Add-Member ScriptMethod Invoke {
    param($target, $arguments)
    if (-not [object]::ReferenceEquals($script:expected, $arguments[0]) -or $arguments[0].IsClosed) {
        throw 'Identity did not receive the exact live guard handle.'
    }
    return $script:info.Invoke($target,$arguments)
}
$bindings.Information=$infoProxy
try {
    $path=Get-BootstrapStockFinalPath -Handle $guard.Handle -Bindings $bindings
    if (-not [string]::Equals($path, '\\?\' + $env:FIXTURE_PATH, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Final path did not name the held object.'
    }
    $identity=Get-BootstrapStockFileIdentity -Handle $guard.Handle -Bindings $bindings
    [pscustomobject]@{sameIdentity=($identity.Volume -eq $guard.Identity.Volume -and
        $identity.IndexHigh -eq $guard.Identity.IndexHigh -and $identity.IndexLow -eq $guard.Identity.IndexLow)
        open=(-not $guard.Handle.IsClosed);calls=$script:calls} | ConvertTo-Json -Compress
} finally { $guard.Handle.Dispose() }
if (-not $guard.Handle.IsClosed) { throw 'Final path leaked its retained handle reference.' }
""", environment={"FIXTURE_PATH": str(path), "IS_DIRECTORY": str(int(directory))})
    assert result == {"sameIdentity": True, "open": True, "calls": 1}


@pytest.mark.parametrize("failure", ("zero", "capacity", "terminator", "embedded", "throw"))
def test_core_final_path_errors_emit_no_path_and_release_reference(
    core: str, tmp_path: Path, failure: str,
) -> None:
    path = tmp_path / "error"
    path.write_bytes(b"untouched")
    result = _run(core, tmp_path, r"""
$guard=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH
$bindings=Get-BootstrapStockFileBindings
$proxy=[pscustomobject]@{}
$proxy | Add-Member ScriptMethod Invoke {
    param($target, $arguments)
    switch ($env:FAILURE) {
        'zero' { [Runtime.InteropServices.Marshal]::SetLastPInvokeError(6); return [uint32]0 }
        'capacity' { return [uint32]32768 }
        'terminator' { $arguments[1][1]=[char]65; return [uint32]1 }
        'embedded' { return [uint32]2 }
        'throw' { throw 'synthetic final-path refusal' }
    }
}
$bindings.FinalPath=$proxy
$script:emitted=0
$message=$null
try {
    Get-BootstrapStockFinalPath -Handle $guard.Handle -Bindings $bindings | ForEach-Object {$script:emitted++}
} catch { $message=$_.Exception.Message }
$guard.Handle.Dispose()
if (-not $guard.Handle.IsClosed) { throw 'Final-path failure leaked its retained handle reference.' }
if (-not $message) { throw 'Final-path failure was lost.' }
[pscustomobject]@{closed=$guard.Handle.IsClosed;emitted=$script:emitted;message=$message} | ConvertTo-Json -Compress
""", environment={"FIXTURE_PATH": str(path), "FAILURE": failure})
    assert result["closed"] is True
    assert result["emitted"] == 0
    assert result["message"]


def test_core_final_path_closed_handle_does_not_invoke_binding(core: str, tmp_path: Path) -> None:
    path = tmp_path / "closed"
    path.write_bytes(b"untouched")
    result = _run(core, tmp_path, r"""
$guard=Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH
$bindings=Get-BootstrapStockFileBindings
$proxy=[pscustomobject]@{}
$proxy | Add-Member ScriptMethod Invoke { throw 'Closed handle reached final path.' }
$bindings.FinalPath=$proxy
$guard.Handle.Dispose()
Get-BootstrapStockFinalPath -Handle $guard.Handle -Bindings $bindings
""", success=False, environment={"FIXTURE_PATH": str(path)})
    assert result.returncode != 0
    assert "DangerousAddRef" in result.stderr
    assert "Closed handle reached final path" not in result.stderr
