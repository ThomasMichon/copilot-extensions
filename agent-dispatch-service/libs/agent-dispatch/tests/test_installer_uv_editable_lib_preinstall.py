"""Guard for install.ps1's non-uv (bare-pip) preinstall loop covering the
uv-editable canonical-reference libs (vendor-pointer-generalization effort):
dropin-registry, plugin-resolve, single-instance-lease, agent-procutil,
plugin-activation. Exercises the loop via real pwsh execution (stubbing the actual install
call and Resolve-VendoredLib's own canonical fallback) to confirm both
resolution and the required dropin-registry/plugin-resolve-before-
plugin-activation install order -- a regression here would still pass
every other installer guard while silently reintroducing the bare-pip
dependency failure Copilot's review caught on PR #4420."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard


def _extract(start: str, end: str) -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    return start + text.split(start, 1)[1].split(end, 1)[0]


def _resolve_vendored_lib_fn() -> str:
    return _extract("function Resolve-VendoredLib {", "\nfunction Resolve-Zdd")


def _preinstall_loop() -> str:
    return _extract(
        "foreach ($lib in @(\n        @{ Dir = 'dropin-registry';",
        "\n\n    # -ReinstallPackage",
    )


def _run(pwsh: str, plugin_dir: Path) -> subprocess.CompletedProcess[str]:
    script = f"""
function Remove-PluginBuildArtifacts {{ param([string]$PluginDir, [string]$ExtraDir) }}
function Write-Fail {{ param([string]$Message) }}
function Write-Ok {{ param([string]$Message) [Console]::Out.Write($Message + "|") }}
function uv {{
    # Stub: report the resolved local-copy dir instead of actually installing.
    # Matches this script's call shape exactly:
    # uv pip install --python $VenvPython "$libDir" --reinstall-package ... --quiet
    [Console]::Out.Write("INSTALL:" + $args[4] + "|")
    $global:LASTEXITCODE = 0
}}
{_resolve_vendored_lib_fn()}
$PluginDir = '{plugin_dir}'
$BuildSrcDir = '{plugin_dir}'
$VenvPython = 'unused'
$UvCommand = 'uv'
$prevEAP = 'Continue'
. '{PLUGIN.parents[1] / 'libs/installer-engine/installer-engine.ps1'}'
function Write-Warn {{ param($Message) Write-Host $Message }}
function Start-Sleep {{ param($Seconds) }}
{_preinstall_loop()}
"""
    return subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ},
        timeout=30,
    )


def test_preinstall_loop_resolves_each_lib_and_installs_in_order(tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-dispatch"
    for lib in ("dropin-registry", "plugin-resolve", "single-instance-lease", "agent-procutil", "plugin-activation"):
        lib_dir = plugin_dir / "libs" / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run(pwsh, plugin_dir)
    assert proc.returncode == 0, proc.stderr

    installs = [
        seg.split(":", 1)[1]
        for seg in proc.stdout.split("|")
        if seg.startswith("INSTALL:")
    ]
    assert len(installs) == 5
    order = [Path(p).name for p in installs]
    assert order.index("plugin-activation") > order.index("dropin-registry")
    assert order.index("plugin-activation") > order.index("plugin-resolve")

    oks = [seg for seg in proc.stdout.split("|") if seg and not seg.startswith("INSTALL:")]
    assert oks == [
        "dropin-registry installed",
        "plugin-resolve installed",
        "single-instance-lease installed",
        "agent-procutil installed",
        "plugin-activation installed",
    ]


def test_preinstall_loop_falls_back_to_repo_root_canonical_via_resolve_vendored_lib(
    tmp_path: Path,
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-dispatch"
    plugin_dir.mkdir(parents=True)
    # No plugins/agent-dispatch/libs/<lib> for any of them -- dev checkout,
    # uv-editable canonical reference with no per-plugin copy.
    canonical = tmp_path / "libs"
    for lib in ("dropin-registry", "plugin-resolve", "single-instance-lease", "agent-procutil", "plugin-activation"):
        lib_dir = canonical / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run(pwsh, plugin_dir)
    assert proc.returncode == 0, proc.stderr
    installs = [
        seg.split(":", 1)[1]
        for seg in proc.stdout.split("|")
        if seg.startswith("INSTALL:")
    ]
    assert len(installs) == 5
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(canonical)))
