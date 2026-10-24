"""Foreground completion owns descendants without substituting terminal streams."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from agent_procutil import interactive as api
from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import OwnedCleanupError

SUPPORTED = os.name == "nt" or sys.platform.startswith("linux") or sys.platform == "darwin"
ROOT = Path(__file__).resolve().parents[1] / "src"


def _environment():
    return {**os.environ, "PYTHONPATH": str(ROOT)}


def _wait(predicate, message):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError(message)


def _gone(process):
    try:
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


@pytest.mark.parametrize("argv", [None, [], "command", [1], ["bad\0argument"]])
def test_bad_command_never_launches(monkeypatch, argv):
    launch = Mock()
    monkeypatch.setattr(api, "spawn_sync_in_kill_on_close_job", launch)
    with pytest.raises(ValueError, match="argv"):
        api.run_interactive(argv)
    launch.assert_not_called()


def test_windows_strict_job_confirms_cleanup_after_interrupt(monkeypatch):
    process = Mock()
    process.wait.side_effect = [KeyboardInterrupt, 0]
    job = Mock()
    launch = Mock(return_value=(process, job))
    monkeypatch.setattr(api, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(api, "spawn_sync_in_kill_on_close_job", launch)
    assert api.run_interactive(["command"], env={"example": "value"}) == 130
    launch.assert_called_once_with(["command"], env={"example": "value"}, require_job=True)
    job.terminate_and_wait.assert_called_once_with(2)
    job.close.assert_not_called()
    assert process.wait.call_count == 2


def test_windows_failed_cleanup_retains_job_not_success(monkeypatch):
    process, job = Mock(), Mock()
    process.wait.return_value = 7
    job.terminate_and_wait.side_effect = TimeoutError("unconfirmed")
    monkeypatch.setattr(api, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        api, "spawn_sync_in_kill_on_close_job", Mock(return_value=(process, job)),
    )
    with pytest.raises(OwnedCleanupError) as caught:
        api.run_interactive(["command"])
    assert caught.value.process is process and caught.value.job is job
    job.close.assert_not_called()


@pytest.mark.parametrize("code,expected", [
    (0, 0), (7, 7), (0xC000013A, 130), (-1073741510, 130),
])
def test_windows_native_exit_status_preserves_normal_codes_and_normalizes_control_c(
    monkeypatch, code, expected,
):
    process, job = Mock(), Mock()
    process.wait.return_value = code
    monkeypatch.setattr(api, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        api, "spawn_sync_in_kill_on_close_job", Mock(return_value=(process, job)),
    )
    assert api.run_interactive(["command"]) == expected
    job.terminate_and_wait.assert_called_once_with(2)


@pytest.mark.parametrize("confirmed", [True, False])
def test_darwin_permission_refusal_is_not_itself_cleanup_confirmation(monkeypatch, confirmed):
    from agent_procutil import _interactive_groups as groups

    custody = object.__new__(groups.SessionCustody)
    custody.census = SimpleNamespace(groups=lambda: set() if confirmed else {123})
    monkeypatch.setattr(groups, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(groups.os, "killpg", Mock(side_effect=PermissionError), raising=False)
    monkeypatch.setattr(groups.signal, "SIGKILL", 9, raising=False)
    if confirmed:
        custody._kill(123)
    else:
        with pytest.raises(PermissionError):
            custody._kill(123)


@pytest.mark.parametrize("confirmed", [True, False])
def test_full_session_confirmation_retains_guardian_until_all_groups_exit(monkeypatch, confirmed):
    from agent_procutil import _interactive_groups as groups
    from agent_procutil import owned_process

    process = Mock(pid=123)
    census = Mock()
    census.groups.return_value = set() if confirmed else {456}
    monkeypatch.setattr(groups, "SessionCensus", Mock(return_value=census))
    monkeypatch.setattr(owned_process, "_observe_unreaped", Mock(return_value=object()))
    if confirmed:
        groups.confirm_session(process)
        process.wait.assert_called_once_with(timeout=2)
    else:
        with pytest.raises(OwnedCleanupError) as caught:
            groups.confirm_session(process, timeout=.001)
        assert caught.value.process is process
        process.wait.assert_not_called()
    assert census.groups.called


def test_a_recycled_group_in_another_session_is_never_signalled(monkeypatch):
    from agent_procutil import _interactive_groups as groups

    custody = object.__new__(groups.SessionCustody)
    custody.session, custody.lifetime = 123, 17
    custody.anchors, custody.pending = {}, []
    custody.census = Mock()
    custody.census.groups.side_effect = [{456}, set(), set()]
    kill = Mock()
    spawn = Mock(side_effect=PermissionError(1, "different session"))
    monkeypatch.setattr(groups, "os", SimpleNamespace(
        getpid=lambda: 123, pipe=lambda: (18, 19), close=Mock(),
        posix_spawn=spawn, environ={}, devnull=os.devnull, O_WRONLY=os.O_WRONLY,
        POSIX_SPAWN_DUP2=2, POSIX_SPAWN_OPEN=0, killpg=kill,
    ))
    custody.retire()
    assert spawn.call_args.kwargs["setpgroup"] == 456
    kill.assert_not_called()


@pytest.mark.skipif(not SUPPORTED, reason="native ownership support required")
@pytest.mark.parametrize("mode", [
    "completed", "signalled", "stopped-without-tty", "owner-killed", "interrupt",
])
def test_real_completion_cleans_descendants_before_observer_cleanup(tmp_path, mode):
    if os.name == "nt" and mode in ("signalled", "stopped-without-tty"):
        pytest.skip("native POSIX signal-status and stop contracts")
    state = tmp_path / "ready.json"
    command = (
        "import json,os,signal,subprocess,sys,time;from pathlib import Path;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
        "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]));"
        "time.sleep(.3 if sys.argv[2] in "
        "('completed','signalled','stopped-without-tty') else 120);"
        "os.kill(os.getpid(),signal.SIGSTOP) if sys.argv[2]=='stopped-without-tty' else None;"
        "os.kill(os.getpid(),signal.SIGTERM) if sys.argv[2]=='signalled' else sys.exit(7)"
    )
    wrapper = """
import os,signal,sys,threading,time
from pathlib import Path
from agent_procutil.interactive import run_interactive
if os.name=='nt' and sys.argv[3]=='interrupt':
    def interrupt():
        while not Path(sys.argv[2]).with_suffix('.interrupt').exists():
            time.sleep(.01)
        signal.raise_signal(signal.SIGINT)
    threading.Thread(target=interrupt,daemon=True).start()
sys.exit(run_interactive([sys.executable,'-c',sys.argv[1],*sys.argv[2:]]))
"""
    # The wrapper has no controlling terminal; PTY semantics are exercised below.
    kwargs = no_window_kwargs() if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(  # noqa: S603 - fixed fixture argv
        [sys.executable, "-c", wrapper, command, str(state), mode],
        env=_environment(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs,
    )
    pins = []
    try:
        pids = _wait(lambda: _read(state), "command did not start")
        pins = [psutil.Process(pid) for pid in pids]
        if mode == "owner-killed":
            process.kill()
        elif mode == "interrupt" and os.name != "nt":
            process.send_signal(signal.SIGINT)
        elif mode == "interrupt":
            state.with_suffix(".interrupt").touch()
        stdout, stderr = process.communicate(timeout=10)
        expected = {"signalled": 143, "completed": 7, "stopped-without-tty": 1}.get(mode, 130)
        if mode != "owner-killed":
            if process.returncode != expected:
                print(stderr.decode(errors="replace"))
            assert process.returncode == expected, (stdout, stderr)
        if mode == "stopped-without-tty":
            assert b"a stopped interactive command requires a foreground terminal" in stderr
        _wait(lambda: all(_gone(pin) for pin in pins), "owned descendants remained alive")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
        for pin in pins:
            if not _gone(pin):
                pin.kill()
                pin.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt" or not SUPPORTED, reason="native POSIX controlling PTY")
@pytest.mark.parametrize("mode", [
    "normal", "control-c", "suspend-resume", "wrapped-suspend-resume",
])
def test_real_pty_input_resize_signals_and_foreground_restore(tmp_path, mode):
    import fcntl
    import pty
    import select
    import struct
    import termios

    receipt = tmp_path / "result.json"
    command = """
import json,os,signal,sys,termios
assert os.isatty(0) and os.isatty(1) and os.isatty(2)
assert os.tcgetpgrp(0)==os.getpgrp()
signal.signal(signal.SIGWINCH,lambda *_:print('resized',flush=True))
attributes=termios.tcgetattr(0)
attributes[3]&=~termios.ECHO
termios.tcsetattr(0,termios.TCSANOW,attributes)
print('ready',flush=True)
assert input()=='value'
print('input-ok',flush=True)
input()
assert not termios.tcgetattr(0)[3]&termios.ECHO
sys.exit(7)
"""
    wrapper = """
import faulthandler,fcntl,json,os,signal,sys,termios
from pathlib import Path
from agent_procutil.interactive import run_interactive
os.setsid()
fcntl.ioctl(0,termios.TIOCSCTTY,0)
signal.signal(signal.SIGTTOU,signal.SIG_IGN)
reader,writer=os.pipe()
caller=os.fork()
if caller:
    os.close(reader)
    os.setpgid(caller,caller)
    os.tcsetpgrp(0,caller)
    os.write(writer,b'g')
    os.close(writer)
    while True:
        _,status=os.waitpid(caller,os.WUNTRACED)
        if os.WIFSTOPPED(status):
            os.tcsetpgrp(0,os.getpgrp())
            print('suspended',flush=True)
            os.tcsetpgrp(0,caller)
            os.killpg(caller,signal.SIGCONT)
            continue
        os.tcsetpgrp(0,os.getpgrp())
        sys.exit(os.waitstatus_to_exitcode(status))
os.close(writer)
os.setpgid(0,0)
assert os.read(reader,1)==b'g'
os.close(reader)
if sys.argv[3]=='wrapped-suspend-resume':
    worker=os.fork()
    if worker:
        _,status=os.waitpid(worker,0)
        sys.exit(os.waitstatus_to_exitcode(status))
faulthandler.dump_traceback_later(6)
before=termios.tcgetattr(0)
code=run_interactive([sys.executable,'-c',sys.argv[1]])
faulthandler.cancel_dump_traceback_later()
Path(sys.argv[2]).write_text(json.dumps({
    'code':code,'foreground':os.tcgetpgrp(0)==os.getpgrp(),
    'attributes':termios.tcgetattr(0)==before}))
"""
    master, slave = pty.openpty()
    process = subprocess.Popen(  # noqa: S603 - fixed fixture argv
        [sys.executable, "-c", wrapper, command, str(receipt), mode],
        stdin=slave, stdout=slave, stderr=slave, env=_environment(),
    )
    output = bytearray()

    def contains(value):
        if select.select([master], [], [], .02)[0]:
            try:
                output.extend(os.read(master, 65536))
            except OSError:
                pass
        return value in output

    os.close(slave)
    try:
        _wait(lambda: contains(b"ready"), f"PTY command not ready: {output!r}")
        os.write(master, b"value\n")
        _wait(lambda: contains(b"input-ok"), "terminal input was not delivered")
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 33, 101, 0, 0))
        _wait(lambda: contains(b"resized"), "kernel resize signal was not delivered")
        if mode in ("suspend-resume", "wrapped-suspend-resume"):
            os.write(master, b"\x1a")
            _wait(lambda: contains(b"suspended"), "caller did not hand suspension to its shell")
        os.write(master, b"\x03" if mode == "control-c" else b"done\n")
        try:
            _wait(
                lambda: contains(b"") and process.poll() is not None,
                "PTY completion timed out while draining terminal output",
            )
        except AssertionError:
            for child in psutil.Process(process.pid).children(recursive=True):
                try:
                    print(
                        "PTY custody", child.pid, child.ppid(),
                        child.status(), os.getpgid(child.pid),
                    )
                except (ProcessLookupError, psutil.NoSuchProcess):
                    pass
            while select.select([master], [], [], 0)[0]:
                try:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                except OSError:
                    break
            print(output.decode(errors="replace"))
            raise
        assert process.returncode == 0, bytes(output)
        assert json.loads(receipt.read_text()) == {
            "code": 130 if mode == "control-c" else 7,
            "foreground": True, "attributes": True,
        }
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        os.close(master)
