"""Source-resolution guard for the canonical dependency-policy preinstall loop.

Both resolver-discovery branches must use the policy adapter and preserve
plugin-local/canonical resolution and activation-library install ordering.
The adapter spy does not exercise dependency-age admission.
"""

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
    assert "--uv uv -- --python $VenvPython" in loop
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    policy = scripts / "installer_dependency_policy.py"
    shutil.copyfile(INSTALLER.parent / policy.name, policy)
    shutil.copyfile(
        INSTALLER.parent / "installer_dependency_config.py",
        scripts / "installer_dependency_config.py",
    )
    script = f"""
$ErrorActionPreference = 'Stop'
function Write-Fail {{ param([string]$Message) }}
function Write-Ok {{ param([string]$Message) [Console]::Out.Write($Message + "|") }}
function Invoke-StubVenvPython {{
    if ($args[0] -ne '-I' -or $args[1] -ne '{policy}' -or
        -not (Test-Path -LiteralPath $args[1]) -or
        $args[2] -ne '--uv' -or $args[3] -ne 'uv' -or
        $args[4] -ne '--python' -or $args[5] -ne 'Invoke-StubVenvPython') {{
        throw 'Preinstall bypassed canonical dependency policy'
    }}
    # PowerShell consumes the standalone separator when binding a function spy.
    $pathIndex = if ($haveUv) {{ 8 }} else {{ 7 }}
    if ($haveUv -and $args[6] -ne '--reinstall-package') {{
        throw 'Preinstall omitted package reinstall'
    }}
    [Console]::Out.Write("POLICY_INSTALL:" + $args[$pathIndex] + "|")
    $global:LASTEXITCODE = 0
}}
$PSScriptRoot = '{scripts}'
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
    for lib in (
        "dropin-registry", "plugin-resolve", "agent-procutil",
        "remote-login-shell", "machine-transport", "plugin-activation",
        "installation-context", "installer-readiness",
    ):
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

    marker = "POLICY_INSTALL:"
    installs = [
        seg.split(":", 1)[1] for seg in proc.stdout.split("|") if seg.startswith(marker)
    ]
    assert len(installs) == 8
    order = [Path(p).name for p in installs]
    assert "machine-transport" in order
    assert order.index("remote-login-shell") < order.index("machine-transport")
    assert order.index("plugin-activation") > order.index("dropin-registry")
    assert order.index("plugin-activation") > order.index("plugin-resolve")
    assert order.index("machine-transport") > order.index("remote-login-shell")
    assert order.index("installer-readiness") > order.index("installation-context")


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

    marker = "POLICY_INSTALL:"
    installs = [
        seg.split(":", 1)[1] for seg in proc.stdout.split("|") if seg.startswith(marker)
    ]
    assert len(installs) == 8
    order = [Path(p).name for p in installs]
    assert order.index("remote-login-shell") < order.index("machine-transport")
    assert order.index("installation-context") < order.index("installer-readiness")
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(canonical)))
