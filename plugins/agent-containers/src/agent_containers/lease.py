"""Lease broker -- exclusive borrowing of fleet containers.

State of record is a host-side JSON file (``~/.agent-containers/leases.json``)
guarded by an exclusive lock file for race-safety across parallel worktree
agents on the same machine. Leases are *advisory*: the ``container:`` resolver
does not hard-block dispatch, but ``borrow`` will not hand out a container that
is already leased to a live holder.

A lease is reclaimed when its holder process is gone (same-host pid check) or
its heartbeat is older than the TTL.
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

from .config import LEASE_FILE, RUNTIME_DIR, ContainersConfig, ensure_runtime_dir
from .lifecycle import list_containers

log = logging.getLogger("agent-containers")

_LOCK_FILE = RUNTIME_DIR / "leases.lock"
# Leases are held by an *effort* (a logical entity), not by the short-lived
# CLI process that created them, so reclamation is TTL-based. A long-running
# holder can refresh via ``heartbeat``; otherwise a forgotten lease expires
# after the TTL. ``release`` is the normal way to free a lease.
DEFAULT_TTL = 24 * 3600.0


@dataclass
class Lease:
    """An exclusive hold on a container by an effort."""

    container: str
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
    """Read leases.json -> {container: Lease}. Returns {} if absent/corrupt."""
    if not LEASE_FILE.exists():
        return {}
    try:
        raw = json.loads(LEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("leases.json unreadable; treating as empty")
        return {}
    leases: dict[str, Lease] = {}
    for container, rec in (raw or {}).items():
        try:
            leases[container] = Lease(**rec)
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
    held by an *effort* and persists across CLI invocations and agent
    dispatches until explicitly released or the TTL elapses.
    """
    return lease.age() > ttl


def _prune(leases: dict[str, Lease], ttl: float) -> dict[str, Lease]:
    """Drop stale leases in-place and return the cleaned dict."""
    live = {}
    for container, lease in leases.items():
        if _is_stale(lease, ttl):
            log.info(
                "Reclaiming stale lease: %s (effort=%s, host=%s, pid=%s)",
                container, lease.effort, lease.host, lease.pid,
            )
            continue
        live[container] = lease
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
    config: ContainersConfig,
    effort: str,
    container: str | None = None,
    fleet: str | None = None,
    ttl: float = DEFAULT_TTL,
) -> Lease:
    """Acquire an exclusive lease on a free fleet container for ``effort``.

    If ``container`` is given, lease that specific one (error if held by a
    different live effort). Otherwise pick the first free fleet member,
    preferring already-running containers.

    Re-borrowing the same container for the same effort is idempotent
    (refreshes the heartbeat).
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        members = list_containers(config)
        if fleet:
            members = [c for c in members if c.fleet == fleet]
        if not members:
            raise RuntimeError(
                "No fleet containers found. Run `agent-containers up` first."
            )

        by_name = {c.name: c for c in members}

        if container:
            if container not in by_name:
                raise RuntimeError(
                    f"Container '{container}' is not a known fleet member"
                )
            held = leases.get(container)
            if held and held.effort != effort:
                raise RuntimeError(
                    f"Container '{container}' is leased by effort "
                    f"'{held.effort}' (host={held.host}, pid={held.pid})"
                )
            chosen = container
        else:
            # Prefer running, then startable; skip those already leased.
            free = [c for c in members if c.name not in leases]
            if not free:
                raise RuntimeError(
                    "All fleet containers are currently leased. "
                    "Release one or grow the fleet."
                )
            free.sort(key=lambda c: (not c.is_running, c.name))
            chosen = free[0].name

        now = time.time()
        lease = Lease(
            container=chosen,
            effort=effort,
            pid=os.getpid(),
            host=_this_host(),
            acquired_at=leases[chosen].acquired_at if chosen in leases else now,
            heartbeat_at=now,
        )
        leases[chosen] = lease
        _write_leases(leases)
        log.info("Leased container '%s' to effort '%s'", chosen, effort)
        return lease


def release(target: str, ttl: float = DEFAULT_TTL) -> bool:
    """Release a lease by container name or effort name.

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


def heartbeat(container: str, ttl: float = DEFAULT_TTL) -> bool:
    """Refresh the heartbeat on a held lease. Returns True if updated."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        lease = leases.get(container)
        if not lease:
            return False
        lease.heartbeat_at = time.time()
        _write_leases(leases)
        return True


def get_lease(container: str, ttl: float = DEFAULT_TTL) -> Lease | None:
    """Return the lease for a container, or None if free."""
    for lease in list_leases(ttl=ttl):
        if lease.container == container:
            return lease
    return None
