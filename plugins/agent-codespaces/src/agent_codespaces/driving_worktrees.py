"""Resolve CodeSpace rows back to their driving worktrees."""

from __future__ import annotations

from collections.abc import Iterable


def _choose_owner(owners: list[dict]) -> str:
    if len(owners) == 1:
        return str(owners[0].get("worktree_id") or "")
    active = [o for o in owners if o.get("status") == "active"]
    if len(active) == 1:
        return str(active[0].get("worktree_id") or "")
    return ""


def codespace_claim_owner_worktrees(codespace_names: Iterable[str]) -> dict[str, str]:
    """Resolve ``codespace`` claim refs to their owning local worktree ids.

    This is a soft cross-plugin integration: agent-codespaces has no hard
    dependency on agent-worktrees, so missing imports, old versions, and bad
    records all degrade to an empty map.
    """
    names = {str(name) for name in codespace_names if str(name)}
    if not names:
        return {}
    try:
        from agent_worktrees import claims_owner
    except ImportError:
        return {}
    try:
        by_ref = claims_owner.find_claim_owners_for_refs("codespace", names)
    except Exception:
        return {}
    return {
        name: worktree_id
        for name, owners in by_ref.items()
        if (worktree_id := _choose_owner(owners))
    }
