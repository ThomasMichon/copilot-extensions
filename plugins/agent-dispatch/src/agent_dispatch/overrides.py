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

import _thread
import base64
import contextlib
import hashlib
import json
import math
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")
LOGICAL_OVERRIDE_PREFIX = "logical:"
STANDING_OVERRIDE_PREFIX = "standing-charter:"
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, _thread.LockType] = {}


class OverrideObservationError(ValueError):
    """Malformed input with the legacy valid-entry subset retained."""

    def __init__(self, data: object) -> None:
        super().__init__("operator override map is malformed")
        self.overrides = {
            key: dict(rec) for key, rec in data.items()
            if isinstance(key, str) and key and isinstance(rec, dict)
        } if isinstance(data, Mapping) else {}


def logical_override_id(owner: str, logical_id: str) -> str:
    """Override token that disables every registration for one logical unit."""
    return f"{LOGICAL_OVERRIDE_PREFIX}{owner}:{logical_id}"


def _thread_lock(path: Path) -> _thread.LockType:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def validate_overrides(data: object) -> None:
    """Reject an incomplete override map instead of silently dropping vetoes."""
    if not isinstance(data, Mapping) or any(
        not isinstance(key, str) or not key or not isinstance(rec, dict)
        for key, rec in data.items()
    ):
        raise OverrideObservationError(data)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("operator override map has duplicate JSON keys")
        result[key] = value
    return result


def load_overrides(path: str | os.PathLike[str] | Path, *, strict: bool = False) -> dict[str, dict]:
    """Load the override map ``{registration_id -> record}``, best-effort.

    Returns ``{}`` when the file is missing, unreadable, or not a well-formed
    JSON object -- an unreadable override store must never crash a reconcile or a
    CLI read; it simply means "no overrides in effect". Each record is a dict like
    ``{"disabled": true, "reason": "...", "at": 1699999999.0}``; malformed entries
    (non-dict values) are dropped. With ``strict=True``, confirmed absence still
    returns an empty map; all other read/parse/entry failures raise.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if strict:
            raise
        return {}
    try:
        data = json.loads(raw, object_pairs_hook=_unique_pairs if strict else None)
    except (ValueError, TypeError):
        if strict:
            raise
        return {}
    if strict:
        validate_overrides(data)
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

    Uses UTF-8/LF on every platform so byte limits match the published file.
    Creates the parent directory if needed. The atomic replace means a concurrent
    daemon read never sees a half-written file.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(overrides), indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".overrides-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
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
    loader: Callable[[Path], dict[str, dict]] | None = None,
) -> T:
    """Serialize one complete override read-modify-write transaction.

    Shared-map writers load strictly by default so an unrelated mutation can
    never rewrite malformed input, or a duplicate-key standing hold, as a
    narrower map. Read-only consumers keep :func:`load_overrides`' tolerance.
    """
    with locked_overrides(path, timeout=timeout, loader=loader) as overrides:
        result = mutator(overrides)
        save_overrides(path, overrides)
        return result


@contextlib.contextmanager
def locked_overrides(
    path: str | os.PathLike[str] | Path,
    *,
    timeout: float = 5.0,
    loader: Callable[[Path], dict[str, dict]] | None = None,
):
    """Keep admission and policy mutation on the same serialization boundary."""
    from .single_instance import SingleInstance

    load = _standing_policy if loader is None else loader
    target = Path(path)
    deadline = time.monotonic() + timeout
    thread_lock = _thread_lock(target)
    if not thread_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError(f"timed out acquiring override lock for {target}")
    try:
        lock = SingleInstance(target.with_name(f"{target.name}.lock"))
        while not lock.acquire():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out acquiring override lock for {target}")
            time.sleep(0.05)
        try:
            yield load(target)
        finally:
            lock.release()
    finally:
        thread_lock.release()


@dataclass(frozen=True)
class StandingAttachmentSelector:
    namespace: str
    charter_key: str
    lane_key: str | None = None

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "lane_key" and value is None:
                continue
            if (
                not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 1024 or any(ord(char) < 32 for char in value)
            ):
                raise ValueError(f"standing attachment selector {name} must be bounded nonempty text")

    @property
    def override_id(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return STANDING_OVERRIDE_PREFIX + base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _standing_policy(path: Path) -> dict[str, dict]:
    overrides = load_overrides(path, strict=True)
    for key, record in overrides.items():
        if not key.startswith(STANDING_OVERRIDE_PREFIX):
            continue
        try:
            selector = StandingAttachmentSelector(**record["selector"])
            valid = (
                set(record) == {"version", "selector", "disabled", "reason", "at"}
                and type(record["version"]) is int and record["version"] == 1
                and type(record["disabled"]) is bool
                and isinstance(record["reason"], str) and bool(record["reason"].strip())
                and len(record["reason"]) <= 1024
                and type(record["at"]) in (int, float) and math.isfinite(record["at"])
                and selector.override_id == key
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("standing attachment policy is malformed or unsupported")
    return overrides


def set_standing_attachment_deny(
    path: str | os.PathLike[str] | Path,
    selector: StandingAttachmentSelector,
    *,
    reason: str,
    now: float | None = None,
) -> dict:
    """Veto future attachments only; no task, occupant or process is changed."""
    if not isinstance(selector, StandingAttachmentSelector):
        raise ValueError("standing attachment deny requires a qualified selector")
    at = time.time() if now is None else now
    if (
        not isinstance(reason, str) or not reason.strip() or len(reason) > 1024
        or type(at) not in (int, float) or not math.isfinite(at)
    ):
        raise ValueError("standing attachment deny requires a bounded reason and finite time")
    record = {
        "version": 1, "selector": asdict(selector), "disabled": True, "reason": reason, "at": at,
    }

    def mutate(overrides: dict[str, dict]) -> dict:
        overrides[selector.override_id] = record
        return record

    return mutate_overrides(path, mutate, loader=_standing_policy)


def clear_standing_attachment_deny(
    path: str | os.PathLike[str] | Path,
    selector: StandingAttachmentSelector,
) -> bool:
    """Clear only the named veto, preserving inherited vetoes and malformed input."""
    if not isinstance(selector, StandingAttachmentSelector):
        raise ValueError("standing attachment clear requires a qualified selector")

    def mutate(overrides: dict[str, dict]) -> bool:
        return overrides.pop(selector.override_id, None) is not None

    return mutate_overrides(path, mutate, loader=_standing_policy)


@contextlib.contextmanager
def standing_attachment_admission(
    path: str | os.PathLike[str] | Path,
    selector: StandingAttachmentSelector,
    *,
    timeout: float = 5.0,
):
    """Hold local policy stable until the caller records the exact native admission.

    The yielded revision is policy evidence, not an attachment receipt. Only the
    admitting host's trusted owner may use it, bound to its original reservation.
    """
    if not isinstance(selector, StandingAttachmentSelector) or selector.lane_key is None:
        raise ValueError("standing attachment admission requires an exact lane")
    with locked_overrides(path, timeout=timeout, loader=_standing_policy) as overrides:
        applicable = {}
        for selected in (StandingAttachmentSelector(selector.namespace, selector.charter_key), selector):
            record = overrides.get(selected.override_id)
            if record is not None:
                if record["disabled"]:
                    raise ValueError(f"standing attachment denied locally: {record['reason']}")
                applicable[selected.override_id] = record
        encoded = json.dumps(applicable, sort_keys=True, separators=(",", ":"), allow_nan=False)
        yield {
            "version": 1, "selector": asdict(selector),
            "policy_revision": "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }


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
