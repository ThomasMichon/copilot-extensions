"""Stock held-file WinTrust qualification, not interpreter/source admission."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "libs/installer-engine/bootstrap-file-trust.ps1"
GUARD = ROOT / "libs/installer-engine/bootstrap-file-guard.ps1"
_OBSERVE = r"""
$script:bindings=Get-BootstrapStockTrustBindings
$script:verify=$script:bindings.Verify
$script:load=$script:bindings.Load
$script:free=$script:bindings.Free
$script:loads=0
$script:frees=0
$script:ownedModule=[IntPtr]::Zero
$loader=[pscustomobject]@{}
$loader | Add-Member ScriptMethod Invoke {
    param($target, $arguments)
    $expected=[IO.Path]::Combine([Environment]::GetFolderPath([Environment+SpecialFolder]::System),'wintrust.dll')
    if ($arguments.Count -ne 3 -or $arguments[0] -cne $expected -or
        $arguments[1] -ne [IntPtr]::Zero -or $arguments[2] -ne 0x800 -or
        ($script:bindings.Core -and $arguments[2] -isnot [int]) -or
        (-not $script:bindings.Core -and $arguments[2] -isnot [uint32])) {
        throw 'Loader did not receive the exact System32 path and explicit dependency-search flags.'
    }
    $script:loads++
    $script:ownedModule=$script:load.Invoke($target,$arguments)
    return $script:ownedModule
}
$freer=[pscustomobject]@{}
$freer | Add-Member ScriptMethod Invoke {
    param($target, $arguments)
    if ($arguments.Count -ne 1 -or $arguments[0] -ne $script:ownedModule -or
        $script:ownedModule -eq [IntPtr]::Zero) { throw 'Free did not receive the owned library reference.' }
    $result=$script:free.Invoke($target,$arguments)
    if ($result) { $script:frees++; $script:ownedModule=[IntPtr]::Zero }
    return $result
}
$script:bindings.Load=$loader
$script:bindings.Free=$freer
$script:observed=[Collections.Generic.List[object]]::new()
$script:releases=0
$script:closedWhileGuardOpen=$false
$script:closeLibrary=${function:Close-BootstrapStockTrustLibrary}
function Close-BootstrapStockTrustLibrary {
    param($Bindings, [IntPtr]$Module)
    & $script:closeLibrary -Bindings $Bindings -Module $Module
    $script:releases++
    $script:closedWhileGuardOpen=-not $script:guard.Handle.IsClosed
    if ($env:SYNTHETIC_LIBRARY_FREE_FAILURE -eq '1') { throw 'synthetic library release failure' }
}
$proxy=[pscustomobject]@{}
$proxy | Add-Member ScriptMethod Invoke {
    param($target, $arguments)
    $owned=[IntPtr]::Zero
    if ($script:bindings.Core) {
        $owned=[Runtime.InteropServices.Marshal]::AllocHGlobal(80)
        $marshal=[Runtime.InteropServices.Marshal].GetMethod('StructureToPtr',
            [type[]]@([object],[IntPtr],[bool]))
        $data=$owned
    } else { $data=$arguments[2] }
    try {
    if ($owned -ne [IntPtr]::Zero) {
        $null=$marshal.Invoke($null,[object[]]@($arguments[2],$data,$false))
    }
    $file=[Runtime.InteropServices.Marshal]::ReadIntPtr($data,40)
    $observation=[pscustomobject]@{
        action=[Runtime.InteropServices.Marshal]::ReadInt32($data,48)
        flags=[Runtime.InteropServices.Marshal]::ReadInt32($data,72)
        ui=[Runtime.InteropServices.Marshal]::ReadInt32($data,24)
        choice=[Runtime.InteropServices.Marshal]::ReadInt32($data,32)
        size=[Runtime.InteropServices.Marshal]::ReadInt32($data,0)
        handle=[Runtime.InteropServices.Marshal]::ReadIntPtr($file,16).ToInt64()
        guardOpen=(-not $script:guard.Handle.IsClosed)
        stateAllocated=$false
    }
    $script:observed.Add($observation)
    $result=$script:verify.Invoke($target,$arguments)
    if ($owned -ne [IntPtr]::Zero) {
        $null=$marshal.Invoke($null,[object[]]@($arguments[2],$data,$false))
    }
    $observation.stateAllocated=[Runtime.InteropServices.Marshal]::ReadIntPtr($data,56) -ne [IntPtr]::Zero
    if ($env:SYNTHETIC_VERIFY_FAILURE -eq '1' -and $observation.action -eq 1) {
        throw 'synthetic failure after native verification'
    }
    if ($env:SYNTHETIC_CLOSE_FAILURE -eq '1' -and
        [Runtime.InteropServices.Marshal]::ReadInt32($data,48) -eq 2) { return [uint32]5 }
    return $result
    } finally {
        if ($owned -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::FreeHGlobal($owned) }
    }
}
$script:bindings.Verify=$proxy
function Get-BootstrapStockTrustBindings { return $script:bindings }
"""


def test_trust_is_not_an_enabled_bootstrap_or_compilation_path():
    text = HELPER.read_text(encoding="utf-8")
    for forbidden in (
        "Add-Type", "Reflection.Emit", "GetDelegateForFunctionPointer", "Process.Start",
        "NativeLibrary]::Load",
    ):
        assert forbidden not in text
    assert "Resolve-EvaluatorBootstrapPython" not in text
    for plugin in ("agent-codespaces", "agent-containers"):
        caller = ROOT / "plugins" / plugin / "scripts/runtime-gate.ps1"
        assert HELPER.name not in caller.read_text(encoding="utf-8")


@pytest.fixture(params=("powershell", "pwsh"))
def host(request: pytest.FixtureRequest) -> str:
    if os.name != "nt":
        pytest.skip("native Windows stock WinTrust")
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


@pytest.fixture
def signed_file(tmp_path):
    source = os.environ.get("EVALUATOR_BOOTSTRAP_TEST_PYTHON", sys._base_executable)
    path = tmp_path / "signed's \u00e9.exe"
    shutil.copyfile(source, path)
    return path


def _run(host, tmp_path, body, *, environment=None, success=True):
    script = tmp_path / "trust-probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        ". $env:GUARD_HELPER\n. $env:TRUST_HELPER\n" + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        env={**os.environ, "GUARD_HELPER": str(GUARD), "TRUST_HELPER": str(HELPER),
             **(environment or {})},
        **no_window_kwargs(),
    )
    if success:
        assert result.returncode == 0, result.stderr
        assert not result.stderr
        return json.loads(result.stdout)
    return result


@pytest.mark.parametrize("kind", ("signed", "unsigned", "tampered", "other-path", "other-handle"))
def test_actual_held_file_trust_and_state_lifetime(host, tmp_path, signed_file, kind):
    unsigned = tmp_path / "unsigned.exe"
    unsigned.write_bytes(b"not a signed PE image")
    path = signed_file
    reported_path = signed_file
    if kind == "unsigned":
        path = reported_path = unsigned
    elif kind == "tampered":
        data = bytearray(signed_file.read_bytes())
        data[512] ^= 1
        signed_file.write_bytes(data)
    elif kind == "other-path":
        reported_path = unsigned
    elif kind == "other-handle":
        path = unsigned
    result = _run(host, tmp_path, _OBSERVE + r"""
$script:guard=Open-BootstrapStockFileGuard -Path $env:FILE_PATH
$script:guard.Path=$env:REPORTED_PATH
try {
    $expected=$script:guard.Handle.DangerousGetHandle().ToInt64()
    for ($i=0;$i -lt [int]$env:REPEATS;$i++) {
        $trust=Get-BootstrapStockFileTrust -Guard $script:guard | ForEach-Object {
            if ($script:releases -ne ($i+1)) { throw 'Trust result was published before cleanup.' }
            $_
        }
    }
    [pscustomobject]@{
        status=$trust.Status;publisher=$trust.Publisher
        calls=@($script:observed.ToArray());expectedHandle=$expected
        releases=$script:releases;closedWhileGuardOpen=$script:closedWhileGuardOpen
        guardClosed=$script:guard.Handle.IsClosed
        loads=$script:loads;frees=$script:frees;ownedModule=$script:ownedModule.ToInt64()
    } | ConvertTo-Json -Depth 5 -Compress
} finally { $script:guard.Handle.Dispose() }
if (-not $script:guard.Handle.IsClosed) { throw 'Caller guard did not close.' }
""", environment={
        "FILE_PATH": str(path), "REPORTED_PATH": str(reported_path),
        "REPEATS": str(2 if kind == "signed" else 1),
    })
    if kind in ("signed", "other-path"):
        assert result["status"] == 0
        assert result["publisher"] == "Python Software Foundation"
    else:
        assert result["status"] != 0
        assert result["publisher"] is None
    repeats = 2 if kind == "signed" else 1
    verifies = [call for call in result["calls"] if call["action"] == 1]
    assert len(verifies) == repeats
    if kind in ("signed", "other-path"):
        assert all(call["stateAllocated"] for call in verifies)
    expected_actions = []
    for call in verifies:
        expected_actions.extend([1, 2] if call["stateAllocated"] else [1])
    assert [call["action"] for call in result["calls"]] == expected_actions
    for call in result["calls"]:
        assert call == {
            "action": call["action"], "flags": 0x1000, "ui": 2, "choice": 1, "size": 80,
            "handle": result["expectedHandle"], "guardOpen": True,
            "stateAllocated": call["stateAllocated"],
        }
    assert result["releases"] == repeats
    assert result["closedWhileGuardOpen"] is True
    assert result["guardClosed"] is False
    assert result["loads"] == result["frees"] == repeats
    assert result["ownedModule"] == 0
    with path.open("r+b"):
        pass


@pytest.mark.parametrize("failure", ("provider", "close", "verify", "library"))
def test_provider_failure_still_closes_state_and_library_before_return(
    host, tmp_path, signed_file, failure,
):
    result = _run(host, tmp_path, _OBSERVE + r"""
$script:guard=Open-BootstrapStockFileGuard -Path $env:FILE_PATH
if ($env:FAILURE -eq 'provider') {
    $proxy=[pscustomobject]@{}
    $proxy | Add-Member ScriptMethod Invoke { throw 'synthetic provider inspection failure' }
    $script:bindings.Provider=$proxy
}
try {
    $message=$null
    $script:emitted=0
    try { Get-BootstrapStockFileTrust -Guard $script:guard | ForEach-Object { $script:emitted++ } }
    catch { $message=$_.Exception.Message }
    if (-not $message) { throw 'Trust failure returned success.' }
    [pscustomobject]@{
        message=$message;calls=@($script:observed.ToArray())
        releases=$script:releases;closedWhileGuardOpen=$script:closedWhileGuardOpen
        guardClosed=$script:guard.Handle.IsClosed
        emitted=$script:emitted
        loads=$script:loads;frees=$script:frees;ownedModule=$script:ownedModule.ToInt64()
    } | ConvertTo-Json -Depth 5 -Compress
} finally { $script:guard.Handle.Dispose() }
if (-not $script:guard.Handle.IsClosed) { throw 'Failure path retained the caller guard reference.' }
""", environment={
        "FILE_PATH": str(signed_file), "FAILURE": failure,
        "SYNTHETIC_CLOSE_FAILURE": str(int(failure == "close")),
        "SYNTHETIC_VERIFY_FAILURE": str(int(failure == "verify")),
        "SYNTHETIC_LIBRARY_FREE_FAILURE": str(int(failure == "library")),
    })
    if failure == "provider":
        assert "synthetic provider inspection failure" in result["message"]
    elif failure == "close":
        assert "Bootstrap signer state close failed: 0x00000005." in result["message"]
    elif failure == "verify":
        assert "synthetic failure after native verification" in result["message"]
    else:
        assert "synthetic library release failure" in result["message"]
    assert [call["action"] for call in result["calls"]] == [1, 2]
    assert result["releases"] == 1
    assert result["closedWhileGuardOpen"] is True
    assert result["guardClosed"] is False
    assert result["emitted"] == 0
    assert result["loads"] == result["frees"] == 1
    assert result["ownedModule"] == 0


@pytest.mark.parametrize("kind", ("closed", "directory"))
def test_unfit_guard_refuses_before_library_load(host, tmp_path, kind):
    path = tmp_path / "guard"
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"fixture")
    result = _run(host, tmp_path, r"""
$guard=Open-BootstrapStockFileGuard -Path $env:FILE_PATH -Directory:($env:KIND -eq 'directory')
try {
    if ($env:KIND -eq 'closed') { $guard.Handle.Dispose() }
    function Open-BootstrapStockTrustLibrary { throw 'Unfit guard reached native load.' }
    Get-BootstrapStockFileTrust -Guard $guard
} finally { $guard.Handle.Dispose() }
""", success=False, environment={"FILE_PATH": str(path), "KIND": kind})
    assert result.returncode != 0
    assert "Unfit guard reached native load" not in result.stderr
    assert ("live caller-owned file guard" if kind == "closed" else "guarded ordinary file") in result.stderr


@pytest.mark.parametrize("mismatch", ("path", "handle"))
def test_loaded_library_identity_refuses_mismatch_while_keeping_owned_reference(
    host, tmp_path, mismatch,
):
    result = _run(host, tmp_path, r"""
$bindings=Get-BootstrapStockTrustBindings
$module=Open-BootstrapStockTrustLibrary -Bindings $bindings
$path=[IO.Path]::Combine([Environment]::GetFolderPath([Environment+SpecialFolder]::System),'wintrust.dll')
try {
    $wrongPath=if ($env:MISMATCH -eq 'path') { $env:WRONG_PATH } else { $path }
    $wrongHandle=if ($env:MISMATCH -eq 'handle') { [IntPtr]::new(1) } else { $module }
    $message=$null
    try { Assert-BootstrapStockTrustModule -Path $wrongPath -Expected $wrongHandle }
    catch { $message=$_.Exception.Message }
    if ($message -cne 'Bootstrap trust refuses a redirected native module.') {
        throw 'Mismatched module was not refused.'
    }
    Assert-BootstrapStockTrustModule -Path $path -Expected $module
    [pscustomobject]@{refused=$true;ownedReferenceStillValid=$true} | ConvertTo-Json -Compress
} finally { Close-BootstrapStockTrustLibrary -Bindings $bindings -Module $module }
""", environment={"MISMATCH": mismatch, "WRONG_PATH": str(tmp_path / "wintrust.dll")})
    assert result == {"refused": True, "ownedReferenceStillValid": True}


def test_unsupported_core_bindings_refuse_before_loading_library(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("native PowerShell Core refusal")
    host = shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell Core is unavailable")
    result = _run(host, tmp_path, r"""
function Open-BootstrapStockTrustLibrary { throw 'Unqualified host reached native load.' }
function Get-BootstrapCoreTrustBindings { throw 'unsupported Core trust layout' }
Get-BootstrapStockFileTrust -Guard ([pscustomobject]@{})
""", success=False)
    assert result.returncode != 0
    assert "Unqualified host reached native load" not in result.stderr
    assert "unsupported Core trust layout" in result.stderr
