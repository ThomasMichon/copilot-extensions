"""Stock current-drive queries without changing DOS mappings or admitting Python."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from .test_bootstrap_file_guard import ROOT, _run
from .test_bootstrap_file_guard import host as host

HELPER = ROOT / "libs/installer-engine/bootstrap-drive-map.ps1"
_LOAD = ". $env:DRIVE_HELPER\n"
_OBSERVE_ALLOCATIONS = r"""
$script:newAllocation=${function:New-BootstrapStockDriveAllocation}
$script:closeAllocation=${function:Close-BootstrapStockDriveAllocation}
$script:allocated=[Collections.Generic.List[IntPtr]]::new()
$script:freed=[Collections.Generic.List[IntPtr]]::new()
function New-BootstrapStockDriveAllocation {
    param($Name,[switch]$OutputBuffer)
    if (-not $OutputBuffer -and $env:FAILURE -eq 'name-allocation') {
        throw 'synthetic name allocation refusal'
    }
    $pointer=& $script:newAllocation -Name $Name -OutputBuffer:$OutputBuffer
    $script:allocated.Add($pointer)
    return $pointer
}
function Close-BootstrapStockDriveAllocation {
    param([IntPtr]$Pointer)
    if (-not $script:allocated.Contains($Pointer) -or $script:freed.Contains($Pointer)) {
        throw 'Drive query freed an unowned or already freed allocation.'
    }
    & $script:closeAllocation -Pointer $Pointer
    $script:freed.Add($Pointer)
    if ($env:FAILURE -eq 'release' -and $script:freed.Count -eq 1) {
        throw 'synthetic refusal after actual allocation release'
    }
}
"""


def test_drive_query_is_private_and_never_compiles_or_changes_mappings() -> None:
    text = HELPER.read_text(encoding="utf-8")
    for forbidden in (
        "Add-Type", "Reflection.Emit", "GetDelegateForFunctionPointer", "Process.Start",
        "DefineDosDevice", "Resolve-EvaluatorBootstrapPython", "SetLastPInvokeError",
    ):
        assert forbidden not in text
    for plugin in ("agent-codespaces", "agent-containers"):
        caller = ROOT / "plugins" / plugin / "scripts/runtime-gate.ps1"
        assert HELPER.name not in caller.read_text(encoding="utf-8")


def test_stock_drive_query_calls_exact_declaration_and_cleans_before_output(
    host: str, tmp_path: Path,
) -> None:
    result = _run(host, tmp_path, _LOAD + _OBSERVE_ALLOCATIONS + r"""
$script:bindingReader=${function:Get-BootstrapStockDriveBindings}
$script:native=$null
$script:core=$false
$script:calls=0
function Get-BootstrapStockDriveBindings {
    $bindings=& $script:bindingReader
    $script:native=$bindings.Query
    $script:core=$bindings.Core
    $proxy=[pscustomobject]@{}
    $proxy | Add-Member ScriptMethod Invoke {
        param($target,$arguments)
        if ($arguments.Count -ne 3) { throw 'Wrong drive query argument count.' }
        if ($script:core) {
            if ($arguments[0] -isnot [IntPtr] -or $arguments[1] -isnot [IntPtr] -or
                $arguments[2] -isnot [uint32] -or $arguments[2] -ne 32768 -or
                [Runtime.InteropServices.Marshal]::PtrToStringUni($arguments[0]) -cne 'C:') {
                throw 'Core drive query did not forward its owned UTF16 pointers and DWORD bound.'
            }
        } elseif ($arguments[0] -cne 'C:' -or $arguments[1] -isnot [Text.StringBuilder] -or
            $arguments[1].Capacity -ne 32768 -or $arguments[2] -isnot [int] -or
            $arguments[2] -ne 32768) {
            throw 'PS5 drive query did not forward the managed buffer and integer bound.'
        }
        $script:calls++
        return $script:native.Invoke($target,$arguments)
    }
    $bindings.Query=$proxy
    return $bindings
}
$observed=0
$mapping=Get-BootstrapStockCurrentDriveMapping -Drive 'C:' | ForEach-Object {
    if ($script:allocated.Count -ne $script:freed.Count) {
        throw 'Drive query emitted a mapping before owned memory release.'
    }
    $observed++
    $_
}
if ($mapping -notmatch '\A\\Device\\HarddiskVolume[0-9]+\z') {
    throw 'Native current mapping was not the expected existing system volume.'
}
[pscustomobject]@{mapping=$mapping;calls=$script:calls;observed=$observed
    allocations=$script:allocated.Count;frees=$script:freed.Count;core=$script:core} |
    ConvertTo-Json -Compress
""", environment={"DRIVE_HELPER": str(HELPER), "FAILURE": ""})
    assert result["mapping"].startswith("\\Device\\HarddiskVolume")
    assert result["calls"] == result["observed"] == 1
    assert result["allocations"] == result["frees"] == (2 if result["core"] else 0)


@pytest.mark.parametrize("drive", (
    "", "?:", "C:\\", "CC:", "C", "\\Device\\HarddiskVolume1",
    "C:\n", "C:\r\n", "C:\r", "C:\x00", "C:\u2028", "\nC:",
))
def test_drive_query_rejects_malformed_input_before_binding(
    host: str, tmp_path: Path, drive: str,
) -> None:
    encoded = base64.b64encode(drive.encode("utf-8")).decode("ascii")
    result = _run(host, tmp_path, _LOAD + r"""
$script:bindings=0
function Get-BootstrapStockDriveBindings {
    $script:bindings++
    throw 'Invalid drive reached native bindings.'
}
$drive=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:DRIVE_B64.Substring(4)))
$script:emitted=0
$message=$null
try {
    Get-BootstrapStockCurrentDriveMapping -Drive $drive | ForEach-Object {$script:emitted++}
} catch { $message=$_.Exception.Message }
if (-not $message) { throw 'Malformed drive was not refused.' }
[pscustomobject]@{message=$message;emitted=$script:emitted;bindings=$script:bindings
    decoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($drive))} |
    ConvertTo-Json -Compress
""", environment={
        "DRIVE_HELPER": str(HELPER),
        "DRIVE_B64": "b64:" + encoded,
    })
    assert result["decoded"] == encoded
    assert result["bindings"] == result["emitted"] == 0
    assert "Invalid drive reached native bindings" not in result["message"]


@pytest.mark.parametrize("failure", (
    "native-zero", "throw", "negative", "short", "overflow",
    "unterminated", "empty", "name-allocation", "release",
))
def test_owned_pointer_failures_emit_nothing_and_release_all_memory(
    host: str, tmp_path: Path, failure: str,
) -> None:
    result = _run(host, tmp_path, _LOAD + _OBSERVE_ALLOCATIONS + r"""
function Get-BootstrapStockDriveBindings {
    $proxy=[pscustomobject]@{}
    $proxy | Add-Member ScriptMethod Invoke {
        param($target,$arguments)
        if ($arguments[0] -isnot [IntPtr] -or $arguments[1] -isnot [IntPtr]) {
            throw 'Owned-pointer control did not reach its intended branch.'
        }
        switch ($env:FAILURE) {
            'native-zero' { return [int]0 }
            'throw' { throw 'synthetic drive query refusal' }
            'negative' { return [int]-1 }
            'short' { return [int]1 }
            'overflow' { return [int]32769 }
            'unterminated' { return [int]3 }
            'empty' {
                [Runtime.InteropServices.Marshal]::WriteInt16($arguments[1],0,0)
                [Runtime.InteropServices.Marshal]::WriteInt16($arguments[1],2,0)
                [Runtime.InteropServices.Marshal]::WriteInt16($arguments[1],4,0)
                return [int]3
            }
            'release' {
                $chars=('X' + [char]0 + [char]0).ToCharArray()
                [Runtime.InteropServices.Marshal]::Copy($chars,0,$arguments[1],$chars.Length)
                return [int]$chars.Length
            }
            default { throw 'Unexpected failure control.' }
        }
    }
    [pscustomobject]@{Core=$true;Query=$proxy}
}
$script:emitted=0
$message=$null
try {
    Get-BootstrapStockCurrentDriveMapping -Drive 'C:' | ForEach-Object {$script:emitted++}
} catch { $message=$_.Exception.Message }
if (-not $message) { throw 'Drive query refusal was lost.' }
if ($script:allocated.Count -ne $script:freed.Count) { throw 'Drive query refusal leaked owned memory.' }
[pscustomobject]@{emitted=$script:emitted;allocations=$script:allocated.Count
    frees=$script:freed.Count;message=$message} | ConvertTo-Json -Compress
""", environment={"DRIVE_HELPER": str(HELPER), "FAILURE": failure})
    expected = 1 if failure == "name-allocation" else 2
    assert result["allocations"] == result["frees"] == expected
    assert result["emitted"] == 0
    if failure == "native-zero":
        assert "does not capture native last error" in result["message"]
    elif failure == "release":
        assert "after actual allocation release" in result["message"]


@pytest.mark.parametrize("failure", ("native-zero", "empty", "embedded", "short-count"))
def test_managed_buffer_refusals_do_not_masquerade_as_current_mapping(
    host: str, tmp_path: Path, failure: str,
) -> None:
    result = _run(host, tmp_path, _LOAD + r"""
function Get-BootstrapStockDriveBindings {
    $proxy=[pscustomobject]@{}
    $proxy | Add-Member ScriptMethod Invoke {
        param($target,$arguments)
        switch ($env:FAILURE) {
            'native-zero' { return [int]0 }
            'empty' { return [int]3 }
            'embedded' {
                [void]$arguments[1].Append(('a' + [char]0 + 'b'))
                return [int]5
            }
            'short-count' {
                [void]$arguments[1].Append('long mapping')
                return [int]3
            }
        }
    }
    [pscustomobject]@{Core=$false;Query=$proxy}
}
$script:emitted=0
$message=$null
try {
    Get-BootstrapStockCurrentDriveMapping -Drive 'C:' | ForEach-Object {$script:emitted++}
} catch { $message=$_.Exception.Message }
if (-not $message) { throw 'Managed buffer refusal was lost.' }
[pscustomobject]@{message=$message;emitted=$script:emitted} | ConvertTo-Json -Compress
""", environment={"DRIVE_HELPER": str(HELPER), "FAILURE": failure})
    assert result["emitted"] == 0
    assert "Bootstrap drive query" in result["message"]
    assert "raw thunk" not in result["message"]


@pytest.mark.parametrize("pointer", (False, True))
def test_drive_query_returns_current_mapping_not_undeleted_prior_mapping(
    host: str, tmp_path: Path, pointer: bool,
) -> None:
    result = _run(host, tmp_path, _LOAD + _OBSERVE_ALLOCATIONS + r"""
function Get-BootstrapStockDriveBindings {
    $proxy=[pscustomobject]@{}
    $proxy | Add-Member ScriptMethod Invoke {
        param($target,$arguments)
        $current='\Device\HarddiskVolume1'
        $text=$current + [char]0 + '\??\C:\prior' + [char]0 + [char]0
        if ($env:POINTER -eq '1') {
            $chars=$text.ToCharArray()
            [Runtime.InteropServices.Marshal]::Copy($chars,0,$arguments[1],$chars.Length)
        } else {
            [void]$arguments[1].Append($current)
        }
        return [int]$text.Length
    }
    [pscustomobject]@{Core=($env:POINTER -eq '1');Query=$proxy}
}
$mapping=Get-BootstrapStockCurrentDriveMapping -Drive 'C:'
if ($script:allocated.Count -ne $script:freed.Count) { throw 'Current-mapping query leaked memory.' }
[pscustomobject]@{mapping=$mapping;allocations=$script:allocated.Count;frees=$script:freed.Count} |
    ConvertTo-Json -Compress
""", environment={"DRIVE_HELPER": str(HELPER), "POINTER": str(int(pointer)), "FAILURE": ""})
    assert result["mapping"] == "\\Device\\HarddiskVolume1"
    assert result["allocations"] == result["frees"] == (2 if pointer else 0)
