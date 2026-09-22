"""Worktree Picker **Containers**-pivot field computation (picker-venue-pivots
Phase 2).

Pure/best-effort helpers layered on top of `__main__._cmd_fleet`'s existing
``fleet --json`` row shape -- unlike agent-codespaces' `pool.picker_payload`
(a dedicated ``--picker-json`` shape), the Containers pivot's manifest
(`pivots/agent-containers.json`, per the approved Phase 0 preview) reuses the
plain ``fleet --json`` output directly, mapping its already-present ``lease``/
``fleet`` fields straight to the pivot's ``worktree``/``group`` entries. This
module adds the fields that output does *not* already carry: ``subtitle``,
``claims_summary``, and ``sess`` -- the same picker-venue-pivots additions
Phase 1 added to agent-codespaces' `pool.py`, duplicated here rather than
imported (agent-containers and agent-codespaces are independent plugins with
no shared glue module for this cross-cutting concern -- consistent with how
both already vendor their own separate ``copilot_venue.py``/``venue-copilot``
lib rather than sharing one).
"""
from __future__ import annotations

from typing import Any

#: agent-bridge liveness labels that read as "a turn is actually running right
#: now" -- see agent-codespaces' `pool._LIVE_TURN_LIVENESS` for the same
#: vocabulary and grounding (routes.live_sessions._live_liveness).
_LIVE_TURN_LIVENESS = frozenset({"active", "stalled"})


def subtitle_for(container_name: str, lease_effort: str | None) -> str:
    """The durable-title half of line two: ``"claimed by {effort}"`` when
    leased, else ``""`` (graceful-absence -- a free container has nothing to
    say yet)."""
    if lease_effort:
        return f"claimed by {lease_effort}"
    return ""


def claims_summary_for_worktree(worktree_id: str | None) -> str:
    """The claiming worktree's ranked claims-list (via the shared
    ``agent_worktrees.claims_rank`` module -- the *same* module Phase 1 wired
    for agent-codespaces, reused here rather than reimplemented per the
    effort's own Plan). Lazily imports ``agent_worktrees`` (agent-containers
    has no hard dependency on it either); degrades to ``""`` on any failure,
    never raises."""
    if not worktree_id:
        return ""
    try:
        from agent_worktrees import claim_kinds_registry, claims_rank, tracking
    except ImportError:
        return ""
    try:
        record = tracking.load_record_by_id(worktree_id)
    except Exception:
        return ""
    if record is None:
        return ""
    try:
        pecking_order = claim_kinds_registry.effective_pecking_order()
        label_overrides = claim_kinds_registry.effective_label_overrides()
    except Exception:
        pecking_order = None
        label_overrides = None
    try:
        return claims_rank.summarize_claims(
            record.resources,
            pecking_order=pecking_order,
            label_overrides=label_overrides,
        )
    except Exception:
        return ""


def bridge_client_from_env() -> Any | None:
    """A ``BridgeClient`` dialed at the locally-configured agent-bridge
    daemon, or ``None`` when agent-bridge isn't installed alongside, has no
    auth token yet, or any resolution step fails.

    Deliberately **not** ``BridgeClient.from_config()`` -- see
    agent-codespaces' `pool._bridge_client_from_env` docstring for why that
    classmethod (prints to stderr + ``sys.exit(1)`` on a missing auth token)
    is wrong for an inline fleet-listing call.
    """
    try:
        import yaml
        from agent_bridge.client import BridgeClient
        from agent_bridge.config import config_dir
        from agent_bridge.models import default_port
    except ImportError:
        return None
    try:
        cfg_path = config_dir() / "config.yaml"
        auth_path = config_dir() / "auth.yaml"
        if not auth_path.exists():
            return None
        auth_data = yaml.safe_load(auth_path.read_text(encoding="utf-8")) or {}
        token = auth_data.get("token")
        if not token:
            return None
        port = default_port()
        bind = "127.0.0.1"
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            port = data.get("port") or port
            bind = data.get("bind", bind) or bind
        if bind in ("0.0.0.0", ""):
            bind = "127.0.0.1"
        elif bind == "::":
            bind = "::1"
        return BridgeClient(f"http://{bind}:{port}", str(token), timeout=5)
    except Exception:
        return None


def live_session_for_venue(kind: str, target: str) -> dict[str, Any] | None:
    """The registered agent-bridge live session whose ``venue`` targets this
    ``kind``/``target`` (e.g. ``"container"``/a container name), or ``None``
    when agent-bridge is unreachable/not installed or no session matches.
    Never raises."""
    if not target:
        return None
    client = bridge_client_from_env()
    if client is None:
        return None
    try:
        sessions = client.list_live_sessions(include_dead=False)
    except Exception:
        return None
    for session in sessions or []:
        venue = (session or {}).get("venue") or {}
        if venue.get("kind") == kind and venue.get("target") == target:
            return session
    return None


def sess_column(live_session: dict[str, Any] | None, worktree_id: str | None) -> str:
    """The Worktrees pane's own compact ``sess``/``live`` column vocabulary,
    reused as-is: ``"LIVE"`` when agent-bridge reports an actually-running
    turn, ``"IDLE"`` when a worktree is driving but no turn is live, else
    ``""`` when nothing is driving at all."""
    if live_session and live_session.get("liveness") in _LIVE_TURN_LIVENESS:
        return "LIVE"
    if worktree_id:
        return "IDLE"
    return ""


def activity_from_live_session(live_session: dict[str, Any] | None) -> str:
    """The transient-activity half of line two: the live session's most
    recent ``latest_progress`` beat (``"{phase}: {summary}"``, or just
    ``summary``), or ``""`` when there is no live session or it hasn't
    reported one yet."""
    if not live_session:
        return ""
    progress = live_session.get("latest_progress") or {}
    summary = progress.get("summary") if isinstance(progress, dict) else None
    if not summary:
        return ""
    phase = progress.get("phase")
    return f"{phase}: {summary}" if phase else str(summary)


def picker_fields(container_name: str, lease_effort: str | None) -> dict[str, str]:
    """The three picker-only fields a Containers fleet row adds to its
    existing ``fleet --json`` shape: ``subtitle``, ``claims_summary``,
    ``sess``. One entry point so `__main__._cmd_fleet` stays a thin caller."""
    live_session = live_session_for_venue("container", container_name)
    subtitle = subtitle_for(container_name, lease_effort)
    activity = activity_from_live_session(live_session)
    if activity:
        subtitle = f"{subtitle} - {activity}" if subtitle else f"{container_name} - {activity}"
    return {
        "subtitle": subtitle,
        "claims_summary": claims_summary_for_worktree(lease_effort),
        "sess": sess_column(live_session, lease_effort),
    }
