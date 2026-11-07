"""Confirm shared return and original-owner settlement before local cleanup."""
from __future__ import annotations

import json
import os

from . import coordination, lease


def _original_holder(name: str, held: lease.Lease, shared: dict) -> str | None:
    holder = shared.get("holder")
    if not isinstance(holder, str) or not lease._same_holder_ref(holder, holder):
        return None
    owner = lease._claim_owner(held)
    if (
        held.host == lease._this_host()
        and lease.resolve_owner_worktree() == owner
        and lease._same_holder_ref(coordination.owner_ref(), holder)
    ):
        return holder
    proc = coordination._run(["claims", "owner", "codespace", name, "--all-states", "--json"])
    if proc is None or proc.returncode:
        return None
    try:
        payload = json.loads(proc.stdout)
    except (TypeError, ValueError):
        return None
    owners = payload.get("owners") if isinstance(payload, dict) else None
    if not isinstance(owners, list) or not os.path.isabs(owner):
        return None
    matches = [
        item for item in owners if isinstance(item, dict)
        and isinstance(path := item.get("worktree_path") or item.get("path"), str)
        and os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(owner))
        and lease._same_holder_ref(item.get("qualified_ref") or item.get("owner_ref"), holder)
    ]
    return holder if len(matches) == 1 else None


def release_claim(name: str, owner: str) -> bool:
    """Keep local custody whenever shared return or ledger settlement is unknown."""
    with lease._lease_lock():
        records = lease._read_records(lease.LEASE_FILE, lease.Lease, fail_closed=True)
        held = records.get(name)
    if held is None or lease._claim_owner(held) != owner:
        return False
    if held.lease_token:
        shared = coordination.inspect(name)
        holder = _original_holder(name, held, shared) if shared else None
        if not holder:
            raise RuntimeError(f"Return of '{name}' is unconfirmed; original owner and local claim are retained")
        if shared.get("state") == "leased" and shared.get("token") == held.lease_token:
            if not coordination.release(name, held.lease_token).ok:
                raise RuntimeError(f"Shared return of '{name}' is unconfirmed; local claim is retained")
        elif shared.get("state") != "released" or shared.get("live") is not False:
            raise RuntimeError(f"Shared ownership of '{name}' changed; local claim is retained")
        if not coordination.settle_obligation(name, holder, released=True, confirmed=True):
            raise RuntimeError(f"Original-owner settlement of '{name}' is unconfirmed; local claim is retained")
    with lease._lease_lock():
        records = lease._read_records(lease.LEASE_FILE, lease.Lease, fail_closed=True)
        if records.get(name) != held:
            raise RuntimeError(f"Local ownership of '{name}' changed during return; current claim is retained")
        del records[name]
        lease._write_leases(records)
        return True
