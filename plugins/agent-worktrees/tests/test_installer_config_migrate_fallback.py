"""Guard for install.ps1's config-migrate resolution: both the plugin-local
(marketplace/release) copy and the repo-root canonical fallback (dev
checkout, uv-editable canonical reference -- vendor-pointer-generalization
effort, Phase 1) must resolve correctly. A regression that drops the
fallback would still pass every other installer guard while silently
reintroducing the bare-pip dependency failure Copilot's review caught on
PR #4409 (Invoke-VenvPackageInstall's fallback to `python -m pip`, used
when `uv` is unavailable, does not honor `[tool.uv.sources]`)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard


def _extract_config_migrate_block() -> str:
    installer = INSTALLER.read_text(encoding="utf-8")
    block = installer.split(
        "# Vendored config-schema-migration lib (agent-config-migrate / module",
        1,
    )[1]
    block = block.split("# Vendored plugin-resolution lib", 1)[0]
    # Re-attach the comment's own first line (split above consumed it).
    return "# Vendored config-schema-migration lib (agent-config-migrate / module" + block


def _run_block(pwsh: str, plugin_dir: Path) -> subprocess.CompletedProcess[str]:
    block = _extract_config_migrate_block()
    script = f"""
function Invoke-VenvPackageInstall {{
    param([string]$VenvPython, [string]$PkgName, [string]$PkgDir)
    # Stub: report the resolved PkgDir instead of actually installing.
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


def test_config_migrate_resolves_plugin_local_copy_when_present(tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-worktrees"
    local_lib = plugin_dir / "libs" / "config-migrate"
    local_lib.mkdir(parents=True)
    (local_lib / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run_block(pwsh, plugin_dir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == str(local_lib)


def test_config_migrate_falls_back_to_repo_root_canonical_when_absent(
    tmp_path: Path,
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-worktrees"
    plugin_dir.mkdir(parents=True)
    # No plugins/agent-worktrees/libs/config-migrate -- dev checkout, uv-editable
    # canonical reference with no per-plugin copy.
    canonical_lib = tmp_path / "libs" / "config-migrate"
    canonical_lib.mkdir(parents=True)
    (canonical_lib / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run_block(pwsh, plugin_dir)
    assert proc.returncode == 0, proc.stderr
    # `Join-Path` is literal-concat (no ".." normalization), so compare
    # resolved real paths rather than the raw string.
    assert os.path.realpath(proc.stdout) == os.path.realpath(canonical_lib)


def test_config_migrate_is_a_noop_when_neither_copy_exists(tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-worktrees"
    plugin_dir.mkdir(parents=True)

    proc = _run_block(pwsh, plugin_dir)
    assert proc.returncode == 0, proc.stderr
    # Neither branch's Invoke-VenvPackageInstall call fires -- nothing written.
    assert proc.stdout == ""
