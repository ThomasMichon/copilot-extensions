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


def _driving_worktree_record(worktree_id: str | None) -> Any | None:
    """The agent-worktrees record for ``worktree_id``, or ``None`` when that
    token is empty, unresolvable, or agent-worktrees isn't installed
    alongside. Containers leases are effort-scoped; this only resolves the
    Phase 4 drill-in actions when the lease token actually names a worktree."""
    if not worktree_id:
        return None
    try:
        from agent_worktrees import tracking
    except ImportError:
        return None
    try:
        return tracking.load_record_by_id(worktree_id)
    except Exception:
        return None


def driving_worktree_id_for(worktree_id: str | None) -> str:
    """The full driving worktree id suitable for in-picker drill-in actions,
    else ``""`` when the lease token doesn't resolve to a tracked worktree."""
    record = _driving_worktree_record(worktree_id)
    return str(record.worktree_id) if record is not None else ""


def _driving_worktree_mark(*, has_driving_worktree: bool) -> str:
    """The reserved line-two mark for a row backed by the current driving
    worktree."""
    return "\u2192" if has_driving_worktree else ""


def _prefixed_subtitle(subtitle: str, *, has_driving_worktree: bool) -> str:
    """Apply the reserved driving-worktree mark to the durable title/activity
    line when appropriate."""
    mark = _driving_worktree_mark(has_driving_worktree=has_driving_worktree)
    if mark and subtitle:
        return f"{mark} {subtitle}"
    return subtitle


def worktree_status_for_worktree(worktree_id: str | None) -> dict[str, Any]:
    """A read-only Worktree Status card payload for the driving worktree, or
    an explicit unavailable card when this row isn't backed by a resolvable
    tracked worktree."""
    unavailable = {
        "title": "Worktree status unavailable",
        "status": "unknown",
        "link": None,
        "body": "No tracked driving worktree is recorded for this container.",
    }
    record = _driving_worktree_record(worktree_id)
    if record is None:
        return unavailable
    try:
        from agent_worktrees import claim_kinds_registry, claims_rank, status_bar_cli
    except ImportError:
        return unavailable
    try:
        payload = status_bar_cli._status_segment_json(record.path)
    except Exception:
        payload = None
    try:
        claims = claims_rank.summarize_claims(
            record.resources,
            pecking_order=claim_kinds_registry.effective_pecking_order(),
            label_overrides=claim_kinds_registry.effective_label_overrides(),
        )
    except Exception:
        claims = ""
    state = str((payload or {}).get("state") or "unknown")
    closure = (payload or {}).get("closure") or {}
    git_bits = [
        f"ahead {payload.get('ahead', 0)}" if payload is not None else None,
        f"behind {payload.get('behind', 0)}" if payload is not None else None,
        "dirty" if payload and payload.get("dirty") else "clean" if payload else None,
    ]
    git_summary = ", ".join(bit for bit in git_bits if bit)
    live = (
        "mux live" if record.mux_live is True else
        "bound live" if record.bound_live is True else
        "idle"
    )
    body = "\n".join([
        f"- Repo: `{record.repo}`",
        f"- Worktree: `{record.worktree_id}`",
        f"- Branch: `{record.branch}`",
        f"- Turns: {(payload or {}).get('turn_count', 0)}",
        f"- Live: {live}",
        f"- Git: {state}" + (f" ({git_summary})" if git_summary else ""),
        f"- Closure: {closure.get('label', 'unknown')}",
        f"- Claims: {claims or 'none'}",
    ])
    return {
        "title": f"Worktree {record.worktree_id} ({record.repo})",
        "status": closure.get("style") or state,
        "link": None,
        "body": body,
    }


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


def supervising_worktree_id(live_session: dict[str, Any] | None) -> str:
    """The tracked worktree id named by a live session's
    ``venue.supervisor_ref`` (the worktree that launched a detached session on
    this container), else ``""``. The fallback when the fleet lease doesn't name
    a worktree -- a detached launch takes no lease -- so the supervising
    worktree's Picker row still shows its worker. The id is taken from the
    ref as-is (it names a worktree in any project, which the Picker matches by
    full id)."""
    venue = (live_session or {}).get("venue") or {}
    ref = str(venue.get("supervisor_ref") or "").split("#", 1)[0]
    return ref.rsplit("/", 1)[-1] if ref.count("/") >= 2 else ""


def picker_fields(container_name: str, lease_effort: str | None) -> dict[str, Any]:
    """The three picker-only fields a Containers fleet row adds to its
    existing ``fleet --json`` shape: ``subtitle``, ``claims_summary``,
    ``sess``, and the Phase 4 drill-in metadata (`worktree_id`,
    `has_driving_worktree`, `worktree_status`). One entry point so
    `__main__._cmd_fleet` stays a thin caller."""
    live_session = live_session_for_venue("container", container_name)
    driving_worktree_id = (driving_worktree_id_for(lease_effort)
                           or supervising_worktree_id(live_session))
    has_driving_worktree = bool(driving_worktree_id)
    subtitle = subtitle_for(container_name, lease_effort)
    activity = activity_from_live_session(live_session)
    if activity:
        subtitle = f"{subtitle} - {activity}" if subtitle else f"{container_name} - {activity}"
    subtitle = _prefixed_subtitle(
        subtitle,
        has_driving_worktree=has_driving_worktree,
    )
    return {
        "subtitle": subtitle,
        "activity": activity,
        "session_id": (live_session or {}).get("session_id") or "",
        "claims_summary": claims_summary_for_worktree(lease_effort),
        "sess": sess_column(live_session, lease_effort),
        "worktree_id": driving_worktree_id,
        "has_driving_worktree": "true" if has_driving_worktree else "false",
        "worktree_status": worktree_status_for_worktree(driving_worktree_id),
    }
