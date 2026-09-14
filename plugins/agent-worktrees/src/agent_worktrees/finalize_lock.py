"""Mechanical extraction of finalization locking helpers from ``finalize.py``.

This sibling module exists only to control ``finalize.py`` module size. The
``FinalizeLock`` implementation was moved verbatim with no behavioral changes.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

from . import locks, output, tracking


class FinalizeLock:
    """Simple file-based lock with timeout and stale detection."""

    def __init__(
        self,
        lock_path: Path,
        timeout: float = 120,
        *,
        stale_after: float | None = None,
    ) -> None:
        self.lock_path = lock_path
        self.timeout = timeout
        self.stale_after = timeout if stale_after is None else stale_after
        self.owner_pid = os.getpid()
        self.owner_start_time = locks.process_start_time(self.owner_pid) or ""
        self.token = (
            f"{self.owner_pid}:{self.owner_start_time}:{uuid.uuid4().hex}"
        )
        self.held = False
        self.guard_path = lock_path.with_name(f"{lock_path.name}.guard")

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()

        while True:
            remaining = self.timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for finalization lock.")
            try:
                with tracking._RecordLock(
                    self.guard_path,
                    timeout=max(0.01, remaining),
                    require_sidecar=True,
                ):
                    if not self.lock_path.exists():
                        fd = os.open(
                            self.lock_path,
                            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                            0o600,
                        )
                        try:
                            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                                handle.write(self.token)
                        except BaseException:
                            try:
                                self.lock_path.unlink()
                            except OSError:
                                pass
                            raise
                        self.held = True
                        return
                    try:
                        observed = self.lock_path.read_text(encoding="utf-8").strip()
                        age = time.time() - self.lock_path.stat().st_mtime
                    except OSError:
                        if time.monotonic() - start > self.timeout:
                            raise TimeoutError(
                                "Timed out waiting for finalization lock."
                            )
                        time.sleep(min(0.1, max(0.01, self.timeout)))
                        continue
                    token_parts = observed.split(":")
                    owner_text = token_parts[0]
                    owner_start_time = (
                        token_parts[1]
                        if len(token_parts) >= 3 and token_parts[1].isdigit()
                        else ""
                    )
                    try:
                        owner_pid = int(owner_text)
                    except ValueError:
                        owner_pid = 0
                    if owner_pid > 0:
                        stale = not locks.pid_alive(owner_pid)
                        if not stale and owner_start_time:
                            current_start_time = locks.process_start_time(owner_pid)
                            stale = bool(
                                current_start_time
                                and current_start_time != owner_start_time
                            )
                        elif not stale and age > self.stale_after:
                            stale = True
                    else:
                        stale = age > self.stale_after
                    if stale:
                        try:
                            output.warn(
                                f"Stale lock detected (age: {int(age)}s) -- breaking."
                            )
                            self.lock_path.unlink()
                            continue
                        except OSError:
                            if time.monotonic() - start > self.timeout:
                                raise TimeoutError(
                                    "Timed out waiting for finalization lock."
                                )
                            time.sleep(min(0.1, max(0.01, self.timeout)))
                            continue
            except TimeoutError:
                pass

            if time.monotonic() - start > self.timeout:
                raise TimeoutError("Timed out waiting for finalization lock.")

            print("Waiting for finalization lock...", file=sys.stderr)
            time.sleep(min(2.0, max(0.01, self.timeout)))

    def release(self) -> None:
        if not self.held:
            return
        try:
            with tracking._RecordLock(
                self.guard_path,
                timeout=min(2.0, max(0.01, self.timeout)),
                require_sidecar=True,
            ):
                try:
                    current = self.lock_path.read_text(encoding="utf-8").strip()
                    if current == self.token:
                        self.lock_path.unlink()
                except OSError:
                    pass
        except (OSError, TimeoutError):
            pass
        finally:
            self.held = False

    def __enter__(self) -> FinalizeLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
