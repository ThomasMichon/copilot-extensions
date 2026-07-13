"""Pydantic models for API requests, responses, and internal state."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# -- Platform defaults -------------------------------------------------------


def _is_wsl() -> bool:
    """True when running as a WSL guest.

    A WSL guest shares the Windows host's TCP port namespace, so it (and only
    it) needs a distinct default port to avoid colliding with the host's own
    daemon. Bare-metal Linux is **not** WSL and must not be treated as such --
    the discriminator is "am I a WSL guest?", not "am I non-Windows?".
    """
    if sys.platform == "win32":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/sys/kernel/osrelease", encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def default_port() -> int:
    """Return the platform-default listen port.

    A host exposes **9280**. Only a **WSL guest** -- which shares the Windows
    host's TCP port namespace -- uses **9281**, to avoid colliding with the
    host's own daemon. Bare-metal Linux (and macOS) are ordinary single-context
    hosts on 9280.
    """
    return 9281 if _is_wsl() else 9280


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
    project: str | None = None  # resolved repo/binstub (agent-worktrees project)
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
    # Liveness (#145): last_output_at advances on every ACP frame (the true
    # progress signal); last_heartbeat_at is a periodic transport-liveness beat;
    # liveness derives active/stalled/disconnected for a RUNNING session.
    last_output_at: str | None = None
    last_heartbeat_at: str | None = None
    liveness: str | None = None


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
    sender_repo: str | None = None  # caller's repo (agent-worktrees `get project`
    #                                 in the CLI cwd) -- bare-venue default source
    force_new: bool = False  # skip caller_id reuse and always create a fresh session
    # Per-session MCP servers mounted into the ACP session at session/new, giving
    # this session a bespoke, run-bound toolset (e.g. the Intelligence Dampener
    # review tools). Each entry is an ACP MCP server spec; ``type`` selects the
    # transport and defaults to ``stdio``:
    #   {"type": "stdio", "name": ..., "command": ..., "args": [...], "env": {...}}
    #   {"type": "http" | "sse", "name": ..., "url": ..., "headers": {...}}
    # None / omitted preserves the historic empty-toolset behavior.
    mcp_servers: list[dict[str, Any]] | None = None
    # Extra ``copilot`` CLI args APPENDED to the resolved agent's copilot_args
    # for THIS session only (e.g. a per-run ``--additional-mcp-config @<file>``
    # to mount a run-bound MCP toolset the argv way -- copilot honors this over
    # --acp, unlike the ACP session/new ``mcp_servers`` path). The registered
    # agent's own args are preserved; these are added after them. None / omitted
    # changes nothing.
    copilot_args: list[str] | None = None


class SubmitPromptRequest(BaseModel):
    """Request to submit a prompt to a session."""

    prompt: str


class ResumeSessionRequest(BaseModel):
    """Request to resume a stopped session."""

    pass


class AnswerAskUserRequest(BaseModel):
    """Answer to a parked ``ask_user`` elicitation on a session.

    ``content`` maps each requested schema field to the human's value
    (str | int | float | bool | list[str]). ``action`` selects the reply kind:
    ``accept`` (submit ``content``), ``decline``, or ``cancel``.
    """

    tool_call_id: str
    content: dict[str, Any] = Field(default_factory=dict)
    action: str = "accept"


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


# -- Live interactive-session registry (extension-backed) --------------------


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


class LiveSessionInfo(BaseModel):
    """Public view of a registered live interactive CLI session."""

    session_id: str
    machine: str | None = None
    cwd: str | None = None
    worktree_id: str | None = None
    repo: str | None = None
    branch: str | None = None
    pid: int | None = None
    role: str | None = None
    driven_by: str | None = None
    status: str = "live"
    #: Coarse turn-state derived from the represented event tail (Phase 7
    #: Channel A): "running" | "idle" | None (no turn signal yet).
    turn_state: str | None = None
    last_activity_at: float | None = None
    #: Friendly liveness label computed on read: active / stalled / idle / None.
    liveness: str | None = None
    #: Operator-driven session's latest progress beat (parsed object) or None
    #: (Phase 7 Slice 7c). The live-session analogue of a task's latest_progress.
    latest_progress: dict[str, Any] | None = None
    registered_at: float
    updated_at: float


class LiveSessionListResponse(BaseModel):
    live_sessions: list[LiveSessionInfo]


class SdkEventIn(BaseModel):
    """One raw Copilot extension SDK event, as forwarded by the extension.

    ``data`` is passed through verbatim to the bridge-side translator; only the
    fields the translator reads are used. ``timestamp``/``id`` are accepted for
    forward-compat but the bridge assigns its own monotonic event ids.
    """

    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: float | None = None
    id: str | None = None


class IngestLiveEventsRequest(BaseModel):
    """A batch of SDK events pushed by a represented live session's extension."""

    events: list[SdkEventIn] = Field(default_factory=list)


class IngestLiveEventsResult(BaseModel):
    """Result of an ingest batch: how many bridge events it produced."""

    ok: bool = True
    session_id: str
    ingested: int
    last_id: int


class LiveProgressRequest(BaseModel):
    """An operator-driven session's progress beat (Phase 7 Slice 7c).

    The live-session analogue of the dispatched-task progress beat: a bounded,
    latest-only status line the agent emits when the extension nudges it.
    """

    summary: str
    phase: str = ""
    blocker: str | None = None
    pr: str | None = None


class SendMessageRequest(BaseModel):
    """Post a message INTO a live interactive session (Phase 2 write path)."""

    sender: str
    body: str
    reply_to: str | None = None
    kind: str = "prompt"
    wait: bool = False
    wait_timeout: float = 120.0


class SendMessageResult(BaseModel):
    """Result of enqueuing a message for delivery into a live session.

    When the request set ``wait``, the bridge also watches the target's
    *represented* event stream for the reply turn (D1): ``replied`` is True once
    the next ``turn_complete`` lands, ``reply`` carries the assistant text of
    that turn, and ``stop_reason`` its stop reason. On a wait timeout ``replied``
    is False and the message still sits durably in the queue.
    """

    ok: bool = True
    session_id: str
    message_id: int
    replied: bool = False
    reply: str | None = None
    stop_reason: str | None = None


class LiveMessage(BaseModel):
    """A pending message awaiting delivery into a live session."""

    id: int
    sender: str
    body: str
    reply_to: str | None = None
    kind: str = "prompt"
    created_at: float


class LiveMessageListResponse(BaseModel):
    """Pending messages for a live session, oldest-first (the poll response)."""

    messages: list[LiveMessage]


class AckMessagesRequest(BaseModel):
    """Ack delivered messages by id (the extension acks after ``session.send``)."""

    ids: list[int]


class AckMessagesResult(BaseModel):
    """Result of acking delivered messages."""

    ok: bool = True
    acked: int


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
        default=True,
        description="Default ON. A dispatched copilot child is spawned inside a "
        "survivable Session Host process that outlives an agent-bridge restart, so "
        "a frontend update/redeploy does not kill or corrupt an active session; the "
        "frontend reattaches over a loopback endpoint (local) or a re-established "
        "SSH -L forward (machine-mesh / CodeSpace). This is the durable-dispatch "
        "default for the whole mesh (see the session_host package + the "
        "codespace-dispatch-reliability effort, #145/#177). Set False to opt a "
        "machine back onto the legacy front-owns-stdio path.",
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
        default=600,
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
        "old-version host). Default 600s: armed by default so an idle Session "
        "Host child can't leak indefinitely if a consumer crashes/forgets to "
        "DELETE its session -- the natural complement to session_host_enabled "
        "being default-on. 0 disables. Only meaningful when session_host_enabled "
        "is True.",
    )
    idle_reap_sweep_seconds: int = Field(
        default=120,
        description="How often the idle-session reaper sweep runs, in seconds "
        "(#1826). Clamped to a 30s floor. Only meaningful when "
        "session_host_enabled and idle_reap_ttl_seconds are both > 0.",
    )
    live_stall_interrupt_after_s: int = Field(
        default=900,
        description="Live-stall interrupt threshold (#2427, Phase 5). When > 0, "
        "the staleness watchdog interrupts a RUNNING session that is liveness "
        "'stalled' (its ACP transport is up but no frame has flowed for "
        "_STALL_AFTER_S = 180s) AND still has a live in-daemon prompt task "
        "(_prompt_task) once its silence (now - last_output_at) exceeds this many "
        "seconds. The interrupt is a graceful ACP session/cancel (interrupt_turn, "
        "#899), never a task-cancel or child kill: the in-flight send_prompt "
        "returns/raises, the runner settles the session to IDLE with a terminal "
        "session_state_changed, and consumers converge instead of watching a "
        "frozen 'Responding...' forever. This is the live-stall case the Sub-B "
        "watchdog (reconcile_wedged_running) otherwise leaves untouched because a "
        "live prompt task looks like a real turn. Deliberately DISTINCT from and "
        "much larger than the 180s stall threshold, because a legitimately long "
        "tool call also shows a live task + 'stalled' liveness -- the long "
        "threshold plus the graceful (non-killing) cancel are what make aborting "
        "acceptable. Set conservatively; 0 disables the live-stall interrupt "
        "entirely (the runner-less resync path is unaffected).",
    )
