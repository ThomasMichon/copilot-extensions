"""Native soft-warning execution retains the existing finite process owner."""

from __future__ import annotations

import subprocess
import sys

import pytest

from agent_procutil.owned_process import run_owned


def _command(program: str) -> list[str]:
    python = str(getattr(sys, "_base_executable", None) or sys.executable)
    return [python, "-I", "-S", "-B", "-c", program]


@pytest.mark.real_installer_processes
def test_silent_command_can_finish_after_its_soft_warning(caplog: pytest.LogCaptureFixture) -> None:
    result = run_owned(
        _command("import os,time;time.sleep(.4);os.write(1,b'\\xe2\\x82\\xac')"),
        timeout=5, soft_timeout=.1,
    )
    assert result.returncode == 0
    assert result.stdout == "\u20ac"
    assert result.stderr == ""
    assert "continuing until the hard deadline" in caplog.text


@pytest.mark.real_installer_processes
def test_outputting_command_still_reaches_its_hard_timeout() -> None:
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        run_owned(
            _command("import os,time\nwhile True:\n os.write(2,b'x');time.sleep(.03)\n"),
            timeout=5, soft_timeout=.3,
        )
    assert raised.value.stderr
    assert raised.value.timeout == 5


@pytest.mark.real_installer_processes
def test_soft_capture_waits_for_descendant_eof_after_root_exit() -> None:
    child = "import os,time;time.sleep(.2);os.write(1,b'late-out');os.write(2,b'late-err')"
    program = (
        "import os,subprocess,sys;os.write(1,b'root-');"
        f"subprocess.Popen([sys.executable,'-I','-S','-B','-c',{child!r}],"
        "stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,"
        "creationflags=(0x08000000 if sys.platform=='win32' else 0));sys.exit(7)"
    )
    result = run_owned(_command(program), timeout=5, soft_timeout=.1)
    assert result.returncode == 7
    assert result.stdout == "root-late-out"
    assert result.stderr == "late-err"


@pytest.mark.real_installer_processes
def test_soft_capture_keeps_open_descendant_pipes_under_the_hard_deadline() -> None:
    program = (
        "import os,subprocess,sys;os.write(1,b'root');"
        "subprocess.Popen([sys.executable,'-I','-S','-B','-c','import time;time.sleep(30)'],"
        "stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,"
        "creationflags=(0x08000000 if sys.platform=='win32' else 0));sys.exit(0)"
    )
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        run_owned(_command(program), timeout=3, soft_timeout=.1)
    assert raised.value.timeout == 3


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("exit_code", [0, 7])
def test_replacement_decoding_is_an_explicit_caller_policy(exit_code: int) -> None:
    program = (
        "import os,sys;os.write(1,b'out\\xff');os.write(2,b'err\\xfe');"
        f"sys.exit({exit_code})"
    )
    result = run_owned(_command(program), timeout=5, soft_timeout=.1, errors="replace")
    assert result.returncode == exit_code
    assert result.stdout == "out\ufffd"
    assert result.stderr == "err\ufffd"


@pytest.mark.real_installer_processes
def test_other_callers_retain_strict_decoding() -> None:
    with pytest.raises(UnicodeDecodeError):
        run_owned(_command("import os;os.write(1,b'\\xff')"), timeout=5, soft_timeout=.1)
