"""Native launch survives the caller's real process group; contained tests do not escape."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from agent_procutil._caller_owned import _pidfd_exited
from agent_procutil._linux_pidfd import open_pidfd

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native daemon admission")


@pytest.mark.parametrize("contained", [False, True])
def test_native_daemon_and_bounded_control_after_caller_group_destruction(tmp_path, contained):
    tests = Path(__file__).parent
    helper = tests.parent / "src" / "agent_procutil" / "native_daemon.sh"
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    if contained:
        env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    parent = subprocess.Popen(
        ["bash", str(tests / "fixture_native_daemon.sh"), str(helper), str(tmp_path)],
        start_new_session=True, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    descriptors = []
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / name).is_file() for name in ("daemon", "control")):
            assert parent.poll() is None, (tmp_path / "daemon.log").read_text()
            assert time.monotonic() < deadline
            time.sleep(.01)
        daemon = int((tmp_path / "daemon").read_text())
        control = int((tmp_path / "control").read_text())
        descriptors = [open_pidfd(daemon), open_pidfd(control)]
        assert (os.getsid(daemon) == parent.pid) == contained
        assert os.getsid(control) == parent.pid
        os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 10
        while not _pidfd_exited(descriptors[1]) or (contained and not _pidfd_exited(descriptors[0])):
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert _pidfd_exited(descriptors[0]) == contained
        assert not list(tmp_path.glob(".native-daemon-*"))
    finally:
        if parent.poll() is None:
            os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        for descriptor in descriptors:
            if not _pidfd_exited(descriptor):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)
