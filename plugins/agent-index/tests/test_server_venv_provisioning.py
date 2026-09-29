"""agent-index-server-venv-split: installer provisioning of the sibling
SERVER venv (Plan item 1).

``config.server_venv_python()`` (see ``test_server_venv_resolution.py``) has
resolved a ``server`` subdirectory of the current interpreter's own venv root
since ``ThomasMichon/copilot-extensions#4530`` -- but until an installer
actually creates that sibling and installs ``agent-index[store,server]`` into
it, the resolver has nothing to find and every caller keeps falling back to
its current behavior. These tests pin the installer side of that contract:
the sibling is provisioned inside the current runtime slot (so per-version GC
already covers it for free), host-role only, and never allowed to fail the
primary client install/update.
"""

from __future__ import annotations

from pathlib import Path

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


def test_server_venv_provisioning_never_fails_the_primary_install():
    """Every failure branch in the provisioning helper must WARN, never FAIL
    -- config.server_venv_python() already tolerates an absent sibling by
    falling back to the shared venv/in-process serve(), so a provisioning
    failure here must never abort the primary client install/update."""
    ps, sh = _read_scripts()
    ps_fn = ps.split("function Install-ServerVenv {", 1)[1].split(
        "\nfunction Install-Runtime {", 1
    )[0]
    sh_fn = sh.split("_install_server_venv() {", 1)[1].split(
        "\n_ensure_runtime() {", 1
    )[0]
    assert "Write-Fail" not in ps_fn
    assert "exit 1" not in ps_fn
    assert "_fail" not in sh_fn
    assert "exit 1" not in sh_fn


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
    assert "Deploy-SetupGatedBinstub" in after_ps.split(
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
    signed_idx = ps_fn.index("Get-SignedBasePython")
    copies_idx = ps_fn.index("-m venv --copies --clear $serverVenvDir")
    uv_idx = ps_fn.index("uv venv $serverVenvDir")
    assert signed_idx < copies_idx < uv_idx
    assert "Server venv created from signed Python" in ps_fn
    assert "falling back to uv" in ps_fn
