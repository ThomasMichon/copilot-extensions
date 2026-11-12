"""Pure worktree action context and lifecycle eligibility."""

from __future__ import annotations

from worktree_identity import parse_machine_identity


def worktree_reclaimable(rec: dict) -> bool:
    """Offer Reclaim for non-muxed bound processes or stale lock residue.

    Live or unconfirmed mux hosting must not be interpreted as residue.
    Bridge-owned processes count even when their home CWD hides them from
    worktree-local lock scans. Mux-plus-bare conflicts use Stop/Repair instead.
    """
    if rec.get("mux_live") or rec.get("mux_observation_unknown"):
        return False
    return bool(
        rec.get("session_bare_orphan")
        or rec.get("session_lock_live")
        or rec.get("session_bound_live")
        or rec.get("session_bridge_live")
        or rec.get("session_lock_stale")
    )


def build_worktree_action_context(rec: dict, *, repo: str = "") -> dict:
    raw = rec.get("raw") or {}
    identity = parse_machine_identity(raw)
    context = {
        key: value for key, value in raw.items()
        if isinstance(value, (str, int, float, bool))
    }
    if identity is not None:
        context["physical_machine"] = identity.machine
    fallback_owner = identity.execution_machine if identity is not None else raw.get("machine")
    context.update({
        "worktree": raw.get("id") or rec.get("id4"),
        "id": raw.get("id") or rec.get("id4"),
        "id4": rec.get("id4"),
        "machine": str(rec.get("machine") or fallback_owner or ""),
        "env": str(rec.get("env") or ""),
        "repo": repo,
        "title": rec.get("title", ""),
        "state": rec.get("state", ""),
    })
    return context
