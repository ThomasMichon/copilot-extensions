"""Native soft-warning execution retains the existing finite process owner."""

from __future__ import annotations

import subprocess
import sys

import pytest

from agent_procutil.owned_process import run_owned


@pytest.mark.real_installer_processes
def test_silent_command_can_finish_after_its_soft_warning(caplog: pytest.LogCaptureFixture) -> None:
    result = run_owned(
        [sys.executable, "-I", "-S", "-B", "-c",
         "import os,time;time.sleep(.4);os.write(1,b'\\xe2\\x82\\xac')"],
        timeout=5, soft_timeout=.1,
    )
    assert result.returncode == 0
    assert result.stdout == "\u20ac"
    assert result.stderr == ""
    assert "continuing until the hard deadline" in caplog.text


@pytest.mark.real_installer_processes
def test_outputting_command_still_reaches_its_hard_timeout() -> None:
    python = getattr(sys, "_base_executable", None) or sys.executable
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        run_owned(
            [python, "-I", "-S", "-B", "-c",
             "import os,time\nwhile True:\n os.write(2,b'x');time.sleep(.03)\n"],
            timeout=5, soft_timeout=.3,
        )
    assert raised.value.stderr
    assert raised.value.timeout == 5
