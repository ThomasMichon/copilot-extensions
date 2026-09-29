"""Guards for install.ps1's vendored shared-lib pre-install blocks.
pre-install blocks (vendor-pointer-generalization effort): each must resolve
both the plugin-local (marketplace/release) copy and the repo-root canonical
fallback (dev checkout, uv-editable canonical reference). A regression that
drops any fallback would still pass every other installer guard while
silently reintroducing the bare-pip dependency failure Copilot's review
caught on PR #4420 -- and the later transitive-dependency blocks must keep
their required order."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard

_BLOCKS = {
    "plugin-resolve": (
        "# Vendored plugin-resolution lib (agent-plugin-resolve / module",
        "# Vendored plugin contribution registry lib",
    ),
    "dropin-registry": (
        "# Vendored plugin contribution registry lib (agent-dropin-registry /",
        "# Vendored plugin activation/inventory lib",
    ),
    "plugin-activation": (
        "# Vendored plugin activation/inventory lib (agent-plugin-activation /",
        "# Vendored zero-downtime cutover lib",
    ),
}


def _extract_block(lib: str) -> str:
    installer = INSTALLER.read_text(encoding="utf-8")
    start, end = _BLOCKS[lib]
    return start + installer.split(start, 1)[1].split(end, 1)[0]


def _run_block(pwsh: str, lib: str, plugin_dir: Path) -> subprocess.CompletedProcess[str]:
    block = _extract_block(lib)
    script = f"""
function Invoke-VenvPackageInstall {{
    param([string]$VenvPython, [string]$PkgName, [string]$PkgDir)
    [Console]::Out.Write($PkgDir)
    return [pscustomobject]@{{ ExitCode = 0; Output = '' }}
}}
function Write-ServiceErr {{ param([string]$Message) }}
$PluginDir = '{plugin_dir}'
$VenvPython = 'unused'
$ErrorActionPreference = 'Stop'
$prevEAP = $ErrorActionPreference
{block}
"""
    return subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ},
        timeout=30,
    )


@pytest.mark.parametrize("lib", ["plugin-resolve", "dropin-registry", "plugin-activation"])
def test_lib_resolves_plugin_local_copy_when_present(lib: str, tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-worktrees"
    local_lib = plugin_dir / "libs" / lib
    local_lib.mkdir(parents=True)
    (local_lib / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run_block(pwsh, lib, plugin_dir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == str(local_lib)


@pytest.mark.parametrize("lib", ["plugin-resolve", "dropin-registry", "plugin-activation"])
def test_lib_falls_back_to_repo_root_canonical_when_absent(lib: str, tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-worktrees"
    plugin_dir.mkdir(parents=True)
    canonical_lib = tmp_path / "libs" / lib
    canonical_lib.mkdir(parents=True)
    (canonical_lib / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run_block(pwsh, lib, plugin_dir)
    assert proc.returncode == 0, proc.stderr
    # `Join-Path` is literal-concat (no ".." normalization), so compare
    # resolved real paths rather than the raw string.
    assert os.path.realpath(proc.stdout) == os.path.realpath(canonical_lib)


@pytest.mark.parametrize("lib", ["plugin-resolve", "dropin-registry", "plugin-activation"])
def test_lib_is_a_noop_when_neither_copy_exists(lib: str, tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-worktrees"
    plugin_dir.mkdir(parents=True)

    proc = _run_block(pwsh, lib, plugin_dir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_plugin_activation_block_runs_after_its_own_transitive_deps():
    """plugin_activation imports dropin_registry and plugin_resolve at module
    load time, so its own install step must come after both of theirs --
    installing it first would leave its own dependencies briefly
    unresolvable mid-install."""
    installer = INSTALLER.read_text(encoding="utf-8")
    no_deps_marker = installer.index("$installRes = Invoke-VenvPackageInstall")
    body = installer[:no_deps_marker]
    positions = {
        lib: body.rindex(f"libs\\{lib}")
        for lib in ("plugin-resolve", "dropin-registry", "plugin-activation")
    }
    assert positions["plugin-activation"] > positions["dropin-registry"]
    assert positions["plugin-activation"] > positions["plugin-resolve"]


def test_zdd_preinstall_block_exists_before_main_package_install():
    installer = INSTALLER.read_text(encoding="utf-8")
    zdd_marker = "# Vendored zero-downtime cutover lib (agent-zdd / module zdd)."
    main_install = "$installRes = Invoke-VenvPackageInstall -VenvPython $VenvPython -PkgName 'agent-worktrees' -PkgDir $PluginDir"
    assert zdd_marker in installer
    assert "-PkgName 'agent-zdd'" in installer
    assert installer.index(zdd_marker) < installer.index(main_install)
