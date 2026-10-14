"""Source-resolution guard for the canonical dependency-policy preinstall loop.

Both resolver-discovery branches must use the policy adapter and preserve
plugin-local/canonical library resolution. The adapter spy does not exercise
dependency-age admission.
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

_LOOP_START = "foreach ($lib in @(\n    @{ Dir = 'credential-relay';"
_LOOP_END = "\n\nif (Get-Command uv -ErrorAction SilentlyContinue) {"


def _extract_loop() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    return _LOOP_START + text.split(_LOOP_START, 1)[1].split(_LOOP_END, 1)[0]


def _run(
    pwsh: str, plugin_dir: Path, have_uv: bool, *, marker: Path
) -> subprocess.CompletedProcess[str]:
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
function Get-Command {{
    param([string]$Name)
    if ($Name -ne 'uv') {{ throw 'Unexpected executable discovery' }}
    if ($haveUv) {{ return 'fixture-uv' }}
}}
function Invoke-Hidden {{
    $cmdArgs = $args
    if ($cmdArgs[0] -ne 'Invoke-StubVenvPython' -or
        $cmdArgs[1] -ne '-I' -or $cmdArgs[2] -ne '{policy}' -or
        -not (Test-Path -LiteralPath $cmdArgs[2]) -or
        $cmdArgs[3] -ne '--uv' -or $cmdArgs[4] -ne 'uv' -or
        $cmdArgs[5] -ne '--python' -or $cmdArgs[6] -ne 'Invoke-StubVenvPython') {{
        throw 'Preinstall bypassed canonical dependency policy'
    }}
    # PowerShell consumes the standalone separator when binding a function spy.
    $pathIndex = if ($haveUv) {{ 9 }} else {{ 8 }}
    if ($haveUv -and $cmdArgs[7] -ne '--reinstall-package') {{
        throw 'Preinstall omitted package reinstall'
    }}
    Add-Content -LiteralPath '{marker}' -Value ("POLICY_INSTALL:" + $cmdArgs[$pathIndex])
    $global:LASTEXITCODE = 0
}}
$PSScriptRoot = '{scripts}'
$haveUv = ${str(have_uv).lower()}
$VenvPython = 'Invoke-StubVenvPython'
$PluginDir = '{plugin_dir}'
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


def _installed_paths(marker: Path) -> list[str]:
    if not marker.exists():
        return []
    return [
        line.removeprefix("POLICY_INSTALL:")
        for line in marker.read_text(encoding="utf-8").splitlines()
        if line.startswith("POLICY_INSTALL:")
    ]


def _make_libs(root: Path) -> None:
    for lib in ("credential-relay", "agent-procutil", "single-instance-lease", "zdd"):
        lib_dir = root / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_resolves_plugin_local_copy(have_uv: bool, tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-mcp"
    _make_libs(plugin_dir / "libs")
    marker = tmp_path / "marker.txt"

    proc = _run(pwsh, plugin_dir, have_uv, marker=marker)
    assert proc.returncode == 0, proc.stderr

    installs = _installed_paths(marker)
    assert len(installs) == 4
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(plugin_dir / "libs")))


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_falls_back_to_repo_root_canonical_when_absent(
    have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-mcp"
    plugin_dir.mkdir(parents=True)
    canonical = tmp_path / "libs"
    _make_libs(canonical)
    marker = tmp_path / "marker.txt"

    proc = _run(pwsh, plugin_dir, have_uv, marker=marker)
    assert proc.returncode == 0, proc.stderr

    installs = _installed_paths(marker)
    assert len(installs) == 4
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(canonical)))
