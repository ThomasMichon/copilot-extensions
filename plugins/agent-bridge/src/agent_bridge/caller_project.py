"""Preferred machine-agent project from the current worktree's ownership."""

from __future__ import annotations

import os
import re

from .caller_session import caller_session_id_from_env

# Durable owner resolved by the worktree CLI in the caller's current directory.
_OWNER_REF = re.compile(
    r"^[^/\s#]+/(?P<project>[^/\s#]+)/(?P<worktree>[^/\s#]+)(?:#(?P<session>[^/\s#]+))?$"
)


class CallerProjectContextError(ValueError):
    """A declared managed caller cannot prove its owning project."""


def owning_project() -> str | None:
    """Resolve the CWD's project and validate a native caller against its head."""
    from . import __main__ as cli
    from .worktree_head import resolve_head

    raw_reference = cli._worktrees_get("owner-ref")
    if not raw_reference:
        if cli._worktrees_get("worktree-dir"):
            raise CallerProjectContextError(
                "Managed caller has no resolved owner; rebind the worktree before dispatch."
            )
        return None
    reference = raw_reference.strip()
    match = _OWNER_REF.fullmatch(reference)
    if match is None:
        raise CallerProjectContextError(
            "Managed caller owner reference is malformed; rebind the worktree before dispatch."
        )
    session_id = caller_session_id_from_env()
    if not session_id and os.environ.get("COPILOT_AGENT_SESSION_ID") is not None:
        raise CallerProjectContextError(
            "Managed caller has an invalid native session identity; "
            "rebind the worktree before dispatch."
        )
    if session_id and match.group("worktree") != "@anchor":
        head = resolve_head(match.group("worktree"), project=match.group("project"))
        if not head.tracked or not head.active or head.head_session != session_id:
            raise CallerProjectContextError(
                "Managed caller is not the current worktree head; "
                "rebind the worktree before dispatch."
            )
    elif not session_id and match.group("session"):
        raise CallerProjectContextError(
            "Managed caller has an invalid native session identity; "
            "rebind the worktree before dispatch."
        )
    return match.group("project")
