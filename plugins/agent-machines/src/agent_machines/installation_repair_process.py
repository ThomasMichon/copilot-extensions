"""Bounded, headless, caller-owned invocations of admitted owner actions."""

from __future__ import annotations

import os
import math
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

MAX_OUTPUT = 64 * 1024


@dataclass(frozen=True)
class ActionResult:
    status: str
    exit_code: int | None
    stdout: bytes = b""


def run_action(
    argv: Sequence[str], *, cwd: Path, environment: Mapping[str, str],
    timeout: float, readonly: bool,
) -> ActionResult:
    """Bound both pipes and terminate owned transient descendants on every exit."""
    if not math.isfinite(timeout) or not 0 < timeout <= 3600 or not argv or any(
        not isinstance(arg, str) or "\0" in arg for arg in argv
    ):
        raise ValueError("invalid bounded owner invocation")
    kwargs = no_window_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True
    try:
        process, job = spawn_sync_in_kill_on_close_job(
            list(argv), require_job=True, allow_breakaway=not readonly,
            cwd=cwd, env=dict(environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs,
        )
    except (OSError, RuntimeError):
        return ActionResult("launch-failed", None)
    overflow = threading.Event()
    chunks: list[bytes] = []
    readers: list[threading.Thread] = []
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
            job.close()
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        terminated = True

    try:
        for stream, retain in ((process.stdout, True), (process.stderr, False)):
            assert stream is not None
            reader = threading.Thread(target=drain, args=(stream,), kwargs={"retain": retain},
                                      daemon=True)
            readers.append(reader)
            reader.start()
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
        terminate()
        for reader in readers:
            reader.join(timeout=5)
