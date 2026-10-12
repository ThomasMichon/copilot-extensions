"""Coordinator HTTP routes for worktree-status relay reads."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from .worktree_status_relay import WorktreeStatusRelayStore


class RelayRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    worktree_id: str


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

    @app.post("/worktree-status-relays")
    def worktree_status_relays(refs: list[RelayRef]) -> list[dict]:
        resolved = relay.get_many(
            [(ref.repo, ref.worktree_id) for ref in refs]
        )
        return [
            {
                "repo": ref.repo,
                "worktree_id": ref.worktree_id,
                "entry": resolved.get((ref.repo, ref.worktree_id)),
            }
            for ref in refs
        ]
