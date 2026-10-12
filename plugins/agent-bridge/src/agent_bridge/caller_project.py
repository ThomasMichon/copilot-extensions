"""Preferred machine-agent project from the current worktree's ownership."""

from __future__ import annotations

import os
import re

from .caller_session import caller_session_id_from_env

# Canonical shape: execution-space/project/worktree#Copilot-session.
_OWNER_REF = re.compile(
    r"^[^/\s#]+/(?P<project>[^/\s#]+)/[^/\s#]+#(?P<session>[^/\s#]+)$"
)


def owning_project() -> str | None:
    """Accept only an owner reference tied to this exact Copilot session."""
    reference = os.environ.get("AGENT_WORKTREES_OWNER_REF", "").strip()
    session_id = caller_session_id_from_env()
    if not reference or not session_id:
        return None
    match = _OWNER_REF.fullmatch(reference)
    if match is None or match.group("session") != session_id:
        return None
    return match.group("project")
