"""Source-resolution guard for the canonical dependency-policy preinstall loop.

Both resolver-discovery branches must use the policy adapter and preserve
plugin-local/canonical library resolution. The adapter spy does not exercise
dependency-age admission.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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
    assert "'--uv', 'uv', '--', '--python', $VenvPython" in loop
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
    param($FilePath, $ArgList)
    $cmdArgs = @($FilePath) + $ArgList
    if ($cmdArgs[0] -ne 'Invoke-StubVenvPython' -or
        $cmdArgs[1] -ne '-I' -or $cmdArgs[2] -ne '{policy}' -or
        -not (Test-Path -LiteralPath $cmdArgs[2]) -or
        $cmdArgs[3] -ne '--uv' -or $cmdArgs[4] -ne 'uv' -or
        $cmdArgs[5] -ne '--' -or $cmdArgs[6] -ne '--python' -or
        $cmdArgs[7] -ne 'Invoke-StubVenvPython') {{
        throw 'Preinstall bypassed canonical dependency policy'
    }}
    $pathIndex = if ($haveUv) {{ 10 }} else {{ 9 }}
    if ($haveUv -and $cmdArgs[8] -ne '--reinstall-package') {{
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


@pytest.mark.parametrize("exit_code", [0, 17])
def test_policy_calls_preserve_native_isolation_separator_and_exit(tmp_path: Path, exit_code: int):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    source = INSTALLER.read_text(encoding="utf-8")
    helper = re.search(r"(?ms)^function Invoke-Hidden \{.*?^\}", source)
    assert helper
    calls = [
        line.strip() for line in source.splitlines()
        if line.strip().startswith("Invoke-Hidden ") and "installer_dependency_policy.py" in line
    ]
    assert len(calls) == 4
    scripts = tmp_path / "scripts with spaces"
    scripts.mkdir()
    marker = tmp_path / "calls.jsonl"
    (scripts / "installer_dependency_policy.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        f"with Path({str(marker)!r}).open('a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps([sys.flags.isolated, sys.argv[1:]]) + '\\n')\n"
        f"raise SystemExit({exit_code})\n", encoding="utf-8",
    )
    script = (
        "$ErrorActionPreference='Stop'\n" + helper.group(0) + "\n"
        f"$VenvPython='{sys.executable}'\n$PSScriptRoot='{scripts}'\n"
        f"$PluginDir='{tmp_path / 'plugin with spaces'}'\n"
        f"$libDir='{tmp_path / 'library with spaces'}'\n"
        "$lib=@{Pkg='agent-zdd'}\n"
        + "\n".join(
            call + f"\nif ($LASTEXITCODE -ne {exit_code}) {{ throw 'child exit lost' }}"
            for call in calls
        )
    )
    harness = tmp_path / "native.ps1"
    harness.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(harness)], capture_output=True,
        text=True, env=os.environ, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    captured = [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]
    assert captured == [
        [1, ["--uv", "uv", "--", "--python", sys.executable, "--reinstall-package",
             "agent-zdd", str(tmp_path / "library with spaces"), "--quiet"]],
        [1, ["--uv", "uv", "--", "--python", sys.executable, "--quiet",
             str(tmp_path / "library with spaces")]],
        [1, ["--uv", "uv", "--", "--python", sys.executable,
             str(tmp_path / "plugin with spaces"), "--quiet"]],
        [1, ["--uv", "uv", "--", "--python", sys.executable, "--quiet",
             str(tmp_path / "plugin with spaces")]],
    ]


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
