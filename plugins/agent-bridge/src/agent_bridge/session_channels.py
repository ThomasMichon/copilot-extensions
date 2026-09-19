"""Captured forward ownership, including failed retired forwards."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session_manager import SessionManager

log = logging.getLogger("agent-bridge")


def capture_forwards(manager: SessionManager, session_id: str) -> list[Any]:
    """Snapshot current and retired cleanup owners before any teardown awaits."""
    owned = list(manager._forward_cleanup_backlog.get(session_id, ()))
    current = manager._forwards.get(session_id)
    if current is not None and not any(current is item for item in owned):
        owned.append(current)
    return owned


async def close_forwards(
    manager: SessionManager, session_id: str, owned: list[Any], *, strict: bool,
) -> None:
    """Keep failed old handles even if a newer forward replaces the primary."""
    failures: list[Exception] = []
    for forward in owned:
        try:
            await forward.cancel()
        except Exception as exc:
            if strict:
                backlog = manager._forward_cleanup_backlog.setdefault(session_id, [])
                if not any(forward is item for item in backlog):
                    backlog.append(forward)
                failures.append(exc)
                log.warning("Forward cleanup failed for %s; owner retained", session_id, exc_info=True)
                continue
            log.warning("Best-effort forward cleanup failed for %s", session_id, exc_info=True)
        if manager._forwards.get(session_id) is forward:
            manager._forwards.pop(session_id, None)
        backlog = manager._forward_cleanup_backlog.get(session_id)
        if backlog is not None:
            backlog[:] = [item for item in backlog if item is not forward]
            if not backlog:
                manager._forward_cleanup_backlog.pop(session_id, None)
    if failures:
        raise failures[0]
