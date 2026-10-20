"""Real Windows sharing failures must not delete an existing Logger resolver."""
from __future__ import annotations

import os
import shutil

import pytest

from .test_source_runtime_install import REPOSITORY, _ps, _run
from .test_source_runtime_reselection import _powershell_functions

pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows sharing boundary")
HELPERS = (
    "resolve-runtime.ps1", "resolve-runtime.sh", "versioned_runtime.py",
    "versioned_runtime_source.py", "versioned_runtime_leases.py",
    "versioned_runtime_publication.py",
    "versioned_runtime_construction.py",
)


def _publish(tmp_path, host, existing, locked, collision=None):
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    source = tmp_path / "source"
    target = tmp_path / "runtime" / "bin"
    source.mkdir()
    target.mkdir(parents=True)
    for name in HELPERS:
        (source / name).write_bytes(("new-" + name).encode())
        if existing:
            (target / name).write_bytes(("previous-" + name).encode())
    if collision:
        destination = target / collision
        destination.unlink()
        destination.mkdir()
        (destination / "keep").write_bytes(b"preserved")
    path = REPOSITORY / "plugins" / "agent-logger" / "scripts" / "install.ps1"
    function = _powershell_functions(
        "agent-logger", ("Deploy-ResolverHelpers",), path.stat().st_mtime_ns,
    ).replace("$PSScriptRoot", "$ScriptDir")
    harness = tmp_path / "boundary.ps1"
    harness.write_text(f"""
$ErrorActionPreference = 'Stop'
$ScriptDir = {_ps(source)}
$InstallDir = {_ps(target.parent)}
$LockSource = ${str(locked).lower()}
$script:held = $null
function Copy-Item {{
    [CmdletBinding()]
    param($LiteralPath, $Destination, [switch]$Force)
    Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters
    if ($LockSource -and [IO.Path]::GetFileName($LiteralPath) -eq 'resolve-runtime.ps1') {{
        $script:held = [IO.File]::Open($Destination, [IO.FileMode]::Open,
            [IO.FileAccess]::Read, [IO.FileShare]::Read)
    }}
}}
{function}
$code = 0
try {{ Deploy-ResolverHelpers }} catch {{
    $failure = $_.Exception.GetBaseException()
    [Console]::Error.WriteLine('NATIVE_ERROR_CODE=' + ($failure.HResult -band 65535))
    [Console]::Error.WriteLine($failure.Message)
    $code = 19
}} finally {{
    if ($script:held) {{ $script:held.Dispose() }}
}}
exit $code
""", encoding="utf-8")
    result = _run([executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)])
    return result, source, target


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_locked_staged_source_preserves_existing_resolver_on_real_sharing_violation(tmp_path, host):
    result, source, target = _publish(tmp_path, host, existing=True, locked=True)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "NATIVE_ERROR_CODE=32" in result.stderr, result.stderr
    for name in HELPERS:
        assert (target / name).read_bytes() == ("previous-" + name).encode()


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
@pytest.mark.parametrize("existing", [False, True], ids=["first-publication", "replacement"])
def test_real_native_resolver_publication_uses_move_or_replace(tmp_path, host, existing):
    result, source, target = _publish(tmp_path, host, existing=existing, locked=False)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in HELPERS:
        assert (target / name).read_bytes() == (source / name).read_bytes()


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_native_resolver_directory_collision_preflights_ordinary_peers(tmp_path, host):
    member = HELPERS[-1]
    result, source, target = _publish(tmp_path, host, existing=True, locked=False, collision=member)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "destination is a directory" in result.stderr
    assert {p.name for p in (target / member).iterdir()} == {"keep"}
    assert (target / member / "keep").read_bytes() == b"preserved"
    for name in HELPERS[:-1]:
        assert (target / name).read_bytes() == ("previous-" + name).encode()
