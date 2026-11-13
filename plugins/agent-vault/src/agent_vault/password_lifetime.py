"""Non-secret password lifetime status, independent of daemon inactivity."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service import VaultService

CONTRACT = "agent-vault.password-lifetime"
VERSION = 1
log = logging.getLogger(__name__)


def expire_passwords(service: VaultService) -> None:
    if not service._credential_lock.acquire(blocking=False):
        return
    try:
        for kpdb, stamp in list(service._password_set_at.items()):
            deadline = service._password_expiration(kpdb, stamp)
            if deadline is None or time.time() < deadline or not service.cli.has_password(kpdb):
                continue
            log.info("Password TTL expired - clearing %s", kpdb)
            service.cli.clear_password(kpdb)
            service.invalidate_cache(kpdb)
            service._password_set_at.pop(kpdb, None)
            service._password_handoff_deadlines.pop(kpdb, None)
    finally:
        service._credential_lock.release()


def describe_password_lifetime(
    service: VaultService, kpdb: str | None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "contract": CONTRACT,
        "version": VERSION,
        "state": "unknown",
        "remaining_seconds": None,
    }
    if not service._credential_lock.acquire(blocking=False):
        return result
    try:
        if not kpdb:
            return result
        if not service.cli.has_password(kpdb):
            result["state"] = "locked"
            return result
        stamp = service._password_set_at.get(kpdb)
        if stamp is None:
            return result
        deadline = service._password_expiration(kpdb, stamp)
        if deadline is None:
            result["state"] = "unbounded"
        else:
            result["state"] = "bounded"
            result["remaining_seconds"] = max(0, int(deadline - time.time()))
        return result
    finally:
        service._credential_lock.release()


def format_password_lifetime(value: object) -> str:
    if not isinstance(value, dict):
        return "unknown (service did not report it)"
    if (
        value.get("contract") != CONTRACT
        or type(value.get("version")) is not int
        or value["version"] != VERSION
    ):
        return "unknown (unsupported status contract)"
    state = value.get("state")
    if state == "locked":
        return "locked"
    if state == "unbounded":
        return "unbounded"
    remaining = value.get("remaining_seconds")
    if state == "bounded" and type(remaining) is int and remaining >= 0:
        return f"{remaining}s"
    return "unknown"
