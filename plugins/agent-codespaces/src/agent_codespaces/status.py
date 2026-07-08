"""CodeSpace eligibility status -- the worktree-style prune lifecycle marker.

Records, per CodeSpace, a lifecycle *state* that is orthogonal to the effort
lease (``lease.py``):

- ``recovered`` -- ``finalize`` recovered its sessions and stopped it; the box is
  preserved (off the active-quota) and **reusable**. NOT a deletion candidate.
- ``prunable`` -- additionally confirmed safe to delete (e.g. PR merged + effort
  archived). The only state ``prune`` will reclaim. Promotion to ``prunable`` is
  set by the ``cleaning-codespaces`` skill (it has the ADO PR-merged context).
- *(absent)* -- ``active``: unmarked / in-use. Reusing a box (``borrow``/``ssh``/
  dispatch) clears any marker back to this.

Unlike leases -- which are TTL-reclaimed because they are held by a short-lived
*effort* -- an eligibility marker must **persist** for a finalized, holder-less
box until it is reused or pruned. So this state lives in its **own**, non-TTL
store (``~/.agent-codespaces/codespace-status.json``), guarded by the same
O_CREAT|O_EXCL lock pattern as ``lease.py`` for race-safety across parallel
worktree agents on one machine.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from .config import RUNTIME_DIR, ensure_runtime_dir

log = logging.getLogger("agent-codespaces")

STATUS_FILE = RUNTIME_DIR / "codespace-status.json"
_LOCK_FILE = RUNTIME_DIR / "codespace-status.lock"

# Lifecycle states. Absence of a record == ACTIVE (in-use / unmarked).
STATE_ACTIVE = "active"
STATE_RECOVERED = "recovered"
STATE_PRUNABLE = "prunable"
_ELIGIBLE_STATES = frozenset({STATE_RECOVERED, STATE_PRUNABLE})


@dataclass
class CodespaceStatus:
    """The lifecycle marker for one CodeSpace."""

    codespace: str
    state: str
    state_at: float
    reason: str = ""

    def age(self) -> float:
        return time.time() - self.state_at


@contextmanager
def _status_lock(timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """Cross-platform exclusive lock via O_CREAT|O_EXCL lock file.

    Mirrors ``lease._lease_lock`` (including stale-lock recovery) so the two
    stores behave identically under contention.
    """
    ensure_runtime_dir()
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:
                    age = time.time() - _LOCK_FILE.stat().st_mtime
                    if age > timeout * 3:
                        _LOCK_FILE.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise RuntimeError(
                    "Could not acquire status lock (held by another process)"
                ) from None
            time.sleep(poll)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        _LOCK_FILE.unlink(missing_ok=True)


def _read() -> dict[str, CodespaceStatus]:
    """Read the status file -> {codespace: CodespaceStatus}. {} if absent/corrupt.

    Tolerant of unknown keys (forward-compat) by filtering each record to the
    dataclass fields, so a newer writer's extra fields never drop a record.
    """
    if not STATUS_FILE.exists():
        return {}
    try:
        raw = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("codespace-status.json unreadable; treating as empty")
        return {}
    known = {"codespace", "state", "state_at", "reason"}
    out: dict[str, CodespaceStatus] = {}
    for name, rec in (raw or {}).items():
        if not isinstance(rec, dict):
            continue
        fields = {k: v for k, v in rec.items() if k in known}
        fields.setdefault("codespace", name)
        try:
            out[name] = CodespaceStatus(**fields)
        except TypeError:
            continue
    return out


def _write(records: dict[str, CodespaceStatus]) -> None:
    """Atomically write the status file."""
    ensure_runtime_dir()
    tmp = STATUS_FILE.with_suffix(".json.tmp")
    payload = {name: asdict(rec) for name, rec in records.items()}
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, STATUS_FILE)


def set_status(codespace: str, state: str, reason: str = "") -> CodespaceStatus:
    """Mark ``codespace`` with a lifecycle ``state`` (e.g. recovered/prunable)."""
    if not codespace:
        raise RuntimeError("set_status requires a CodeSpace name")
    if state not in (STATE_ACTIVE, STATE_RECOVERED, STATE_PRUNABLE):
        raise RuntimeError(f"unknown codespace state: {state!r}")
    with _status_lock():
        records = _read()
        if state == STATE_ACTIVE:
            # ACTIVE is the absence of a marker -- drop any record.
            records.pop(codespace, None)
            _write(records)
            rec = CodespaceStatus(codespace, STATE_ACTIVE, time.time(), reason)
        else:
            rec = CodespaceStatus(codespace, state, time.time(), reason)
            records[codespace] = rec
            _write(records)
        log.info("CodeSpace %s marked %s%s", codespace, state,
                 f" ({reason})" if reason else "")
        return rec


def clear_status(codespace: str) -> bool:
    """Un-mark a CodeSpace (back to ACTIVE). Returns True if a marker was removed.

    Called when a box is reused (borrow/ssh/dispatch) so a previously-finalized
    box is no longer a prune candidate.
    """
    with _status_lock():
        records = _read()
        if codespace not in records:
            return False
        del records[codespace]
        _write(records)
        log.info("CodeSpace %s marker cleared (back to active)", codespace)
        return True


def get_status(codespace: str) -> CodespaceStatus | None:
    """Return the marker for a CodeSpace, or None if unmarked (active)."""
    with _status_lock():
        return _read().get(codespace)


def list_status() -> list[CodespaceStatus]:
    """Return all lifecycle markers."""
    with _status_lock():
        return list(_read().values())


def list_by_state(state: str) -> list[CodespaceStatus]:
    """Return markers in a given state (e.g. all ``prunable`` boxes)."""
    return [s for s in list_status() if s.state == state]


def is_eligible(codespace: str) -> bool:
    """True if the box carries any eligibility marker (recovered/prunable)."""
    rec = get_status(codespace)
    return bool(rec and rec.state in _ELIGIBLE_STATES)
