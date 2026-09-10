"""Keep a running SSH operation's own leases live without blocking its relay."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("agent-codespaces")
HEARTBEAT_INTERVAL = 30.0


def refresh_ownership(name: str, claim_owner: str | None, owner_tenant: str | None) -> None:
    from . import connection_owner, lease

    if claim_owner:
        try:
            if not lease.heartbeat(name, owner=claim_owner):
                log.warning("SSH claim heartbeat for %s was not renewed", name)
        except Exception as exc:
            log.warning("SSH claim heartbeat for %s failed: %s", name, exc)
    if owner_tenant:
        try:
            hold = connection_owner.heartbeat(name, owner_tenant)
            if hold is None or owner_tenant not in hold.tenants:
                log.warning("SSH relay tenant for %s is no longer held", name)
        except Exception as exc:
            log.warning("SSH relay tenant heartbeat for %s failed: %s", name, exc)


async def maintain_ownership(
    name: str, claim_owner: str | None, owner_tenant: str | None,
) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        refresh = asyncio.create_task(asyncio.to_thread(
            refresh_ownership, name, claim_owner, owner_tenant,
        ))
        try:
            await asyncio.shield(refresh)
        except asyncio.CancelledError:
            # Join the owned write before cleanup releases its tenant.
            await refresh
            raise
