"""Cross-machine L2 coordination for CodeSpace claims via agent-worktrees leases.

The host-local lease (``lease.py``) gives **same-machine** mutual exclusion
(L1: a JSON file + an exclusive lock). This module adds the **cross-machine L2
authority**: an atomic Git-ref compare-and-swap lease on the CodeSpace, brokered
by ``agent-worktrees lease`` -- agent-codespaces runs in its own venv and never
imports ``agent_worktrees``, so it *shells* the binstub (the same loose-coupling
seam as ``resolve_owner_worktree``).

**Degrade-safe by construction.** When the ``agent-worktrees`` binstub is absent,
the ``lease`` verb is unavailable, or no store origin is configured, L2 is
treated as **UNAVAILABLE** and the caller falls back to L1-only (today's
behavior). Only a *definitive* lease conflict (exit 3) blocks a claim. This keeps
same-box behavior identical when the cross-machine store is not wired.

Holder identity is the qualified ClaimRef (``machine/project/worktree_id
[#session]``) obtained from ``agent-worktrees get owner-ref`` -- the same
identity ``claimant`` liveness resolves, so the fencing token (atomic acquire)
and the holder ref (cross-machine liveness for stale takeover) compose.

The store origin follows ``agent-worktrees lease``'s own resolution (the project
remote, overridable via ``AGENT_WORKTREES_LEASE_ORIGIN`` / ``--origin``). Pin it
to the harness control-plane repo via that env to coordinate *all* harness agents
regardless of project; unset, agents coordinate per-project (the common
same-project, cross-machine CodeSpace case).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass

log = logging.getLogger("agent-codespaces")

#: Exit codes from ``agent-worktrees lease`` (see lease_cli.run_lease).
_EXIT_OK = 0
_EXIT_CONFLICT = 3  # LeaseConflict / LeaseLost

#: The resource kind used for CodeSpace leases in the shared namespace.
KIND = "codespace"

#: A SEPARATE git-ref record kind carrying a per-CodeSpace **cleanliness
#: verdict** (venue-pool Phase 3 / codespace-clean-beacon). Distinct from the
#: ``codespace`` exclusion lease so it can describe an **unheld** box (the
#: recycle case): "is all work off-box (nothing uncommitted/unpushed) as of the
#: last agent that left this box?". Published proactively at ssh-disconnect /
#: ``verify`` so the pool can read it **without SSH** and the picker can gate a
#: destructive Recycle on a fresh, known-clean verdict.
KIND_CLEAN = "codespace-clean"

# L2 lease TTL (seconds). Short enough that a crashed holder's grip frees on the
# store's timer without manual cleanup; refreshed by ``heartbeat``. Independent
# of the L1 TTL (which is effort-scoped and much longer).
DEFAULT_L2_TTL = 3600

# Cleanliness-beacon TTL (seconds). Deliberately modest: a stale beacon must NOT
# outlive its trust window and falsely enable a Recycle. A box only becomes
# Recycle-eligible once ``stale`` (unheld + idle past the ~24h threshold), by
# which point a beacon this short has expired -> the pool reads it as ``unknown``
# -> the picker hides Recycle and offers ``Verify`` (a fresh on-demand probe).
# So the passive beacon only auto-enables Recycle for a box retired within this
# window of a clean disconnect; everything else is Verify-gated (safe by
# default). Must stay <= the store's 604800s ceiling.
DEFAULT_CLEAN_TTL = 6 * 3600


@dataclass
class L2Result:
    """Outcome of an L2 (cross-machine) lease operation.

    ``status`` is one of ``"ok"`` (the op succeeded; ``token`` is the current
    fencing token), ``"conflict"`` (a live lease is held by another holder;
    ``holder`` names it), or ``"unavailable"`` (L2 is not wired / not reachable
    -- the caller degrades to L1-only).
    """

    status: str
    token: str = ""
    holder: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def conflict(self) -> bool:
        return self.status == "conflict"

    @property
    def unavailable(self) -> bool:
        return self.status == "unavailable"


@dataclass
class L2Lease:
    """A cross-machine L2 lease as seen by a *read-only* pool overlay.

    A projection of one ``agent-worktrees lease`` record onto the fields the
    pool view needs: the CodeSpace ``key``, its cross-machine ``holder``
    (a qualified ClaimRef), whether it is still ``live`` (unexpired, not
    tombstoned), and its ``expires_at`` deadline. Derived, never authoritative
    -- a missing/failed read simply omits the overlay.
    """

    key: str
    holder: str
    live: bool
    expires_at: str = ""
    token: str = ""


def _aw() -> str | None:
    return shutil.which("agent-worktrees")


def _creationflags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _run(args: list[str], *, timeout: float = 45.0) -> subprocess.CompletedProcess[str] | None:
    """Run ``agent-worktrees <args>``; return the process, or None if unrunnable."""
    aw = _aw()
    if not aw:
        return None
    try:
        return subprocess.run(
            [aw, *args],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_creationflags(),
        )
    except Exception as exc:  # binstub vanished / exec error -> unavailable
        log.debug("agent-worktrees %s failed to run: %s", args[:2], exc)
        return None


def owner_ref(explicit: str | None = None, session_id: str | None = None) -> str | None:
    """Resolve the qualified ClaimRef holder for the calling worktree.

    An ``explicit`` value wins (e.g. an agent-bridge dispatch passing the caller's
    ref). Otherwise shell ``agent-worktrees get owner-ref`` (honoring a
    ``--session-id`` binding). Returns None when unresolvable -- the caller then
    skips L2 (degrade-safe), preserving L1-only behavior.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    args = ["get", "owner-ref"]
    if session_id:
        args += ["--session-id", session_id]
    proc = _run(args, timeout=10)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def journal_obligation(name: str, holder_ref: str | None) -> bool:
    """Journal a CodeSpace obligation onto the BORROWING worktree's ledger.

    On borrow, shells ``agent-worktrees claims add codespace <name> --owner-ref
    <holder_ref>`` so the finalize obligation gate can hold the borrowing
    worktree accountable for the CodeSpace (resource-obligation-settlement
    Ph3b-wiring/2). The ``--owner-ref`` (the qualified holder ClaimRef this
    connect already resolved) makes it land on the RIGHT worktree even when the
    caller's cwd is the daemon's, not the borrowing worktree.

    Best-effort + degrade-safe: no holder_ref, no binstub, a cross-machine owner
    (deferred to the lease mirror), or any error -> ``False`` (never raises,
    never blocks the connect). Idempotent (``claims add`` dedups by ref).
    """
    if not holder_ref or not holder_ref.strip():
        return False
    proc = _run(
        ["claims", "add", KIND, name, "--owner-ref", holder_ref.strip(), "--json"],
    )
    if proc is None:
        return False
    if proc.returncode != 0:
        log.debug("claims add for %s degraded (exit %s): %s",
                  name, proc.returncode, (proc.stderr or "").strip())
        return False
    return True


def settle_obligation(
    name: str, holder_ref: str | None, *, released: bool = False,
) -> bool:
    """Settle the borrowing worktree's CodeSpace obligation to ``at-rest``.

    On a definitive at-rest verdict (see ``cleanliness.at_rest``) at ssh
    disconnect / heartbeat, shells ``agent-worktrees claims settle <name>
    --owner-ref <holder_ref>`` (``--released`` to hand the claim back entirely)
    so the borrowing worktree's finalize gate stops treating the CodeSpace as
    unsettled. Same ``--owner-ref`` resolution + degrade-safety as
    :func:`journal_obligation`. Returns ``True`` only on a confirmed settle.
    """
    if not holder_ref or not holder_ref.strip():
        return False
    args = ["claims", "settle", name, "--owner-ref", holder_ref.strip(), "--json"]
    if released:
        args.append("--released")
    proc = _run(args)
    if proc is None:
        return False
    if proc.returncode != 0:
        log.debug("claims settle for %s degraded (exit %s): %s",
                  name, proc.returncode, (proc.stderr or "").strip())
        return False
    return True


def harness_identity() -> str | None:
    """Resolve THIS harness's identity -- the Git-ref lease store origin URL.

    Shells ``agent-worktrees get lease-origin``: the pushable store repo URL that
    ``lease_config`` derives (the ``AGENT_WORKTREES_LEASE_ORIGIN`` override, else
    the bound control-plane repo's origin, else the project's default remote).
    Because every agent of one harness resolves the **same** origin, it is the
    cross-harness identity for the in-CodeSpace lockfile fence (see ``fence.py``)
    -- a marker written by a *different* harness carries a different origin.

    Returns None when unresolvable / the binstub is absent -- the degrade-safe
    signal the fence uses to switch itself off (no identity -> proceed, never a
    blind block).
    """
    proc = _run(["get", "lease-origin"], timeout=10)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _parse_token(proc: subprocess.CompletedProcess[str]) -> str:
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return ""
    return str(data.get("token", "")) if isinstance(data, dict) else ""


def inspect(key: str, *, origin: str | None = None) -> dict | None:
    """Return the current lease record for a CodeSpace, or None (absent/unavailable)."""
    args = ["lease", "inspect", KIND, key]
    if origin:
        args += ["--origin", origin]
    proc = _run(args)
    if proc is None or proc.returncode != _EXIT_OK:
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("state") == "absent":
        return None
    return data


def list_leases(
    *, kind: str = KIND, origin: str | None = None
) -> dict[str, L2Lease] | None:
    """Read every cross-machine L2 lease of a kind, keyed by resource key.

    Shells ``agent-worktrees lease list --kind <kind>`` and projects each record
    onto an :class:`L2Lease`. Returns a ``{key: L2Lease}`` map (possibly empty
    when no leases exist), or **None** when L2 is unavailable / unreadable -- the
    degrade-safe signal a caller uses to simply omit the overlay. Never raises.
    """
    args = ["lease", "list", "--kind", kind]
    if origin:
        args += ["--origin", origin]
    proc = _run(args)
    if proc is None or proc.returncode != _EXIT_OK:
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    result: dict[str, L2Lease] = {}
    for rec in data:
        if not isinstance(rec, dict):
            continue
        res = rec.get("resource")
        key = str(res.get("key", "")) if isinstance(res, dict) else ""
        if not key:
            continue
        result[key] = L2Lease(
            key=key,
            holder=str(rec.get("holder", "")),
            live=bool(rec.get("live", False)),
            expires_at=str(rec.get("expires_at", "")),
            token=str(rec.get("token", "")),
        )
    return result


def acquire(
    key: str,
    holder: str,
    *,
    ttl: int | None = DEFAULT_L2_TTL,
    origin: str | None = None,
) -> L2Result:
    """Attempt an atomic cross-machine acquire of the CodeSpace lease.

    Returns ``ok`` (with the fencing ``token``), ``conflict`` (with the current
    ``holder``), or ``unavailable`` (L2 not wired/reachable). A ``conflict`` is
    the only blocking outcome; every degradation is ``unavailable``.
    """
    args = ["lease", "acquire", KIND, key, "--holder", holder]
    if ttl is not None:
        args += ["--ttl", str(ttl)]
    if origin:
        args += ["--origin", origin]
    proc = _run(args)
    if proc is None:
        return L2Result("unavailable", detail="agent-worktrees lease not available")
    if proc.returncode == _EXIT_OK:
        return L2Result("ok", token=_parse_token(proc))
    if proc.returncode == _EXIT_CONFLICT:
        rec = inspect(key, origin=origin)
        holder_of = str(rec.get("holder", "")) if rec else ""
        return L2Result("conflict", holder=holder_of, detail=(proc.stderr or "").strip())
    # config/protocol (2) or git (4) error -> degrade to L1-only.
    log.debug("L2 acquire degraded (exit %s): %s", proc.returncode, (proc.stderr or "").strip())
    return L2Result("unavailable", detail=(proc.stderr or "").strip())


def renew(
    key: str, token: str, *, ttl: int | None = DEFAULT_L2_TTL, origin: str | None = None
) -> L2Result:
    """Renew the L2 lease with its current fencing token; best-effort."""
    if not token:
        return L2Result("unavailable", detail="no L2 token")
    args = ["lease", "renew", KIND, key, "--token", token]
    if ttl is not None:
        args += ["--ttl", str(ttl)]
    if origin:
        args += ["--origin", origin]
    proc = _run(args)
    if proc is None:
        return L2Result("unavailable")
    if proc.returncode == _EXIT_OK:
        return L2Result("ok", token=_parse_token(proc))
    if proc.returncode == _EXIT_CONFLICT:
        return L2Result("conflict", detail=(proc.stderr or "").strip())
    return L2Result("unavailable", detail=(proc.stderr or "").strip())


def release(key: str, token: str, *, origin: str | None = None) -> L2Result:
    """Release (tombstone) the L2 lease with its current token; best-effort."""
    if not token:
        return L2Result("unavailable", detail="no L2 token")
    args = ["lease", "release", KIND, key, "--token", token]
    if origin:
        args += ["--origin", origin]
    proc = _run(args)
    if proc is None:
        return L2Result("unavailable")
    if proc.returncode == _EXIT_OK:
        return L2Result("ok")
    if proc.returncode == _EXIT_CONFLICT:
        # Someone else already moved the ref (took over / released); nothing owed.
        return L2Result("conflict", detail=(proc.stderr or "").strip())
    return L2Result("unavailable", detail=(proc.stderr or "").strip())


# --- Cleanliness beacon (per-box git-safety verdict, read without SSH) --------


@dataclass
class CleanRecord:
    """A CodeSpace's last-published git-cleanliness verdict, read from the store.

    A read-only projection of one ``codespace-clean`` git-ref record's free-form
    ``context`` onto the safety fields the pool needs. ``live`` is whether the
    record is still within its TTL (an expired verdict is not trusted). ``known``
    mirrors the probe's own ``known`` (False when the last probe could not
    evaluate the box). ``clean`` is the definitive "all work off-box" verdict
    (nothing uncommitted, nothing unpushed). ``at`` is when it was published;
    ``by`` who published it.
    """

    key: str
    known: bool
    clean: bool
    dirty: bool
    ahead: int
    unpushed_branches: int
    at: str
    by: str
    live: bool

    @property
    def off_box_safe(self) -> bool | None:
        """The pool's tri-state safety verdict: True (safe to delete), False
        (work still on-box), or None (unknown -- no fresh/known verdict).

        Conservative: only a **live** and **known** record yields a definite
        True/False; anything else (expired, unknown, absent) is None so the
        picker hides the destructive Recycle and offers Verify instead."""
        if not (self.live and self.known):
            return None
        return self.clean


def _clean_holder(explicit: str | None = None) -> str:
    """A valid, informative ``--holder`` for a cleanliness publish.

    Prefers the caller's qualified ClaimRef (who is leaving the box), else the
    harness lease-origin identity, else a stable sentinel -- so a publish never
    fails ``validate_holder`` for lack of an identity."""
    if explicit and explicit.strip():
        return explicit.strip()[:256]
    ref = owner_ref()
    if ref:
        return ref[:256]
    return "cleanliness-probe"


def publish_cleanliness(
    name: str,
    *,
    known: bool,
    clean: bool,
    dirty: bool,
    ahead: int,
    unpushed_branches: int,
    holder: str | None = None,
    at: str | None = None,
    ttl: int | None = DEFAULT_CLEAN_TTL,
    origin: str | None = None,
) -> bool:
    """Publish a CodeSpace's git-cleanliness verdict to the ``codespace-clean``
    git-ref store so the pool can read it **without SSH**.

    Shells ``agent-worktrees lease acquire codespace-clean <name> --holder <ref>
    --ttl N --context ...``. Fully **best-effort + degrade-safe**: no binstub,
    L2 not wired, or a still-live prior record (``conflict``) -> ``False`` (the
    existing recent verdict simply stands). Never raises, never blocks a
    disconnect/finalize. Returns ``True`` only on a confirmed write.

    A ``conflict`` is safe to drop: a box is only Recycle-eligible once it is
    ``stale`` (unheld, idle past the ~24h threshold), by which point any prior
    record (TTL << 24h) has expired, so the last writer before staleness wins
    and no live foreign record can pin a falsely-clean verdict onto a
    recycle-eligible box.
    """
    import datetime as _dt

    stamp = at or _dt.datetime.now(_dt.timezone.utc).isoformat()
    context = [
        f"known={'1' if known else '0'}",
        f"clean={'1' if clean else '0'}",
        f"dirty={'1' if dirty else '0'}",
        f"ahead={int(ahead)}",
        f"unpushed_branches={int(unpushed_branches)}",
        f"at={stamp}",
    ]
    args = ["lease", "acquire", KIND_CLEAN, name, "--holder", _clean_holder(holder)]
    if ttl is not None:
        args += ["--ttl", str(ttl)]
    for kv in context:
        args += ["--context", kv]
    if origin:
        args += ["--origin", origin]
    proc = _run(args)
    if proc is None:
        return False
    if proc.returncode == _EXIT_OK:
        return True
    log.debug("cleanliness publish for %s degraded (exit %s): %s",
              name, proc.returncode, (proc.stderr or "").strip())
    return False


def _clean_bool(value: object) -> bool:
    """Coerce a context value (``"1"``/``"0"``/``True``/...) to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _clean_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return 0


def list_cleanliness(*, origin: str | None = None) -> dict[str, CleanRecord] | None:
    """Read every ``codespace-clean`` verdict, keyed by CodeSpace name.

    Shells ``agent-worktrees lease list --kind codespace-clean`` and projects
    each record's free-form ``context`` (+ ``live``) onto a :class:`CleanRecord`.
    Returns a ``{name: CleanRecord}`` map (possibly empty), or **None** when the
    store is unavailable/unreadable -- the degrade-safe signal the pool uses to
    simply omit the overlay (Recycle then stays Verify-gated). Never raises.
    """
    args = ["lease", "list", "--kind", KIND_CLEAN]
    if origin:
        args += ["--origin", origin]
    proc = _run(args)
    if proc is None or proc.returncode != _EXIT_OK:
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    result: dict[str, CleanRecord] = {}
    for rec in data:
        if not isinstance(rec, dict):
            continue
        res = rec.get("resource")
        key = str(res.get("key", "")) if isinstance(res, dict) else ""
        if not key:
            continue
        ctx = rec.get("context") if isinstance(rec.get("context"), dict) else {}
        result[key] = CleanRecord(
            key=key,
            known=_clean_bool(ctx.get("known")),
            clean=_clean_bool(ctx.get("clean")),
            dirty=_clean_bool(ctx.get("dirty")),
            ahead=_clean_int(ctx.get("ahead")),
            unpushed_branches=_clean_int(ctx.get("unpushed_branches")),
            at=str(ctx.get("at", "")),
            by=str(rec.get("holder", "")),
            live=bool(rec.get("live", False)),
        )
    return result
