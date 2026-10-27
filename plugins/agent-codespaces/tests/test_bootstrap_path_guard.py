"""Retained ancestor custody, not canonical-drive or interpreter admission."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs

from .test_bootstrap_file_guard import HELPER, ROOT, _run
from .test_bootstrap_file_guard import host as host


def test_ancestor_guard_is_not_an_enabled_bootstrap_path() -> None:
    text = HELPER.read_text(encoding="utf-8")
    assert "function Open-BootstrapStockPathGuard" in text
    assert "function Close-BootstrapStockPathGuard" in text
    for forbidden in ("Add-Type", "Reflection.Emit", "GetDelegateForFunctionPointer", "Process.Start"):
        assert forbidden not in text
    for plugin in ("agent-codespaces", "agent-containers"):
        caller = ROOT / "plugins" / plugin / "scripts/runtime-gate.ps1"
        assert HELPER.name not in caller.read_text(encoding="utf-8")


@pytest.mark.parametrize("directory", (False, True))
def test_ancestor_guard_retains_each_component_until_explicit_close(
    host: str, tmp_path: Path, directory: bool,
) -> None:
    ancestor = tmp_path / "ancestor's \u00e9"
    ancestor.mkdir()
    leaf = ancestor / "leaf"
    if directory:
        leaf.mkdir()
    else:
        leaf.write_bytes(b"untouched")
    result = _run(host, tmp_path, r"""
$guard=Open-BootstrapStockPathGuard -Path $env:FIXTURE_PATH -Directory:($env:IS_DIRECTORY -eq '1')
try {
    $expected=@($env:FIXTURE_PATH.Substring(3).Split('\') | Where-Object { $_ }).Count + 1
    if ($guard.Entries.Count -ne $expected) { throw 'Ancestor custody skipped a component.' }
    if (-not [object]::ReferenceEquals($guard.Leaf, $guard.Entries[$expected - 1])) {
        throw 'Leaf is not the retained chain entry.'
    }
    $current=$env:FIXTURE_PATH.Substring(0,3)
    for ($i=0; $i -lt $expected; $i++) {
        $entry=$guard.Entries[$i]
        if (-not [string]::Equals($entry.Path,$current,[StringComparison]::Ordinal)) {
            throw 'Ancestor custody did not open root to leaf.'
        }
        if ($entry.Handle.IsClosed -or $entry.Handle.IsInvalid) { throw 'An ancestor was released early.' }
        $same=Get-BootstrapStockFileIdentity -Handle $entry.Handle -Bindings (Get-BootstrapStockFileBindings)
        if ($same.Volume -ne $entry.Identity.Volume -or
            $same.IndexHigh -ne $entry.Identity.IndexHigh -or $same.IndexLow -ne $entry.Identity.IndexLow) {
            throw 'An ancestor identity changed.'
        }
        if ($i -lt $expected - 1) {
            $current=[IO.Path]::Combine($current,$env:FIXTURE_PATH.Substring(3).Split('\')[$i])
        }
    }
    $blocked=$false
    try { [IO.Directory]::Move($env:ANCESTOR_PATH,$env:ANCESTOR_PATH + '-moved') }
    catch [IO.IOException] {
        if (($_.Exception.HResult -band 0xffff) -ne 32) { throw }
        $blocked=$true
    }
    if (-not $blocked) { throw 'Held ancestor could be renamed.' }
} finally { Close-BootstrapStockPathGuard -Guard $guard }
if (@($guard.Entries | Where-Object { -not $_.Handle.IsClosed }).Count) {
    throw 'Ancestor close leaked a retained reference.'
}
Close-BootstrapStockPathGuard -Guard $guard
[IO.Directory]::Move($env:ANCESTOR_PATH,$env:ANCESTOR_PATH + '-released')
[IO.Directory]::Move($env:ANCESTOR_PATH + '-released',$env:ANCESTOR_PATH)
[pscustomobject]@{blocked=$blocked;closed=$true;count=$guard.Entries.Count} | ConvertTo-Json -Compress
""", environment={
        "FIXTURE_PATH": str(leaf), "ANCESTOR_PATH": str(ancestor),
        "IS_DIRECTORY": str(int(directory)),
    })
    assert result["blocked"] is True
    assert result["closed"] is True
    assert result["count"] == len(leaf.parts)
    assert leaf.exists()


@pytest.mark.parametrize("failure", ("missing", "wrong-kind", "throw"))
def test_ancestor_guard_failure_releases_previously_opened_components_before_error(
    host: str, tmp_path: Path, failure: str,
) -> None:
    ancestor = tmp_path / "release"
    ancestor.mkdir()
    leaf = ancestor / "leaf"
    if failure != "missing":
        leaf.write_bytes(b"untouched")
    result = _run(host, tmp_path, r"""
$script:open=${function:Open-BootstrapStockFileGuard}
$script:opened=[Collections.Generic.List[object]]::new()
function Open-BootstrapStockFileGuard {
    param($Path,[switch]$Directory)
    if ($env:FAILURE -eq 'throw' -and $Path -ceq $env:FIXTURE_PATH) {
        throw 'synthetic leaf-open refusal'
    }
    $entry=& $script:open -Path $Path -Directory:$Directory
    $script:opened.Add($entry)
    return $entry
}
$message=$null
$script:emitted=0
try {
    Open-BootstrapStockPathGuard -Path $env:FIXTURE_PATH -Directory:($env:FAILURE -eq 'wrong-kind') |
        ForEach-Object { $script:emitted++ }
} catch { $message=$_.Exception.Message }
if (-not $message) { throw 'Ancestor admission did not refuse.' }
if ($script:emitted -ne 0) { throw 'Failed ancestor admission emitted success.' }
if ($script:opened.Count -lt 2 -or
    @($script:opened | Where-Object { -not $_.Handle.IsClosed }).Count) {
    throw 'Ancestor refusal leaked a handle or was not exercised.'
}
[IO.Directory]::Move($env:ANCESTOR_PATH,$env:ANCESTOR_PATH + '-released')
[IO.Directory]::Move($env:ANCESTOR_PATH + '-released',$env:ANCESTOR_PATH)
[pscustomobject]@{closed=$true;emitted=$script:emitted;message=$message} | ConvertTo-Json -Compress
""", environment={
        "FIXTURE_PATH": str(leaf), "ANCESTOR_PATH": str(ancestor), "FAILURE": failure,
    })
    assert result["closed"] is True
    assert result["emitted"] == 0
    if failure == "throw":
        assert result["message"] == "synthetic leaf-open refusal"
    elif failure == "wrong-kind":
        assert "object kind does not match" in result["message"]
    else:
        assert "file guard open failed" in result["message"]


@pytest.mark.parametrize("path", (
    "relative", r"\\server\share\file", r"C:\base\..\file", r"C:\base\file:stream",
    r"C:\base\file.", "C:\\base\\file ", "C:/base/file",
    "C:\\" + "\\".join(["p"] * 129), "C:\\", "C:\\base\\file\\",
))
def test_ancestor_guard_rejects_bad_path_and_depth_before_open(
    host: str, tmp_path: Path, path: str,
) -> None:
    result = _run(host, tmp_path, r"""
function Open-BootstrapStockFileGuard { throw 'Unfit path reached a native open.' }
Open-BootstrapStockPathGuard -Path $env:FIXTURE_PATH
""", success=False, environment={"FIXTURE_PATH": path})
    assert result.returncode != 0
    assert "Unfit path reached a native open" not in result.stderr
    assert "Bootstrap" in result.stderr
    if path.count("\\") == 129:
        assert "bounded component count" in result.stderr


@pytest.mark.parametrize("directory", (False, True))
@pytest.mark.parametrize("chain", (False, True))
def test_guards_refuse_existing_nonroot_trailing_separator_before_open(
    host: str, tmp_path: Path, directory: bool, chain: bool,
) -> None:
    path = tmp_path / "existing"
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"untouched")
    result = _run(host, tmp_path, r"""
function Get-BootstrapStockFileBindings { throw 'Trailing separator reached a native binding.' }
$script:emitted=0
$message=$null
try {
    if ($env:CHAIN -eq '1') {
        Open-BootstrapStockPathGuard -Path $env:FIXTURE_PATH -Directory:($env:DIRECTORY -eq '1') |
            ForEach-Object {$script:emitted++}
    } else {
        Open-BootstrapStockFileGuard -Path $env:FIXTURE_PATH -Directory:($env:DIRECTORY -eq '1') |
            ForEach-Object {$script:emitted++}
    }
} catch { $message=$_.Exception.Message }
if ($message -cne 'Bootstrap file guard requires an ordinary absolute normalized drive path.') {
    throw 'Trailing separator refusal was lost or reached native admission.'
}
[pscustomobject]@{emitted=$script:emitted;message=$message} | ConvertTo-Json -Compress
""", environment={
        "FIXTURE_PATH": str(path) + "\\",
        "DIRECTORY": str(int(directory)), "CHAIN": str(int(chain)),
    })
    assert result["emitted"] == 0
    assert path.exists()
    if not directory:
        assert path.read_bytes() == b"untouched"
        with path.open("r+b"):
            pass


def test_root_directory_remains_a_valid_single_entry_chain(host: str, tmp_path: Path) -> None:
    result = _run(host, tmp_path, r"""
$root=[IO.Path]::GetPathRoot($env:FIXTURE_PATH)
$guard=Open-BootstrapStockPathGuard -Path $root -Directory
try {
    if ($guard.Entries.Count -ne 1 -or $guard.Path -cne $root -or $guard.Leaf.Path -cne $root -or
        $guard.Leaf.Handle.IsClosed -or ($guard.Leaf.Identity.Attributes -band 0x10) -eq 0) {
        throw 'Root-directory custody did not preserve the exact addressed root.'
    }
} finally { Close-BootstrapStockPathGuard -Guard $guard }
[pscustomobject]@{closed=$guard.Leaf.Handle.IsClosed;count=$guard.Entries.Count} |
    ConvertTo-Json -Compress
""", environment={"FIXTURE_PATH": str(tmp_path)})
    assert result == {"closed": True, "count": 1}


def test_ancestor_guard_accepts_exact_component_bound_and_closes_leaf_first(
    host: str, tmp_path: Path,
) -> None:
    result = _run(host, tmp_path, r"""
$script:opened=[Collections.Generic.List[object]]::new()
$script:closed=[Collections.Generic.List[int]]::new()
function Open-BootstrapStockFileGuard {
    param($Path,[switch]$Directory)
    $number=$script:opened.Count
    if ([bool]$Directory -ne ($number -lt 128)) {
        throw 'Ancestor guard did not distinguish its leaf from its directory prefix.'
    }
    $handle=[pscustomobject]@{Number=$number}
    $handle | Add-Member ScriptMethod Dispose {$script:closed.Add($this.Number)}
    $entry=[pscustomobject]@{Path=$Path;Handle=$handle}
    $script:opened.Add($entry)
    return $entry
}
$path='C:\' + (('p ' * 128).Trim().Split(' ') -join '\')
$guard=Open-BootstrapStockPathGuard -Path $path
if ($guard.Entries.Count -ne 129 -or $script:closed.Count -ne 0) {
    throw 'Exact ancestor limit did not retain all 129 entries.'
}
Close-BootstrapStockPathGuard -Guard $guard
if ($script:closed.Count -ne 129) { throw 'Ancestor close omitted an entry.' }
for ($i=0;$i -lt 129;$i++) {
    if ($script:closed[$i] -ne 128-$i) { throw 'Ancestor close was not leaf to root.' }
}
[pscustomobject]@{opened=$script:opened.Count;closed=$script:closed.Count} | ConvertTo-Json -Compress
""")
    assert result == {"opened": 129, "closed": 129}


@pytest.mark.parametrize("admission_failure", (False, True))
def test_ancestor_cleanup_continues_after_release_error_and_preserves_original_failure(
    host: str, tmp_path: Path, admission_failure: bool,
) -> None:
    result = _run(host, tmp_path, r"""
$script:opened=[Collections.Generic.List[object]]::new()
$script:closed=[Collections.Generic.List[int]]::new()
function Open-BootstrapStockFileGuard {
    param($Path,[switch]$Directory)
    if ($env:ADMISSION_FAILURE -eq '1' -and -not $Directory) {
        throw 'synthetic leaf admission refusal'
    }
    $number=$script:opened.Count
    $handle=[pscustomobject]@{Number=$number}
    $handle | Add-Member ScriptMethod Dispose {
        $script:closed.Add($this.Number)
        if ($this.Number -eq 1) { throw 'synthetic ancestor release refusal' }
    }
    $entry=[pscustomobject]@{Path=$Path;Handle=$handle}
    $script:opened.Add($entry)
    return $entry
}
$message=$null
try {
    $guard=Open-BootstrapStockPathGuard -Path 'C:\base\leaf'
    Close-BootstrapStockPathGuard -Guard $guard
} catch { $message=$_.Exception.ToString() }
if (-not $message) { throw 'Ancestor release failure was lost.' }
if ($script:closed.Count -ne $script:opened.Count -or $script:closed[$script:closed.Count-1] -ne 0) {
    throw 'One failed release prevented later ancestor cleanup.'
}
[pscustomobject]@{message=$message;closed=$script:closed.Count;opened=$script:opened.Count} |
    ConvertTo-Json -Compress
""", environment={"ADMISSION_FAILURE": str(int(admission_failure))})
    assert result["closed"] == result["opened"] == (2 if admission_failure else 3)
    assert "synthetic ancestor release refusal" in result["message"]
    if admission_failure:
        assert "synthetic leaf admission refusal" in result["message"]


def test_ancestor_guard_refuses_junction_component_and_releases_prefix(
    host: str, tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "leaf").write_bytes(b"unchanged target")
    junction = tmp_path / "junction"
    creation = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert creation.returncode == 0, creation.stderr
    result = _run(host, tmp_path, r"""
$script:open=${function:Open-BootstrapStockFileGuard}
$script:opened=[Collections.Generic.List[object]]::new()
function Open-BootstrapStockFileGuard {
    param($Path,[switch]$Directory)
    $entry=& $script:open -Path $Path -Directory:$Directory
    $script:opened.Add($entry)
    return $entry
}
$message=$null
try { $null=Open-BootstrapStockPathGuard -Path $env:FIXTURE_PATH }
catch { $message=$_.Exception.Message }
if ($message -cne 'Bootstrap file guard refuses a final reparse point.') {
    throw 'Ancestor junction refusal was lost.'
}
if (@($script:opened | Where-Object { -not $_.Handle.IsClosed }).Count) {
    throw 'Ancestor junction refusal leaked a prefix handle.'
}
if (@($script:opened | Where-Object { $_.Path -ceq $env:FIXTURE_PATH }).Count) {
    throw 'Ancestor junction admission opened the target leaf.'
}
[IO.Directory]::Move($env:TARGET_PATH,$env:TARGET_PATH + '-released')
[IO.Directory]::Move($env:TARGET_PATH + '-released',$env:TARGET_PATH)
[pscustomobject]@{closed=$true;message=$message} | ConvertTo-Json -Compress
""", environment={"FIXTURE_PATH": str(junction / "leaf"), "TARGET_PATH": str(target)})
    assert result["closed"] is True
    assert (target / "leaf").read_bytes() == b"unchanged target"
