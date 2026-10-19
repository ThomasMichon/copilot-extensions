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


_START = '-I -X utf8 -c "$_NEW_SESSION_PY" "$LINK_PYTHON" -I -X utf8 -m agent_index start'


def test_direct_start_uses_the_new_session_launcher() -> None:
    start = re.search(r"nohup \"\$LINK_PYTHON\"([^\n]*)\\\n", _install_text())
    assert start, "the direct service start is missing"
    # The launcher's own interpreter is isolated too, not only the exec target.
    assert start.group(1).strip() == _START


@pytest.mark.parametrize("contained", [False, True])
def test_new_session_launcher(tmp_path: Path, contained: bool) -> None:
    definition = next(line for line in _install_text().splitlines()
                      if line.startswith("_NEW_SESSION_PY="))
    record = tmp_path / "session"
    script = (
        f"{definition}\n"
        'LINK_PYTHON="$1"; record="$2"\n'
        f'"$LINK_PYTHON" {_START.split(" -m ")[0]} -c '
        "'import os, sys; open(sys.argv[1], \"w\").write(\"%d %d %s\" % "
        "(os.getpid(), os.getsid(0), sys.flags.isolated))' \"$record\" &\n"
        "echo $!\n"
        "wait\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_TEST_CONTAINED"}
    # Ignored by -I; without it the launcher itself could not start.
    env["PYTHONHOME"] = str(tmp_path / "missing-python-home")
    if contained:
        env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    shell = subprocess.Popen(
        ["bash", "-c", script, "bash", sys.executable, str(record)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        start_new_session=True,
    )
    stdout, stderr = shell.communicate(timeout=30)
    assert shell.returncode == 0, stderr
    deadline = time.monotonic() + 10
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert record.exists(), stderr
    pid, sid, isolated = record.read_text().split()
    assert pid == stdout.strip(), "the launcher execs in place, so $! is the service"
    assert isolated == "1", "the original interpreter flags are kept"
    if contained:
        assert sid == str(shell.pid), "a contained run stays in the runner's session"
    else:
        assert sid == pid, "the service leads a session of its own"
