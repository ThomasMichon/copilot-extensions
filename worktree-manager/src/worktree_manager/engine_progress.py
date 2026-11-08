"""Stream launch preparation diagnostics while retaining the JSON boundary."""

from __future__ import annotations

import os
import json
import sys
import tempfile
import threading
from contextlib import contextmanager
from .engine_posix_process import cleanup_phase


def error_from_envelope(stdout: str) -> str | None:
    """Pull the ``error`` field out of a JSON error envelope, if stdout is one."""
    try:
        obj = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("error"):
        return str(obj["error"])
    return None


@contextmanager
def streamed_stderr():
    fd, path = tempfile.mkstemp(prefix="worktree-launch-", suffix=".log")
    stopped = threading.Event()

    def relay():
        with open(path, encoding="utf-8", errors="replace") as reader:
            while True:
                text = reader.read()
                if text:
                    sys.stderr.write(text)
                    sys.stderr.flush()
                if stopped.wait(0.1):
                    text = reader.read()
                    if text:
                        sys.stderr.write(text)
                        sys.stderr.flush()
                    return

    thread = threading.Thread(target=relay, name="launch-progress", daemon=True)
    thread.start()
    try:
        yield fd, path
    finally:
        try:
            with cleanup_phase():
                os.close(fd)
        finally:
            stopped.set()
            with cleanup_phase():
                thread.join()
        try:
            os.unlink(path)
        except OSError as exc:
            print(f"Could not remove launch diagnostic log {path}: {exc}", file=sys.stderr)
