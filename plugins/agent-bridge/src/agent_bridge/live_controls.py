"""Session controls for a represented live session: requests the bridge
queues for the session's own extension to apply (today, switching its agent
mode), polled apart from its messages."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: A Copilot CLI session's agent mode, as ``/autopilot`` and ``/plan`` set it.
LiveSessionMode = Literal["interactive", "plan", "autopilot"]
#: The ``live_messages.kind`` of a mode change (a session control, polled apart
#: from messages so it is never delivered as a prompt).
SET_MODE_CONTROL = "control:set-mode"


class SetModeRequest(BaseModel):
    """Switch a live session's agent mode (what ``/autopilot on`` does)."""

    mode: LiveSessionMode
    sender: str = "operator"
    #: How long to wait for the session's extension to apply it (seconds).
    wait_timeout: float = Field(default=30.0, ge=1.0, le=120.0)
    expected_session_id: str | None = None


class SetModeResult(BaseModel):
    """Whether the session's extension applied the mode change.

    ``applied`` is False when it didn't within ``wait_timeout`` (the session's
    agent-bridge extension may predate mode changes, or the session is
    unresponsive); the request is then withdrawn, so it never applies later.
    """

    ok: bool = True
    session_id: str
    mode: LiveSessionMode
    applied: bool
    detail: str | None = None
