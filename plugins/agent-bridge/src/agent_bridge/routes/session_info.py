"""Public session projection shared by session and worktree routes."""
from __future__ import annotations
from ..models import SessionInfo

def _session_info(s) -> SessionInfo:  # noqa: ANN001
    """Convert an internal Session to the public SessionInfo model."""
    from datetime import datetime, timezone

    status, at_rest, liveness = s.public_state()
    return SessionInfo(
        session_id=s.session_id,
        name=s.name,
        agent_name=s.agent_name,
        caller_id=s.caller_id,
        acp_session_id=s.acp_session_id,
        target_dir=s.target.cwd,
        target_type=s.target.type,
        target_host=s.target.host,
        project=getattr(s.target, "project", None),
        worktree_id=s.target.worktree_id,
        elevated=s.target.elevated,
        read_only=False,
        status=status,
        pid=s.pid,
        turn_count=s.turn_count,
        context_size=s.context_size,
        context_used=s.context_used,
        context_pct=s.context_pct,
        usage_model=s.usage_model,
        last_usage_at=(
            datetime.fromtimestamp(s.last_usage_at, tz=timezone.utc).isoformat()
            if s.last_usage_at else None
        ),
        created_at=datetime.fromtimestamp(s.created_at, tz=timezone.utc),
        updated_at=datetime.fromtimestamp(s.updated_at, tz=timezone.utc),
        last_output_at=(
            datetime.fromtimestamp(s.last_output_at, tz=timezone.utc).isoformat()
            if s.last_output_at else None
        ),
        last_heartbeat_at=(
            datetime.fromtimestamp(s.last_heartbeat_at, tz=timezone.utc).isoformat()
            if s.last_heartbeat_at else None
        ),
        liveness=liveness,
        at_rest=at_rest,
    )

