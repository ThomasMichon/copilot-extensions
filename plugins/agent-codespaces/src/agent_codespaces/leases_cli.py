"""``agent-codespaces leases`` and the claim self-recognition helpers.

Split out of ``__main__.py`` (module-size budget). Each lease row is annotated
with the box's **live local users** (:mod:`live_users`) so a caller never has
to infer "stale" from the lease's recorded pid -- that pid is the short-lived
process that wrote the lease, not whatever is still using the box.
"""

from __future__ import annotations

import argparse
import json
import os


def _short_owner(owner: str) -> str:
    """A readable owner label: the basename of a worktree path, else as-is.

    A claim's owner is an absolute worktree path (long); an advisory borrow's
    owner is a short effort name. Show the basename for a path so the ``pool`` /
    ``leases`` tables stay legible (#904).
    """
    if owner and os.path.isabs(owner):
        return os.path.basename(owner.rstrip("/\\")) or owner
    return owner


def _self_claim_identity() -> tuple[str | None, str | None]:
    """Resolve THIS caller's own claim identity for ``(you)`` self-marking.

    Returns ``(self_owner, self_worktree_id)``: ``self_owner`` is the calling
    worktree's L1 claim-owner (from ``resolve_owner_worktree`` -- matches a
    lease's ``worktree`` field); ``self_worktree_id`` is the worktree id parsed
    from the caller's qualified ClaimRef (matches the worktree id in an L2 holder
    ref, and cross-machine-independent). Both best-effort -> ``None`` when not in
    a worktree, so the surfaces degrade to no marker (never wrong). Lets an agent
    recognize a claim it holds itself instead of steering clear of its own box
    (#1362 / agent-claim-awareness).
    """
    self_owner = None
    self_wtid = None
    try:
        from .lease import resolve_owner_worktree
        self_owner = resolve_owner_worktree() or None
    except Exception:
        self_owner = None
    try:
        from . import coordination
        ref = coordination.owner_ref()
        if ref:
            self_wtid = ref.split("/")[-1].split("#")[0].strip() or None
    except Exception:
        self_wtid = None
    if self_wtid is None and self_owner:
        self_wtid = os.path.basename(self_owner.rstrip("/\\")) or None
    return self_owner, self_wtid


def _hold_is_self(owner: str | None, l2_holder: str | None,
                  self_owner: str | None, self_wtid: str | None) -> bool:
    """Is a hold (L1 owner and/or L2 holder ref) held by THIS caller? (#1362)."""
    if self_owner and owner and owner == self_owner:
        return True
    if self_wtid:
        if owner and os.path.basename(str(owner).rstrip("/\\")) == self_wtid:
            return True
        if l2_holder and l2_holder.split("/")[-1].split("#")[0].strip() == self_wtid:
            return True
    return False


def cmd_leases(args: argparse.Namespace | None = None) -> int:
    """Show active CodeSpace leases (advisory borrows and #897 claims).

    ``--owner`` filters to one worktree/effort's own leases/claims; ``--json``
    emits a machine-readable list instead of the human table -- the read-only
    query surface a caller (e.g. ``agent-worktrees finalize``'s claim-warning
    step) uses to discover exactly which CodeSpaces a worktree still has
    claimed, WITHOUT releasing anything itself.

    Each row also carries ``in_use`` / ``live_users``: the live local processes
    (target-lock holder, ControlMasters, forwards, ``gh codespace ssh``) still
    using the box. The lease's ``pid`` is only the process that WROTE the lease,
    so a dead ``pid`` never means the box is free -- ``in_use`` does. It is
    ``None`` (unknown) for a lease recorded on a different host.
    """
    from .lease import _this_host, list_leases

    leases = list_leases()
    owner_filter = getattr(args, "owner", None) if args is not None else None
    if owner_filter:
        leases = [
            lease for lease in leases
            if (lease.worktree or lease.effort) == owner_filter
        ]
    json_output = bool(getattr(args, "json_output", False)) if args is not None else False
    usage = _live_usage(leases, _this_host())
    if json_output:
        print(json.dumps([
            {
                "codespace": lease.codespace,
                "owner": lease.worktree or lease.effort,
                "kind": "claim" if lease.worktree else "borrow",
                "host": lease.host,
                "pid": lease.pid,
                "in_use": usage[lease.codespace][0],
                "live_users": [u.to_dict() for u in usage[lease.codespace][1]],
            }
            for lease in leases
        ]))
        return 0
    if not leases:
        print("No active leases.")
        return 0
    self_owner, self_wtid = _self_claim_identity()
    any_self = False
    print(f"{'CODESPACE':<40} {'OWNER':<28} {'KIND':<7} {'HOST':<16} {'PID':<8} {'IN-USE'}")
    for lease in leases:
        # A claim keys its owner on ``worktree`` (with ``effort`` empty); an
        # advisory borrow keys on ``effort``. Show the single owner + which
        # flavor recorded it, so a dispatched (claimed) CodeSpace is no longer a
        # blank row (#904).
        owner = lease.worktree or lease.effort
        kind = "claim" if lease.worktree else "borrow"
        is_self = _hold_is_self(owner, None, self_owner, self_wtid)
        any_self = any_self or is_self
        label = _short_owner(owner) + ("  (you)" if is_self else "")
        print(
            f"{lease.codespace:<40} {label:<28} {kind:<7} "
            f"{lease.host:<16} {lease.pid!s:<8} "
            f"{_in_use_label(*usage[lease.codespace])}"
        )
    if any_self:
        print("\n(you) = held by THIS worktree -- reuse it; you already own it "
              "(no need to create a new box or --force-claim).")
    return 0




def _live_usage(leases: list, this_host: str) -> dict[str, tuple[bool | None, list]]:
    """``{codespace: (in_use, live_users)}`` from one process-table scan."""
    from . import live_users

    table = live_users.process_table()
    usage: dict[str, tuple[bool | None, list]] = {}
    for lease in leases:
        if lease.host and lease.host != this_host:
            usage[lease.codespace] = (None, [])
            continue
        users = live_users.live_users(lease.codespace, table=table)
        usage[lease.codespace] = (bool(users), users)
    return usage


def _in_use_label(in_use: bool | None, users: list) -> str:
    if in_use is None:
        return "unknown (other host)"
    if not in_use:
        return "no"
    return "yes (" + ", ".join(sorted({u.role for u in users})) + ")"
