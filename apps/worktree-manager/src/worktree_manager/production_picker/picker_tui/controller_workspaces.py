"""Pure eligibility and navigation for controller-owned workspaces."""

from __future__ import annotations


def sessionless(raw):
    """Positive evidence of no own conversation and no live execution."""
    count = raw.get("session_count")
    if count is None or count > 0 or raw.get("last_session_id"):
        return False
    if (raw.get("kind") or "session") in ("system", "bridge"):
        return False
    if (
        raw.get("turn_count", 0) > 0 or raw.get("mux_session")
        or raw.get("mux_attached") or raw.get("session_ahp_live")
        or raw.get("execution_leg_live")
    ):
        return False
    return True


def managed_child(raw, relation):
    """Control is not a local conversation, even when a child has live residue."""
    if raw.get("session_count") != 0 or raw.get("last_session_id"):
        return False
    control = relation.get("control") or {}
    if control.get("state") == "released":
        return False
    if control.get("state") in ("released", "none") and not raw.get("owner_ref"):
        return False
    return bool(
        control.get("state") in ("controlled-local", "controlled-remote")
        or raw.get("owner_ref")
        or any(
            isinstance(controller, dict) and controller.get("state") == "active"
            for controller in raw.get("controllers") or []
        )
    )


def target_row(rec, rows):
    """Return the one exact loaded controller target, if navigable."""
    relation = rec.get("reciprocal_relation") or {}
    candidates = [
        action
        for action in relation.get("actions") or []
        if isinstance(action, dict)
        and action.get("kind") == "navigate-worktree"
        and isinstance(action.get("target"), dict)
    ]
    if len(candidates) != 1:
        return None
    target = candidates[0]["target"]
    matches = [
        row
        for row in rows
        if (row.get("raw") or {}).get("id") == target.get("worktree_id")
        and (row.get("raw") or {}).get("repo") == target.get("project")
        and (
            not target.get("machine")
            or row.get("machine") == target.get("machine")
        )
    ]
    return matches[0] if len(matches) == 1 else None
