"""Preferred machine-agent project from the current worktree's ownership."""

from __future__ import annotations

import os
import re

from .caller_session import caller_session_id_from_env

# Durable owner: execution-space/project/worktree; an optional session qualifier
# must agree with the current caller rather than silently rebinding it.
_OWNER_REF = re.compile(
    r"^[^/\s#]+/(?P<project>[^/\s#]+)/[^/\s#]+(?:#(?P<session>[^/\s#]+))?$"
)


class CallerProjectContextError(ValueError):
    """A declared managed caller cannot prove its owning project."""


def owning_project() -> str | None:
    """Read a managed project, validating the caller and any session qualifier."""
    raw_reference = os.environ.get("AGENT_WORKTREES_OWNER_REF")
    if raw_reference is None:
        return None
    reference = raw_reference.strip()
    session_id = caller_session_id_from_env()
    if not reference or not session_id:
        raise CallerProjectContextError(
            "Managed caller has an empty owner reference or invalid session identity; "
            "rebind the worktree before dispatch."
        )
    match = _OWNER_REF.fullmatch(reference)
    if match is None:
        raise CallerProjectContextError(
            "Managed caller owner reference is malformed; rebind the worktree before dispatch."
        )
    if match.group("session") is not None and match.group("session") != session_id:
        raise CallerProjectContextError(
            "Managed caller owner reference belongs to a different session; "
            "rebind the worktree before dispatch."
        )
    return match.group("project")
