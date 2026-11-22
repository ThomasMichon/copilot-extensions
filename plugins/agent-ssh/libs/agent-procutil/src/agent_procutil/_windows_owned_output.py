"""Output collection within the existing finite Windows Job owner."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any


def communicate(
    process: Any, job: Any, *, input_text: str | bytes | None, deadline: float,
    argv: list[str], timeout: float, soft_timeout: float | None = None,
) -> tuple[str | bytes | None, str | bytes | None]:
    """Collect output, then settle the original Job, process and pipe readers."""
    from .owned_process import _remaining

    if job is None:
        raise RuntimeError("native child Windows Job custody is unavailable")
    stop = threading.Event()
    expired = threading.Event()
    retirement = threading.Lock()
    retired = False
    failures: list[BaseException] = []
    stream_capture = None

    def retire() -> None:
        nonlocal retired
        with retirement:
            if not retired:
                job.terminate_and_wait(2.0)
                retired = True

    def supervise() -> None:
        if stop.wait(max(0.0, deadline - time.monotonic())):
            return
        expired.set()
        try:
            retire()
        except BaseException as error:
            failures.append(error)

    watchdog = (
        threading.Thread(target=supervise, name="pr-native-input-deadline", daemon=False)
        if input_text is not None else None
    )
    try:
        if watchdog is not None:
            watchdog.start()
        if soft_timeout is None:
            stdout, stderr = process.communicate(
                input_text, timeout=_remaining(deadline, argv, timeout),
            )
        else:
            from .streaming import SyncProcessOutput

            stream_capture = SyncProcessOutput(
                process, input_bytes=(
                    input_text.encode("utf-8") if isinstance(input_text, str) else input_text
                ),
            )
            stream_capture.wait(None, deadline=deadline, soft_timeout=soft_timeout)
        completed_at = time.monotonic()
        stop.set()
    finally:
        stop.set()
        try:
            retire()
        finally:
            if watchdog is not None and watchdog.ident is not None:
                watchdog.join()
            process.wait(timeout=2.0)
            if stream_capture is not None:
                stream_capture.finish(2.0)
                stdout, stderr = stream_capture.output()
                pending = sys.exc_info()[1]
                if isinstance(pending, subprocess.TimeoutExpired):
                    pending.output = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
                    pending.stderr = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
            for reader in (
                getattr(process, "stdout_thread", None), getattr(process, "stderr_thread", None),
            ):
                if reader is not None:
                    reader.join()
        if failures:
            raise failures[0]
    if expired.is_set() or completed_at >= deadline:
        raise subprocess.TimeoutExpired(argv, timeout, stdout, stderr)
    return stdout, stderr
