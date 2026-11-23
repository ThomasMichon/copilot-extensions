"""Unlock request dispatch under the existing service credential custody."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from . import cache_warm_selection

if TYPE_CHECKING:
    from .service import VaultService


def handle_unlock(
    service: VaultService, request: dict[str, Any], kpdb: str, group: str | None,
    vault_name: str, reason: str, *, password_ttl: int, logger: logging.Logger,
) -> dict[str, Any]:
    """Keep unlock success separate from request-source warming acceptance."""
    password = request.get("password", "")
    if not password:
        # Provider-only unlock stays non-prompting unless explicitly requested.
        want_prompt = bool(request.get("prompt"))
        if service.ensure_unlocked(kpdb, vault_name, reason, allow_prompt=want_prompt):
            warmed = cache_warm_selection.enqueue(service, kpdb, group)
            return cache_warm_selection.unlocked(request, warmed)
        error_msg = service._last_error(kpdb) or service._last_error("") or "Unlock failed"
        return {"ok": False, "error": error_msg, "needs_unlock": True}
    if not kpdb:
        return {"ok": False, "error": "KeePass database path is not configured; set KPDB"}
    if service.cli.verify_password(kpdb, password):
        service.cli.set_password(kpdb, password)
        service._password_set_at[kpdb] = time.time()
        service._password_handoff_deadlines.pop(kpdb, None)
        service._unlock_failed_at.pop(kpdb, None)
        service._record_unlock_error(kpdb, None)
        service._last_dismiss[kpdb] = False
        warmed = cache_warm_selection.enqueue(service, kpdb, group, confirmed=True)
        logger.info("CLI backend unlocked (TTL %ds) [%s]", password_ttl, reason)
        return cache_warm_selection.unlocked(request, warmed)
    return {"ok": False, "error": "Invalid password"}
