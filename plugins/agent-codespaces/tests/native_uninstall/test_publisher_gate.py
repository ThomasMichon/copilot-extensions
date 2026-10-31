"""Real cross-native publisher admission on the retained installer lock inode."""
from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from test_state_preservation import _host, _quote

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@contextmanager
def owner(root):
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(SCRIPTS / "install-transaction.py"), str(root), "1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=FLAGS,
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        yield
    finally:
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stdout + stderr


def native(root, shell, tmp_path, *, blocked):
    host = _host(shell)
    quote = lambda value: _quote(value, shell)
    if shell == "bash":
        text = f"""
set -euo pipefail
SCRIPT_DIR={quote(SCRIPTS)}
INSTALL_DIR={quote(root)}
COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1
_bootstrap_python() {{ printf '%s' {quote(sys.executable)}; }}
source "$SCRIPT_DIR/install-transaction.sh"
enter_codespaces_install_transaction || exit 73
printf admitted
exit_codespaces_install_transaction
"""
        command = [host]
        suffix = "sh"
    else:
        text = f"""
$ErrorActionPreference='Stop'
$InstallDir={quote(root)}
$env:COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC='1'
. {quote(SCRIPTS / 'install-transaction.ps1')}
$lock=Enter-CodespacesInstallTransaction
try {{ Write-Output 'admitted' }} finally {{ $lock.Dispose() }}
"""
        command = [host, "-NoProfile", "-NonInteractive", "-File"]
        suffix = "ps1"
    script = tmp_path / ("publisher." + suffix)
    script.write_text(text)
    result = subprocess.run(
        [*command, str(script)], cwd=tmp_path, capture_output=True,
        text=True, timeout=10, check=False, creationflags=FLAGS,
    )
    if blocked:
        assert result.returncode != 0
        assert "admitted" not in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "admitted" in result.stdout


@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_codespaces_publisher_and_uninstaller_share_actual_lock(tmp_path, shell):
    root = tmp_path / "install"
    root.mkdir()
    lock = root / ".install.lock"
    lock.write_bytes(b"retained inode bytes")
    inode = lock.stat().st_ino
    with owner(root):
        native(root, shell, tmp_path, blocked=True)
    native(root, shell, tmp_path, blocked=False)
    with owner(root):
        assert lock.stat().st_ino == inode
    assert lock.read_bytes() == b"retained inode bytes"


def test_bash_every_publisher_action_requires_whole_transaction(tmp_path):
    bash = _host("bash")
    text = f"""
source {_quote(SCRIPTS / "install-transaction.sh", "bash")}
for action in install update stamp provision dev uninstall; do
    codespaces_requires_install_transaction "$action" false || exit 2
done
for action in install update stamp provision dev; do
    codespaces_requires_install_transaction "$action" true || exit 3
done
if codespaces_requires_install_transaction status false; then exit 4; fi
if codespaces_requires_install_transaction uninstall true; then exit 5; fi
"""
    script = tmp_path / "admission.sh"
    script.write_text(text)
    result = subprocess.run(
        [bash, str(script)], cwd=tmp_path, capture_output=True, text=True,
        timeout=10, check=False, creationflags=FLAGS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    installer = (SCRIPTS / "install.sh").read_text()
    assert installer.index('enter_codespaces_install_transaction || exit 1') < installer.index(
        '_initialize_source_runtime || exit 1', installer.index("# -- Dispatch")
    )


@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_publisher_gate_rejects_parent_traversal_before_lock_creation(tmp_path, shell):
    root = tmp_path / "install"
    root.mkdir()
    (root / "state").write_bytes(b"operator")
    native(root / ".." / "install", shell, tmp_path, blocked=True)
    assert (root / "state").read_bytes() == b"operator"
    assert not (root / ".install.lock").exists()
