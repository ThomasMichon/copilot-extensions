"""PowerShell execution regression coverage for install.ps1's
Invoke-UvPipInstallResilient build-artifact scrub -- the agent-bridge
counterpart to agent-dispatch's test_install_ps1_build_artifact_scrub.py.

Installing FROM the pristine payload directory ($PluginDir) leaves
setuptools' own build/ + *.egg-info staging behind IN that tree. Left in
place, a stale build/ can silently shadow fresh src/ on a later install if
setuptools' incremental-build mtime check decides nothing "changed" --
confirmed live on POSIX (copilot-extensions#3444): a truncated recipes_cli.py
shipped this way and crash-looped a production daemon for ~8h. The Windows
`Invoke-UvPipInstallResilient` had no scrub at all before this fix (unlike
its POSIX sibling, `_uv_pip_install_resilient`): this module actually
EXECUTES the extracted function under `pwsh`, with a stubbed `uv`, to prove
the fix works.
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


def _extract_function(name: str) -> str:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    start = text.index(f"function {name}")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name!r}")


def _run_harness(plugin_dir: Path, stub_body: str, extra_script: str) -> subprocess.CompletedProcess:
    harness = plugin_dir / "harness.ps1"
    harness.write_text(
        f'$PluginDir = "{plugin_dir}"\n'
        "function Write-Warn { param($msg) }\n"
        "function Test-IsSreModuleMismatch { param($Output) return $Output -match 'SRE module mismatch' }\n"
        + stub_body
        + "\n\n"
        + _extract_function("Invoke-UvPipInstallResilient")
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


def test_successful_install_scrubs_before_and_after(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    stub = """
function uv { Write-Output 'Installed 1 package'; $global:LASTEXITCODE = 0 }
"""
    extra = """
$result = Invoke-UvPipInstallResilient @('some-package')
Write-Output "EXITCODE=$($result.ExitCode)"
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXITCODE=0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()
    assert not (plugin_dir / "src" / "some_pkg.egg-info").exists()


def test_preexisting_residue_is_gone_before_the_first_attempt(tmp_path: Path) -> None:
    """Regression (2026-09-23, copilot-extensions#3456 review): this
    function previously had NO scrub at all. The stub `uv` asserts the
    residue (including the src-layout egg-info) is already gone by the time
    it's first invoked."""
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
    extra = """
$result = Invoke-UvPipInstallResilient @('some-package')
Write-Output "EXITCODE=$($result.ExitCode)"
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXITCODE=0" in result.stdout
    assert "residue still present" not in result.stderr


def test_retry_rescrubs_residue_recreated_during_the_failed_attempt(tmp_path: Path) -> None:
    """Regression: a failed first attempt (or a concurrent installer racing
    the retry delay) can recreate residue -- the retry must see a clean
    directory too, not just the very first call."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    stub = f"""
function uv {{
    $n = [int](Get-Content '{counter_file}')
    $n += 1
    Set-Content -Path '{counter_file}' -Value $n
    if ($n -lt 2) {{
        New-Item -ItemType Directory -Force -Path "{plugin_dir}\\build\\lib\\some_pkg" | Out-Null
        New-Item -ItemType Directory -Force -Path "{plugin_dir}\\some_pkg.egg-info" | Out-Null
        Write-Output 'AssertionError: SRE module mismatch'
        $global:LASTEXITCODE = 1
        return
    }}
    if ((Test-Path "{plugin_dir}\\build") -or (Test-Path "{plugin_dir}\\some_pkg.egg-info")) {{
        [Console]::Error.WriteLine('residue still present at retry time')
        $global:LASTEXITCODE = 1
        return
    }}
    Write-Output 'Installed 1 package'
    $global:LASTEXITCODE = 0
}}
function Start-Sleep {{ param($Seconds) }}
"""
    extra = """
$result = Invoke-UvPipInstallResilient @('some-package')
Write-Output "EXITCODE=$($result.ExitCode)"
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXITCODE=0" in result.stdout
    assert "residue still present" not in result.stderr


def test_local_vendored_source_dir_is_also_scrubbed(tmp_path: Path) -> None:
    """Regression (copilot-extensions#3456 review): agent-bridge installs
    vendored dependencies (ssh-manager, credential-relay, zdd, ...) from
    their OWN local source trees, not from $PluginDir. Those trees use the
    same setuptools src-layout and accumulate the identical stale
    build/egg-info residue -- a $PluginDir-only scrub never reaches it, so
    passing -SourceDir must scrub that tree too."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    vendored_dir = tmp_path / "ssh-manager"
    vendored_dir.mkdir()
    _seed_build_residue(vendored_dir)
    _seed_src_layout_egg_info(vendored_dir)
    stub = """
function uv { Write-Output 'Installed 1 package'; $global:LASTEXITCODE = 0 }
"""
    extra = f"""
$result = Invoke-UvPipInstallResilient -SourceDir "{vendored_dir}" @('some-package')
Write-Output "EXITCODE=$($result.ExitCode)"
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXITCODE=0" in result.stdout
    assert not (vendored_dir / "build").exists()
    assert not (vendored_dir / "some_pkg.egg-info").exists()
    assert not (vendored_dir / "src" / "some_pkg.egg-info").exists()


def test_local_vendored_source_dir_scrub_does_not_touch_unrelated_plugin_dir(
    tmp_path: Path,
) -> None:
    """The vendored-source scrub is additive, not a replacement: $PluginDir
    residue unrelated to the vendored install must be left alone."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    unrelated = plugin_dir / "unrelated.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")
    vendored_dir = tmp_path / "ssh-manager"
    vendored_dir.mkdir()
    _seed_build_residue(vendored_dir)
    stub = """
function uv { Write-Output 'Installed 1 package'; $global:LASTEXITCODE = 0 }
"""
    extra = f"""
$result = Invoke-UvPipInstallResilient -SourceDir "{vendored_dir}" @('some-package')
Write-Output "EXITCODE=$($result.ExitCode)"
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXITCODE=0" in result.stdout
    assert not (vendored_dir / "build").exists()
    assert unrelated.exists()
