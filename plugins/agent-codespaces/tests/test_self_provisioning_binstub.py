"""Guards for the POSIX self-provisioning binstub template."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
INSTALL_PS1 = INSTALL_SH.with_suffix(".ps1")
pytestmark = pytest.mark.guard
_BINSTUB_TIMEOUT_SECONDS = 20


def _isolated_binstub_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    roots = {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_STATE_HOME": home / ".local" / "state",
        "TEMP": home / "tmp",
        "TMP": home / "tmp",
        "TMPDIR": home / "tmp",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    env.update({name: str(path) for name, path in roots.items()})
    env["AGENT_CODESPACES_NO_SELFPROVISION"] = "1"
    return env


def test_binstub_resolves_marker_only_runtime_after_provision() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    stub = text.split("cat > \"$stub_path\" << 'STUB'", 1)[1].split("\nSTUB", 1)[0]

    assert '$_root/.venv/bin/$_name' not in stub
    assert "for _marker in current-version last-known-good" in stub
    assert ".install-complete.json" in stub
    assert "_version_key" in stub
    assert "sort -V" not in stub
    assert '$_root/versions/$_ver/bin/python' in stub
    assert 'exec "$_python" -m agent_codespaces "$@"' in stub


def test_posix_activation_requires_completion_marker() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    activate = text.split("_versioned_activate() {", 1)[1].split("\n}", 1)[0]
    marker = text.split("_versioned_mark_complete() {", 1)[1].split("\n}", 1)[0]
    deploy = text.split("deploy_venv() {", 1)[1].split("\n}", 1)[0]

    assert "_versioned_mark_complete || return 1" in activate
    assert "Failed to mark runtime slot complete" in marker
    assert '"$py" "${args[@]}"' in marker
    assert "|| true" not in marker
    assert "_versioned_slot_clean || return 1" in deploy


def test_posix_cleanup_is_vacuous_without_slot_and_bootstraps_with_uv() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    bootstrap = text.split("_bootstrap_python() {", 1)[1].split("\n}", 1)[0]
    runner = text.split("_run_versioned_runtime() {", 1)[1].split("\n}", 1)[0]
    clean = text.split("_versioned_slot_clean() {", 1)[1].split("\n}", 1)[0]

    assert '[[ -d "$VENV_DIR" ]] || return 0' in clean
    assert "command -v uv" in runner
    assert "uv run --no-project --python 3.11" in runner
    assert runner.index("_bootstrap_python") < runner.index("command -v uv")
    assert "$VENV_PYTHON" not in bootstrap


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="a native POSIX bash is unavailable",
)
def test_posix_binstub_rejects_incomplete_marker_slot_without_running_it(
    tmp_path: Path,
) -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    stub = text.split("cat > \"$stub_path\" << 'STUB'", 1)[1].split("\nSTUB", 1)[0]
    binstub = tmp_path / "agent-codespaces"
    binstub.write_text(stub.lstrip(), encoding="utf-8")
    binstub.chmod(0o755)
    home = tmp_path / "home"
    slot = home / ".agent-codespaces" / "versions" / "1.0.0"
    python = slot / "bin" / "python"
    python.parent.mkdir(parents=True)
    sentinel = tmp_path / "spawned"
    python.write_text(
        f"#!/bin/sh\nprintf spawned > '{sentinel}'\nexit 0\n", encoding="utf-8"
    )
    python.chmod(0o755)
    (home / ".agent-codespaces" / "current-version").write_text(
        "1.0.0\n", encoding="utf-8"
    )
    env = _isolated_binstub_env(home)

    result = subprocess.run(
        [shutil.which("bash"), str(binstub), "version"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=_BINSTUB_TIMEOUT_SECONDS,
    )

    assert result.returncode != 0
    assert not sentinel.exists()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="a native POSIX bash is unavailable",
)
def test_posix_binstub_tier3_prefers_dev10_over_dev9(tmp_path: Path) -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    stub = text.split("cat > \"$stub_path\" << 'STUB'", 1)[1].split("\nSTUB", 1)[0]
    binstub = tmp_path / "agent-codespaces"
    binstub.write_text(stub.lstrip(), encoding="utf-8")
    binstub.chmod(0o755)
    home = tmp_path / "home"
    for version in ("0.4.0-dev9", "0.4.0-dev10"):
        slot = home / ".agent-codespaces" / "versions" / version
        python = slot / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(
            f"#!/bin/sh\nprintf '%s' '{version}'\n", encoding="utf-8"
        )
        python.chmod(0o755)
        (slot / ".install-complete.json").write_text(
            (
                f'{{"version": "{version}", '
                '"completed_at": "2026-08-27T00:00:00Z", "pid": 1}'
            ),
            encoding="utf-8",
        )
    env = _isolated_binstub_env(home)

    result = subprocess.run(
        [shutil.which("bash"), str(binstub), "version"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=_BINSTUB_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0.4.0-dev10"

    slot = home / ".agent-codespaces" / "versions" / "0.4.0"
    python = slot / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nprintf '%s' '0.4.0'\n", encoding="utf-8")
    python.chmod(0o755)
    (slot / ".install-complete.json").write_text(
        (
            '{"version": "0.4.0", '
            '"completed_at": "2026-08-27T00:00:00Z", "pid": 1}'
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [shutil.which("bash"), str(binstub), "version"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=_BINSTUB_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "0.4.0"


def test_windows_binstubs_share_safe_resolution_and_locking() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    ps1 = text.split("$ps1Content = @'", 1)[1].split("\n'@", 1)[0]
    cmd = text.split("$stubContent = @'", 1)[1].split("\n'@", 1)[0]

    assert "@('current-version', 'last-known-good')" in ps1
    assert ".install-complete.json" in ps1
    assert "_version_key" in ps1
    assert "PadLeft(20, '0')" in ps1
    assert "System.Threading.Mutex" in ps1
    assert 'agent-codespaces.ps1" %*' in cmd
    assert "powershell" in cmd


def test_installers_preinstall_uv_editable_workspace_dependencies() -> None:
    install_sh = INSTALL_SH.read_text(encoding="utf-8")
    install_ps1 = INSTALL_PS1.read_text(encoding="utf-8")

    for lib, shell_var, ps_var in (
        ("zdd", "ZDD_DIR", "ZddDir"),
        ("venue-copilot", "VENUE_COPILOT_DIR", "VenueCopilotDir"),
        ("session-liveness-probe", "SESSION_LIVENESS_PROBE_DIR", "SessionLivenessProbeDir"),
        ("single-instance-lease", "SINGLE_INSTANCE_LEASE_DIR", "SingleInstanceLeaseDir"),
        ("remote-login-shell", "REMOTE_LOGIN_SHELL_DIR", "RemoteLoginShellDir"),
    ):
        assert f'{shell_var}="$PLUGIN_DIR/libs/{lib}"' in install_sh
        assert f'"${shell_var}"' in install_sh
        assert f"agent-{lib}" in install_sh
        assert f"Join-Path $PluginDir 'libs\\{lib}'" in install_ps1
        assert f"'agent-{lib}' = ${ps_var}" in install_ps1
    assert 'mode_args=(--reinstall-package "${packages[$i]}")' in install_sh
    assert 'mode_args=(--editable)' in install_sh
    assert 'invoke_uv_pip_install_resilient "$UV_CMD" --python "$py"' in install_sh
    assert "'--editable' } else { '--reinstall-package', $entry.Key }" in install_ps1
    assert "Invoke-UvPipInstallResilient -UvCommand $UvCommand" in install_ps1
