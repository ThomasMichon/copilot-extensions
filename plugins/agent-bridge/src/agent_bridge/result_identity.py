"""Existing owned conversation and lineage identities for result projections."""

from __future__ import annotations

from typing import Any

from .models import ResultIdentity


class ResultIdentityChangedError(ValueError):
    """The native conversation changed while its result was being observed."""


def owned_identity(
    row: dict[str, Any],
    *,
    session_id: str,
    worktree_id: str | None,
    requested_ref: str,
    native_session_id: str | None,
) -> ResultIdentity:
    """Project existing lineage without treating a managed ID as a native ID."""
    successor_id = row.get("successor_id")
    return ResultIdentity(
        logical_delegate_kind="worktree" if worktree_id else "session",
        logical_delegate_id=worktree_id or session_id,
        requested_ref=requested_ref,
        snapshot_session_id=session_id,
        current_session_id=str(successor_id or session_id),
        native_session_id=native_session_id,
        predecessor_id=row.get("predecessor_id"),
        successor_id=successor_id,
    )
