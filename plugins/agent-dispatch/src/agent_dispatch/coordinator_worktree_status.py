"""Coordinator HTTP routes for worktree-status relay reads."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .worktree_status_relay import WorktreeStatusRelayStore


def register_worktree_status_routes(
    app: FastAPI,
    relay: WorktreeStatusRelayStore,
) -> None:
    @app.get("/worktree-status-relay")
    def worktree_status_relay(repo: str, worktree_id: str) -> dict:
        entry = relay.get(repo, worktree_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no such worktree relay entry")
        return entry
