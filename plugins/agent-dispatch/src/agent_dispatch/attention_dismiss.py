"""Operator dismissals of attention items, kept on this machine.

A dismissed item is hidden from the queue (and reported under ``dismissed``,
never silently dropped) in one of three modes:

* ``changed`` (the default): until its condition changes -- its
  ``display_state``, ``lifecycle_state`` or ``reason`` (numbers in a reason,
  such as a waiting time or a count, are ignored, so a condition that only ages
  stays dismissed). ``updated_at`` is not compared: some sources stamp it with
  the read time.
* ``until``: until a time, even if nothing changed.
* ``forever``: until it is undismissed.

A ``changed`` or ``until`` dismissal ends by itself once it no longer applies,
and when an ``ok`` read of its source no longer has the item (the condition
ended, so a later recurrence is new). Dismissals are keyed by the item ``id``
and stored beside the coordinator's install (``attention-dismissed.json``),
never in a repository. A store that can't be read hides nothing.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import attention_contract as ac
from .attention_store import locked

MODES = ("changed", "until", "forever")
_NUMBERS = re.compile(r"\d+")


def default_path() -> Path:
    from .config import install_dir

    return install_dir() / "attention-dismissed.json"


def fingerprint(item: dict[str, Any]) -> dict[str, Any]:
    """What a ``changed`` dismissal compares: the condition, not its age."""
    return {"display_state": item.get("display_state"), "lifecycle_state": item.get("lifecycle_state"),
            "reason": _NUMBERS.sub("#", str(item.get("reason") or ""))}


def source_of(item_id: str) -> str:
    return item_id.split(":", 1)[0]


def _canonical(value: Any) -> bool:
    try:
        return ac.canonical_time(value) == value
    except ac.ContractError:
        return False


def _valid(entry: Any) -> bool:
    """Only an entry this store wrote is honored: a damaged one hides nothing."""
    if not isinstance(entry, dict) or entry.get("mode") not in MODES or not _canonical(entry.get("at")):
        return False
    if entry["mode"] == "until":
        return _canonical(entry.get("until"))
    return entry["mode"] != "changed" or isinstance(entry.get("fingerprint"), dict)


class Dismissals:
    """The persisted ``id -> {mode, at, until?, fingerprint?}``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()

    def _read(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        """``(entries, error)``; a malformed store reads as empty, with an error."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, None
        except (OSError, ValueError) as exc:
            return {}, ac.one_line(f"{self.path} is unreadable: {exc}")
        entries = raw.get("dismissed") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return {}, ac.one_line(f"{self.path} has no dismissed object")
        valid = {k: v for k, v in entries.items() if isinstance(k, str) and _valid(v)}
        bad = len(entries) - len(valid)
        return valid, (ac.one_line(f"{self.path}: ignored {bad} malformed dismissal(s)") if bad else None)

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": 1, "dismissed": entries}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def entries(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        return self._read()

    def dismiss(self, item_id: str, mode: str, at: str, *, item: dict[str, Any] | None = None,
                until: str | None = None) -> dict[str, Any]:
        """Record a dismissal. ``changed`` needs the item as it is now; ``until``
        a canonical time."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        entry: dict[str, Any] = {"mode": mode, "at": at}
        if mode == "changed":
            if item is None:
                raise ValueError("a dismissal until the item changes needs the item as it is now")
            entry["fingerprint"] = fingerprint(item)
        if mode == "until":
            entry["until"] = ac.canonical_time(until)
        with locked(self.path):
            entries, _ = self._read()
            entries[item_id] = entry
            self._write(entries)
        return entry

    def undismiss(self, item_id: str) -> bool:
        with locked(self.path):
            entries, _ = self._read()
            removed = entries.pop(item_id, None) is not None
            if removed:
                self._write(entries)
        return removed

    def split(self, items: list[dict[str, Any]], now: str,
              ok_sources: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        """``(visible, dismissed, error)``. A dismissed item carries its
        ``dismissal``; an ended dismissal is removed from the store."""
        entries, error = self._read()
        if not entries:
            return items, [], error
        visible, hidden, ended = [], [], set()
        for item in items:
            entry = entries.get(item["id"])
            if entry is None:
                visible.append(item)
            elif (entry["mode"] == "forever" or (entry["mode"] == "until" and now < entry["until"])
                  or (entry["mode"] == "changed" and entry["fingerprint"] == fingerprint(item))):
                hidden.append({**item, "dismissal": {k: v for k, v in entry.items() if k != "fingerprint"}})
            else:
                ended.add(item["id"])
                visible.append(item)
        seen = {i["id"] for i in items}
        ended |= {k for k, v in entries.items()
                  if v["mode"] != "forever" and k not in seen and source_of(k) in ok_sources}
        if ended:
            with locked(self.path):
                current, _ = self._read()
                # Only what this read judged: a dismissal recorded again meanwhile stays.
                stale = [k for k in ended if k in current and current[k] == entries.get(k)]
                for k in stale:
                    del current[k]
                if stale:
                    self._write(current)
        return visible, hidden, error
