"""Native pause/rollback and exit confirmation for an original external child."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil.process_retirement import PinnedProcess, RetirementRefused


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="External process pinning requires Windows or Linux",
)
def test_pause_rollback_and_confirmed_exit(tmp_path):
    progress = tmp_path / "progress"
    code = (
        "from pathlib import Path\n"
        "import os,sys,time\n"
        "p=Path(sys.argv[1])\n"
        "print(os.getpid(),flush=True)\n"
        "while True:\n"
        " p.write_text(str(os.getpid())+':'+str(time.monotonic()))\n"
        " time.sleep(.01)\n"
    )
    process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-c", code, str(progress)],
        stdout=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        assert int(process.stdout.readline()) == process.pid
        deadline = time.monotonic() + 5
        while not progress.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert progress.exists()
        identity = birth_identity(process.pid)
        with pytest.raises(RetirementRefused, match="identity changed"):
            PinnedProcess(process.pid, identity + "1")
        with PinnedProcess(process.pid, identity) as pinned:
            pinned.pause()
            before = progress.read_bytes()
            time.sleep(.05)
            assert progress.read_bytes() == before
            assert not pinned.exited()
        deadline = time.monotonic() + 2
        observed = progress.read_bytes()
        while observed == before and time.monotonic() < deadline:
            time.sleep(.01)
            observed = progress.read_bytes()
        assert observed != before
        with PinnedProcess(process.pid, identity) as pinned:
            pinned.pause()
            pinned.terminate()
            assert pinned.exited()
        assert process.wait(timeout=5) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_own_caller_cannot_be_pinned():
    with pytest.raises(RetirementRefused, match="caller"):
        PinnedProcess(os.getpid(), "not a process authority")
