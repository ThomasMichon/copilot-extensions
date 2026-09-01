"""Operator overrides -- the emergency stop for supervised units.

The running set of supervised work is the declared/registered set **reconciled
with operator overrides**, and an override **wins** (vision
``visions/plugins/agent-dispatch`` -- Behavior *overrides-take-precedence*). An
override is a fast, local **enable/disable** toggle on one supervised unit -- an
emitter (stop it producing) or a pool (stop it binding) -- addressed by the
unit's **registration id**. The single supervisor daemon subtracts overridden-off
ids from its desired set on every reconcile, so an overridden unit **winds down**
(the reconcile's stop-not-desired step) and stays down until the override is
cleared.

The store is deliberately:

* **local + out-of-band** -- a machine-local JSON file (``overrides.json``), *not*
  a repo commit + sync cycle, so an operator can stop a misbehaving unit *right
  now* without editing or racing a repo-sync against its declaration;
* **higher-precedence than discovery** -- because the daemon applies overrides
  *after* it merges the declared + store-backed sets, a later repo re-sync that
  re-declares the same unit does **not** quietly undo the override;
* **reversible** -- clearing an override returns the unit to whatever its
  declaration/registration says;
* **legible** -- what is overridden-off, and why, is readable beside what is
  declared (``supervise override list`` / ``supervise daemon-status``).

This module is pure data + small file helpers; nothing here talks to the
coordinator or spawns a process. The daemon consults :func:`overridden_off_ids`;
the ``supervise override`` CLI reads/writes via :func:`set_override` /
:func:`clear_override` / :func:`load_overrides`.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")
LOGICAL_OVERRIDE_PREFIX = "logical:"


def logical_override_id(logical_id: str) -> str:
    """Override token that disables every registration for one logical unit."""
    return f"{LOGICAL_OVERRIDE_PREFIX}{logical_id}"


def load_overrides(path: str | os.PathLike[str] | Path) -> dict[str, dict]:
    """Load the override map ``{registration_id -> record}``, best-effort.

    Returns ``{}`` when the file is missing, unreadable, or not a well-formed
    JSON object -- an unreadable override store must never crash a reconcile or a
    CLI read; it simply means "no overrides in effect". Each record is a dict like
    ``{"disabled": true, "reason": "...", "at": 1699999999.0}``; malformed entries
    (non-dict values) are dropped.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for key, rec in data.items():
        if isinstance(key, str) and key and isinstance(rec, dict):
            out[key] = dict(rec)
    return out


def save_overrides(
    path: str | os.PathLike[str] | Path, overrides: Mapping[str, dict]
) -> None:
    """Persist the override map atomically (write-temp-then-replace).

    Creates the parent directory if needed. The atomic replace means a concurrent
    daemon read never sees a half-written file.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(overrides), indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".overrides-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, p)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def mutate_overrides(
    path: str | os.PathLike[str] | Path,
    mutator: Callable[[dict[str, dict]], T],
    *,
    timeout: float = 5.0,
) -> T:
    """Serialize one complete override read-modify-write transaction."""
    from .single_instance import SingleInstance

    target = Path(path)
    lock = SingleInstance(target.with_name(f"{target.name}.lock"))
    deadline = time.monotonic() + timeout
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out acquiring override lock for {target}")
        time.sleep(0.05)
    try:
        overrides = load_overrides(target)
        result = mutator(overrides)
        save_overrides(target, overrides)
        return result
    finally:
        lock.release()


def set_override(
    path: str | os.PathLike[str] | Path,
    unit_id: str,
    *,
    disabled: bool = True,
    reason: str | None = None,
    now: float | None = None,
) -> dict:
    """Record (or replace) an override for ``unit_id`` and return its record.

    ``disabled=True`` (the default) is the kill-switch: the daemon will wind the
    unit down and keep it down. The record carries an optional ``reason`` and the
    wall-clock ``at`` it was set (for legibility). Persisted immediately.
    """
    def mutate(overrides: dict[str, dict]) -> dict:
        record = {
            "disabled": bool(disabled),
            "reason": reason,
            "at": time.time() if now is None else now,
        }
        overrides[unit_id] = record
        return record

    return mutate_overrides(path, mutate)


def clear_override(path: str | os.PathLike[str] | Path, unit_id: str) -> bool:
    """Remove ``unit_id``'s override, returning it to its declared/registered state.

    Returns ``True`` if an override was present and removed, ``False`` if there was
    nothing to clear.
    """
    def mutate(overrides: dict[str, dict]) -> bool:
        if unit_id not in overrides:
            return False
        del overrides[unit_id]
        return True

    return mutate_overrides(path, mutate)


def overridden_off_ids(overrides: Mapping[str, dict]) -> set[str]:
    """The set of registration ids currently disabled by an override.

    An entry counts as off when its ``disabled`` flag is truthy; a record left with
    ``disabled: false`` is inert (the same as no override), so the daemon only ever
    winds down units an operator explicitly disabled.
    """
    return {
        rid
        for rid, rec in overrides.items()
        if isinstance(rec, Mapping) and rec.get("disabled")
    }
