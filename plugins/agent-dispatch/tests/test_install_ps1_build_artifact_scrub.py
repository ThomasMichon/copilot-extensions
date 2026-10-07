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


def _extract_function_block(name: str) -> str:
    """Extract one `function <name> { ... }` block by brace-counting from
    its own opening `{`."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    func_start = text.index(f"function {name}")
    brace_start = text.index("{", func_start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[func_start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting function {name!r}")


def _extract_install_pkg_block() -> str:
    """Extract `Remove-PluginBuildArtifacts` (which `$scrubArtifacts` now
    delegates to) plus the `$StaleCacheRefreshPackages = @(...)`
    declaration through the end of the `$installPkg = { ... }` scriptblock,
    by brace-counting from the scriptblock's own opening `{` (the leading
    `@(...)` array is copied verbatim first since $installPkg's body
    references it)."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    remove_artifacts_fn = _extract_function_block("Remove-PluginBuildArtifacts")
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
                return remove_artifacts_fn + "\n\n" + text[array_start : i + 1]
        i += 1
    raise AssertionError("unbalanced braces extracting $installPkg")


def _run_harness(plugin_dir: Path, stub_body: str, extra_script: str) -> subprocess.CompletedProcess:
    harness = plugin_dir / "harness.ps1"
    harness.write_text(
        f'$PluginDir = "{plugin_dir}"\n'
        # Install-Runtime now builds from $BuildSrcDir (New-PluginBuildSnapshot's
        # durable per-version snapshot), never from the live $PluginDir -- see
        # that function's docstring. These tests only exercise scrub
        # correctness, so point $BuildSrcDir at the same fixture directory
        # $installPkg's extracted block actually reads/writes.
        f'$BuildSrcDir = "{plugin_dir}"\n'
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


def _seed_vendored_lib_build_residue(plugin_dir: Path, lib_name: str) -> Path:
    """Seed stale build/egg-info residue inside libs/<lib_name>/ -- the
    Windows counterpart to the POSIX helper of the same name (see
    test_install_sh_build_artifact_scrub.py)."""
    lib_dir = plugin_dir / "libs" / lib_name
    (lib_dir / "build" / "lib" / "some_vendored_pkg").mkdir(parents=True)
    (lib_dir / "build" / "lib" / "some_vendored_pkg" / "mod.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (lib_dir / "some_vendored_pkg.egg-info").mkdir()
    (lib_dir / "some_vendored_pkg.egg-info" / "PKG-INFO").write_text(
        "stub\n", encoding="utf-8"
    )
    (lib_dir / "src" / "some_vendored_pkg.egg-info").mkdir(parents=True)
    (lib_dir / "src" / "some_vendored_pkg.egg-info" / "PKG-INFO").write_text(
        "stub\n", encoding="utf-8"
    )
    return lib_dir


def test_vendored_lib_build_residue_is_also_scrubbed(tmp_path: Path) -> None:
    """Regression (2026-09-27, confirmed in a live POSIX deployment, same gap
    on Windows): every vendored `[tool.uv.sources]` workspace path dep
    under libs/<name>/ is its OWN independent setuptools build root and
    accumulates the identical build/*.egg-info residue as $PluginDir --
    the scrub must reach every immediate child of libs/, not just
    $PluginDir itself."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    procutil_dir = _seed_vendored_lib_build_residue(plugin_dir, "agent-procutil")
    zdd_dir = _seed_vendored_lib_build_residue(plugin_dir, "zdd")
    stub = f"""
function uv {{
    if ((Test-Path "{procutil_dir}\\build") -or (Test-Path "{procutil_dir}\\some_vendored_pkg.egg-info") `
        -or (Test-Path "{zdd_dir}\\build") -or (Test-Path "{zdd_dir}\\some_vendored_pkg.egg-info")) {{
        [Console]::Error.WriteLine('vendored lib residue still present at install time')
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
    assert not (plugin_dir / "build").exists()
    for lib_dir in (procutil_dir, zdd_dir):
        assert not (lib_dir / "build").exists()
        assert not (lib_dir / "some_vendored_pkg.egg-info").exists()
        assert not (lib_dir / "src" / "some_vendored_pkg.egg-info").exists()


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
function Get-Command {{ [CmdletBinding()] param($Name) return $null }}
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


def _extract_zdd_preinstall_block() -> str:
    """Extract the standalone zdd pre-install block (runs BEFORE
    $installPkg is even defined, from its own separately-resolved
    libs/zdd/ tree) plus Remove-PluginBuildArtifacts and Test-ZddInstalled,
    which it depends on. Brace-counts the full if/elseif/else chain from
    its own opening `{` so the extracted text is a syntactically complete,
    standalone statement."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    comment_start = text.index("# zdd (zero-downtime graceful-cutover primitives")
    if_start = text.index("if ($ZddDir) {", comment_start)
    brace_start = text.index("{", if_start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                # Keep consuming through any trailing `elseif`/`else` arms
                # chained onto this same statement.
                j = i + 1
                while True:
                    rest = text[j:].lstrip()
                    if rest.startswith("elseif") or rest.startswith("else"):
                        arm_brace = text.index("{", j)
                        arm_depth = 0
                        k = arm_brace
                        while k < len(text):
                            if text[k] == "{":
                                arm_depth += 1
                            elif text[k] == "}":
                                arm_depth -= 1
                                if arm_depth == 0:
                                    break
                            k += 1
                        j = k + 1
                    else:
                        break
                block = text[comment_start:j]
                return "\n\n".join(
                    [
                        _extract_function_block("Remove-PluginBuildArtifacts"),
                        _extract_function_block("Test-ZddInstalled"),
                        block,
                    ]
                )
        i += 1
    raise AssertionError("unbalanced braces extracting the zdd pre-install block")


def test_standalone_zdd_preinstall_scrubs_before_building(tmp_path: Path) -> None:
    """Regression: the standalone zdd pre-install (which runs BEFORE the
    main $installPkg scriptblock is even defined, building from the exact
    same libs/zdd/ tree) must also scrub stale build artifacts first --
    otherwise a stale libs/zdd/build/lib can still be consumed by THIS
    earlier build, and the later main-install scrub only cleans up after
    the broken package has already been installed."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    zdd_dir = plugin_dir / "libs" / "zdd"
    (zdd_dir / "build" / "lib" / "zdd").mkdir(parents=True)
    (zdd_dir / "build" / "lib" / "zdd" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (zdd_dir / "some_pkg.egg-info").mkdir()

    stub = f"""
function Write-Ok {{ param($m) Write-Output "OK: $m" }}
function Write-Fail {{ param($m) Write-Output "FAIL: $m" }}
function Write-Skip {{ param($m) Write-Output "SKIP: $m" }}
function Get-Command {{ [CmdletBinding()] param($Name) return $null }}
function Resolve-Zdd {{ param($LibRoot) return "{zdd_dir}" }}
$VenvPython = "python3"
$PluginDir = "{plugin_dir}"
$BuildSrcDir = "{plugin_dir}"
$prevEAP = $ErrorActionPreference
function python3 {{
    param()
    if ((Test-Path "{zdd_dir}\\build") -or (Test-Path "{zdd_dir}\\some_pkg.egg-info")) {{
        [Console]::Error.WriteLine('residue still present at zdd install time')
        $global:LASTEXITCODE = 1
        return
    }}
    $global:LASTEXITCODE = 0
}}
"""
    harness = plugin_dir / "harness.ps1"
    harness.write_text(
        stub + "\n\n" + _extract_zdd_preinstall_block() + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
    )
    assert "residue still present" not in result.stderr, result.stdout + result.stderr
    assert not (zdd_dir / "build").exists()
    assert not (zdd_dir / "some_pkg.egg-info").exists()


def test_standalone_zdd_preinstall_scrubs_external_resolved_path(tmp_path: Path) -> None:
    """Regression: Resolve-Zdd (via Resolve-VendoredLib's registry/checkout
    fallback branches) can resolve to a path OUTSIDE $PluginDir entirely --
    a sibling copilot-extensions checkout, not the marketplace-installed
    payload. $PluginDir/libs/* scrubbing never reaches that tree, so the
    standalone zdd pre-install must pass its own resolved $ZddDir to
    Remove-PluginBuildArtifacts's -ExtraDir explicitly."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    # Deliberately a SIBLING of plugin_dir, not nested under it -- not
    # reachable via $PluginDir/libs/* scrubbing at all.
    external_zdd_dir = tmp_path / "external-checkout" / "libs" / "zdd"
    (external_zdd_dir / "build" / "lib" / "zdd").mkdir(parents=True)
    (external_zdd_dir / "build" / "lib" / "zdd" / "__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (external_zdd_dir / "some_pkg.egg-info").mkdir()

    stub = f"""
function Write-Ok {{ param($m) Write-Output "OK: $m" }}
function Write-Fail {{ param($m) Write-Output "FAIL: $m" }}
function Write-Skip {{ param($m) Write-Output "SKIP: $m" }}
function Get-Command {{ [CmdletBinding()] param($Name) return $null }}
function Resolve-Zdd {{ param($LibRoot) return "{external_zdd_dir}" }}
$VenvPython = "python3"
$PluginDir = "{plugin_dir}"
$BuildSrcDir = "{plugin_dir}"
$prevEAP = $ErrorActionPreference
function python3 {{
    param()
    if ((Test-Path "{external_zdd_dir}\\build") -or (Test-Path "{external_zdd_dir}\\some_pkg.egg-info")) {{
        [Console]::Error.WriteLine('external residue still present at zdd install time')
        $global:LASTEXITCODE = 1
        return
    }}
    $global:LASTEXITCODE = 0
}}
"""
    harness = plugin_dir / "harness.ps1"
    harness.write_text(
        stub + "\n\n" + _extract_zdd_preinstall_block() + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
    )
    assert "external residue still present" not in result.stderr, result.stdout + result.stderr
    assert not (external_zdd_dir / "build").exists()
    assert not (external_zdd_dir / "some_pkg.egg-info").exists()
