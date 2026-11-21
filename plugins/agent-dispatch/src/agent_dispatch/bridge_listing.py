"""Read managed Bridge sessions across the legacy and inventory CLI forms."""

from __future__ import annotations

from typing import Any


def managed_rows(value: object) -> list[Any]:
    """Retain the managed subset without treating workspaces or Hosts as sessions."""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("its output isn't a supported session listing")
    targets, sources = value.get("targets"), value.get("sources")
    if (
        not isinstance(targets, list) or not isinstance(sources, dict)
        or not isinstance(sources.get("managed"), dict)
        or sources["managed"].get("availability") != "available"
    ):
        raise ValueError("its managed-session source is unavailable")
    rows = []
    for target in targets:
        if not isinstance(target, dict) or target.get("kind") not in {
            "managed", "live", "session_host", "worktree",
        }:
            raise ValueError("its session inventory has an unsupported target")
        if target["kind"] == "managed":
            session = target.get("session")
            if not isinstance(session, dict):
                raise ValueError("its managed-session record is malformed")
            rows.append(session)
    return rows
