"""The installer's direct service start leaves the installer's session.

An update runs each installer under a deadline that, on POSIX, kills every
process still in the installer's session. A service started with a plain
``nohup ... &`` stays in that session and would be killed with it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="POSIX sessions",
)


def _install_text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def test_direct_start_uses_the_new_session_launcher() -> None:
    start = re.search(r"nohup \"\$LINK_PYTHON\"([^\n]*)\\\n([^\n]*)", _install_text())
    assert start, "the direct service start is missing"
    assert start.group(1).strip().startswith('-c "$_NEW_SESSION_PY" "$LINK_PYTHON" -I -X utf8 -m agent_index start')


def test_new_session_launcher_detaches_and_keeps_the_command(tmp_path: Path) -> None:
    definition = next(line for line in _install_text().splitlines()
                      if line.startswith("_NEW_SESSION_PY="))
    record = tmp_path / "session"
    script = (
        f"{definition}\n"
        'py="$1"; record="$2"\n'
        '"$py" -c "$_NEW_SESSION_PY" "$py" -I -X utf8 -c '
        "'import os, sys; open(sys.argv[1], \"w\").write(\"%d %d %s\" % "
        "(os.getpid(), os.getsid(0), sys.flags.isolated))' \"$record\" &\n"
        "echo $!\n"
        "wait\n"
    )
    shell = subprocess.run(
        ["bash", "-c", script, "bash", sys.executable, str(record)],
        capture_output=True, text=True, timeout=30, check=True, start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    pid, sid, isolated = record.read_text().split()
    assert pid == shell.stdout.strip(), "the launcher execs in place, so $! is the service"
    assert sid == pid, "the service leads a session of its own"
    assert isolated == "1", "the original interpreter flags are kept"
