"""Guards for the POSIX self-provisioning binstub template."""

from __future__ import annotations

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
