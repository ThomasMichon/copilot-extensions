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
    caller_id: str | None = None
    acp_session_id: str | None = None  # ACP-sourced session id (durable identity)
    target_dir: str | None = None
    target_type: Literal["local", "ssh", "command"] = "local"
    target_host: str | None = None
    worktree_id: str | None = None  # agent-worktrees worktree ID
    status: SessionStatus
    pid: int | None = None
    turn_count: int = 0
    context_size: int | None = None
    context_used: int | None = None
    context_pct: float | None = None
    usage_model: str | None = None
    last_usage_at: str | None = None
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
    caller_id: str | None = None  # caller identity for session affinity
    force_new: bool = False  # skip caller_id reuse and always create a fresh session
    # Per-session MCP servers mounted into the ACP session at session/new, giving
    # this session a bespoke, run-bound toolset (e.g. the Intelligence Dampener
    # review tools). Each entry is an ACP MCP server spec; ``type`` selects the
    # transport and defaults to ``stdio``:
    #   {"type": "stdio", "name": ..., "command": ..., "args": [...], "env": {...}}
    #   {"type": "http" | "sse", "name": ..., "url": ..., "headers": {...}}
    # None / omitted preserves the historic empty-toolset behavior.
    mcp_servers: list[dict[str, Any]] | None = None


class SubmitPromptRequest(BaseModel):
    """Request to submit a prompt to a session."""

    prompt: str


class ResumeSessionRequest(BaseModel):
    """Request to resume a stopped session."""

    pass


class CursorAckRequest(BaseModel):
    """Acknowledge delivery of events up to ``last_id`` for a caller.

    The delivery cursor advances only on these acks (after the client has
    flushed the content to its host), so an ungraceful client death never
    advances the cursor past undelivered content.
    """

    caller_id: str | None = None
    last_id: int = Field(ge=0)


# -- API responses -----------------------------------------------------------


class StartSessionResponse(BaseModel):
    session_id: str
    name: str
    status: SessionStatus


class SubmitPromptResponse(BaseModel):
    turn_index: int
    status: SessionStatus


class ResyncSessionResponse(BaseModel):
    """Result of rebuilding a session's event log from the agent replay."""

    event_count: int
    latest_id: int
    status: SessionStatus


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]


class CursorInfo(BaseModel):
    """Current delivery-cursor position for a caller on a session."""

    session_id: str
    caller_id: str | None = None
    last_acked_id: int = 0
    head_id: int = 0
    """The session's current max event id (the live head). Lets a caller tell
    whether it is behind unseen history without reading the whole backlog."""


# -- SSE events --------------------------------------------------------------


class SseEventData(BaseModel):
    """Wire format for an SSE event."""

    id: int
    event: str
    data: dict[str, Any]
    timestamp: float


# -- Config models -----------------------------------------------------------


class ContextThresholds(BaseModel):
    """Configurable context window usage thresholds (percentages)."""

    warning: int = 75
    critical: int = 90


class PhasedTimeouts(BaseModel):
    """Separate timeouts (seconds) for the distinct phases of a ``send``.

    A single coarse timeout cannot distinguish a slow codespace cold-start
    from a hung turn. These let each phase be bounded independently.
    """

    codespace_boot: float = Field(
        default=180.0,
        description="Max seconds to wait for a Shutdown codespace to boot.",
    )
    ssh_connect: float = Field(
        default=120.0,
        description="Max seconds (with retry) to establish the SSH connection "
        "to a target -- patient for wake-on-LAN / ProxyJump / slow boot.",
    )
    session_start: float = Field(
        default=60.0,
        description="Max seconds for a freshly spawned session to become idle.",
    )
    command: float = Field(
        default=1800.0,
        description="Max seconds to wait for a single turn/command to complete.",
    )


class RetentionConfig(BaseModel):
    """Garbage-collection policy for completed/disconnected sessions.

    agent-bridge's ``sessions.db`` is a *relay log* of cross-agent turns and
    events -- it is **not** the canonical Copilot session history (that lives
    in each target machine's ``~/.copilot/session-state`` and is archived
    separately by the session-sync flow). GC therefore only prunes the
    bridge's own metadata for **terminal** sessions older than the retention
    window; live sessions are never touched. The default 7-day window also
    gives session-sync time to archive before the relay copy is reclaimed.
    """

    enabled: bool = True
    max_age_hours: float = Field(
        default=168.0,
        description="Prune terminal sessions whose last update is older than "
        "this many hours (default 7 days).",
    )
    statuses: list[str] = Field(
        default_factory=lambda: ["ended", "failed", "stopped"],
        description="Terminal session states eligible for GC. Live states "
        "(created/starting/running/idle) are never pruned.",
    )
    vacuum: bool = Field(
        default=True,
        description="Compact (VACUUM) the DB after pruning so freed pages are "
        "returned to the filesystem -- SQLite never shrinks the file otherwise.",
    )
    vacuum_min_free_mb: float = Field(
        default=128.0,
        description="Only VACUUM when at least this many MB of freelist "
        "(reclaimable) pages exist, to avoid churn on a healthy DB.",
    )
    sweep_interval_hours: float = Field(
        default=12.0,
        description="Hours between background GC sweeps while the daemon runs. "
        "0 disables periodic sweeps (startup + manual `gc` only).",
    )


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
    context_thresholds: ContextThresholds = Field(default_factory=ContextThresholds)
    timeouts: PhasedTimeouts = Field(default_factory=PhasedTimeouts)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    worktree_discovery_interval: float = Field(
        default=0,
        description="Seconds between periodic worktree discovery sweeps. "
        "0 disables periodic crawling (on-demand only).",
    )
    idle_shutdown_seconds: int = Field(
        default=0,
        description="If > 0, the daemon exits after this many seconds with no "
        "active sessions. Used by the elevated sub-daemon so it does not linger "
        "once no host needs it; the persistent task restarts it headlessly. "
        "0 disables (the primary daemon stays up indefinitely).",
    )
    enable_credential_relay: bool = Field(
        default=True,
        description="If True, this daemon starts the shared credential relay "
        "(loopback port 9857) during startup. The primary daemon owns the relay; "
        "the elevated sub-daemon seeds this False so it never re-binds (and thus "
        "never evicts) the primary's relay -- local elevated agents reuse the "
        "primary's relay on the same host.",
    )
    session_host_enabled: bool = Field(
        default=False,
        description="EXPERIMENTAL (default off). When True, a LOCAL copilot child "
        "is spawned inside a survivable Session Host process that outlives an "
        "agent-bridge restart, so a frontend update does not kill or corrupt an "
        "active session; the frontend reattaches over a loopback endpoint. See "
        "the session_host package / effort agent-bridge-version-mux (#1759).",
    )
    session_host_stale_reap_seconds: int = Field(
        default=0,
        description="Version-mux sprawl bound (Phase 4, #1765). When > 0, a "
        "Session Host whose wire protocol this build no longer speaks (a rare "
        "breaking host-layer change) and whose child never idles is force-reaped "
        "once it has outlived this many seconds, so an immortal session cannot "
        "pin an old on-disk install forever. A stranded host whose child has "
        "already stopped is always reaped regardless. 0 disables the age bound "
        "(the default -- such a host then strands until its child's own stop). "
        "Only meaningful when session_host_enabled is True.",
    )
    graceful_cancel_settle_seconds: int = Field(
        default=45,
        description="Redeploy graceful-cancel settle budget (Session-Host mode). "
        "On drain/shutdown the daemon assertively-but-nicely cancels in-flight "
        "turns (ACP session/cancel) instead of killing or blocking on them, then "
        "waits up to this many seconds for the cancelled turns to reach their own "
        "stop (capturing final streamed messages) before stopping. Mid-turn "
        "sessions are flagged to receive a 'Resume' nudge once the restarted "
        "frontend reattaches. Only meaningful when session_host_enabled is True.",
    )
    idle_reap_ttl_seconds: int = Field(
        default=0,
        description="Idle-session reaper TTL (#1826, ownership inversion). When "
        "> 0, a session that is IDLE (the agent reached its own stop, not "
        "mid-turn), has ZERO active subscribers (no SSE stream / front watching "
        "it), and has been idle-and-unwatched for at least this many seconds is "
        "STOPPED -- freeing its Copilot child while preserving state for resume "
        "(a fresh child + load_session replay). This lets the back-end own "
        "session process lifetime by connection + state, so a front (Neuron "
        "Forge) need only connect/disconnect and never reaps for resource "
        "reasons. Never touches a running/mid-turn session (goal 1) nor one with "
        "a live subscriber or active background sub-agents. Complementary to "
        "session_host_stale_reap_seconds (which bounds a never-idle stranded "
        "old-version host). 0 disables (the default). Only meaningful when "
        "session_host_enabled is True.",
    )
    idle_reap_sweep_seconds: int = Field(
        default=300,
        description="How often the idle-session reaper sweep runs, in seconds "
        "(#1826). Clamped to a 30s floor. Only meaningful when "
        "session_host_enabled and idle_reap_ttl_seconds are both > 0.",
    )
