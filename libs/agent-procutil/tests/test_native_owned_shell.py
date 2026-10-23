"""Real pre-Python acquisition guardian and the same-process Python custody handoff."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_procutil._caller_owned import _pidfd_exited
from agent_procutil._linux_pidfd import open_pidfd
from agent_procutil.owned_process import run_owned

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native acquisition guardian")


def command(area, mode):
    tests = Path(__file__).parent
    package = tests.parent / "src"
    return [
        "bash", str(tests / "fixture_native_owned.sh"),
        str(package / "agent_procutil" / "native_owned.sh"), sys.executable, str(package),
        str(area), mode,
    ]


def test_owned_shell_keeps_finished_root_unreaped_until_exec_and_retires_whole_group(tmp_path):
    result = run_owned(command(tmp_path, "finished"), timeout=20)
    assert result.returncode == 0, result.stderr
    assert '"settled": true' in result.stdout


def test_native_command_refuses_before_effects_without_its_own_session_group(tmp_path):
    helper = Path(__file__).parents[1] / "src" / "agent_procutil" / "native_owned.sh"
    marker = tmp_path / "must-not-exist"
    result = subprocess.run(
        ["bash", str(helper), "--owned-child", "30", "bash", "-c",
         'printf effect >"$1"', "foreign-group", str(marker)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode != 0 and not marker.exists()


def test_parent_loss_during_native_acquisition_retires_exact_group_not_unrelated_control(tmp_path):
    control = subprocess.Popen(
        ["sleep", "300"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    parent = subprocess.Popen(
        command(tmp_path, "blocked"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    descriptors = []
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / name).is_file() for name in ("leader", "descendant")):
            assert parent.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        for name in ("leader", "descendant"):
            pid = int((tmp_path / name).read_text())
            descriptors.append(open_pidfd(pid))
            assert not _pidfd_exited(descriptors[-1])
            assert not Path(f"/proc/{pid}/fd/8").exists()
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 10
        while not all(_pidfd_exited(fd) for fd in descriptors):
            assert time.monotonic() < deadline, "native acquisition descendant survived parent death"
            time.sleep(.01)
        assert control.poll() is None
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        control.kill()
        control.wait(timeout=5)
        for descriptor in descriptors:
            os.close(descriptor)
