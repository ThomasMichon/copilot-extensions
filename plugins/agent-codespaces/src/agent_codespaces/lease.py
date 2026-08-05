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
import shutil
import subprocess
import sys
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
    """An advisory hold on a CodeSpace by an effort.

    When ``worktree`` is set, the hold is an **exclusive claim** owned by that
    worktree (the #897 auto-claim path): a CodeSpace is fronted by a single
    agent-bridge Session Host, so only one worktree may control it at a time.
    A legacy record (``worktree == ""``) is an advisory effort lease, owned by
    ``effort``.
    """

    codespace: str
    effort: str
    pid: int
    host: str
    acquired_at: float
    heartbeat_at: float
    worktree: str = ""

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


# --------------------------------------------------------------------------
# Exclusive, worktree-keyed claims (#897)
# --------------------------------------------------------------------------
# A CodeSpace is fronted by exactly one agent-bridge Session Host, so two local
# worktrees driving the same CodeSpace clobber each other. The claim path makes
# control **exclusive** and **automatic**: ``agent-codespaces ssh`` acquires a
# claim keyed by the calling worktree, sweeps existing claims, bounces a live
# different owner, and auto-releases a claim whose owning worktree is gone.
# Unlike the advisory effort ``borrow`` above, ``claim`` is enforcing and its
# liveness is tied to the owner worktree's existence (not only the TTL).


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _agent_worktrees_bin() -> str | None:
    return shutil.which("agent-worktrees")


class ClaimConflict(RuntimeError):
    """A CodeSpace is exclusively claimed by a *different, still-live* worktree."""

    def __init__(self, codespace: str, holder: str, host: str, pid: int) -> None:
        self.codespace = codespace
        self.holder = holder
        self.host = host
        self.pid = pid
        super().__init__(
            f"CodeSpace '{codespace}' is exclusively claimed by worktree "
            f"'{holder}' (host={host}, pid={pid})."
        )


def _claim_owner(lease: Lease) -> str:
    """The owning identity of a hold: the worktree for a claim, else the effort."""
    return lease.worktree or lease.effort


def resolve_owner_worktree(
    explicit: str | None = None, session_id: str | None = None
) -> str | None:
    """Resolve the claim owner: an explicit id, else the *calling* worktree dir.

    Shells ``agent-worktrees get worktree-dir`` (loose coupling -- agent-codespaces
    runs in its own venv, so it never imports agent_worktrees). Returns ``None``
    when unresolvable (not a worktree, agent-worktrees absent, error) -- the
    caller then skips claiming, preserving today's behavior (degrade-safe). This
    is why an agent-bridge dispatch, whose ``ssh`` subprocess runs from the
    daemon's cwd, must pass the caller's worktree explicitly (``--effort``).
    """
    if explicit:
        return explicit.strip() or None
    aw = _agent_worktrees_bin()
    if not aw:
        return None
    args = [aw, "get", "worktree-dir"]
    if session_id:
        args += ["--session-id", session_id]
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=10,
            creationflags=_creation_flags(),
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def active_worktree_ids() -> set[str] | None:
    """Active worktree dirs via ``agent-worktrees list --json``.

    Returns the set of worktree ``path``s, or ``None`` when unavailable -- in
    which case the caller cannot sweep by liveness and falls back to
    path-existence + the TTL backstop. Terminal-status worktrees are excluded so
    a finalized-but-not-yet-pruned worktree does not keep a claim alive here.
    """
    aw = _agent_worktrees_bin()
    if not aw:
        return None
    try:
        r = subprocess.run(
            [aw, "list", "--json"], capture_output=True, text=True,
            timeout=15, creationflags=_creation_flags(),
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except Exception:
        return None
    items = data.get("worktrees", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    ids: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        status = str(it.get("status", "")).lower()
        if status in ("finalized", "completed", "removed", "pruned"):
            continue
        path = it.get("path")
        if path:
            ids.add(str(path))
    return ids


def _worktree_alive(owner: str, active: set[str] | None) -> bool:
    """Conservative liveness for a claim owner (bias toward *alive*).

    A claim is only treated dead (auto-released) when we can positively confirm
    the owner is gone: it is a worktree **path** that is both absent from the
    active set AND no longer present on disk. This avoids wrongly reclaiming a
    live holder when ``list`` is unavailable/partial (a different repo's
    worktree, say). A non-path/legacy owner is treated alive (the TTL governs).
    """
    if not owner:
        return True
    if active is not None and owner in active:
        return True
    is_path = os.path.isabs(owner)
    if is_path:
        try:
            if os.path.exists(owner):
                return True
        except OSError:
            return True
        # absolute path, not in active set, gone from disk -> positively dead
        return False
    # legacy/non-path owner with no positive death signal -> keep (TTL governs)
    return True


def sweep_dead(
    active: set[str] | None = None, ttl: float = DEFAULT_TTL
) -> list[str]:
    """Drop claims whose owning worktree is gone; return released CodeSpaces.

    Combines the TTL prune (``list_leases``/``_prune``) with worktree-liveness:
    a claim owned by a positively-dead worktree is released now rather than
    waiting out the TTL. Advisory (worktree-less) leases are untouched here.
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        released: list[str] = []
        for cs, lease in list(leases.items()):
            owner = _claim_owner(lease)
            if lease.worktree and not _worktree_alive(owner, active):
                log.info(
                    "Auto-releasing claim on '%s' -- owner worktree '%s' is gone",
                    cs, owner,
                )
                del leases[cs]
                released.append(cs)
        if released:
            _write_leases(leases)
        return released


def claim(
    codespace: str,
    owner: str,
    *,
    force: bool = False,
    ttl: float = DEFAULT_TTL,
    active: set[str] | None = None,
) -> Lease:
    """Acquire an **exclusive** claim on ``codespace`` for worktree ``owner``.

    Enforcing (unlike ``borrow``): if the CodeSpace is already claimed by a
    *different* worktree that is **still live**, raise :class:`ClaimConflict`
    unless ``force``. A claim held by a **gone** worktree is auto-released and
    taken over. Re-claiming for the same owner is idempotent (refreshes the
    heartbeat, preserves ``acquired_at``). Pass ``active`` (from
    :func:`active_worktree_ids`) so liveness is judged against the real
    worktree set; ``None`` falls back to path-existence + TTL.
    """
    if not codespace:
        raise RuntimeError("claim requires a CodeSpace name")
    if not owner:
        raise RuntimeError("claim requires an owner worktree")
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        held = leases.get(codespace)
        if held and _claim_owner(held) != owner:
            holder = _claim_owner(held)
            if _worktree_alive(holder, active) and not force:
                raise ClaimConflict(codespace, holder, held.host, held.pid)
            log.info(
                "Taking CodeSpace '%s' claim from '%s' for '%s' (%s)",
                codespace, holder, owner,
                "forced" if force else "prior owner gone",
            )
        now = time.time()
        keep_acquired = (
            held.acquired_at if held and _claim_owner(held) == owner else now
        )
        lease = Lease(
            codespace=codespace,
            # A worktree claim's owner is a lock holder, NOT an effort -- keep the
            # legacy ``effort`` field empty so the two concepts never conflate
            # (the owner lives in ``worktree``; ``_claim_owner`` reads it).
            effort="",
            pid=os.getpid(),
            host=_this_host(),
            acquired_at=keep_acquired,
            heartbeat_at=now,
            worktree=owner,
        )
        leases[codespace] = lease
        _write_leases(leases)
        return lease


def release_claim(codespace: str, owner: str, ttl: float = DEFAULT_TTL) -> bool:
    """Release ``codespace``'s claim iff it is owned by ``owner``. Idempotent."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        held = leases.get(codespace)
        if not held or _claim_owner(held) != owner:
            return False
        del leases[codespace]
        _write_leases(leases)
        log.info("Released claim on '%s' (owner '%s')", codespace, owner)
        return True


def release_worktree_claims(owner: str, ttl: float = DEFAULT_TTL) -> list[str]:
    """Release **all** claims owned by ``owner`` (the worktree-finalize hook).

    Returns the CodeSpaces released. This is the immediate-release path for
    ``agent-worktrees finalize``; the sweep on the next launch is the safety net.
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        released = [
            cs for cs, lease in leases.items() if _claim_owner(lease) == owner
        ]
        for cs in released:
            del leases[cs]
        if released:
            _write_leases(leases)
            log.info("Released %d claim(s) for worktree '%s'", len(released), owner)
        return released
