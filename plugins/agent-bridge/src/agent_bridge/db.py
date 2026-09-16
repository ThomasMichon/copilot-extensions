"""SQLite database -- schema, migrations, and query helpers."""

from __future__ import annotations

from .db_core import (
    LIVE_SESSION_PURGE_SECONDS,
    LIVE_SESSION_STALE_SECONDS,
    SCHEMA_VERSION,
    _SESSIONS_ENSURE_COLUMNS,
    _CoreMixin,
    live_session_is_fresh,
    local_pid_alive,
)
from .db_events import _EventsMixin
from .db_live_sessions import _LiveSessionsMixin
from .db_maintenance import _MaintenanceMixin
from .db_prompts import _PromptsMixin
from .db_schema import _SchemaMixin
from .db_sessions import _SessionsMixin

__all__ = [
    "Database",
    "LIVE_SESSION_PURGE_SECONDS",
    "LIVE_SESSION_STALE_SECONDS",
    "SCHEMA_VERSION",
    "_SESSIONS_ENSURE_COLUMNS",
    "live_session_is_fresh",
    "local_pid_alive",
]


class Database(
    _CoreMixin,
    _SchemaMixin,
    _SessionsMixin,
    _LiveSessionsMixin,
    _EventsMixin,
    _PromptsMixin,
    _MaintenanceMixin,
):
    """Thread-safe SQLite database for agent-bridge session persistence.

    Uses WAL mode for concurrent readers + single writer. All writes go
    through ``execute_write`` which holds a threading lock.
    """
