"""Caller-affinity selection for the legacy session-start HTTP boundary."""

from .models import SessionStatus

_REUSABLE_STATES = frozenset({
    SessionStatus.CREATED,
    SessionStatus.STARTING,
    SessionStatus.RUNNING,
    SessionStatus.IDLE,
})


def find_reusable_caller_session(manager, agent_name, caller_id):
    """Return the newest alive session matching the caller and agent."""
    for session in manager.list_sessions():
        if (
            session.caller_id == caller_id and session.agent_name == agent_name
            and session.status in _REUSABLE_STATES
        ):
            return session
    return None
