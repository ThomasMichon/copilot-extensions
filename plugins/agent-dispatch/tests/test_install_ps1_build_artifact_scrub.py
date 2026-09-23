"""PowerShell execution regression coverage for install.ps1's build-artifact
scrub (before AND after an install attempt, not just after) -- the Windows
counterpart to test_install_sh_build_artifact_scrub.py.

Installing FROM the pristine payload directory ($PluginDir, under the plugin
install root) leaves setuptools' own build/ + *.egg-info staging behind IN
that tree. Left in place, a stale build/ can silently shadow fresh src/ on a
later install if setuptools' incremental-build mtime check decides nothing
"changed" -- confirmed live on POSIX (copilot-extensions#3444): a truncated
recipes_cli.py shipped this way and crash-looped a production daemon for
~8h. The Windows installer had the identical gap (an after-only scrub in the
`finally` block): this module actually EXECUTES the extracted `$installPkg`
scriptblock under `pwsh`, with stubbed `uv`/pip functions, to prove the fix
works, not just that the source text is ordered a certain way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")


def _extract_install_pkg_block() -> str:
    """Extract the `$StaleCacheRefreshPackages = @(...)` declaration through
    the end of the `$installPkg = { ... }` scriptblock, by brace-counting
    from the scriptblock's own opening `{` (the leading `@(...)` array is
    copied verbatim first since $installPkg's body references it)."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    array_start = text.index("$StaleCacheRefreshPackages = @(")
    block_start = text.index("$installPkg = {")
    brace_start = text.index("{", block_start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[array_start : i + 1]
        i += 1
    raise AssertionError("unbalanced braces extracting $installPkg")


def _run_harness(plugin_dir: Path, stub_body: str, extra_script: str) -> subprocess.CompletedProcess:
    harness = plugin_dir / "harness.ps1"
    harness.write_text(
        f'$PluginDir = "{plugin_dir}"\n'
        '$VenvPython = "python3"\n'
        + stub_body
        + "\n\n"
        + _extract_install_pkg_block()
        + "\n\n"
        + extra_script
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
        check=True,
    )


def _seed_build_residue(plugin_dir: Path) -> None:
    (plugin_dir / "build" / "lib" / "some_pkg").mkdir(parents=True)
    (plugin_dir / "build" / "lib" / "some_pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (plugin_dir / "some_pkg.egg-info").mkdir()
    (plugin_dir / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def _seed_src_layout_egg_info(plugin_dir: Path) -> None:
    (plugin_dir / "src" / "some_pkg.egg-info").mkdir(parents=True)
    (plugin_dir / "src" / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def test_uv_branch_scrubs_before_and_after(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    stub = """
function uv { Write-Output 'Installed 1 package'; $global:LASTEXITCODE = 0 }
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_src_layout_egg_info_is_also_scrubbed(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    stub = """
function uv { Write-Output 'Installed 1 package'; $global:LASTEXITCODE = 0 }
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "src" / "some_pkg.egg-info").exists()


def test_preexisting_residue_is_gone_before_uv_runs(tmp_path: Path) -> None:
    """Regression (2026-09-23, copilot-extensions#3444 review): the Windows
    path only scrubbed in the `finally` block after install -- pre-existing
    residue would still shadow the current install's own build. The stub
    `uv` function asserts the residue is already gone by the time it runs."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    stub = f"""
function uv {{
    if ((Test-Path "{plugin_dir}\\build") -or (Test-Path "{plugin_dir}\\some_pkg.egg-info") `
        -or (Test-Path "{plugin_dir}\\src\\some_pkg.egg-info")) {{
        [Console]::Error.WriteLine('residue still present at install time')
        $global:LASTEXITCODE = 1
        return
    }}
    Write-Output 'Installed 1 package'
    $global:LASTEXITCODE = 0
}}
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXIT:0" in result.stdout
    assert "residue still present" not in result.stderr


def test_no_uv_fallback_rescrubs_between_the_two_pip_calls(tmp_path: Path) -> None:
    """Regression: the no-uv fallback runs TWO sequential pip calls
    (--force-reinstall --no-deps, then a plain install). The first call can
    itself recreate build/egg-info residue before the second call's own
    build starts -- confirm the second call sees a clean directory."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    marker = tmp_path / "second-call.json"
    stub = f"""
function Get-Command {{ param($Name, [switch]$ErrorAction2) return $null }}
function python3 {{
    param()
    $args2 = $args
    if ($args2 -contains '--force-reinstall') {{
        New-Item -ItemType Directory -Force -Path "{plugin_dir}\\build\\lib\\some_pkg" | Out-Null
        New-Item -ItemType Directory -Force -Path "{plugin_dir}\\some_pkg.egg-info" | Out-Null
        $global:LASTEXITCODE = 0
        return
    }}
    $residue = (Test-Path "{plugin_dir}\\build") -or (Test-Path "{plugin_dir}\\some_pkg.egg-info")
    Set-Content -Path "{marker}" -Value ([string]$residue)
    $global:LASTEXITCODE = 0
}}
"""
    # python3 stands in for $VenvPython (set to "python3" above); Get-Command
    # is stubbed out so the uv branch is never taken.
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXIT:0" in result.stdout
    assert marker.exists(), result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8").strip() == "False"
