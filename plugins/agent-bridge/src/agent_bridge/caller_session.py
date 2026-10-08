"""The calling Copilot session's id, recorded on the bridge sessions it creates.

``caller_id`` names the caller's worktree directory -- a folder, not a session.
The Copilot CLI exports ``COPILOT_AGENT_SESSION_ID`` to every shell command it
runs, so a bridge session created from one also records that exact id as
``caller_session_id``. Capture is best-effort: a missing or malformed value is
recorded as nothing and never fails the command.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from typing import Any

CALLER_SESSION_ENV = "COPILOT_AGENT_SESSION_ID"
MAX_CALLER_SESSION_ID_LENGTH = 128
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]*")
_unsupported_warned = False


def gate_caller_session_id(client: Any, value: str | None) -> str | None:
    """Return ``value`` only when ``client``'s daemon records it, else ``None``.

    The field is gated on ``CALLER_SESSION_ID_PROTOCOL_VERSION``: an older
    daemon would silently ignore it, so it is omitted there instead (capture
    stays best-effort -- the create still succeeds) with one stderr warning per
    process so the lost attribution is visible rather than silent.
    """
    global _unsupported_warned
    if not value:
        return None
    from .protocol import CALLER_SESSION_ID_PROTOCOL_VERSION

    if client.daemon_supports(CALLER_SESSION_ID_PROTOCOL_VERSION):
        return value
    if not _unsupported_warned:
        _unsupported_warned = True
        print(
            "agent-bridge: the running daemon predates caller_session_id "
            f"recording (HTTP protocol v{CALLER_SESSION_ID_PROTOCOL_VERSION}); "
            "the session is created without the calling session's id. Update "
            "the agent-bridge runtime to record it.",
            file=sys.stderr,
        )
    return None


def normalize_caller_session_id(value: Any) -> str | None:
    """Return ``value`` stripped when it is a safe id, else ``None``."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > MAX_CALLER_SESSION_ID_LENGTH
        or _SAFE_ID.fullmatch(normalized) is None
    ):
        return None
    return normalized


def caller_session_id_from_env(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Read and validate ``COPILOT_AGENT_SESSION_ID`` from the environment."""
    env = os.environ if environ is None else environ
    return normalize_caller_session_id(env.get(CALLER_SESSION_ENV))
