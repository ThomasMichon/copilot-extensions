"""Preferred machine-agent project from the current worktree's ownership."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import TYPE_CHECKING

from .caller_session import caller_session_id_from_env, normalize_caller_session_id

if TYPE_CHECKING:
    from .worktree_head import HeadInfo

# Durable owner resolved by the worktree CLI in the caller's current directory.
_OWNER_REF = re.compile(
    r"^[^/\s#]+/(?P<project>[^/\s#]+)/(?P<worktree>[^/\s#]+)(?:#(?P<session>[^/\s#]+))?$"
)


class CallerProjectContextError(ValueError):
    """A declared managed caller cannot prove its owning project."""


def _caller_head(worktree_id: str, *, project: str) -> HeadInfo:
    """Read one exact project record, never the global head-session fallback."""
    from .agent_registry import _agent_worktrees_bin
    from .provider_process import run_captured
    from .worktree_head import HeadInfo

    unknown = HeadInfo(active=False)
    executable = _agent_worktrees_bin()
    if not executable:
        return unknown
    try:
        result = run_captured(
            [executable, "--project", project, "record",
             "--worktree-id", worktree_id, "--json"],
            timeout=8,
        )
        if result.returncode != 0:
            return unknown
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError, RuntimeError, ValueError, TypeError):
        return unknown
    record = payload.get("worktree") if isinstance(payload, dict) and payload.get("ok") is True else None
    if (
        not isinstance(record, dict) or record.get("id") != worktree_id
        or record.get("repo") != project or "last_session_id" not in record
    ):
        return unknown
    head = record["last_session_id"]
    if head is not None and (not isinstance(head, str) or normalize_caller_session_id(head) != head):
        return unknown
    return HeadInfo(
        tracked=True, active=head is not None, occupied=head is not None, head_session=head,
    )


def owning_project() -> str | None:
    """Resolve the CWD's project and validate a native caller against its head."""
    from . import __main__ as cli

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
        head = _caller_head(match.group("worktree"), project=match.group("project"))
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
