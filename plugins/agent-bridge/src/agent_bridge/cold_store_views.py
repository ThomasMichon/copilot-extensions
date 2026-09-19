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
    """Convert a cold-store provider's answer to the public ``SessionInfo``."""
    try:
        status = SessionStatus(cold.status) if cold.status else SessionStatus.ENDED
    except ValueError:
        status = SessionStatus.ENDED
    return SessionInfo(
        session_id=cold.session_id,
        name=cold.session_id,
        target_dir=cold.cwd,
        project=cold.project,
        worktree_id=cold.worktree_id,
        read_only=True,
        status=status,
        created_at=parse_cold_store_timestamp(cold.created_at),
        updated_at=parse_cold_store_timestamp(cold.updated_at),
        at_rest=True,
    )
