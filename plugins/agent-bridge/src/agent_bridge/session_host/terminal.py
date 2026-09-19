"""POSIX PTY adapter for the existing, nonce-authenticated execution host."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys

from .osutil import child_preexec


class _OwnedProcess:
    """Observe exit without reaping the PID that anchors our process group."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self.pid = process.pid
        self._returncode = None

    @property
    def returncode(self):
        if self._returncode is None:
            result = os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if result is not None:
                self._returncode = result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
        return self._returncode

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(.02)
        return self.returncode

    def reap(self):
        if self.returncode is not None:
            self.process.wait()


class _PtyWriter:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.pending = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise BrokenPipeError("terminal is closed")
        if len(self.pending) + len(data) > 1024 * 1024:
            raise ValueError("terminal input exceeds the bounded write queue")
        self.pending.extend(data)

    async def drain(self) -> None:
        loop = asyncio.get_running_loop()
        while self.pending:
            try:
                count = os.write(self.fd, self.pending)
                del self.pending[:count]
            except BlockingIOError:
                ready = loop.create_future()

                def wake() -> None:
                    if not ready.done():
                        ready.set_result(None)

                loop.add_writer(self.fd, wake)
                try:
                    await ready
                finally:
                    loop.remove_writer(self.fd)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.fd)


class PtyChild:
    def __init__(self, process, reader, writer, transport, gate: int | None = None) -> None:
        self.process = process
        self.stdout = reader
        self.stdin = writer
        self.transport = transport
        self.gate = gate
        self._group_settled = False
        from pathlib import Path
        self._leader_ticks = Path(f"/proc/{self.pid}/stat").read_text().rpartition(")")[2].split()[19]

    def start(self) -> None:
        if self.gate is not None:
            os.write(self.gate, b"1")
            os.close(self.gate)
            self.gate = None

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def wait(self) -> int:
        return await self.process.wait()

    def resize(self, rows: int, columns: int) -> None:
        import fcntl
        import struct
        import termios

        if not 1 <= rows <= 65535 or not 1 <= columns <= 65535:
            raise ValueError("invalid terminal size")
        fcntl.ioctl(self.stdin.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        if self.returncode is None:
            try:
                os.killpg(self.pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    def kill(self) -> None:
        if self._group_settled:
            return
        from pathlib import Path

        # Only the living host retains this authority. Never reacquire it from
        # a saved PGID, or signal a replacement process using the leader's PID.
        try:
            ticks = Path(f"/proc/{self.pid}/stat").read_text().rpartition(")")[2].split()[19]
        except FileNotFoundError:
            self._group_settled = True
            return
        if ticks != self._leader_ticks:
            raise RuntimeError("terminal process-group identity changed")
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            self._group_settled = True

    def close(self) -> None:
        self._group_settled = True
        self.transport.close()
        self.stdin.close()
        if self.gate is not None:
            os.close(self.gate)
            self.gate = None
        self.process.reap()


async def spawn_terminal(
    argv: list[str], cwd: str | None, env: dict[str, str], *, start_paused: bool = False,
) -> PtyChild:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("native execution hosting requires a Linux venue")
    import fcntl
    import pty
    import termios

    master, slave = pty.openpty()
    os.set_blocking(master, False)
    preexec = child_preexec()

    def prepare() -> None:
        if preexec:
            preexec()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    process = None
    gate_read = gate_write = None
    try:
        spawn_argv = argv
        inherited = ()
        if start_paused:
            gate_read, gate_write = os.pipe()
            inherited = (gate_read,)
            program = (
                "import os,sys\nfd=int(sys.argv[1])\nallowed=os.read(fd,1)\nos.close(fd)\n"
                "if allowed!=b'1': raise SystemExit(125)\n"
                "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)\n"
            )
            spawn_argv = [sys.executable, "-c", program, str(gate_read), *argv]
        process = _OwnedProcess(subprocess.Popen(
            spawn_argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
            start_new_session=True, preexec_fn=prepare, pass_fds=inherited,
        ))
        reader = asyncio.StreamReader(limit=128 * 1024)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            os.fdopen(os.dup(master), "rb", buffering=0),
        )
        child = PtyChild(process, reader, _PtyWriter(master), transport, gate_write)
        child.resize(24, 80)
        return child
    except BaseException:
        os.close(master)
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            process.reap()
        if gate_write is not None:
            os.close(gate_write)
        raise
    finally:
        os.close(slave)
        if gate_read is not None:
            os.close(gate_read)
