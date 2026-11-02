"""Physical-volume addressing and retained long-name custody, not Python admission."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from .bootstrap_canonical_support import OBSERVE as _OBSERVE
from .test_bootstrap_file_guard import ROOT, _run
from .test_bootstrap_file_guard import host as host

HELPER = ROOT / "libs/installer-engine/bootstrap-canonical-path.ps1"
_LOAD = r"""
. $env:DRIVE_HELPER
. $env:CANONICAL_HELPER
"""
_ENV = {
    "DRIVE_HELPER": str(ROOT / "libs/installer-engine/bootstrap-drive-map.ps1"),
    "CANONICAL_HELPER": str(HELPER),
}


def test_canonical_guard_remains_private_and_compiler_free() -> None:
    text = HELPER.read_text(encoding="utf-8")
    for forbidden in (
        "Add-Type", "Reflection.Emit", "GetDelegateForFunctionPointer", "Process.Start",
        "DefineDosDevice", "Get-BootstrapStockFinalPath", "get_cFileName",
    ):
        assert forbidden not in text
    for plugin in ("agent-codespaces", "agent-containers"):
        assert HELPER.name not in (ROOT / "plugins" / plugin / "scripts/runtime-gate.ps1").read_text(
            encoding="utf-8",
        )


@pytest.mark.parametrize("directory", (False, True))
def test_canonical_guard_pins_physical_components_and_releases_query_resources(
    host: str, tmp_path: Path, directory: bool,
) -> None:
    ancestor = tmp_path / "long ancestor's \u00e9"
    ancestor.mkdir()
    leaf = ancestor / "long leaf's \u00e9"
    if directory:
        leaf.mkdir()
    else:
        leaf.write_bytes(b"untouched")
    result = _run(host, tmp_path, _LOAD + _OBSERVE + r"""
$guard=Open-BootstrapStockCanonicalPathGuard -Path $env:FIXTURE_PATH -Directory:($env:DIRECTORY -eq '1')
try {
    if ($guard.Path -cne $guard.Leaf.Path -or $guard.AddressedPath -cne $env:FIXTURE_PATH -or
        $guard.Path -cne ('\\?\GLOBALROOT' + $guard.Mapping + '\' + $env:FIXTURE_PATH.Substring(3))) {
        throw 'Canonical custody lost the internally derived physical address.'
    }
    if ($script:finds.Count -ne $guard.Entries.Count - 1 -or
        @($script:finds | Where-Object {-not $_.IsClosed}).Count -or
        $script:allocations.Count -ne $script:frees.Count -or $script:liveMemory.Count -ne 0) {
        throw 'Canonical custody returned before search/memory cleanup.'
    }
    $bindings=Get-BootstrapStockFileBindings
    foreach ($entry in $guard.Entries) {
        if ($entry.Handle.IsClosed -or $entry.Handle.IsInvalid) { throw 'Ancestor released early.' }
        $same=Get-BootstrapStockFileIdentity -Handle $entry.Handle -Bindings $bindings
        if ($same.Volume -ne $entry.Identity.Volume -or $same.IndexHigh -ne $entry.Identity.IndexHigh -or
            $same.IndexLow -ne $entry.Identity.IndexLow) { throw 'Retained physical identity changed.' }
    }
    $logical=& $script:newGuard -Path $env:FIXTURE_PATH -Directory:($env:DIRECTORY -eq '1') -Bindings $bindings
    try {
        if ($logical.Identity.Volume -ne $guard.Leaf.Identity.Volume -or
            $logical.Identity.IndexHigh -ne $guard.Leaf.Identity.IndexHigh -or
            $logical.Identity.IndexLow -ne $guard.Leaf.Identity.IndexLow) {
            throw 'Physical and addressed objects disagree.'
        }
    } finally { $logical.Handle.Dispose() }
    $blocked=$false
    try { [IO.Directory]::Move($env:ANCESTOR_PATH,$env:ANCESTOR_PATH + '-moved') }
    catch [IO.IOException] {
        if (($_.Exception.HResult -band 0xffff) -ne 32) { throw }
        $blocked=$true
    }
    if (-not $blocked) { throw 'Canonical ancestor could be renamed.' }
} finally { Close-BootstrapStockPathGuard -Guard $guard }
if (@($guard.Entries | Where-Object {-not $_.Handle.IsClosed}).Count) { throw 'Canonical handle leak.' }
[IO.Directory]::Move($env:ANCESTOR_PATH,$env:ANCESTOR_PATH + '-released')
[IO.Directory]::Move($env:ANCESTOR_PATH + '-released',$env:ANCESTOR_PATH)
[pscustomobject]@{closed=$true;count=$guard.Entries.Count;queries=$script:finds.Count} |
    ConvertTo-Json -Compress
""", environment={
        **_ENV, "FIXTURE_PATH": str(leaf), "ANCESTOR_PATH": str(ancestor),
        "DIRECTORY": str(int(directory)), "FAILURE": "",
    })
    assert result == {"closed": True, "count": len(leaf.parts), "queries": len(leaf.parts) - 1}
    assert leaf.exists()


@pytest.mark.parametrize("path", (
    r"relative", r"\\server\share\leaf", r"\\?\C:\leaf", r"C:\base\..\leaf",
    r"C:\base\leaf:stream", "C:\\base\\leaf\\", r"C:\base\leaf.", "C:\\base\\leaf ",
    r"C:\base\*.exe", r"C:\base\?", r"C:\base\NUL", r"C:\base\con.txt",
    r"C:\base\NUL .txt", r"C:\base\LPT2 .log",
    "C:\\base\\COM\u00b9.txt", r"C:\base\lpt9", "C:\\" + "\\".join(["p"] * 129),
))
def test_canonical_guard_rejects_unfit_paths_before_binding(
    host: str, tmp_path: Path, path: str,
) -> None:
    encoded = base64.b64encode(path.encode("utf-8")).decode("ascii")
    result = _run(host, tmp_path, _LOAD + r"""
$script:calls=0
function Get-BootstrapStockCanonicalBindings {$script:calls++;throw 'Invalid input reached bindings.'}
$path=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:INPUT_B64.Substring(4)))
$message=$null
$script:emitted=0
try { Open-BootstrapStockCanonicalPathGuard -Path $path | ForEach-Object {$script:emitted++} }
catch {$message=$_.Exception.Message}
[pscustomobject]@{calls=$script:calls;emitted=$script:emitted;message=$message
    decoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($path))} | ConvertTo-Json -Compress
""", environment={**_ENV, "INPUT_B64": "b64:" + encoded})
    assert result["decoded"] == encoded
    assert result["calls"] == result["emitted"] == 0
    assert result["message"] and "Invalid input reached" not in result["message"]


@pytest.mark.parametrize("mapping", (
    r"\??\C:\other", r"\Device\Mup\share", r"\Device\HarddiskVolume3\extra",
    "\\Device\\HarddiskVolume3\n", r"\Device\HarddiskVolume03", r"\Device\HarddiskVolume12345678901",
))
def test_canonical_guard_rejects_nonphysical_mapping_before_open(
    host: str, tmp_path: Path, mapping: str,
) -> None:
    result = _run(host, tmp_path, _LOAD + r"""
function Get-BootstrapStockCanonicalBindings {return [pscustomobject]@{}}
function Get-BootstrapStockCurrentDriveMapping {return $env:MAPPING}
function New-BootstrapStockFileGuard {throw 'Rejected mapping reached a file open.'}
$message=$null
try {Open-BootstrapStockCanonicalPathGuard -Path 'C:\leaf'} catch {$message=$_.Exception.Message}
if ($message -cne 'Bootstrap canonical guard requires a direct physical volume mapping.') {
    throw 'Physical mapping refusal lost.'
}
[pscustomobject]@{refused=$true} | ConvertTo-Json -Compress
""", environment={**_ENV, "MAPPING": mapping})
    assert result == {"refused": True}


@pytest.mark.parametrize("failure", ("allocation", "release", "name", "missing", "kind", "drive"))
def test_canonical_failure_closes_every_owned_guard_and_query_before_error(
    host: str, tmp_path: Path, failure: str,
) -> None:
    leaf = tmp_path / "failure leaf"
    if failure != "missing":
        leaf.write_bytes(b"retained")
    result = _run(host, tmp_path, _LOAD + _OBSERVE + r"""
$script:longName=${function:Get-BootstrapStockLongLeafName}
function Get-BootstrapStockLongLeafName {
    param($Path,$Bindings)
    $name=& $script:longName -Path $Path -Bindings $Bindings
    if ($env:FAILURE -eq 'name') {return 'unrelated long name'}
    return $name
}
if ($env:FAILURE -eq 'drive') {
    $script:canonicalBindings=${function:Get-BootstrapStockCanonicalBindings}
    function Get-BootstrapStockCanonicalBindings {
        $bindings=& $script:canonicalBindings
        $proxy=[pscustomobject]@{}
        $proxy | Add-Member ScriptMethod Invoke {param($target,$arguments);return 4}
        $bindings.DriveType=$proxy
        return $bindings
    }
}
$message=$null
$script:emitted=0
try {
    Open-BootstrapStockCanonicalPathGuard -Path $env:FIXTURE_PATH -Directory:($env:FAILURE -eq 'kind') |
        ForEach-Object {$script:emitted++}
} catch {$message=$_.Exception.Message}
if (-not $message -or $script:emitted -ne 0 -or $script:guards.Count -eq 0 -or
    @($script:guards | Where-Object {-not $_.Handle.IsClosed}).Count -or
    @($script:finds | Where-Object {-not $_.IsClosed}).Count -or
    $script:allocations.Count -ne $script:frees.Count -or $script:liveMemory.Count -ne 0) {
    throw 'Canonical refusal leaked custody or output.'
}
[pscustomobject]@{closed=$true;emitted=$script:emitted;message=$message} | ConvertTo-Json -Compress
""", environment={**_ENV, "FIXTURE_PATH": str(leaf), "FAILURE": failure})
    assert result["closed"] is True
    assert result["emitted"] == 0
    expected = {
        "allocation": "synthetic allocation refusal", "release": "synthetic refusal after actual memory release",
        "name": "short-name or noncanonical", "missing": "file guard open failed",
        "kind": "object kind does not match", "drive": "fixed physical volume",
    }
    assert expected[failure] in result["message"]
    if leaf.exists():
        with leaf.open("r+b"):
            pass


@pytest.mark.parametrize("ancestor", (False, True))
@pytest.mark.parametrize("normalization", ("stock", "unexpanded"))
def test_canonical_guard_refuses_actual_short_name(
    host: str, tmp_path: Path, ancestor: bool, normalization: str,
) -> None:
    import ctypes
    from ctypes import wintypes

    folder = tmp_path / "canonical long directory"
    folder.mkdir()
    leaf = folder / "canonical long filename.txt"
    leaf.write_bytes(b"unchanged")
    queried = folder if ancestor else leaf
    query = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800).GetShortPathNameW
    query.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    query.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = query(str(queried), buffer, len(buffer))
    assert 0 < length < len(buffer), ctypes.get_last_error()
    alias = str(queried.parent / Path(buffer.value).name)
    if alias.casefold() == str(queried).casefold():
        pytest.skip("fixture volume does not expose an actual short name")
    addressed = str(Path(alias) / leaf.name) if ancestor else alias
    result = _run(host, tmp_path, _LOAD + _OBSERVE + r"""
$normalized=[IO.Path]::GetFullPath($env:FIXTURE_PATH)
if ([string]::Equals($normalized,$env:FIXTURE_PATH,[StringComparison]::Ordinal) -or
    -not [string]::Equals($normalized,$env:LONG_PATH,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Actual short-name fixture did not expand to its known long counterpart.'
}
$script:ordinaryValidator=${function:Assert-BootstrapStockOrdinaryPath}
$script:validationCalls=0
if ($env:NORMALIZATION -eq 'unexpanded') {
    # Isolate the post-open defense without changing native lookup or production validation.
    function Assert-BootstrapStockOrdinaryPath {
        param($Path,[int]$MaximumComponents=0)
        if ($Path -cne $env:FIXTURE_PATH) {throw 'Unexpected input reached the isolated normalization control.'}
        $script:validationCalls++
        & $script:ordinaryValidator -Path $env:LONG_PATH -MaximumComponents $MaximumComponents
    }
}
$message=$null
$script:emitted=0
try {
    Open-BootstrapStockCanonicalPathGuard -Path $env:FIXTURE_PATH | ForEach-Object {$script:emitted++}
} catch {$message=$_.Exception.Message}
try {
    if ($script:emitted -ne 0 -or @($script:guards | Where-Object {-not $_.Handle.IsClosed}).Count -or
        @($script:finds | Where-Object {-not $_.IsClosed}).Count -or
        $script:allocations.Count -ne $script:frees.Count -or $script:liveMemory.Count -ne 0) {
        throw 'Actual short-name control emitted success or retained resources.'
    }
    if ($env:NORMALIZATION -eq 'stock') {
        if ($message -cne 'Bootstrap file guard requires an ordinary absolute normalized drive path.' -or
            $script:guards.Count -ne 0 -or $script:finds.Count -ne 0 -or $script:allocations.Count -ne 0) {
            throw 'Stock normalization did not refuse the expanded alias before opening.'
        }
    } elseif ($message -cne 'Bootstrap canonical guard refuses a short-name or noncanonical component.' -or
        $script:validationCalls -ne 1 -or $script:guards.Count -lt 2 -or $script:finds.Count -eq 0) {
        throw 'Held physical long-name query did not independently refuse the actual alias.'
    }
} finally {
    Close-BootstrapStockPathGuard -Guard ([pscustomobject]@{Entries=$script:guards.ToArray()})
}
[pscustomobject]@{refused=$true;emitted=$script:emitted;normalization=$env:NORMALIZATION} | ConvertTo-Json -Compress
""", environment={
        **_ENV, "FIXTURE_PATH": addressed, "LONG_PATH": str(leaf),
        "NORMALIZATION": normalization, "FAILURE": "",
    })
    assert result == {"refused": True, "emitted": 0, "normalization": normalization}
    assert leaf.read_bytes() == b"unchanged"


@pytest.mark.parametrize("failure", ("empty", "unterminated", "separator", "native-zero", "throw"))
def test_long_name_refusal_releases_actual_find_handle_and_bounded_memory(
    host: str, tmp_path: Path, failure: str,
) -> None:
    leaf = tmp_path / "long name error fixture"
    leaf.write_bytes(b"untouched")
    result = _run(host, tmp_path, _LOAD + r"""
$guard=Open-BootstrapStockCanonicalPathGuard -Path $env:FIXTURE_PATH
$bindings=Get-BootstrapStockCanonicalBindings
$script:nativeFind=$bindings.Find
$script:record=$bindings.Record
$script:findHandle=$null
$proxy=[pscustomobject]@{}
$proxy | Add-Member ScriptMethod Invoke {
    param($target,$arguments)
    if ($env:FAILURE -eq 'throw') {throw 'synthetic find refusal'}
    if ($env:FAILURE -eq 'native-zero') {$arguments[0]+='-missing'}
    $script:findHandle=$script:nativeFind.Invoke($target,$arguments)
    if ($env:FAILURE -in @('empty','unterminated','separator')) {
        $pointer=[Runtime.InteropServices.Marshal]::AllocHGlobal(592)
        try {
            [Runtime.InteropServices.Marshal]::Copy([byte[]]::new(592),0,$pointer,592)
            if ($env:FAILURE -eq 'unterminated') {
                for ($i=0;$i -lt 260;$i++) {
                    [Runtime.InteropServices.Marshal]::WriteInt16($pointer,44 + 2 * $i,65)
                }
            } elseif ($env:FAILURE -eq 'separator') {
                [Runtime.InteropServices.Marshal]::WriteInt16($pointer,44,92)
            }
            $reader=[Runtime.InteropServices.Marshal].GetMethod('PtrToStructure',[type[]]@([IntPtr],[type]))
            $arguments[1]=$reader.Invoke($null,[object[]]@($pointer,$script:record))
        } finally {[Runtime.InteropServices.Marshal]::FreeHGlobal($pointer)}
    }
    return $script:findHandle
}
$bindings.Find=$proxy
$script:newMemory=${function:New-BootstrapStockFindAllocation}
$script:freeMemory=${function:Close-BootstrapStockFindAllocation}
$script:allocated=0
$script:freed=0
function New-BootstrapStockFindAllocation {$script:allocated++; & $script:newMemory}
function Close-BootstrapStockFindAllocation {
    param($Pointer)
    & $script:freeMemory -Pointer $Pointer
    $script:freed++
}
$message=$null
$script:emitted=0
try {
    Get-BootstrapStockLongLeafName -Path $guard.Path -Bindings $bindings |
        ForEach-Object {$script:emitted++}
} catch {$message=$_.Exception.Message}
finally {Close-BootstrapStockPathGuard -Guard $guard}
if (-not $message -or $script:emitted -ne 0 -or $script:allocated -ne $script:freed -or
    ($null -ne $script:findHandle -and -not $script:findHandle.IsClosed) -or
    @($guard.Entries | Where-Object {-not $_.Handle.IsClosed}).Count) {
    throw 'Long-name refusal leaked search, memory, custody or output.'
}
[pscustomobject]@{message=$message;emitted=$script:emitted;allocated=$script:allocated;freed=$script:freed} |
    ConvertTo-Json -Compress
""", environment={**_ENV, "FIXTURE_PATH": str(leaf), "FAILURE": failure})
    assert result["emitted"] == 0
    assert result["allocated"] == result["freed"] == (0 if failure in ("throw", "native-zero") else 1)
    expected = {
        "empty": "empty or unterminated", "unterminated": "empty or unterminated",
        "separator": "non-leaf", "native-zero": "long-name query failed", "throw": "synthetic find refusal",
    }
    assert expected[failure] in result["message"]


def test_canonical_root_is_a_single_retained_physical_directory(host: str, tmp_path: Path) -> None:
    result = _run(host, tmp_path, _LOAD + r"""
$guard=Open-BootstrapStockCanonicalPathGuard -Path ([IO.Path]::GetPathRoot($env:FIXTURE_PATH)) -Directory
try {
    if ($guard.Entries.Count -ne 1 -or $guard.Path -cne ('\\?\GLOBALROOT' + $guard.Mapping + '\') -or
        $guard.Leaf.Handle.IsClosed -or ($guard.Leaf.Identity.Attributes -band 0x10) -eq 0) {
        throw 'Physical root custody failed.'
    }
} finally {Close-BootstrapStockPathGuard -Guard $guard}
[pscustomobject]@{closed=$guard.Leaf.Handle.IsClosed} | ConvertTo-Json -Compress
""", environment={**_ENV, "FIXTURE_PATH": str(tmp_path)})
    assert result == {"closed": True}


def test_canonical_mapping_is_a_single_snapshot_and_exact_bound_closes_in_reverse(
    host: str, tmp_path: Path,
) -> None:
    result = _run(host, tmp_path, _LOAD + r"""
$script:mappingCalls=0
$script:currentMapping='\Device\HarddiskVolume3'
$script:opened=[Collections.Generic.List[object]]::new()
$script:closed=[Collections.Generic.List[int]]::new()
function Get-BootstrapStockCurrentDriveMapping {
    param($Drive)
    if ($Drive -cne 'C:') {throw 'Wrong addressed drive.'}
    $script:mappingCalls++
    return $script:currentMapping
}
function Get-BootstrapStockCanonicalBindings {
    $drive=[pscustomobject]@{}
    $drive | Add-Member ScriptMethod Invoke {
        param($target,$arguments)
        if ($arguments.Count -ne 1 -or $arguments[0] -cne '\\?\GLOBALROOT\Device\HarddiskVolume3\') {
            throw 'Drive-type query used a mutable or different address.'
        }
        return 3
    }
    return [pscustomobject]@{Files=[pscustomobject]@{};DriveType=$drive}
}
function New-BootstrapStockFileGuard {
    param($Path,[switch]$Directory,$Bindings)
    $number=$script:opened.Count
    if (-not $Path.StartsWith('\\?\GLOBALROOT\Device\HarddiskVolume3\',[StringComparison]::Ordinal) -or
        [bool]$Directory -ne ($number -lt 128)) {
        throw 'Physical snapshot or leaf-kind custody changed.'
    }
    $handle=[pscustomobject]@{Number=$number;IsClosed=$false}
    $handle | Add-Member ScriptMethod Dispose {
        $this.IsClosed=$true
        $script:closed.Add($this.Number)
        if ($this.Number -eq 2) {throw 'synthetic refusal after component release'}
    }
    $entry=[pscustomobject]@{Path=$Path;Handle=$handle}
    $script:opened.Add($entry)
    $script:currentMapping='\Device\HarddiskVolume4'
    return $entry
}
function Get-BootstrapStockLongLeafName {
    param($Path,$Bindings)
    return $Path.Substring($Path.LastIndexOf('\') + 1)
}
$path='C:\' + (('p ' * 128).Trim().Split(' ') -join '\')
$guard=Open-BootstrapStockCanonicalPathGuard -Path $path
if ($guard.Entries.Count -ne 129 -or $script:closed.Count -ne 0 -or $script:mappingCalls -ne 1) {
    throw 'Exact component bound or single mapping snapshot failed.'
}
$message=$null
try {Close-BootstrapStockPathGuard -Guard $guard} catch {$message=$_.Exception.Message}
if (-not $message -or $script:closed.Count -ne 129 -or
    @($script:opened | Where-Object {-not $_.Handle.IsClosed}).Count) {
    throw 'Canonical release failed to attempt every component.'
}
for ($i=0;$i -lt 129;$i++) {
    if ($script:closed[$i] -ne 128 - $i) {throw 'Canonical release was not reversed.'}
}
[pscustomobject]@{mappingCalls=$script:mappingCalls;opened=$script:opened.Count;closed=$script:closed.Count} |
    ConvertTo-Json -Compress
""", environment=_ENV)
    assert result == {"mappingCalls": 1, "opened": 129, "closed": 129}


def test_canonical_guard_refuses_a_junction_without_opening_its_target(
    host: str, tmp_path: Path,
) -> None:
    import subprocess

    from agent_procutil import no_window_kwargs

    target = tmp_path / "junction target"
    target.mkdir()
    leaf = target / "leaf"
    leaf.write_bytes(b"untouched target")
    junction = tmp_path / "junction"
    creation = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert creation.returncode == 0, creation.stderr
    result = _run(host, tmp_path, _LOAD + _OBSERVE + r"""
$message=$null
$script:emitted=0
try {
    Open-BootstrapStockCanonicalPathGuard -Path $env:FIXTURE_PATH | ForEach-Object {$script:emitted++}
} catch {$message=$_.Exception.Message}
if ($message -cne 'Bootstrap file guard refuses a final reparse point.' -or $script:emitted -ne 0 -or
    $script:guards.Count -eq 0 -or @($script:guards | Where-Object {-not $_.Handle.IsClosed}).Count -or
    @($script:guards | Where-Object {$_.Path.EndsWith('\junction\leaf')}).Count) {
    throw 'Canonical junction refusal did not release its prefix before target traversal.'
}
[IO.Directory]::Move($env:TARGET_PATH,$env:TARGET_PATH + '-released')
[IO.Directory]::Move($env:TARGET_PATH + '-released',$env:TARGET_PATH)
[pscustomobject]@{closed=$true;emitted=$script:emitted} | ConvertTo-Json -Compress
""", environment={**_ENV, "FIXTURE_PATH": str(junction / "leaf"), "TARGET_PATH": str(target), "FAILURE": ""})
    assert result == {"closed": True, "emitted": 0}
    assert leaf.read_bytes() == b"untouched target"


def test_canonical_allocation_observer_accepts_recycled_addresses_but_not_double_free(
    host: str, tmp_path: Path,
) -> None:
    result = _run(host, tmp_path, _LOAD + _OBSERVE + r"""
$script:slot=[Runtime.InteropServices.Marshal]::AllocHGlobal(592)
$script:backendReleases=0
try {
    # One fixture-owned block models two sequential allocation lifetimes at the same address.
    $script:newMemory={return $script:slot}
    $script:freeMemory={
        param([IntPtr]$Pointer)
        if ($Pointer -ne $script:slot) {throw 'Observer released a foreign fixture address.'}
        $script:backendReleases++
    }
    for ($i=0;$i -lt 2;$i++) {
        $pointer=New-BootstrapStockFindAllocation
        Close-BootstrapStockFindAllocation -Pointer $pointer
    }
    foreach ($invalid in @($pointer,[IntPtr]::Zero)) {
        $message=$null
        try {Close-BootstrapStockFindAllocation -Pointer $invalid}
        catch {$message=$_.Exception.Message}
        if ($message -cne 'Long-name query freed unowned memory.') {
            throw 'Observer lost double-free or unknown-address refusal.'
        }
    }
    if ($script:liveMemory.Count -ne 0 -or $script:allocations.Count -ne 2 -or
        $script:frees.Count -ne 2 -or $script:backendReleases -ne 2) {
        throw 'Observer conflated an address with an allocation lifetime.'
    }
} finally {[Runtime.InteropServices.Marshal]::FreeHGlobal($script:slot)}
[pscustomobject]@{allocations=$script:allocations.Count;frees=$script:frees.Count
    live=$script:liveMemory.Count;backendReleases=$script:backendReleases} | ConvertTo-Json -Compress
""", environment={**_ENV, "FAILURE": ""})
    assert result == {"allocations": 2, "frees": 2, "live": 0, "backendReleases": 2}
