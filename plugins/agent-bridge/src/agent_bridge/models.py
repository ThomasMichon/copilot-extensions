"""Pydantic models for API requests, responses, and internal state."""

from __future__ import annotations

import sys
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# -- Platform defaults -------------------------------------------------------


def default_port() -> int:
    """Return the platform-default listen port.

    Windows uses 9280, Linux/WSL uses 9281.  This avoids TCP port
    collisions when both environments run on the same host (WSL2
    shares the Windows TCP port space).
    """
    return 9280 if sys.platform == "win32" else 9281


# -- Session status ----------------------------------------------------------


class SessionStatus(str, Enum):
    """Lifecycle states for an agent-bridge session."""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ENDED = "ended"


# -- Agent config ------------------------------------------------------------


class AgentProfile(BaseModel):
    """An agent launch profile from the agent registry."""

    name: str
    description: str = ""
    target_type: Literal["local", "ssh", "command"] = "local"
    cwd: str | None = None
    host: str | None = None
    user: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


# -- Session models ----------------------------------------------------------


class SessionInfo(BaseModel):
    """Public view of a session."""

    session_id: str
    name: str
    agent_name: str | None = None
    target_dir: str | None = None
    target_type: Literal["local", "ssh", "command"] = "local"
    target_host: str | None = None
    worktree_id: str | None = None  # agent-worktrees worktree ID
    status: SessionStatus
    pid: int | None = None
    turn_count: int = 0
    created_at: datetime
    updated_at: datetime


class TurnInfo(BaseModel):
    """Public view of a single turn."""

    turn_index: int
    prompt: str
    response_text: str = ""
    thought_text: str = ""
    stop_reason: str | None = None
    tool_calls: list[ToolCallInfo] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ToolCallInfo(BaseModel):
    """A tool call within a turn."""

    tool_call_id: str
    title: str
    kind: str = ""
    status: str = ""
    content: list[str] = Field(default_factory=list)


# -- Fix forward references --
TurnInfo.model_rebuild()


# -- API requests ------------------------------------------------------------


class StartSessionRequest(BaseModel):
    """Request to start a new agent session."""

    agent: str | None = None
    target_dir: str | None = None
    topology: str | None = None
    worktree_id: str | None = None  # agent-worktrees worktree ID for session roll


class SubmitPromptRequest(BaseModel):
    """Request to submit a prompt to a session."""

    prompt: str


class ResumeSessionRequest(BaseModel):
    """Request to resume a stopped session."""

    pass


# -- API responses -----------------------------------------------------------


class StartSessionResponse(BaseModel):
    session_id: str
    name: str
    status: SessionStatus


class SubmitPromptResponse(BaseModel):
    turn_index: int
    status: SessionStatus


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]


# -- SSE events --------------------------------------------------------------


class SseEventData(BaseModel):
    """Wire format for an SSE event."""

    id: int
    event: str
    data: dict[str, Any]
    timestamp: float


# -- Config models -----------------------------------------------------------


class TopologyProfile(BaseModel):
    """A topology profile pointing to external config files."""

    machines_yaml: str | None = None
    agents_config: str | None = None


class ServiceConfig(BaseModel):
    """Root config loaded from ~/.agent-bridge/config.yaml."""

    port: int = Field(default_factory=default_port)
    bind: str = "127.0.0.1"
    db_path: str = "~/.agent-bridge/sessions.db"
    log_level: str = "info"
    topologies: dict[str, TopologyProfile] = Field(default_factory=dict)
    worktree_discovery_interval: float = Field(
        default=0,
        description="Seconds between periodic worktree discovery sweeps. "
        "0 disables periodic crawling (on-demand only).",
    )
