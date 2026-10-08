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


def _read(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}  # malformed state is recovered as empty, like unreadable JSON
    return {k: v for k, v in entries.items() if isinstance(k, str) and isinstance(v, str)}


def _write(path: Path, entries: dict[str, str]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"version": 1, "entries": entries}, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class FirstObserved:
    """The persisted ``(source, entity, entity_ref, display_state) -> first seen``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()

    def apply(self, results: dict[str, dict[str, Any]], read_at: str) -> None:
        """Fill each item's missing ``created_at`` from the store (or ``read_at`` when
        first seen), and clear what an ``ok`` read of that same source dropped.
        ``results`` maps a source name to its result; items are updated in place."""
        with locked(self.path):
            entries = _read(self.path)
            before = dict(entries)
            for source, result in results.items():
                if result["status"] not in ("ok", "uncertain"):
                    continue
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
            if entries != before:
                _write(self.path, entries)
