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
#: The longest a requester waits for a control (``SetModeRequest.wait_timeout``).
MAX_CONTROL_WAIT_SECONDS = 120.0
#: After its requester's wait, how long a control the extension already
#: claimed is still waited on for its outcome (applying it is a local RPC).
CLAIMED_CONTROL_GRACE_SECONDS = 10.0
#: A control older than this has no live requester (e.g. the daemon restarted
#: mid-wait): the control poll expires it rather than applying it late.
CONTROL_MAX_AGE_SECONDS = MAX_CONTROL_WAIT_SECONDS + CLAIMED_CONTROL_GRACE_SECONDS


class SetModeRequest(BaseModel):
    """Switch a live session's agent mode (what ``/autopilot on`` does)."""

    mode: LiveSessionMode
    sender: str = "operator"
    #: How long to wait for the session's extension to apply it (seconds).
    wait_timeout: float = Field(default=30.0, ge=1.0, le=MAX_CONTROL_WAIT_SECONDS)
    expected_session_id: str | None = None


#: How a mode change ended: ``applied``; ``rejected`` by the session;
#: ``withdrawn`` before the session took it (it never applies later); or
#: ``in_flight`` -- the session took it but reported no outcome in time, so it
#: may still apply.
ControlState = Literal["applied", "rejected", "withdrawn", "in_flight"]


class SetModeResult(BaseModel):
    """Whether the session's extension applied the mode change.

    ``applied`` is True or False only when that is known (``state`` applied,
    or rejected/withdrawn); it is ``None`` for ``in_flight``.
    """

    ok: bool = True
    session_id: str
    mode: LiveSessionMode
    applied: bool | None
    state: ControlState
    detail: str | None = None


class ControlAckRequest(BaseModel):
    """The extension's outcome for controls it claimed from ``/controls``."""

    ids: list[int]
    #: False when the session couldn't apply them (recorded as ``rejected``).
    applied: bool = True
