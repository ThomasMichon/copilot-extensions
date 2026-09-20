"""Render a :class:`agent_bridge.cold_store.ColdStoreSession` into the same
public :class:`agent_bridge.models.SessionInfo` shape a live/persisted session
uses -- the cold-store-provider fallback (see ``routes/sessions.py``'s
``get_session``) changes *where* the answer comes from, never the
caller-facing response shape (this effort's Phase 2b plan: "NF's interface to
the bridge does not need to change shape -- only its target does").

Split out of ``routes/sessions.py`` to keep that already-large module from
growing (see ``tools/module-size-baseline.json``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .cold_store import ColdStoreSession
from .models import SessionInfo, SessionStatus


def parse_cold_store_timestamp(value: str | None) -> datetime:
    """Best-effort parse of a cold-store provider's timestamp field.

    A provider's archival timestamps may not be strict ISO-8601 (or may be
    absent/non-string for older/malformed records); this never raises -- an
    unparseable/missing/wrong-typed value falls back to the current time so
    the response always validates.
    """
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def cold_store_session_info(cold: ColdStoreSession) -> SessionInfo:
    """Convert a cold-store provider's answer to the public ``SessionInfo``.

    ``cold.session_id`` already IS the durable Copilot ACP session id (the
    archive is keyed by nothing else) -- ``durable_session_id`` mirrors it
    directly, no ``acp_session_id`` ambiguity to resolve here.
    """
    try:
        status = SessionStatus(cold.status) if cold.status else SessionStatus.ENDED
    except ValueError:
        status = SessionStatus.ENDED
    return SessionInfo(
        session_id=cold.session_id,
        name=cold.session_id,
        durable_session_id=cold.session_id,
        target_dir=cold.cwd,
        project=cold.project,
        worktree_id=cold.worktree_id,
        read_only=True,
        status=status,
        created_at=parse_cold_store_timestamp(cold.created_at),
        updated_at=parse_cold_store_timestamp(cold.updated_at),
        at_rest=True,
    )


#: A live-sessions registry row's own status values (distinct from
#: agent-bridge's bridge-owned ``SessionStatus`` lifecycle) that mean the
#: registration is still genuinely live right now.
_LIVE_REGISTRATION_STATUSES = frozenset({"live", "wedged"})


def live_registration_to_session_info(row: dict) -> SessionInfo:
    """Convert a ``live_sessions`` registry row (an interactive CLI session
    the bridge represents but does not own -- see ``routes/live_sessions.py``)
    to the public ``SessionInfo`` shape.

    Its ``session_id`` is already the real Copilot ACP session id (the
    bundled extension registers with ``process.env.SESSION_ID`` -- see
    ``extensions/agent-bridge/extension.mjs``), so -- like the cold-store
    case, and unlike a bridge-owned live session's own escrow id -- there is
    no ``acp_session_id`` ambiguity to resolve: ``durable_session_id`` mirrors
    ``session_id`` directly.
    """
    status = (
        SessionStatus.RUNNING
        if row.get("status") in _LIVE_REGISTRATION_STATUSES
        else SessionStatus.STOPPED
    )
    session_id = row["session_id"]
    return SessionInfo(
        session_id=session_id,
        name=session_id,
        durable_session_id=session_id,
        acp_session_id=session_id,
        target_dir=row.get("cwd"),
        worktree_id=row.get("worktree_id"),
        read_only=True,
        status=status,
        pid=row.get("pid"),
        created_at=datetime.fromtimestamp(row["registered_at"], tz=timezone.utc),
        updated_at=datetime.fromtimestamp(row["updated_at"], tz=timezone.utc),
    )
