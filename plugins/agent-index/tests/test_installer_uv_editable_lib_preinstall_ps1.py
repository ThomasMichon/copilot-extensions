"""Guard for install.ps1's two `agent-procutil` non-uv (bare-pip) preinstall
blocks (vendor-pointer-generalization effort): the service-runtime venv and
the durable-engine-runtime venv. Both mirror the existing `zdd` preinstall
immediately above them. Exercises each block via real pwsh execution
(stubbing `Resolve-VendoredLib`'s own local-copy/canonical-fallback
resolution and the actual install call) for both the uv path and the
bare-pip fallback path -- a regression here would still pass every other
installer guard while silently reintroducing the bare-pip dependency
failure the review on PR #4465 caught."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard

_RESOLVE_VENDORED_LIB_START = "function Resolve-VendoredLib {"
_RESOLVE_VENDORED_LIB_END = "\n}\n\n"

_BLOCKS = {
    "service": (
        "    # agent-procutil is a `uv`-editable canonical reference in a dev",
        "\n    Remove-ConsoleTrampolines -VenvDir $VenvDir",
    ),
    "engine": (
        "    # agent-procutil is likewise a `uv`-editable canonical reference in a",
        "\n\n    # agent-index-engine",
    ),
}


def _extract(text: str, start: str, end: str) -> str:
    return start + text.split(start, 1)[1].split(end, 1)[0]


def _resolve_vendored_lib_fn(text: str) -> str:
    return _extract(text, _RESOLVE_VENDORED_LIB_START, _RESOLVE_VENDORED_LIB_END) + "\n}"


def _run(
    pwsh: str, block_name: str, plugin_dir: Path, have_uv: bool, *, isolated_home: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    text = INSTALLER.read_text(encoding="utf-8")
    start, end = _BLOCKS[block_name]
    block = _extract(text, start, end)
    block = block.replace("$VenvPython", "$TheVenvPython").replace(
        "$EngineVenvPython", "$TheVenvPython"
    )
    # The engine block pipes install output through `ForEach-Object` for
    # display formatting -- stub output must go to a marker FILE, never
    # stdout, so that pipeline can never swallow the proof of what ran.
    marker = isolated_home.parent / "marker.txt"
    # No `function uv` stub at all when `have_uv` is false: a same-named
    # function would still resolve via `Get-Command uv`, defeating the
    # PATH-removal simulation below (function resolution outranks native
    # executables in PowerShell's command lookup order).
    uv_stub = f"""
function uv {{
    Add-Content -Path '{marker}' -Value ("UV_INSTALL_ARGS:" + ($args -join ' '))
    $global:LASTEXITCODE = 0
}}
""" if have_uv else ""
    script = f"""
function Write-Fail {{ param([string]$Message) }}
function Write-Host {{ param([Parameter(ValueFromPipeline=$true)]$Object, [string]$ForegroundColor) }}
{uv_stub}
function Invoke-StubVenvPython {{
    Add-Content -Path '{marker}' -Value ("PIP_INSTALL_ARGS:" + ($args -join ' '))
    $global:LASTEXITCODE = 0
}}
$TheVenvPython = 'Invoke-StubVenvPython'
$PluginDir = '{plugin_dir}'
$prevEAP = 'Continue'
$ErrorActionPreference = 'Continue'
{_resolve_vendored_lib_fn(text)}
{block}
"""
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if not have_uv:
        uv_dir = os.path.dirname(shutil.which("uv") or "")
        path_entries = [p for p in path_entries if p != uv_dir]
    environment = {
        **os.environ,
        "HOME": str(isolated_home),
        "USERPROFILE": str(isolated_home),
        "PATH": os.pathsep.join(path_entries),
    }
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )
    return proc, marker


def _install_path(marker: Path) -> str | None:
    if not marker.exists():
        return None
    for line in marker.read_text(encoding="utf-8").splitlines():
        if line.startswith("PIP_INSTALL_ARGS:"):
            args = line[len("PIP_INSTALL_ARGS:"):].split()
            return args[-1] if args else None
        if not line.startswith("UV_INSTALL_ARGS:"):
            continue
        args = line[len("UV_INSTALL_ARGS:"):].split()
        for arg in args:
            if arg not in {
                "pip", "install", "--python", "--reinstall-package",
                "--refresh-package", "--quiet", "agent-procutil",
                "Invoke-StubVenvPython",
            }:
                return arg
    return None


@pytest.mark.parametrize("block_name", ["service", "engine"])
@pytest.mark.parametrize("have_uv", [True, False])
def test_procutil_preinstall_resolves_plugin_local_copy(
    block_name: str, have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    lib_dir = plugin_dir / "libs" / "agent-procutil"
    lib_dir.mkdir(parents=True)
    (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc, marker = _run(pwsh, block_name, plugin_dir, have_uv, isolated_home=tmp_path / "home")
    assert proc.returncode == 0, proc.stderr

    resolved = _install_path(marker)
    assert resolved is not None
    assert os.path.realpath(resolved) == os.path.realpath(lib_dir)


@pytest.mark.parametrize("block_name", ["service", "engine"])
@pytest.mark.parametrize("have_uv", [True, False])
def test_procutil_preinstall_falls_back_to_repo_root_canonical_when_absent(
    block_name: str, have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    plugin_dir.mkdir(parents=True)
    canonical = tmp_path / "libs" / "agent-procutil"
    canonical.mkdir(parents=True)
    (canonical / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc, marker = _run(pwsh, block_name, plugin_dir, have_uv, isolated_home=tmp_path / "home")
    assert proc.returncode == 0, proc.stderr

    resolved = _install_path(marker)
    assert resolved is not None
    assert os.path.realpath(resolved) == os.path.realpath(canonical)


@pytest.mark.parametrize("block_name", ["service", "engine"])
@pytest.mark.parametrize("have_uv", [True, False])
def test_procutil_preinstall_is_a_noop_when_neither_copy_exists(
    block_name: str, have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    plugin_dir.mkdir(parents=True)

    proc, marker = _run(pwsh, block_name, plugin_dir, have_uv, isolated_home=tmp_path / "home")
    assert proc.returncode == 0, proc.stderr
    assert _install_path(marker) is None
