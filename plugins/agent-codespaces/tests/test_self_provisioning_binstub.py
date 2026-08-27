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


def test_binstub_resolves_marker_only_runtime_after_provision() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    stub = text.split("cat > \"$stub_path\" << 'STUB'", 1)[1].split("\nSTUB", 1)[0]

    assert '$_root/.venv/bin/$_name' not in stub
    assert "for _marker in current-version last-known-good" in stub
    assert ".install-complete.json" in stub
    assert "ls -1t" in stub
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
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AGENT_CODESPACES_NO_SELFPROVISION"] = "1"

    result = subprocess.run(
        [shutil.which("bash"), str(binstub), "version"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not sentinel.exists()


def test_windows_binstubs_share_safe_resolution_and_locking() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    ps1 = text.split("$ps1Content = @'", 1)[1].split("\n'@", 1)[0]
    cmd = text.split("$stubContent = @'", 1)[1].split("\n'@", 1)[0]

    assert "@('current-version', 'last-known-good')" in ps1
    assert ".install-complete.json" in ps1
    assert "Sort-Object LastWriteTimeUtc" in ps1
    assert "System.Threading.Mutex" in ps1
    assert 'agent-codespaces.ps1" %*' in cmd
    assert "current-version" in cmd
    assert "last-known-good" in cmd
    assert ".install-complete.json" in cmd
    assert "versions\\%_VER%\\Scripts\\python.exe" in cmd
