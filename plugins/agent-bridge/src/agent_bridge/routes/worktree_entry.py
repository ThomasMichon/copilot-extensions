"""Stable worktree discovery row and compatible rendering serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class WorktreeEntry:
    """A discovered worktree on a machine."""

    id: str
    agent_name: str
    machine: str
    path: str
    branch: str
    status: str
    title: str | None = None
    started_at: str | None = None
    resume_count: int = 0
    session_count: int = 0
    turn_count: int = 0
    # Interactive-mux (wt-<id> tmux/psmux) liveness on the owning machine --
    # the *second ownership* a rendering consumer must see (#1883), distinct from a bridge
    # ACP session.
    mux_session: bool = False
    mux_clients: int | None = None
    mux_attached: bool | None = None
    # #2668 two-axis taxonomy, surfaced from ``agent-worktrees list --json``.
    # ``interface`` is cli|acp, ``origin`` is user|system|delegate,
    # ``picker_hidden`` is the Picker's own visibility verdict. All default
    # to "unknown/shown" for an older agent-worktrees runtime.
    interface: str | None = None
    origin: str | None = None
    picker_hidden: bool = False
    # worktree-status-core (#2917/#2956): the agent-asserted disposition
    # (follow_up + summary) and the derived live-intent pulse. ``summary``
    # is also what ``agent-dispatch focus`` writes (derive-don't-
    # duplicate). Freshness is computed at render time, not cached.
    follow_up: bool = False
    summary: str | None = None
    status_note_at: str | None = None
    live_intent: str | None = None
    live_intent_at: str | None = None
    live_intent_idle: bool = False
    # worktree-finality-and-obligations (Phase 5): the canonical closure
    # descriptor, surfaced raw/opaque from ``agent-worktrees list --json
    # --classify``. Only the consumer that knows the current
    # ``DESCRIPTOR_VERSION`` may treat it as authoritative. Absent on an
    # older runtime or when git classification wasn't run.
    closure: dict[str, Any] | None = None
    # agent-bridge-worktree-native-agents: the charter (spawn profile name)
    # bound to this worktree at create/embody time, surfaced from
    # ``agent-worktrees list --json`` as ``bound_agent``. None = unbound
    # (the venue's default agent drives, today's behavior).
    bound_agent: str | None = None

    def interactive_cli_state(self) -> str:
        """Classify interactive-CLI ownership from mux liveness.

        - ``held``    -- a mux session exists, terminal attached (or
          unknown): a live interactive CLI owns it. Do-not-disturb.
        - ``at-rest`` -- a mux session exists but detached: still a live
          process -- reclaim via take-over.
        - ``none``    -- no interactive mux session holds the worktree.
        """
        if not self.mux_session:
            return "none"
        if self.mux_attached is False:
            return "at-rest"
        return "held"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "machine": self.machine,
            "path": self.path,
            "branch": self.branch,
            "status": self.status,
            "title": self.title,
            "started_at": self.started_at,
            "resume_count": self.resume_count,
            "session_count": self.session_count,
            "turn_count": self.turn_count,
            "mux_session": self.mux_session,
            "mux_clients": self.mux_clients,
            "mux_attached": self.mux_attached,
            "interactive_cli": self.interactive_cli_state(),
            "interface": self.interface,
            "origin": self.origin,
            "picker_hidden": self.picker_hidden,
            # worktree-status-core (#2956): disposition + derived live pulse.
            "follow_up": self.follow_up,
            "summary": self.summary,
            "status_note_at": self.status_note_at,
            "live_intent": self.live_intent,
            "live_intent_at": self.live_intent_at,
            "live_intent_idle": self.live_intent_idle,
            "closure": self.closure,
            "bound_agent": self.bound_agent,
        }
