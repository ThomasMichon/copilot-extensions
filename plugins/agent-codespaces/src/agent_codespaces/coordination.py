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

# L2 lease TTL (seconds). Short enough that a crashed holder's grip frees on the
# store's timer without manual cleanup; refreshed by ``heartbeat``. Independent
# of the L1 TTL (which is effort-scoped and much longer).
DEFAULT_L2_TTL = 3600


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
