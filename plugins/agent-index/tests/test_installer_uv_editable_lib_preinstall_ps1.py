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

# A wider span than `_BLOCKS["engine"]`: includes the `$engRc`-gate and the
# start of the main-package install call, so a test can prove a failed
# `agent-procutil` preinstall actually SKIPS the main install rather than
# merely being logged (PR #4465 review).
_ENGINE_WITH_GATE_START = _BLOCKS["engine"][0]
_ENGINE_WITH_GATE_END = "\n            $srvOut = Invoke-IndexUvPipInstall @pipArgs"


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
function Invoke-IndexUvPipInstall {{ uv pip install @args }}
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
        # A single `shutil.which("uv")` hit only finds the FIRST PATH
        # directory carrying a `uv` executable -- insufficient on a
        # machine where a package manager (e.g. WinGet) installs both the
        # real binary AND a separate shim/Links directory, each own PATH
        # entry, both resolving `uv` (coverage-guided-ci effort, Phase
        # 3.5, 2026-10-07: this duplication is real on at least one dev
        # machine). Strip every PATH directory carrying a `uv`/`uv.exe`
        # executable, not just the first one `which` would report.
        uv_exe = "uv.exe" if os.name == "nt" else "uv"
        path_entries = [
            p for p in path_entries if not (Path(p) / uv_exe).is_file()
        ]
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


def test_engine_skips_main_install_when_procutil_preinstall_fails(tmp_path: Path):
    """`agent-index` declares only an UNVERSIONED `agent-procutil`
    requirement, so if the preinstall refresh fails, the subsequent main-
    package install could otherwise still "succeed" against a stale copy
    already present in a preserved engine venv (PR #4465 review). Proves
    the main install is genuinely SKIPPED, not merely logged."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    text = INSTALLER.read_text(encoding="utf-8")
    block = _extract(text, _ENGINE_WITH_GATE_START, _ENGINE_WITH_GATE_END)
    block += "\n        }\n    }"
    block = block.replace("$EngineVenvPython", "$TheVenvPython")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    lib_dir = plugin_dir / "libs" / "agent-procutil"
    lib_dir.mkdir(parents=True)
    (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    marker = tmp_path / "marker.txt"

    script = f"""
function Write-Fail {{ param([string]$Message) }}
function Write-Host {{ param([Parameter(ValueFromPipeline=$true)]$Object, [string]$ForegroundColor) }}
function uv {{
    Add-Content -Path '{marker}' -Value ("UV_CALL:" + ($args -join ' '))
    if (($args -join ' ') -match 'agent-procutil') {{
        $global:LASTEXITCODE = 1
    }} else {{
        $global:LASTEXITCODE = 0
    }}
}}
function Invoke-IndexUvPipInstall {{ uv pip install @args }}
function Resolve-VendoredLib {{
    param([string]$LibName)
    $candidate = Join-Path $PluginDir "libs\\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {{ return $candidate }}
    return $null
}}
function Invoke-StubVenvPython {{ $global:LASTEXITCODE = 0 }}
$TheVenvPython = 'Invoke-StubVenvPython'
$PluginDir = '{plugin_dir}'
$Upgrade = $false
$prevEAP = 'Continue'
$ErrorActionPreference = 'Continue'
{block}
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home")},
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    uv_calls = [line for line in calls if line.startswith("UV_CALL:")]
    assert len(uv_calls) == 1, uv_calls
    assert "agent-procutil" in uv_calls[0]
