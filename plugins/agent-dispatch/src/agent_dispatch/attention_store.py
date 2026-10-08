"""When each attention condition was first observed, kept per source across reads.

A source that only knows *that* something needs the operator (not when it
began) gets its item's ``created_at`` from here, so repeated reads -- separate
CLI invocations included -- keep the same order. A source's time is cleared
only by proof from that same source that the condition ended: an ``ok`` read
that no longer contains the item. A ``failed``, ``uncertain`` or ``disabled``
read proves nothing, so it keeps every time (an outage never reorders an
unchanged queue), and one source never clears another's evidence.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

from . import attention_contract as ac

_LOCK_TIMEOUT = 10.0
_SEP = "\t"


def default_path() -> Path:
    from .config import install_dir

    return install_dir() / "attention-observed.json"


def _key(source: str, item: dict[str, Any]) -> str:
    return _SEP.join((source, item["entity"], item["entity_ref"], item["display_state"]))


@contextlib.contextmanager
def locked(path: Path, timeout: float = _LOCK_TIMEOUT) -> Iterator[None]:
    """Serialize a whole read-modify-write of ``path`` across processes with the
    OS-released single-instance lock, so a holder that dies never wedges the next."""
    from .single_instance import SingleInstance

    path.parent.mkdir(parents=True, exist_ok=True)
    lock = SingleInstance(path.with_name(path.name + ".lock"))
    deadline = time.monotonic() + timeout
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{path} stayed locked for {timeout:g}s")
        time.sleep(0.02)
    try:
        yield
    finally:
        lock.release()


def _read(path: Path) -> tuple[dict[str, str], dict[str, int], int]:
    """``(entries, applied, next_read)``: first-observed times, each source's
    newest applied read number (the watermark that keeps an older snapshot from
    overwriting newer proof), and the next read number to hand out."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}, 1
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}, {}, 1  # malformed state is recovered as empty, like unreadable JSON
    applied = data.get("applied")
    applied = {k: v for k, v in (applied if isinstance(applied, dict) else {}).items()
               if isinstance(k, str) and type(v) is int}
    next_read = data.get("next_read")
    # Never hand out a number at or below one already applied, even after damage.
    next_read = max([next_read if type(next_read) is int else 1, *(v + 1 for v in applied.values())])
    return ({k: v for k, v in entries.items() if isinstance(k, str) and _canonical(v)}, applied, next_read)


def _canonical(value: object) -> bool:
    """A stored time is used only if it's still a canonical timestamp; anything
    else is dropped as malformed state (the item is then first seen now)."""
    try:
        return ac.canonical_time(value) == value
    except ac.ContractError:
        return False


def _write(path: Path, entries: dict[str, str], applied: dict[str, int], next_read: int) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    doc = {"version": 1, "entries": entries, "applied": applied, "next_read": next_read}
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class FirstObserved:
    """The persisted ``(source, entity, entity_ref, display_state) -> first seen``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()

    def begin_read(self) -> int:
        """Allocate this read's number when it starts: persisted and strictly
        increasing across processes (unlike a clock), so concurrent reads have a
        total order the stale-snapshot guard can rely on."""
        with locked(self.path):
            entries, applied, next_read = _read(self.path)
            _write(self.path, entries, applied, next_read + 1)
        return next_read

    def apply(self, results: dict[str, dict[str, Any]], read_at: str, read_token: int | None = None) -> None:
        """Fill each item's missing ``created_at`` from the store (or ``read_at`` when
        first seen), and clear what an ``ok`` read of that same source dropped.
        ``results`` maps a source name to its result; items are updated in place.
        A read older than one already applied for a source only reads the store:
        concurrent CLI reads can finish out of order, and a slow, older snapshot
        must not re-add a time a newer ``ok`` read proved had ended. Reads are
        ordered by ``read_token``, the number :meth:`begin_read` gave the read
        when it started (allocated now when omitted)."""
        token = self.begin_read() if read_token is None else read_token
        with locked(self.path):
            entries, applied, next_read = _read(self.path)
            before = (dict(entries), dict(applied))
            for source, result in results.items():
                if result["status"] not in ("ok", "uncertain"):
                    continue
                if applied.get(source, -1) > token:
                    for item in result["items"]:
                        if not item.get("created_at"):
                            item["created_at"] = entries.get(_key(source, item), read_at)
                    continue
                applied[source] = token
                seen = set()
                for item in result["items"]:
                    key = _key(source, item)
                    seen.add(key)
                    if item.get("created_at"):
                        entries.setdefault(key, item["created_at"])
                    else:
                        item["created_at"] = entries.setdefault(key, read_at)
                if result["status"] == "ok":
                    prefix = source + _SEP
                    for key in [k for k in entries if k.startswith(prefix) and k not in seen]:
                        del entries[key]
            if (entries, applied) != before:
                _write(self.path, entries, applied, max(next_read, token + 1))
