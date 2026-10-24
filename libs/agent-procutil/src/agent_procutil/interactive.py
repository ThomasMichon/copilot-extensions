"""Caller-owned foreground commands with inherited terminal streams."""

from __future__ import annotations

import errno
import os
import select
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from agent_procutil import spawn_sync_in_kill_on_close_job
from agent_procutil.owned_process import OwnedCleanupError

_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from agent_procutil.interactive import _main;_main()"
)
_EXEC = (
    "import os,signal,sys;"
    "[signal.signal(getattr(signal,n),signal.SIG_DFL) for n in "
    "('SIGHUP','SIGINT','SIGQUIT','SIGTSTP','SIGTTIN','SIGTTOU','SIGPIPE')];"
    "os.execvpe(sys.argv[1],sys.argv[1:],os.environ)"
)


@contextmanager
def _ignore(signum):
    previous = signal.signal(signum, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signum, previous)


class _Terminal:
    def __init__(self):
        import termios

        self.fd = None
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError as error:
            if error.errno not in (errno.ENXIO, errno.ENODEV, errno.ENOENT):
                raise
            return
        try:
            self.group = os.getpgrp()
            if os.tcgetpgrp(fd) != self.group:
                raise RuntimeError(
                    "interactive completion requires the caller's foreground terminal"
                )
            self.attributes = termios.tcgetattr(fd)
            self.fd = fd
        finally:
            if self.fd is None:
                os.close(fd)

    def settings(self):
        import termios

        return None if self.fd is None else termios.tcgetattr(self.fd)

    def give(self, group, attributes=None):
        import termios

        if self.fd is not None:
            if os.tcgetpgrp(self.fd) != self.group:
                raise RuntimeError("interactive foreground terminal ownership changed")
            with _ignore(signal.SIGTTOU):
                os.tcsetpgrp(self.fd, group)
                if attributes is not None:
                    termios.tcsetattr(self.fd, termios.TCSANOW, attributes)

    def restore(self, child):
        import termios

        if self.fd is not None and os.tcgetpgrp(self.fd) in (child, self.group):
            with _ignore(signal.SIGTTOU):
                os.tcsetpgrp(self.fd, self.group)
                termios.tcsetattr(self.fd, termios.TCSANOW, self.attributes)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)


def _event(fd, *, deadline=None):
    frame = bytearray()
    while len(frame) < 64:
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        if not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError("interactive owner did not become ready")
        value = os.read(fd, 1)
        if not value:
            raise RuntimeError("interactive completion is unconfirmed")
        if value == b"\n":
            return frame.decode("ascii")
        frame.extend(value)
    raise RuntimeError("interactive owner returned an invalid event")


def _settle_posix(process):
    from agent_procutil._caller_owned import _confirm_group
    from agent_procutil.owned_process import _observe_unreaped, _retire_posix_group

    try:
        try:
            _retire_posix_group(process)
        except PermissionError:
            # Darwin can reject killpg when the pinned group is already all
            # zombies. This is not settlement: require the kernel census below.
            if sys.platform != "darwin":
                raise
        # The owner may have failed before establishing its group. Its unreaped
        # PID still belongs to us; never use Popen.poll/kill before group census.
        if _observe_unreaped(process) is None:
            os.kill(process.pid, signal.SIGKILL)
        _confirm_group(process)
        process.wait(timeout=2)
    except BaseException as error:
        if isinstance(error, OwnedCleanupError):
            raise
        _observe_unreaped(process)
        raise OwnedCleanupError(process) from error


def _posix(argv, env):
    from agent_procutil._caller_owned import (
        _lifetime_lock,
        _lifetime_writers,
        _require_posix_support,
    )

    _require_posix_support()
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError("interactive completion requires the default SIGCHLD disposition")
    terminal = _Terminal()
    descriptors = []
    writer = process = None
    try:
        with _lifetime_lock:
            lifetime, writer = os.pipe()
            descriptors.extend((lifetime, writer))
            _lifetime_writers.add(writer)
        events, reply = os.pipe()
        descriptors.extend((events, reply))
        gate, start = os.pipe()
        descriptors.extend((gate, start))
        # headless-guard: allow real user-interactive stdio in the existing terminal
        process = subprocess.Popen(  # noqa: S603 - argv, no shell
            [
                sys.executable, "-I", "-S", "-u", "-c", _BOOTSTRAP,
                str(Path(__file__).resolve().parents[1]),
                str(lifetime), str(reply), str(gate), str(os.getpid()), *argv,
            ],
            env=env, pass_fds=(lifetime, reply, gate),
        )
        for fd in (lifetime, reply, gate):
            os.close(fd)
            descriptors.remove(fd)
        if _event(events, deadline=time.monotonic() + 5) != "ready":
            raise RuntimeError("interactive owner readiness is invalid")
        terminal.give(process.pid)
        os.write(start, b"g")
        os.close(start)
        descriptors.remove(start)
        while True:
            event = _event(events)
            if event.startswith("exit "):
                code = int(event[5:])
                return 128 - code if code < 0 else code
            if event != "stopped":
                raise RuntimeError("interactive owner completion is invalid")
            if terminal.fd is None:
                raise RuntimeError("a stopped interactive command requires a foreground terminal")
            command_attributes = terminal.settings()
            terminal.restore(process.pid)
            previous = signal.signal(signal.SIGTSTP, signal.SIG_DFL)
            try:
                os.killpg(terminal.group, signal.SIGTSTP)
            finally:
                signal.signal(signal.SIGTSTP, previous)
            terminal.give(process.pid, command_attributes)
            os.killpg(process.pid, signal.SIGCONT)
    finally:
        with _ignore(signal.SIGINT):
            try:
                if process is not None:
                    _settle_posix(process)
            finally:
                try:
                    if process is not None:
                        terminal.restore(process.pid)
                finally:
                    terminal.close()
                    with _lifetime_lock:
                        if writer is not None:
                            _lifetime_writers.discard(writer)
                        for fd in descriptors:
                            os.close(fd)


def run_interactive(argv: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run foreground stdio unchanged; settle owned descendants before return.

    Explicitly detached managed workloads are outside this finite ownership
    scope. Cleanup failures retain custody in ``OwnedCleanupError``. Call from
    the main thread. POSIX signal exits use 128 + signal; interruption returns
    130 only after cleanup, including Windows' native control-C exit status.
    """
    if (
        not isinstance(argv, list) or not argv
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
    ):
        raise ValueError("interactive argv must be a nonempty list of strings without NUL")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("interactive completion requires the main thread")
    try:
        if os.name != "nt":
            return _posix(argv, env)
        # headless-guard: allow real user-interactive stdio in the existing console
        process, job = spawn_sync_in_kill_on_close_job(argv, env=env, require_job=True)
        try:
            while True:
                try:
                    code = process.wait(timeout=0.1)
                    return 130 if code in (0xC000013A, -1073741510) else code
                except subprocess.TimeoutExpired:
                    pass
        finally:
            with _ignore(signal.SIGINT):
                try:
                    job.terminate_and_wait(2)
                    process.wait(timeout=2)
                except BaseException as error:
                    raise OwnedCleanupError(process, job) from error
    except KeyboardInterrupt:
        return 130


def _main():
    lifetime, reply, gate, owner = map(int, sys.argv[1:5])
    for signum in (
        signal.SIGHUP, signal.SIGINT, signal.SIGQUIT,
        signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU,
    ):
        signal.signal(signum, signal.SIG_IGN)
    os.setpgid(0, 0)
    if os.getppid() != owner:
        os.killpg(os.getpid(), signal.SIGKILL)

    def retire():
        os.killpg(os.getpid(), signal.SIGKILL)

    def watch():
        try:
            os.read(lifetime, 1)
        finally:
            retire()

    threading.Thread(target=watch, name="interactive-caller-lifetime", daemon=True).start()
    if sys.platform == "darwin":
        from agent_procutil._darwin_caller_owned import OwnerWatch

        owner_watch = OwnerWatch(owner)

        def watch_owner():
            try:
                owner_watch.wait()
            finally:
                retire()

        threading.Thread(target=watch_owner, name="interactive-caller-exit", daemon=True).start()
    try:
        os.write(reply, b"ready\n")
        if os.read(gate, 1) != b"g":
            return
        os.close(gate)
        # Reset the guard's ignored control signals before executing user code.
        # headless-guard: allow real user-interactive stdio in the foreground group
        child = subprocess.Popen(  # noqa: S603 - argv, no shell
            [sys.executable, "-I", "-S", "-c", _EXEC, *sys.argv[5:]],
        )
        while True:
            pid, status = os.waitpid(child.pid, os.WUNTRACED)
            if pid != child.pid:
                raise RuntimeError("interactive command identity changed")
            if os.WIFSTOPPED(status):
                os.write(reply, b"stopped\n")
                continue
            child.returncode = os.waitstatus_to_exitcode(status)
            os.write(reply, f"exit {child.returncode}\n".encode("ascii"))
            return
    finally:
        retire()
