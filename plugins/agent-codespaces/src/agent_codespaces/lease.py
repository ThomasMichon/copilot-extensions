"""Lease broker -- advisory borrowing of CodeSpaces by an effort.

Mirrors ``agent_containers.lease`` for GitHub CodeSpaces. State of record is a
host-side JSON file (``~/.agent-codespaces/leases.json``) guarded by an
exclusive lock file for race-safety across parallel worktree agents on the same
machine. A lease records that a given local worktree/effort is "borrowing" a
CodeSpace so a second agent on the same box doesn't dispatch to it concurrently.

Leases are **advisory**: connecting (``agent-codespaces ssh``) does not hard-
block on a lease, but ``borrow`` will refuse to hand out a CodeSpace already
held by a *different* live effort unless ``--force`` is given (the escape hatch
for stale/buggy holders).

Unlike the container fleet -- a fixed local pool from which ``borrow`` *picks* a
free member -- a CodeSpace is addressed by name: the caller already knows which
CodeSpace it wants, so ``borrow`` takes an explicit name and simply guards
concurrent ownership of it. CodeSpaces are cloud resources that can be borrowed
from more than one machine; a host-local lease coordinates the common
same-machine case only (documented limitation, see the borrowing-codespaces
skill).

A lease is reclaimed when its heartbeat is older than the TTL (it is held by an
*effort*, a logical entity, not by the short-lived CLI process that created it).
``release`` is the normal way to free one.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from .config import RUNTIME_DIR, ensure_runtime_dir

log = logging.getLogger("agent-codespaces")

LEASE_FILE = RUNTIME_DIR / "leases.json"
_LOCK_FILE = RUNTIME_DIR / "leases.lock"
# Leases are held by an *effort*, not by the CLI process, so reclamation is
# TTL-based. A long-running holder can refresh via ``heartbeat``; otherwise a
# forgotten lease expires after the TTL. ``release`` is the normal way to free.
DEFAULT_TTL = 24 * 3600.0


@dataclass
class Lease:
    """An advisory hold on a CodeSpace by an effort."""

    codespace: str
    effort: str
    pid: int
    host: str
    acquired_at: float
    heartbeat_at: float

    def age(self) -> float:
        return time.time() - self.heartbeat_at


def _this_host() -> str:
    return platform.node()


@contextmanager
def _lease_lock(timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """Cross-platform exclusive lock via O_CREAT|O_EXCL lock file."""
    ensure_runtime_dir()
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                # Stale lock recovery: if older than timeout*3, steal it.
                try:
                    age = time.time() - _LOCK_FILE.stat().st_mtime
                    if age > timeout * 3:
                        _LOCK_FILE.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise RuntimeError(
                    "Could not acquire lease lock (held by another process)"
                ) from None
            time.sleep(poll)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        _LOCK_FILE.unlink(missing_ok=True)


def _read_leases() -> dict[str, Lease]:
    """Read leases.json -> {codespace: Lease}. Returns {} if absent/corrupt."""
    if not LEASE_FILE.exists():
        return {}
    try:
        raw = json.loads(LEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("leases.json unreadable; treating as empty")
        return {}
    leases: dict[str, Lease] = {}
    for codespace, rec in (raw or {}).items():
        try:
            leases[codespace] = Lease(**rec)
        except TypeError:
            continue
    return leases


def _write_leases(leases: dict[str, Lease]) -> None:
    """Atomically write leases.json."""
    ensure_runtime_dir()
    tmp = LEASE_FILE.with_suffix(".json.tmp")
    payload = {c: asdict(lease) for c, lease in leases.items()}
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, LEASE_FILE)


def _is_stale(lease: Lease, ttl: float) -> bool:
    """A lease is stale once it exceeds the TTL since its last heartbeat.

    Liveness is intentionally NOT tied to the borrowing process: a lease is
    held by an *effort* and persists across CLI invocations and dispatches
    until explicitly released or the TTL elapses.
    """
    return lease.age() > ttl


def _prune(leases: dict[str, Lease], ttl: float) -> dict[str, Lease]:
    """Drop stale leases in-place and return the cleaned dict."""
    live = {}
    for codespace, lease in leases.items():
        if _is_stale(lease, ttl):
            log.info(
                "Reclaiming stale lease: %s (effort=%s, host=%s, pid=%s)",
                codespace, lease.effort, lease.host, lease.pid,
            )
            continue
        live[codespace] = lease
    return live


def list_leases(ttl: float = DEFAULT_TTL, prune: bool = True) -> list[Lease]:
    """Return current (optionally pruned) leases."""
    with _lease_lock():
        leases = _read_leases()
        if prune:
            cleaned = _prune(leases, ttl)
            if len(cleaned) != len(leases):
                _write_leases(cleaned)
            leases = cleaned
        return list(leases.values())


def borrow(
    effort: str,
    codespace: str,
    force: bool = False,
    ttl: float = DEFAULT_TTL,
) -> Lease:
    """Acquire an advisory lease on ``codespace`` for ``effort``.

    A CodeSpace is addressed by name (unlike the container fleet, there is no
    "pick a free one" -- the caller knows which CodeSpace it wants). If the
    CodeSpace is already leased by a *different* live effort, refuse unless
    ``force`` is set (the escape hatch for a stale/buggy holder).

    Re-borrowing the same CodeSpace for the same effort is idempotent
    (refreshes the heartbeat, preserves ``acquired_at``).
    """
    if not codespace:
        raise RuntimeError("borrow requires a CodeSpace name")
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        held = leases.get(codespace)
        if held and held.effort != effort and not force:
            raise RuntimeError(
                f"CodeSpace '{codespace}' is leased by effort "
                f"'{held.effort}' (host={held.host}, pid={held.pid}). "
                f"Use --force to take it over."
            )
        now = time.time()
        # Preserve acquired_at only when the same effort re-borrows; a forced
        # takeover by a new effort starts a fresh acquisition.
        keep_acquired = (
            held.acquired_at
            if held and held.effort == effort
            else now
        )
        lease = Lease(
            codespace=codespace,
            effort=effort,
            pid=os.getpid(),
            host=_this_host(),
            acquired_at=keep_acquired,
            heartbeat_at=now,
        )
        leases[codespace] = lease
        _write_leases(leases)
        if held and held.effort != effort:
            log.info(
                "Force-took CodeSpace '%s' from effort '%s' for effort '%s'",
                codespace, held.effort, effort,
            )
        else:
            log.info("Leased CodeSpace '%s' to effort '%s'", codespace, effort)
        return lease


def release(target: str, ttl: float = DEFAULT_TTL) -> bool:
    """Release a lease by CodeSpace name or effort name.

    Returns True if a lease was removed.
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        to_remove = [
            c for c, lease in leases.items()
            if c == target or lease.effort == target
        ]
        if not to_remove:
            return False
        for c in to_remove:
            del leases[c]
            log.info("Released lease on '%s'", c)
        _write_leases(leases)
        return True


def heartbeat(codespace: str, ttl: float = DEFAULT_TTL) -> bool:
    """Refresh the heartbeat on a held lease. Returns True if updated."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        lease = leases.get(codespace)
        if not lease:
            return False
        lease.heartbeat_at = time.time()
        _write_leases(leases)
        return True


def get_lease(codespace: str, ttl: float = DEFAULT_TTL) -> Lease | None:
    """Return the lease for a CodeSpace, or None if free."""
    for lease in list_leases(ttl=ttl):
        if lease.codespace == codespace:
            return lease
    return None
