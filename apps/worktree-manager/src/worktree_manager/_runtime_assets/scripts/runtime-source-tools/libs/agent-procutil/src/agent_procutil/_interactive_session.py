"""Finite private-session guardian; no product code runs before terminal admission."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import threading
import time

from ._interactive_groups import SessionCustody
from .owned_process import require_contained_worker

_EXEC = """
import os,signal,sys
ready,gate=map(int,sys.argv[1:3])
os.setpgid(0,0)
os.write(ready,b'ready\\n')
os.close(ready)
allowed=os.read(gate,1)==b'g'
os.close(gate)
if not allowed:
    os._exit(0)
for name in ('SIGHUP','SIGINT','SIGQUIT','SIGTSTP','SIGTTIN','SIGTTOU','SIGPIPE'):
    signal.signal(getattr(signal,name),signal.SIG_DFL)
os.execvpe(sys.argv[3],sys.argv[3:],os.environ)
"""


def main(*, asynchronous=False):
    require_contained_worker()
    lifetime, reply, control, owner, slave = map(int, sys.argv[1:6])
    for fd in (lifetime, reply, control, slave):
        if fd >= 0:
            os.set_inheritable(fd, False)
    for name in ("SIGHUP", "SIGINT", "SIGQUIT", "SIGTSTP", "SIGTTIN", "SIGTTOU"):
        signal.signal(getattr(signal, name), signal.SIG_IGN)
    if os.getppid() != owner:
        os.killpg(os.getpid(), signal.SIGKILL)
    custody = SessionCustody(lifetime)
    retiring = threading.Event()
    retirement_lock = threading.Lock()
    native_wait_lock = threading.Lock()
    child = None
    exit_reported = False

    def emit(frame):
        try:
            os.write(reply, frame)
        except BrokenPipeError:
            pass

    def report_exit():
        nonlocal exit_reported
        if asynchronous and child is not None and not exit_reported:
            emit(f"exit {child.returncode}\n".encode("ascii"))
            exit_reported = True

    def retire():
        retiring.set()
        with retirement_lock:
            failed = False
            while True:
                try:
                    custody.retire()
                    if asynchronous:
                        with native_wait_lock:
                            if child is not None:
                                if child.returncode is None:
                                    child.kill()
                                child.wait(timeout=2)
                                report_exit()
                        custody.retire()
                        emit(f"settled {os.getpid()}\n".encode("ascii"))
                    os.killpg(os.getpid(), signal.SIGKILL)
                except Exception:
                    # Keep the session and unreaped group anchors in custody.
                    # Neither a failed census nor a deadline certifies cleanup.
                    if not failed:
                        emit(b"failed\n")
                        failed = True
                    time.sleep(.1)

    def watch():
        try:
            os.read(lifetime, 1)
        finally:
            retire()

    threading.Thread(target=watch, name="interactive-caller-lifetime", daemon=True).start()
    if sys.platform == "darwin":
        from ._darwin_caller_owned import OwnerWatch

        owner_watch = OwnerWatch(owner)

        def watch_owner():
            try:
                owner_watch.wait()
            finally:
                retire()

        threading.Thread(target=watch_owner, name="interactive-caller-exit", daemon=True).start()
    try:
        if slave >= 0:
            import fcntl
            import termios

            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        emit(b"ready\n")
        if os.read(control, 1) != b"g" or retiring.is_set():
            return
        ready, acknowledgement = os.pipe()
        gate, start = os.pipe()
        try:
            # headless-guard: allow the actual user-interactive completion terminal
            with retirement_lock:
                if retiring.is_set():
                    return
                child = subprocess.Popen(  # noqa: S603 - argv, no shell
                    [
                        sys.executable, "-I", "-S", "-c", _EXEC,
                        str(acknowledgement), str(gate), *sys.argv[6:],
                    ],
                    pass_fds=(acknowledgement, gate),
                )
        finally:
            os.close(acknowledgement)
            os.close(gate)
        try:
            if (
                not select.select([ready], [], [], 5)[0]
                or os.read(ready, 64) != b"ready\n"
            ):
                raise RuntimeError("interactive command group did not become ready")
            if retiring.is_set():
                return
            if asynchronous:
                emit(f"native {child.pid}\n".encode("ascii"))
                if os.read(control, 1) != b"g" or retiring.is_set():
                    return
            if slave >= 0:
                os.tcsetpgrp(slave, child.pid)
            os.write(start, b"g")
            if asynchronous:
                emit(b"running\n")
        finally:
            os.close(ready)
            os.close(start)
        while True:
            with native_wait_lock:
                if asynchronous and child.returncode is not None:
                    return
                pid, status = os.waitpid(
                    child.pid, os.WUNTRACED | (os.WNOHANG if asynchronous else 0),
                )
                if pid and not os.WIFSTOPPED(status):
                    child.returncode = os.waitstatus_to_exitcode(status)
                    report_exit()
            if not pid:
                time.sleep(.005)
                continue
            if pid != child.pid:
                raise RuntimeError("interactive command identity changed")
            if os.WIFSTOPPED(status):
                emit(b"stopped\n")
                if os.read(control, 1) != b"c":
                    return
                os.killpg(child.pid, signal.SIGCONT)
                continue
            if not asynchronous:
                emit(f"exit {child.returncode}\n".encode("ascii"))
            return
    except Exception:
        emit(b"failed\n")
    finally:
        retire()
