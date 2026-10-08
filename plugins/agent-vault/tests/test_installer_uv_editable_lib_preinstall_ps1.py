"""Guard for install.ps1's non-uv (bare-pip) preinstall loop covering the
`[tool.uv.sources]` workspace path deps: zdd (real local copy),
agent-procutil, single-instance-lease (both `uv`-editable canonical
references, vendor-pointer-generalization effort). Exercises the loop via
real pwsh execution for both the uv path and the bare-pip fallback path --
a regression here would still pass every other installer guard while
silently reintroducing the bare-pip dependency failure the review on
PR #4465 caught."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard

_LOOP_START = "    foreach ($lib in @('zdd', 'agent-procutil', 'single-instance-lease')) {"
_LOOP_END = "\n    if (Get-Command uv -ErrorAction SilentlyContinue) {"


def _extract_loop() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.rfind(_LOOP_START)
    end = text.index(_LOOP_END, start)
    return text[start:end]


def _run(
    pwsh: str, plugin_dir: Path, have_uv: bool, *, marker: Path
) -> subprocess.CompletedProcess[str]:
    loop = _extract_loop()
    uv_stub = f"""
function uv {{
    Add-Content -Path '{marker}' -Value ("UV_INSTALL_ARGS:" + ($args -join ' '))
    $global:LASTEXITCODE = 0
}}
function Invoke-UvPipInstallResilient {{
    param([string[]]$Arguments, [string]$UvCommand = 'uv', [string]$PayloadDirToScrub)
    & $UvCommand @Arguments
    return [pscustomobject]@{{ Output = ''; ExitCode = $LASTEXITCODE }}
}}
""" if have_uv else ""
    script = f"""
function Write-Fail {{ param([string]$Message) }}
function Write-Host {{ param([Parameter(ValueFromPipeline=$true)]$Object) }}
{uv_stub}
function Invoke-StubVenvPython {{
    Add-Content -Path '{marker}' -Value ("PIP_INSTALL_ARGS:" + ($args -join ' '))
    $global:LASTEXITCODE = 0
}}
$VenvPython = 'Invoke-StubVenvPython'
$PluginDir = '{plugin_dir}'
$prevEAP = 'Continue'
$ErrorActionPreference = 'Continue'
{loop}
"""
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if not have_uv:
        # A single `shutil.which("uv")` hit only finds the FIRST PATH
        # directory carrying a `uv` executable -- insufficient on a
        # machine where a package manager (e.g. WinGet) installs both the
        # real binary AND a separate shim/Links directory, each its own
        # PATH entry, both resolving `uv` (coverage-guided-ci effort,
        # Phase 3.5, 2026-10-07: this duplication is real on at least one
        # dev machine). Strip every PATH directory carrying a `uv`/`uv.exe`
        # executable, not just the first one `which` would report.
        uv_exe = "uv.exe" if os.name == "nt" else "uv"
        path_entries = [
            p for p in path_entries if not (Path(p) / uv_exe).is_file()
        ]
    environment = {**os.environ, "PATH": os.pathsep.join(path_entries)}
    return subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


def _installed_paths(marker: Path) -> list[str]:
    if not marker.exists():
        return []
    out = []
    for line in marker.read_text(encoding="utf-8").splitlines():
        if line.startswith("PIP_INSTALL_ARGS:"):
            out.append(line[len("PIP_INSTALL_ARGS:"):].split()[-1])
        elif line.startswith("UV_INSTALL_ARGS:"):
            args = line[len("UV_INSTALL_ARGS:"):].split()
            for arg in args:
                if arg not in {
                    "pip", "install", "--python", "Invoke-StubVenvPython", "--quiet",
                }:
                    out.append(arg)
                    break
    return out


def _make_libs(root: Path) -> None:
    for lib in ("zdd", "agent-procutil", "single-instance-lease"):
        lib_dir = root / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_resolves_plugin_local_copy(have_uv: bool, tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-vault"
    _make_libs(plugin_dir / "libs")
    marker = tmp_path / "marker.txt"

    proc = _run(pwsh, plugin_dir, have_uv, marker=marker)
    assert proc.returncode == 0, proc.stderr

    installs = _installed_paths(marker)
    assert len(installs) == 3
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(plugin_dir / "libs")))


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_falls_back_to_repo_root_canonical_when_absent(
    have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-vault"
    plugin_dir.mkdir(parents=True)
    canonical = tmp_path / "libs"
    _make_libs(canonical)
    marker = tmp_path / "marker.txt"

    proc = _run(pwsh, plugin_dir, have_uv, marker=marker)
    assert proc.returncode == 0, proc.stderr

    installs = _installed_paths(marker)
    assert len(installs) == 3
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(canonical)))
