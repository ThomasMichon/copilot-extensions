"""Native host stacks live only in the required, slot-owned server sibling."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]


def _read_scripts() -> tuple[str, str]:
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    return ps, sh


def test_server_venv_provisioning_functions_exist():
    ps, sh = _read_scripts()
    assert "function Install-ServerVenv {" in ps
    assert "_install_server_venv() {" in sh


def test_server_venv_path_matches_the_resolver_convention():
    """The installer must provision to exactly the sibling path
    ``config.server_venv_python()`` resolves -- a ``server`` subdirectory of
    the current runtime slot, NOT the effort's original (corrected)
    ``.venv-server`` naming."""
    ps, sh = _read_scripts()
    ps_fn = ps.split("function Install-ServerVenv {", 1)[1].split(
        "\nfunction Install-Runtime {", 1
    )[0]
    sh_fn = sh.split("_install_server_venv() {", 1)[1].split(
        "\n_ensure_runtime() {", 1
    )[0]
    assert "Join-Path $VenvDir 'server'" in ps_fn
    assert '"$VENV_DIR/server"' in sh_fn
    assert ".venv-server" not in ps_fn
    assert ".venv-server" not in sh_fn


def test_server_venv_provisioning_is_host_role_only():
    ps, sh = _read_scripts()
    ps_fn = ps.split("function Install-ServerVenv {", 1)[1].split(
        "\nfunction Install-Runtime {", 1
    )[0]
    sh_fn = sh.split("_install_server_venv() {", 1)[1].split(
        "\n_ensure_runtime() {", 1
    )[0]
    assert "-ne 'host'" in ps_fn or "!= 'host'" in ps_fn
    assert '!= "host"' in sh_fn


def test_server_venv_installs_store_and_server_extras():
    ps, sh = _read_scripts()
    ps_fn = ps.split("function Install-ServerVenv {", 1)[1].split(
        "\nfunction Install-Runtime {", 1
    )[0]
    sh_fn = sh.split("_install_server_venv() {", 1)[1].split(
        "\n_ensure_runtime() {", 1
    )[0]
    assert "$PluginDir[store,server]" in ps_fn
    assert '${PLUGIN_DIR}[store,server]' in sh_fn


def test_failed_server_venv_prevents_host_activation():
    ps, sh = _read_scripts()
    ps_fn = ps.split("function Install-ServerVenv {", 1)[1].split(
        "\nfunction Install-Runtime {", 1
    )[0]
    sh_fn = sh.split("_install_server_venv() {", 1)[1].split(
        "\n_ensure_runtime() {", 1
    )[0]
    assert "return $false" in ps_fn
    assert "return 1" in sh_fn
    assert "import fastapi, uvicorn, numpy, pyarrow, lancedb" in ps_fn
    assert "import fastapi, uvicorn, numpy, pyarrow, lancedb" in sh_fn
    assert "Test-RuntimeOrigin -Python $serverVenvPython -Slot $serverVenvDir" in ps_fn
    assert '_runtime_origin_under "$server_venv_python" "$server_venv_dir"' in sh_fn
    assert "Required host runtime failed provisioning -- not activating" in ps
    assert "Required host runtime failed provisioning -- not activating" in sh


def test_native_primary_runtime_is_base_only():
    ps, sh = _read_scripts()
    ps_runtime = ps.split("function Install-Runtime {", 1)[1].split(
        "\nfunction Write-Manifest {", 1
    )[0]
    sh_runtime = sh.split("_ensure_runtime() {", 1)[1].split(
        "\n_write_manifest() {", 1
    )[0]
    assert "$PluginDir[store,server]" not in ps_runtime
    assert "${PLUGIN_DIR}[store,server]" not in sh_runtime
    assert '$pkgSpec = "$PluginDir"' in ps_runtime
    assert 'local pkg="$PLUGIN_DIR"' in sh_runtime


def test_server_preinstalls_all_local_base_libraries_for_pip():
    ps, sh = _read_scripts()
    assert "@('zdd', 'agent-procutil', 'dropin-registry')" in ps
    assert "for lib_name in zdd agent-procutil dropin-registry" in sh


def test_server_venv_provisioning_is_called_after_the_main_package_install():
    ps, sh = _read_scripts()
    ps_runtime = ps.split("function Install-Runtime {", 1)[1].split(
        "\nfunction Write-Manifest {", 1
    )[0]
    sh_runtime = sh.split("_ensure_runtime() {", 1)[1].split(
        "\n_write_manifest() {", 1
    )[0]
    after_ps = ps_runtime.split("Write-Ok 'Package installed: agent-index'", 1)[1]
    after_sh = sh_runtime.split("_ok 'Package installed: agent-index'", 1)[1]
    assert "Install-ServerVenv -InstallRole $installRole" in after_ps
    assert "Publish-IndexRuntime" in after_ps.split(
        "Install-ServerVenv -InstallRole $installRole", 1
    )[1]
    assert '_install_server_venv "$install_role"' in after_sh
    assert "deploy_binstub" in after_sh.split(
        '_install_server_venv "$install_role"', 1
    )[1]


def test_server_venv_prefers_signed_python_copies_mode_before_uv_fallback():
    """Mirrors the main venv's own preference (SSH-invocable + Smart App
    Control-allowed, see Get-SignedBasePython's docstring) -- Windows only;
    install.sh has no equivalent concept. NOT a latency optimization: both
    a --copies venv and a uv-created one re-exec the base interpreter as a
    child process on Windows (confirmed empirically), so this changes
    SSH/SAC compatibility, not spawn hop count."""
    ps, _sh = _read_scripts()
    ps_fn = ps.split("function Install-ServerVenv {", 1)[1].split(
        "\nfunction Install-Runtime {", 1
    )[0]
    assert "New-IndexVenv -Dir $serverVenvDir -Python $serverVenvPython" in ps_fn
    shared = ps.split("function New-IndexVenv {", 1)[1].split(
        "\nfunction Install-ServerVenv {", 1
    )[0]
    signed_idx = shared.index("Get-SignedBasePython")
    copies_idx = shared.index("-m venv --copies $Dir")
    uv_idx = shared.index("Invoke-UvVenvResilient")
    assert signed_idx < copies_idx < uv_idx
    assert "Test-IndexVenv -Dir $Dir -Python $Python" in shared
    assert "retrying with uv" in shared


@pytest.mark.parametrize(
    "case,role,expected,library_count",
    [
        ("client", "client", True, 0),
        ("creation-failed", "host", False, 0),
        ("missing-library", "host", False, 2),
        ("package-failed", "host", False, 3),
    ],
)
def test_real_powershell_server_provisioning_outcomes(
    tmp_path, case, role, expected, library_count
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    ps, _sh = _read_scripts()
    function = "function Install-ServerVenv {" + ps.split(
        "function Install-ServerVenv {", 1
    )[1].split("\nfunction Install-Runtime {", 1)[0]
    script = f"""
$ErrorActionPreference = 'Stop'
$case = '{case}'
$VenvDir = '{tmp_path}'
$PluginDir = '{tmp_path}'
$script:UvCommand = 'fixture-uv'
$script:Libraries = @()
function Write-Skip {{ param($Message) }}
function Write-Warn {{ param($Message) [Console]::Error.WriteLine($Message) }}
function Write-Ok {{ param($Message) }}
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
function New-IndexVenv {{ param($Dir, $Python, $PythonCmd) return $case -ne 'creation-failed' }}
function Resolve-VendoredLib {{
    param($LibName)
    if ($case -eq 'missing-library' -and $LibName -eq 'dropin-registry') {{ return $null }}
    $script:Libraries += $LibName
    return "fixture-lib-$LibName"
}}
function Invoke-IndexUvPipInstall {{
    $global:LASTEXITCODE = 0
    if ($case -eq 'package-failed' -and ($args -join ' ') -match '\\[store,server\\]') {{
        $global:LASTEXITCODE = 9
    }}
}}
{function}
$ok = Install-ServerVenv -InstallRole '{role}' -PythonCmd 'fixture-python'
@{{ok = $ok; libraries = @($script:Libraries)}} | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
        check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["ok"] is expected
    assert len(outcome["libraries"]) == library_count
