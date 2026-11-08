"""Bounded, headless, caller-owned invocations of admitted owner actions."""

from __future__ import annotations

import os
import math
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

MAX_OUTPUT = 64 * 1024
_termination = threading.local()
_CANCEL_SIGNALS = (
    (signal.SIGINT,) if os.name == "nt" else (signal.SIGINT, signal.SIGTERM)
)


def _ignore_cancellation():
    for signum in _CANCEL_SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def _cancel_signal(signum, frame):
    _termination.pending = True
    if not getattr(_termination, "deferring", False):
        # A second termination request must not interrupt descendant cleanup.
        _ignore_cancellation()
        raise KeyboardInterrupt


@contextmanager
def cancel_on_termination():
    """Keep CLI termination inside the same cleanup/lock lifetime as cancellation."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {signum: signal.getsignal(signum) for signum in _CANCEL_SIGNALS}
    if (
        any(handler is _cancel_signal for handler in previous.values())
        and all(handler is _cancel_signal or handler == signal.SIG_IGN
                for handler in previous.values())
    ):
        yield
        return
    _termination.pending = False
    try:
        for signum, handler in previous.items():
            if handler != signal.SIG_IGN:
                signal.signal(signum, _cancel_signal)
        yield
    finally:
        for signum, handler in reversed(tuple(previous.items())):
            signal.signal(signum, handler)


@dataclass(frozen=True)
class ActionResult:
    status: str
    exit_code: int | None
    stdout: bytes = b""


def _posix_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    if not sys.platform.startswith("linux"):
        return True
    # Zombies have released their resources but can await an external reaper.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            with (entry / "stat").open("rb") as stream:
                stat = stream.read(8192)
        except (FileNotFoundError, ProcessLookupError):
            continue
        fields = stat[stat.rfind(b")") + 2:].split()
        if len(fields) < 3:
            raise RuntimeError("repair could not inspect process-group lifetime")
        if int(fields[2]) == pgid and fields[0] not in (b"Z", b"X"):
            return True
    return False


@cancel_on_termination()
def run_action(
    argv: Sequence[str], *, cwd: Path, environment: Mapping[str, str],
    timeout: float, readonly: bool, cmd: bool = False,
) -> ActionResult:
    """Bound both pipes and terminate owned transient descendants on every exit."""
    if not math.isfinite(timeout) or not 0 < timeout <= 3600 or not argv or any(
        not isinstance(arg, str) or "\0" in arg for arg in argv
    ):
        raise ValueError("invalid bounded owner invocation")
    kwargs = no_window_kwargs()
    command: list[str] | str = list(argv)
    if cmd:
        if os.name != "nt" or len(argv) != 5 or list(argv[1:4]) != ["/d", "/s", "/c"]:
            raise ValueError("invalid admitted CMD invocation")
        # CMD's already-quoted tail is not a CRT argument; do not escape it again.
        command = subprocess.list2cmdline(argv[:4]) + " " + argv[4]
    if os.name != "nt":
        kwargs["start_new_session"] = True
    # Defer in Python rather than masking signals, which a spawned service would inherit.
    _termination.deferring = True
    try:
        process, job = spawn_sync_in_kill_on_close_job(
            command, require_job=True, allow_breakaway=not readonly,
            cwd=cwd, env=dict(environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs,
        )
    except BaseException as error:
        _termination.deferring = False
        if getattr(_termination, "pending", False):
            _ignore_cancellation()
            raise KeyboardInterrupt from error
        if isinstance(error, (OSError, RuntimeError)):
            return ActionResult("launch-failed", None)
        raise
    overflow = threading.Event()
    chunks: list[bytes] = []
    readers: list[threading.Thread] = []
    started_streams = []
    terminated = False

    def drain(stream, *, retain: bool):
        size = 0
        try:
            while block := stream.read(4096):
                size += len(block)
                if size > MAX_OUTPUT:
                    overflow.set()
                elif retain:
                    chunks.append(block)
        finally:
            stream.close()

    def terminate():
        nonlocal terminated
        if terminated:
            return
        if job is not None:
            job.terminate_and_wait(5)
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if os.name != "nt":
            deadline = time.monotonic() + 5
            while _posix_group_alive(process.pid):
                if time.monotonic() >= deadline:
                    raise TimeoutError("repair transient process group did not exit")
                time.sleep(0.01)
        terminated = True

    try:
        _termination.deferring = False
        if getattr(_termination, "pending", False):
            _ignore_cancellation()
            raise KeyboardInterrupt
        for stream, retain in ((process.stdout, True), (process.stderr, False)):
            assert stream is not None
            reader = threading.Thread(target=drain, args=(stream,), kwargs={"retain": retain},
                                      daemon=True)
            reader.start()
            readers.append(reader)
            started_streams.append(stream)
        deadline = time.monotonic() + timeout
        while process.poll() is None or any(reader.is_alive() for reader in readers):
            if overflow.is_set():
                terminate()
                return ActionResult("output-limit", process.returncode)
            if time.monotonic() >= deadline:
                terminate()
                return ActionResult("timeout", process.returncode)
            time.sleep(0.02)
        if overflow.is_set():
            return ActionResult("output-limit", process.returncode)
        return ActionResult("exited", process.returncode, b"".join(chunks))
    finally:
        _termination.deferring = True
        try:
            terminate()
            for reader in readers:
                reader.join(timeout=5)
            # Started drainers own their streams; closing a still-reading buffer can block.
            for stream in (process.stdout, process.stderr):
                if stream is not None and stream not in started_streams:
                    stream.close()
        finally:
            _termination.deferring = False
        if getattr(_termination, "pending", False):
            _ignore_cancellation()
            raise KeyboardInterrupt
