"""Identity-bound native live-session registration."""
from __future__ import annotations
from pydantic import BaseModel, Field

class RegisterLiveSessionRequest(BaseModel):
    """Registration payload from the bundled agent-bridge extension."""

    session_id: str
    machine: str | None = None
    cwd: str | None = None
    worktree_id: str | None = None
    repo: str | None = None
    branch: str | None = None
    pid: int | None = None
    role: str | None = None
    driven_by: str | None = None
    execution_id: str | None = Field(default=None, max_length=128)
    execution_generation: str | None = Field(default=None, max_length=128)

