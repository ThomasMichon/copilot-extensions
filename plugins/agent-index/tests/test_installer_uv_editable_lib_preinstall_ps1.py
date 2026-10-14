"""Source-attributable procutil preinstalls retain canonical dependency policy."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_install_engine_adoption import environment, fake_uv
from test_installer_publication_provisioning import stage_index_policy_sources


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
        "    $engRc = 0\n    $ProcutilDir = Resolve-VendoredLib -LibName 'agent-procutil'",
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
    original = plugin_dir / "libs" / "agent-procutil"
    if not original.is_dir():
        original = plugin_dir.parents[1] / "libs" / "agent-procutil"
    uv = fake_uv(
        isolated_home.parent,
        "import tomllib\n"
        "candidate = pathlib.Path(sys.argv[sys.argv.index('--python') + 2])\n"
        f"original = pathlib.Path({str(original)!r})\n"
        "private = pathlib.Path(sys.argv[sys.argv.index('--config-file') + 1]).parent\n"
        "assert candidate.resolve().is_relative_to(private.resolve())\n"
        "assert candidate.resolve() != original.resolve()\n"
        "assert tomllib.loads((candidate / 'pyproject.toml').read_text())['project'] == "
        "tomllib.loads((original / 'pyproject.toml').read_text())['project']\n"
        "for member in original.rglob('*.py'):\n"
        " assert (candidate / member.relative_to(original)).read_bytes() == member.read_bytes()\n"
        "sys.argv[sys.argv.index('--python') + 2] = str(original)\n"
        f"with pathlib.Path({str(marker)!r}).open('a', encoding='utf-8') as stream:\n"
        " stream.write('UV_INSTALL_ARGS:' + ' '.join(sys.argv[1:]) + '\\n')\n",
    )
    policy = plugin_dir / "scripts/installer_dependency_policy.py"
    policy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN / "scripts/installer_dependency_policy.py", policy)
    shutil.copy2(
        PLUGIN / "scripts/installer_dependency_config.py",
        policy.with_name("installer_dependency_config.py"),
    )
    uv_stub = f"""
function Invoke-IndexUvPipInstall {{
    & '{sys._base_executable}' -I '{policy}' --uv '{uv}' -- @args
}}
""" if have_uv else ""
    script = f"""
function Write-Fail {{ param([string]$Message) }}
function Write-Host {{ param([Parameter(ValueFromPipeline=$true)]$Object, [string]$ForegroundColor) }}
{uv_stub}
function Invoke-StubVenvPython {{
    if ($args[0] -ne '-I' -or $args[1] -ne '{policy}' -or $args[2] -ne '--uv' -or $args[3] -ne 'uv') {{
        throw 'canonical policy adapter was bypassed'
    }}
    $policyArgs = @($args)
    # PowerShell consumes -- at a function boundary; restore the native argv.
    if ($policyArgs[4] -ne '--') {{
        $policyArgs = @($policyArgs[0..3]) + @('--') + @($policyArgs[4..($policyArgs.Count - 1)])
    }}
    Add-Content -Path '{marker}' -Value ("POLICY_INSTALL_ARGS:" + ($policyArgs -join ' '))
    $policyArgs[3] = '{uv}'
    & '{sys._base_executable}' @policyArgs
}}
$PSScriptRoot = '{plugin_dir / "scripts"}'
$TheVenvPython = 'Invoke-StubVenvPython'
$script:UvCommand = {"'uv'" if have_uv else "$null"}
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
    env = environment(isolated_home.parent)
    env.update(HOME=str(isolated_home), USERPROFILE=str(isolated_home),
               PATH=os.pathsep.join(path_entries))
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=plugin_dir,
        timeout=30,
    )
    return proc, marker


def _install_path(marker: Path) -> str | None:
    if not marker.exists():
        return None
    for line in marker.read_text(encoding="utf-8").splitlines():
        if not line.startswith("UV_INSTALL_ARGS:"):
            continue
        args = line[len("UV_INSTALL_ARGS:"):].split()
        assert args[:2] == ["pip", "install"]
        assert "--show-settings" not in args
        assert "--exclude-newer" in args and "--reinstall" in args
        assert "--no-deps" not in args and "--no-build-isolation" not in args
        return args[args.index("--python") + 2]
    return None


@pytest.mark.parametrize("block_name", ["service", "engine"])
@pytest.mark.parametrize("have_uv", [True, False])
def test_procutil_preinstall_resolves_plugin_local_copy(
    block_name: str, have_uv: bool, tmp_path: Path
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = stage_index_policy_sources(tmp_path, materialized=True)
    lib_dir = plugin_dir / "libs" / "agent-procutil"

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

    plugin_dir = stage_index_policy_sources(tmp_path, materialized=False)
    canonical = tmp_path / "libs" / "agent-procutil"

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
$script:UvCommand = 'uv'
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
        env=environment(tmp_path),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    uv_calls = [line for line in calls if line.startswith("UV_CALL:")]
    assert len(uv_calls) == 1, uv_calls
    assert "agent-procutil" in uv_calls[0]
