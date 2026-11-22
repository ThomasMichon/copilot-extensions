"""Reconcile compatible host observations before applying consumer filters."""

from __future__ import annotations

from typing import Any


def merge_rows(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    """Prefer positive current evidence without turning history into ownership."""
    ordered = sorted(rows, key=lambda row: str((row.get("route") or {}).get("host") or ""))
    active = [
        row
        for row in ordered
        if row.get("live") is True
        or row.get("control") == "registered"
        or row.get("control_reason") == "identity_ambiguous"
    ]
    current_ids = {
        value
        for row in active
        for value in [
            row.get("session_id"),
            *(
                row.get("observed_session_ids", [])
                if row.get("control_reason") == "identity_ambiguous"
                else []
            ),
        ]
        if isinstance(value, str) and value
    }
    conflict = len(current_ids) > 1 or any(
        row.get("control_reason") == "identity_ambiguous" for row in active
    )
    retained_ids = {
        row["session_id"]
        for row in ordered
        if isinstance(row.get("session_id"), str) and row["session_id"]
    }
    native = (
        next(iter(current_ids))
        if len(current_ids) == 1
        else (next(iter(retained_ids)) if not active and len(retained_ids) == 1 else None)
    )
    candidates = active or ordered
    if native:
        candidates = [row for row in candidates if row.get("session_id") == native] or candidates
    primary = max(
        candidates,
        key=lambda row: (
            row.get("control") == "registered",
            row.get("live") is True,
            (row.get("route") or {}).get("kind") == "live",
        ),
    )
    merged = dict(primary)
    live = (
        True
        if any(row.get("live") is True for row in ordered)
        else (None if any(row.get("live") is None for row in ordered) else False)
    )
    merged.update(
        session_id=None if conflict else native,
        live=live,
        status="running" if live else "dormant" if live is False else "retained",
        session_count=max(
            (
                row.get("session_count", 0)
                for row in ordered
                if type(row.get("session_count", 0)) is int
            ),
            default=0,
        ),
        sources=list(
            dict.fromkeys(source for row in ordered for source in row.get("sources", []))
        ),
        observed_session_ids=sorted(current_ids if active else retained_ids),
    )
    routes = []
    for row in ordered:
        route = row.get("route")
        if isinstance(route, dict) and route not in routes:
            routes.append(dict(route))
    if routes:
        merged["routes"] = routes
    if conflict:
        merged.update(
            control="unavailable",
            control_reason="identity_ambiguous",
            managed_session_id=None,
            registered_session_id=None,
        )
        merged["route"] = {
            **(primary.get("route") or {}),
            "handle": merged["handle"],
            "kind": merged.get("location_kind", "worktree"),
        }
    elif not active and len(retained_ids) > 1:
        merged.update(
            control="unavailable",
            control_reason="retained_head_unknown",
            managed_session_id=None,
            registered_session_id=None,
            retained_managed_session_id=None,
        )
    return merged, conflict
