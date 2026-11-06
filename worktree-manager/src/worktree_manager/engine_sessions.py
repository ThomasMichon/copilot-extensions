"""Session-list envelopes over the selected engine boundary."""

from __future__ import annotations


def list_worktree_session_snapshot(
    project: str | None, worktree_id: str, *, runner=None, engine_command: list[str] | None = None
) -> dict:
    """Retain optional provider declarations without another discovery call."""
    from . import engine_client as ec

    return ec.run_json(
        project,
        ["list-sessions", "--worktree", worktree_id, "--json"],
        runner=runner,
        engine_command=engine_command,
    )
