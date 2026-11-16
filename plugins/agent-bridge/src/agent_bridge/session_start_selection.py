"""Caller-affinity selection for the legacy session-start HTTP boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .session_status import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager

_REUSABLE_STATES = frozenset({
    SessionStatus.CREATED,
    SessionStatus.STARTING,
    SessionStatus.RUNNING,
    SessionStatus.IDLE,
})


def find_reusable_caller_session(
    manager: SessionManager, agent_name: str | None, caller_id: str,
) -> Session | None:
    """Return the newest alive session matching the caller and agent."""
    for session in manager.list_sessions():
        if (
            session.caller_id == caller_id and session.agent_name == agent_name
            and session.status in _REUSABLE_STATES
        ):
            return session
    return None
