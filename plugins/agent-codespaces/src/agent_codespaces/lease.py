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
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from agent_procutil import no_window_flags

from . import coordination
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
    # git-ref-resource-leases (Phase 2): the L2 cross-machine fencing token (the
    # commit OID of the CodeSpace's Git-ref lease) when this claim also holds a
    # distributed lease. Empty for a legacy record or when L2 is unavailable
    # (L1-only). Used to renew/release the L2 lease. Default keeps old
    # ``leases.json`` records loadable via ``Lease(**rec)``.
    lease_token: str = ""

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
    """Refresh the heartbeat on a held lease. Returns True if updated.

    Also renews the cross-machine L2 lease (best-effort) and rotates the stored
    fencing token when one is held -- so a live holder keeps its distributed grip
    and a crashed holder's L2 lease expires on the store's timer.
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        lease = leases.get(codespace)
        if not lease:
            return False
        token = lease.lease_token
    new_token = ""
    if token:
        res = coordination.renew(codespace, token)
        if res.ok:
            new_token = res.token
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        lease = leases.get(codespace)
        if not lease:
            return False
        lease.heartbeat_at = time.time()
        if new_token:
            lease.lease_token = new_token
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
    return no_window_flags()


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


class CoordinationRejected(RuntimeError):
    """The owning worktree cannot create durable coordination state."""


def _claim_owner(lease: Lease) -> str:
    """The owning identity of a hold: the worktree for a claim, else the effort."""
    return lease.worktree or lease.effort


def _same_holder_ref(a: str | None, b: str | None) -> bool:
    """True when two qualified ClaimRefs name the **same worktree**.

    A ClaimRef is ``machine/project/worktree_id[#session]``; the worktree id (the
    last path segment, minus any ``#session`` suffix) is the stable identity.
    Two refs match iff their worktree ids match -- so a re-entry from a *new
    session* of the same worktree still recognizes its own lease. Empty/None →
    no match. Used to spot a self-conflict on the L2 acquire path (#1362).
    """
    if not a or not b:
        return False
    aw = a.split("/")[-1].split("#")[0].strip()
    bw = b.split("/")[-1].split("#")[0].strip()
    return bool(aw) and aw == bw


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
    holder_ref: str | None = None,
    coordinate: bool = True,
    preflight_result=None,
) -> Lease:
    """Acquire an **exclusive** claim on ``codespace`` for worktree ``owner``.

    Enforcing (unlike ``borrow``): if the CodeSpace is already claimed by a
    *different* worktree that is **still live**, raise :class:`ClaimConflict`
    unless ``force``. A claim held by a **gone** worktree is auto-released and
    taken over. Re-claiming for the same owner is idempotent (refreshes the
    heartbeat, preserves ``acquired_at``). Pass ``active`` (from
    :func:`active_worktree_ids`) so liveness is judged against the real
    worktree set; ``None`` falls back to path-existence + TTL.

    **Two-tier (git-ref-resource-leases Phase 2).** The host-local store above is
    the same-machine L1 fast path. When ``coordinate`` and a qualified
    ``holder_ref`` (a ``machine/project/worktree_id[#session]`` ClaimRef) are
    given, an atomic **cross-machine L2** Git-ref lease is taken via
    ``agent-worktrees lease`` *before* the local write, so a live claim on
    **another machine** raises :class:`ClaimConflict` (naming the remote holder)
    unless ``force``. The L2 network op runs **outside** the local lock (which
    only guards the fast local file R/W). Degrade-safe: if L2 is not wired /
    reachable, this falls back to L1-only -- identical to today's behavior.
    """
    if not codespace:
        raise RuntimeError("claim requires a CodeSpace name")
    if not owner:
        raise RuntimeError("claim requires an owner worktree")

    # L2 (cross-machine) acquire/renew, network, *outside* the local lock. Peek
    # the local store lock-free only to choose renew (we already hold it) vs
    # acquire (new/takeover) and to carry the prior fencing token.
    lease_token = ""
    if coordinate and holder_ref:
        peek = _read_leases().get(codespace)
        same_owner = bool(peek and _claim_owner(peek) == owner)
        prior_token = peek.lease_token if (same_owner and peek) else ""
        lease_token = prior_token
        if same_owner and prior_token:
            res = coordination.renew(codespace, prior_token)
        else:
            readiness = preflight_result or coordination.preflight(holder_ref)
            if readiness.rejected:
                raise CoordinationRejected(
                    f"{readiness.code}: {readiness.detail}"
                )
            res = coordination.acquire(codespace, holder_ref)
        if res.ok:
            lease_token = res.token
        elif res.rejected:
            raise CoordinationRejected(res.detail)
        elif res.conflict:
            # A conflict whose holder is THIS SAME worktree is re-entry, not a
            # real conflict -- the "holder" is the caller's own prior lease
            # (whose L2 token the local L1 record lost or never persisted, e.g.
            # an L1-only first claim). Adopt it instead of bouncing the caller
            # from a CodeSpace it already holds (#1362): proceed L1-only (the
            # same-owner L1 lock block below refreshes the record idempotently).
            self_conflict = same_owner or _same_holder_ref(res.holder, holder_ref)
            if self_conflict:
                log.info(
                    "Re-entrant claim on '%s' by its own owner '%s'; adopting "
                    "the existing lease (L1-only; token heals on next renew).",
                    codespace, owner,
                )
                lease_token = prior_token
            elif not force:
                # A genuine conflict. Resolve the holder against L1 first so the
                # message carries the real worktree/host/pid when a concrete
                # local record exists; only fall back to the cross-machine
                # ClaimRef (no pid) when the holder truly isn't on this box.
                held_local = _read_leases().get(codespace)
                if held_local is not None:
                    raise ClaimConflict(
                        codespace, _claim_owner(held_local),
                        held_local.host, held_local.pid,
                    )
                raise ClaimConflict(
                    codespace, res.holder or "(cross-machine)",
                    "(cross-machine)", 0,
                )
            else:
                log.warning(
                    "Forced claim on '%s' over a live cross-machine lease held "
                    "by '%s'; proceeding without the L2 lease.",
                    codespace, res.holder or "?",
                )
                lease_token = ""
        # unavailable -> keep prior_token (renew) or "" (acquire): L1-only.

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
            lease_token=lease_token,
        )
        leases[codespace] = lease
        _write_leases(leases)
        return lease


def lease_token_for(codespace: str, ttl: float = DEFAULT_TTL) -> str | None:
    """Return the cross-machine (L2) fencing token this box holds for ``codespace``.

    Reads the local L1 lease store and returns the held lease's ``lease_token``
    (the git-ref fencing token), or ``None`` when no live lease is held / it
    carries no L2 token. Best-effort: any read error -> ``None``. Used to mirror
    the obligation disposition onto the shared exclusion lease
    (:func:`coordination.mirror_disposition`) at settle time, when the disconnect
    hook has the CodeSpace name + holder but not the token in scope.
    """
    try:
        with _lease_lock():
            held = _prune(_read_leases(), ttl).get(codespace)
        token = held.lease_token if held else ""
        return token or None
    except Exception:
        return None


def release_claim(codespace: str, owner: str, ttl: float = DEFAULT_TTL) -> bool:
    """Release ``codespace``'s claim iff it is owned by ``owner``. Idempotent.

    Also tombstones the cross-machine L2 lease (best-effort) when one was held.
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        held = leases.get(codespace)
        if not held or _claim_owner(held) != owner:
            return False
        token = held.lease_token
        del leases[codespace]
        _write_leases(leases)
        log.info("Released claim on '%s' (owner '%s')", codespace, owner)
    if token:
        coordination.release(codespace, token)
    return True


def release_worktree_claims(owner: str, ttl: float = DEFAULT_TTL) -> list[str]:
    """Release **all** claims owned by ``owner`` (the worktree-finalize hook).

    Returns the CodeSpaces released. This is the immediate-release path for
    ``agent-worktrees finalize``; the sweep on the next launch is the safety net.
    Also tombstones each held cross-machine L2 lease (best-effort).
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        released_tokens = {
            cs: lease.lease_token
            for cs, lease in leases.items()
            if _claim_owner(lease) == owner
        }
        for cs in released_tokens:
            del leases[cs]
        if released_tokens:
            _write_leases(leases)
            log.info(
                "Released %d claim(s) for worktree '%s'", len(released_tokens), owner
            )
    for cs, token in released_tokens.items():
        if token:
            coordination.release(cs, token)
    return list(released_tokens)
