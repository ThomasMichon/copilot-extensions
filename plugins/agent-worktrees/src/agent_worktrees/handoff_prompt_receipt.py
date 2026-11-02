"""Atomic context-handoff prompt observations in the existing durable trace."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time

from .handoff_trace import _append_lock, trace_path

EVENT = "context_handoff_prompt_received"
_MAX_RECORD_BYTES = 64 * 1024
MAX_SCAN_BYTES = 1_048_576
MAX_SCAN_ROWS = 4096
MAX_SCAN_SECONDS = 0.2
MAX_LOCK_SECONDS = 0.1
MAX_PRE_EFFECT_SECONDS = 1.0


def _validate(locator: str, session_id: str | None, *, record: bool) -> None:
    if (
        not isinstance(locator, str)
        or len(locator) > 512
        or not locator.startswith(("task:", "file:"))
        or len(locator.split(":", 1)[1]) == 0
        or not locator.isascii()
        or any(ord(char) <= 32 or ord(char) == 127 for char in locator)
    ):
        raise ValueError("invalid recovery locator")
    if record and (
        not isinstance(session_id, str) or not session_id or len(session_id) > 256
        or any(ord(char) < 32 for char in session_id)
    ):
        raise ValueError("prompt receipt requires a known session")


def _matching(
    path: Path, worktree_id: str, locator: str, session_id: str | None,
    *, terminal_row_bytes: list[int] | None = None,
) -> bool:
    try:
        handle = path.open("r+b")
    except FileNotFoundError:
        return False
    with handle:
        while line := handle.readline(_MAX_RECORD_BYTES + 1):
            if len(line) > _MAX_RECORD_BYTES:
                raise ValueError("receipt history record exceeds the bounded reader")
            if terminal_row_bytes is not None:
                terminal_row_bytes[0] = 0 if line.endswith(b"\n") else len(line)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if (
                isinstance(event, dict) and event.get("event") == EVENT
                and event.get("worktree_id") == worktree_id
                and event.get("locator") == locator
                and (session_id is None or event.get("session_id") == session_id)
            ):
                os.fsync(handle.fileno())
                return True
    return False


def prompt_receipt(
    project: str, worktree_id: str, locator: str, session_id: str | None = None,
    *, record: bool = False, legacy_path: Path | None = None,
) -> dict:
    """Serialize replay detection and append with the trace owner's kernel lock.

    The trace, lock and cleanup lifecycle are unchanged. Reads return one bounded
    result, not retained history. Errors propagate: unavailable is not absent.
    """
    _validate(locator, session_id, record=record)
    path = trace_path(project, worktree_id)
    with _append_lock(path.with_suffix(path.suffix + ".lock")):
        terminal_row_bytes = [0]
        if _matching(
            path, worktree_id, locator, session_id, terminal_row_bytes=terminal_row_bytes,
        ):
            return {"received": True, "replayed": True}
        legacy = legacy_path is not None and _matching(
            legacy_path, worktree_id, locator, session_id,
        )
        if not record:
            return {"received": legacy, "replayed": legacy}
        if terminal_row_bytes[0] >= _MAX_RECORD_BYTES:
            raise ValueError("record-limit")
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": EVENT, "worktree_id": worktree_id, "session_id": session_id,
            "source": "context-handoff", "locator": locator,
        }
        data = (json.dumps(event, ensure_ascii=True) + "\n").encode("utf-8")
        with path.open("a+b", buffering=0) as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            offset = 0
            while offset < len(data):
                written = handle.write(data[offset:])
                if not written:
                    raise OSError("prompt receipt append made no progress")
                offset += written
            os.fsync(handle.fileno())
        return {"received": True, "replayed": bool(legacy)}


class _ReceiptUnavailable(ValueError):
    pass


class _ScanBudget:
    def __init__(self, deadline: float):
        self.deadline = min(deadline, time.monotonic() + MAX_SCAN_SECONDS)
        self.bytes = 0
        self.rows = 0
        self.terminal_row_bytes = 0
        self.matching_handle = None

    def check(self):
        if time.monotonic() >= self.deadline:
            raise _ReceiptUnavailable("scan-timeout")
        if self.bytes > MAX_SCAN_BYTES or self.rows > MAX_SCAN_ROWS:
            raise _ReceiptUnavailable("scan-limit")

    def match(self, path, worktree_id, locator, session_id, creation_nonce, files):
        self.check()
        self.terminal_row_bytes = 0
        try:
            handle = files.enter_context(path.open("r+b"))
        except FileNotFoundError:
            self.check()
            return False
        remaining = os.fstat(handle.fileno()).st_size
        while remaining:
            self.check()
            if self.rows >= MAX_SCAN_ROWS or self.bytes >= MAX_SCAN_BYTES:
                raise _ReceiptUnavailable("scan-limit")
            line = handle.readline(min(_MAX_RECORD_BYTES, MAX_SCAN_BYTES - self.bytes, remaining))
            if not line:
                raise _ReceiptUnavailable("history-changed")
            remaining -= len(line)
            self.bytes += len(line)
            self.rows += 1
            self.terminal_row_bytes = 0 if line.endswith(b"\n") else len(line)
            self.check()
            if len(line) == _MAX_RECORD_BYTES and not line.endswith(b"\n") and remaining:
                raise _ReceiptUnavailable("record-limit")
            if self.bytes == MAX_SCAN_BYTES and not line.endswith(b"\n") and remaining:
                raise _ReceiptUnavailable("scan-limit")
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                self.check()
                continue
            self.check()
            if (
                isinstance(event, dict) and event.get("event") == EVENT
                and event.get("worktree_id") == worktree_id
                and event.get("locator") == locator
                and event.get("session_id") == session_id
            ):
                nonce = event.get("creation_nonce")
                if not isinstance(nonce, str) or not nonce:
                    raise _ReceiptUnavailable("legacy-incarnation-unavailable")
                if nonce == creation_nonce:
                    self.matching_handle = handle
                    return True
        self.check()
        return False


def resident_prompt_receipt(
    project: str, worktree_id: str, locator: str, session_id: str, *,
    creation_nonce: str, record: bool, deadline: float, admission,
) -> dict:
    """Closed resident operation; the caller owns admission and in-flight lifetime.

    The supplied record guard precedes the canonical trace lock. Nothing here
    cancels fsync or admits a second mutation after a transport timeout.
    """
    may_have_committed = False
    pre_effect_deadline = 0.0

    def check():
        if time.monotonic() >= pre_effect_deadline:
            raise _ReceiptUnavailable("pre-effect-timeout")

    def result(status, reason, replayed=False):
        return {"status": status, "reason": reason, "replayed": replayed}

    try:
        _validate(locator, session_id, record=True)
        if (
            not isinstance(creation_nonce, str) or not creation_nonce or len(creation_nonce) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in creation_nonce)
            or type(record) is not bool or not callable(admission)
            or isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            return result("unavailable", "invalid-admission")
        pre_effect_deadline = min(deadline, time.monotonic() + MAX_PRE_EFFECT_SECONDS)
        check()
        path = trace_path(project, worktree_id)
        with admission() as revalidate:
            check()
            if not callable(revalidate):
                return result("unavailable", "invalid-admission")
            with _append_lock(
                path.with_suffix(path.suffix + ".lock"),
                deadline=min(pre_effect_deadline, time.monotonic() + MAX_LOCK_SECONDS),
            ), ExitStack() as files:
                revalidate()
                check()
                budget = _ScanBudget(pre_effect_deadline)
                received = budget.match(
                    path, worktree_id, locator, session_id, creation_nonce, files,
                )
                terminal_row_bytes = budget.terminal_row_bytes
                if not received:
                    from . import config as cfg

                    received = budget.match(
                        cfg.install_dir() / "logs" / "activity.jsonl",
                        worktree_id, locator, session_id, creation_nonce, files,
                    )
                revalidate()
                check()
                if received:
                    # A readable row can survive a failed append fsync. Confirm
                    # durability under the same lock before clearing uncertainty.
                    may_have_committed = True
                    revalidate()
                    check()
                    os.fsync(budget.matching_handle.fileno())
                    if time.monotonic() >= deadline:
                        return result("unknown", "completion-after-deadline")
                    return result("received", "exact-replay", True)
                if not record:
                    return result("absent", "exact-query-absent")
                if terminal_row_bytes >= _MAX_RECORD_BYTES:
                    return result("unavailable", "record-limit")
                event = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": EVENT, "worktree_id": worktree_id,
                    "creation_nonce": creation_nonce, "session_id": session_id,
                    "source": "context-handoff", "locator": locator,
                }
                data = (json.dumps(event, ensure_ascii=True) + "\n").encode("utf-8")
                if len(data) > _MAX_RECORD_BYTES:
                    return result("unavailable", "record-limit")

                def reserve_capacity(separator_bytes=0):
                    if (
                        budget.rows + 1 > MAX_SCAN_ROWS
                        or budget.bytes + len(data) + separator_bytes > MAX_SCAN_BYTES
                    ):
                        raise _ReceiptUnavailable("history-capacity-unavailable")

                reserve_capacity()
                with path.open("a+b", buffering=0) as handle:
                    handle.seek(0, os.SEEK_END)
                    tail = b"\n"
                    if handle.tell():
                        handle.seek(-1, os.SEEK_END)
                        tail = handle.read(1)
                    reserve_capacity(int(tail != b"\n"))
                    revalidate()
                    check()
                    may_have_committed = True
                    if tail != b"\n":
                        handle.write(b"\n")
                    offset = 0
                    while offset < len(data):
                        written = handle.write(data[offset:])
                        if not written:
                            raise OSError("receipt append made no progress")
                        offset += written
                    os.fsync(handle.fileno())
                if time.monotonic() >= deadline:
                    return result("unknown", "completion-after-deadline")
                return result("received", "recorded")
    except (OSError, ValueError, RuntimeError) as error:
        if may_have_committed:
            return result("unknown", "commit-unknown")
        reason = str(error)
        if isinstance(error, TimeoutError):
            reason = "lock-admission-timeout"
        elif not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", reason):
            reason = "receipt-unavailable"
        return result("unavailable", reason)
