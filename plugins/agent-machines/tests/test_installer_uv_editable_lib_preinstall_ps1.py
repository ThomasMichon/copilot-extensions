"""Guard for init.ps1's non-uv (bare-pip) preinstall loop covering the
uv-editable canonical-reference libs (vendor-pointer-generalization effort):
dropin-registry, plugin-resolve, agent-procutil, plugin-activation.
Exercises the loop via real pwsh execution for both the uv path and the
bare-pip fallback path,
confirming plugin-local/repo-root-canonical resolution and the required
dropin-registry/plugin-resolve-before-plugin-activation install order -- a
regression here would still pass every other installer guard while
silently reintroducing the bare-pip dependency failure Copilot's review
caught on PR #4420."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "init.ps1"

pytestmark = pytest.mark.guard


def _extract_loop() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    start = "foreach ($lib in @(\n    @{ Dir = 'dropin-registry';"
    end = "\n\n# -- 3. Install the package"
    return start + text.split(start, 1)[1].split(end, 1)[0]


def _run(pwsh: str, plugin_dir: Path, have_uv: bool) -> subprocess.CompletedProcess[str]:
    loop = _extract_loop()
    uv_stub = """
function uv {
    [Console]::Out.Write("UV_INSTALL:" + $args[-2] + "|")
    $global:LASTEXITCODE = 0
}
""" if have_uv else "function uv { throw 'uv must not be invoked when haveUv is false' }"
    script = f"""
function Write-Fail {{ param([string]$Message) }}
function Write-Ok {{ param([string]$Message) [Console]::Out.Write($Message + "|") }}
{uv_stub}
function Invoke-StubVenvPython {{ [Console]::Out.Write("PIP_INSTALL:" + $args[-1] + "|"); $global:LASTEXITCODE = 0 }}
$VenvPython = 'Invoke-StubVenvPython'
$PluginDir = '{plugin_dir}'
$haveUv = ${str(have_uv).lower()}
{loop}
"""
    return subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ},
        timeout=30,
    )


def _make_libs(root: Path) -> None:
    for lib in ("dropin-registry", "plugin-resolve", "agent-procutil", "plugin-activation"):
        lib_dir = root / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_resolves_plugin_local_copy_and_orders_correctly(
    have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-machines"
    _make_libs(plugin_dir / "libs")

    proc = _run(pwsh, plugin_dir, have_uv)
    assert proc.returncode == 0, proc.stderr

    marker = "UV_INSTALL:" if have_uv else "PIP_INSTALL:"
    installs = [
        seg.split(":", 1)[1] for seg in proc.stdout.split("|") if seg.startswith(marker)
    ]
    assert len(installs) == 4
    order = [Path(p).name for p in installs]
    assert order.index("plugin-activation") > order.index("dropin-registry")
    assert order.index("plugin-activation") > order.index("plugin-resolve")


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_falls_back_to_repo_root_canonical_when_absent(
    have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-machines"
    plugin_dir.mkdir(parents=True)
    canonical = tmp_path / "libs"
    _make_libs(canonical)

    proc = _run(pwsh, plugin_dir, have_uv)
    assert proc.returncode == 0, proc.stderr

    marker = "UV_INSTALL:" if have_uv else "PIP_INSTALL:"
    installs = [
        seg.split(":", 1)[1] for seg in proc.stdout.split("|") if seg.startswith(marker)
    ]
    assert len(installs) == 4
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(canonical)))
