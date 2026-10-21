"""Pure action-template fields, separate from source/transport selection."""

from __future__ import annotations

from worktree_identity import parse_machine_identity


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
