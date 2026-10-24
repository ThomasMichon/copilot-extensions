"""Bounded byte relay between a caller terminal and a private owned session."""

from __future__ import annotations

import errno
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from ._interactive_groups import confirm_session

_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from agent_procutil._interactive_session import main;main()"
)
_BUFFER = 1024 * 1024


def _above_stdio(fd):
    if fd > 2:
        return fd
    import fcntl

    moved = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
    os.close(fd)
    return moved


def _private_pair(pair):
    descriptors = list(pair)
    try:
        for index, fd in enumerate(descriptors):
            descriptors[index] = _above_stdio(fd)
        return tuple(descriptors)
    except BaseException:
        for fd in descriptors:
            os.close(fd)
        raise


class _Termination(BaseException):
    def __init__(self, signum):
        self.signum = signum


@contextmanager
def _termination():
    previous = {}

    def terminate(signum, _frame):
        for number in previous:
            signal.signal(number, signal.SIG_IGN)
        raise _Termination(signum)

    try:
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGQUIT):
            if signal.getsignal(signum) == signal.SIG_DFL:
                previous[signum] = signal.signal(signum, terminate)
        try:
            yield
        except _Termination as error:
            signum = error.signum
        else:
            return
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    os.kill(os.getpid(), signum)


@contextmanager
def _ignore(signum):
    previous = signal.signal(signum, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signum, previous)


class _Terminal:
    def __init__(self, stdio):
        import termios

        self.fd = None
        if "tty" not in stdio:
            return
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno not in (errno.ENXIO, errno.ENODEV, errno.ENOENT):
                raise
            if any(value == "tty" for value in stdio):
                raise RuntimeError(
                    "interactive streams require the caller's controlling terminal"
                ) from error
            return
        try:
            fd = _above_stdio(fd)
            self.group = os.getpgrp()
            if os.tcgetpgrp(fd) != self.group or any(
                value == "tty" and os.tcgetpgrp(index) != self.group
                for index, value in enumerate(stdio)
            ):
                raise RuntimeError(
                    "interactive completion requires the caller's foreground terminal"
                )
            self.attributes = termios.tcgetattr(fd)
            self.fd = fd
        finally:
            if self.fd is None:
                os.close(fd)

    def raw(self):
        import termios
        import tty

        if self.fd is not None:
            if os.tcgetpgrp(self.fd) != self.group:
                raise RuntimeError("interactive foreground terminal ownership changed")
            tty.setraw(self.fd, termios.TCSANOW)

    def restore(self):
        import termios

        if self.fd is not None:
            try:
                if os.tcgetpgrp(self.fd) == self.group:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.attributes)
            except OSError as error:
                if error.errno not in (errno.EIO, errno.ENXIO, errno.ENOTTY):
                    raise

    def suspend(self):
        if self.fd is None:
            raise RuntimeError("a stopped interactive command requires a foreground terminal")
        self.restore()
        previous = signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        try:
            os.killpg(self.group, signal.SIGTSTP)
        finally:
            signal.signal(signal.SIGTSTP, previous)
        self.raw()

    def size(self, master):
        import fcntl
        import termios

        if self.fd is not None:
            size = fcntl.ioctl(self.fd, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(master, termios.TIOCSWINSZ, size)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)


def _stdio():
    values = []
    for fd in range(3):
        try:
            os.fstat(fd)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
            values.append("absent")
        else:
            values.append("tty" if os.isatty(fd) else "inherit")
    return values


def _ready(fd):
    frame = bytearray()
    deadline = time.monotonic() + 5
    while len(frame) < 64:
        if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
            raise TimeoutError("interactive owner did not become ready")
        value = os.read(fd, 1)
        if not value:
            raise RuntimeError("interactive owner readiness is unconfirmed")
        if value == b"\n":
            if frame != b"ready":
                raise RuntimeError("interactive owner readiness is invalid")
            return
        frame.extend(value)
    raise RuntimeError("interactive owner readiness exceeded its frame bound")


def _read_pty(master, limit=65536):
    try:
        return os.read(master, limit)
    except BlockingIOError:
        return None
    except OSError as error:
        if error.errno == errno.EIO:
            return b""
        raise


def _relay(events, control, master, terminal, output):
    incoming = bytearray()
    frame = bytearray()
    open_master = master is not None
    resize = True
    previous = None
    if terminal.fd is not None:
        def changed(*_args):
            nonlocal resize
            resize = True

        previous = signal.signal(signal.SIGWINCH, changed)
    try:
        while True:
            if resize and open_master:
                terminal.size(master)
                resize = False
            readers = [events]
            writers = []
            if open_master and len(output) < _BUFFER:
                readers.append(master)
            if terminal.fd is not None:
                if len(incoming) < _BUFFER:
                    readers.append(terminal.fd)
                if output:
                    writers.append(terminal.fd)
            if open_master and incoming:
                writers.append(master)
            readable, writable, _ = select.select(readers, writers, [], .05)
            code = None
            if events in readable:
                data = os.read(events, 4096)
                if not data:
                    raise RuntimeError("interactive completion is unconfirmed")
                frame.extend(data)
                while b"\n" in frame:
                    value, _, tail = frame.partition(b"\n")
                    frame[:] = tail
                    if value == b"stopped":
                        terminal.suspend()
                        os.write(control, b"c")
                    elif value.startswith(b"exit "):
                        code = int(value[5:])
                    else:
                        raise RuntimeError("interactive session did not complete")
                if len(frame) > 64:
                    raise RuntimeError("interactive completion frame exceeded its bound")
            if master in readable:
                data = _read_pty(master, min(65536, _BUFFER - len(output)))
                if data:
                    output.extend(data)
                elif data == b"":
                    open_master = False
            if terminal.fd in readable:
                try:
                    data = os.read(terminal.fd, min(65536, _BUFFER - len(incoming)))
                except BlockingIOError:
                    data = None
                if data == b"":
                    raise KeyboardInterrupt
                if data:
                    incoming.extend(data)
            for fd, buffer in ((master, incoming), (terminal.fd, output)):
                if fd in writable and buffer:
                    try:
                        count = os.write(fd, buffer[:65536])
                        del buffer[:count]
                    except BlockingIOError:
                        pass
            if code is not None:
                return 128 - code if code < 0 else code
    finally:
        if previous is not None:
            signal.signal(signal.SIGWINCH, previous)


def _drain(master, terminal, output):
    if master is None:
        return
    deadline = time.monotonic() + 2
    finished = False
    while output or not finished:
        if not finished and len(output) < _BUFFER:
            data = _read_pty(master, min(65536, _BUFFER - len(output)))
            if data:
                output.extend(data)
            else:
                finished = True
        if not output:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [terminal.fd], [], remaining)[1]:
            raise TimeoutError("interactive terminal output did not drain")
        try:
            count = os.write(terminal.fd, output[:65536])
            del output[:count]
            if count:
                deadline = time.monotonic() + 2
        except BlockingIOError:
            pass


def _pump(master, terminal, output):
    if master is None:
        return
    if len(output) < _BUFFER:
        data = _read_pty(master, min(65536, _BUFFER - len(output)))
        if data:
            output.extend(data)
    if output and select.select([], [terminal.fd], [], 0)[1]:
        try:
            count = os.write(terminal.fd, output[:65536])
            del output[:count]
        except BlockingIOError:
            pass


def _run(argv, env):
    from ._caller_owned import _lifetime_lock, _lifetime_writers, _require_posix_support

    _require_posix_support()
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError("interactive completion requires the default SIGCHLD disposition")
    stdio = _stdio()
    terminal = _Terminal(stdio)
    descriptors = []
    writer = process = master = None
    output = bytearray()
    try:
        slave = -1
        if terminal.fd is not None:
            import termios

            master, slave = _private_pair(os.openpty())
            descriptors.extend((master, slave))
            termios.tcsetattr(slave, termios.TCSANOW, terminal.attributes)
            terminal.size(master)
            os.set_blocking(master, False)
        with _lifetime_lock:
            lifetime, writer = _private_pair(os.pipe())
            descriptors.extend((lifetime, writer))
            _lifetime_writers.add(writer)
        events, reply = _private_pair(os.pipe())
        descriptors.extend((events, reply))
        gate, start = _private_pair(os.pipe())
        descriptors.extend((gate, start))
        stream_values = [
            slave if value == "tty" else subprocess.DEVNULL if value == "absent" else None
            for value in stdio
        ]
        inherited = (lifetime, reply, gate, *([slave] if slave >= 0 else []))
        # headless-guard: allow a private terminal for post-UI interactive completion
        process = subprocess.Popen(  # noqa: S603 - argv, no shell
            [
                sys.executable, "-I", "-S", "-u", "-c", _BOOTSTRAP,
                str(Path(__file__).resolve().parents[1]),
                str(lifetime), str(reply), str(gate), str(os.getpid()), str(slave), *argv,
            ],
            env=env, pass_fds=inherited, start_new_session=True,
            stdin=stream_values[0], stdout=stream_values[1], stderr=stream_values[2],
        )
        for fd in inherited:
            os.close(fd)
            descriptors.remove(fd)
        _ready(events)
        terminal.raw()
        os.write(start, b"g")
        return _relay(events, start, master, terminal, output)
    finally:
        with _ignore(signal.SIGINT):
            try:
                if writer is not None:
                    with _lifetime_lock:
                        _lifetime_writers.discard(writer)
                        os.close(writer)
                        descriptors.remove(writer)
                    writer = None
                if process is not None:
                    # BSD terminal close may wait for the master consumer.
                    confirm_session(process, on_wait=lambda: _pump(master, terminal, output))
                    _drain(master, terminal, output)
            finally:
                try:
                    terminal.restore()
                finally:
                    terminal.close()
                    for fd in descriptors:
                        os.close(fd)


def run(argv, env):
    with _termination():
        return _run(argv, env)
