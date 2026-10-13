"""The installer's no-flock pid-symlink fallback serializes stale-owner recovery."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SH_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def _posix_bash() -> str | None:
    if os.name == "nt":
        return None
    return shutil.which("bash")


def _lock_functions() -> str:
    text = SH_SCRIPT.read_text(encoding="utf-8")
    parts = [
        re.search(rf"^{name}\(\) {{\n.*?^}}\n", text, re.M | re.S)
        for name in ("_exit_install_lock", "_enter_install_lock")
    ]
    assert all(parts), "install.sh must define both lock functions"
    return '__install_lock_link=""\n__install_lock_fd=""\n' + "".join(p.group(0) for p in parts)


def _contender(bash: str, install_dir: Path, body: str) -> subprocess.Popen[str]:
    script = f"set -euo pipefail\nINSTALL_DIR='{install_dir}'\n" + _lock_functions() + body
    return subprocess.Popen(
        [bash, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )


def _dead_pid() -> int:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    return dead.pid


def test_simultaneous_stale_recovery_never_overlaps(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    os.symlink(str(_dead_pid()), install_dir / ".install.lock.pid")
    log = tmp_path / "log"
    body = (
        "_enter_install_lock || exit 3\n"
        f"echo start >> '{log}'; sleep 0.2; echo end >> '{log}'\n"
        "_exit_install_lock\n"
    )
    contenders = [_contender(bash, install_dir, body) for _ in range(6)]
    for proc in contenders:
        _out, err = proc.communicate(timeout=90)
        assert proc.returncode == 0, err
    assert log.read_text().split() == ["start", "end"] * 6
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert not os.path.lexists(install_dir / ".install.lock.pid.reap")


def test_reap_mutex_left_by_a_dead_reaper_is_reclaimed(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    os.symlink(str(_dead_pid()), install_dir / ".install.lock.pid")
    os.symlink(str(_dead_pid()), install_dir / ".install.lock.pid.reap")
    proc = _contender(
        bash, install_dir, "if _enter_install_lock; then echo acquired; else echo refused; fi\n"
    )
    out, err = proc.communicate(timeout=60)
    assert out.strip() == "acquired", err
    assert not os.path.lexists(install_dir / ".install.lock.pid.reap")
