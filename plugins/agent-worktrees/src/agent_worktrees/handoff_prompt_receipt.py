"""Atomic context-handoff prompt observations in the existing durable trace."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .handoff_trace import _append_lock, trace_path

EVENT = "context_handoff_prompt_received"
_MAX_RECORD_BYTES = 64 * 1024


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


def _matching(path: Path, worktree_id: str, locator: str, session_id: str | None) -> bool:
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return False
    with handle:
        while line := handle.readline(_MAX_RECORD_BYTES + 1):
            if len(line) > _MAX_RECORD_BYTES:
                raise ValueError("receipt history record exceeds the bounded reader")
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
        if _matching(path, worktree_id, locator, session_id):
            if record:
                with path.open("a+b") as handle:
                    os.fsync(handle.fileno())
            return {"received": True, "replayed": True}
        legacy = legacy_path is not None and _matching(
            legacy_path, worktree_id, locator, session_id,
        )
        if not record:
            return {"received": legacy, "replayed": legacy}
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
