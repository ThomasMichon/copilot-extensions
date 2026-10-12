"""Preferred machine-agent project from the current worktree's ownership."""

from __future__ import annotations

import os
import re

_OWNER_REF = re.compile(r"^[^/\s#]+/([^/\s#]+)/[^/\s#]+#([^/\s#]+)$")


def owning_project() -> str | None:
    """Accept only an owner reference tied to this exact Copilot session."""
    reference = os.environ.get("AGENT_WORKTREES_OWNER_REF", "").strip()
    session_id = os.environ.get("COPILOT_AGENT_SESSION_ID", "").strip()
    if not reference or not session_id:
        return None
    match = _OWNER_REF.fullmatch(reference)
    if match is None or match.group(2) != session_id:
        return None
    return match.group(1)
