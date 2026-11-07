"""Confirm shared return and original-owner settlement before local cleanup."""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict

from . import coordination, lease
from .claim_guard import claim_guard

RETURN_KEY = "__agent_codespaces_returns_v1__"


def validate_pending(value, raw: dict) -> dict:
    try:
        if set(value) != {"version", "returns"} or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError
        records = value["returns"]
        if not isinstance(records, dict):
            raise ValueError
        for name, progress in records.items():
            if (
                not isinstance(name, str) or not name or name == RETURN_KEY
                or set(progress) != {"holder", "lease", "phase"}
                or progress["phase"] not in ("prepared", "released")
                or not lease._same_holder_ref(progress["holder"], progress["holder"])
                or progress["lease"] != raw.get(name)
            ):
                raise ValueError
            held = lease.Lease(**progress["lease"])
            if (
                held.codespace != name or not held.lease_token
                or not lease._claim_owner(held)
                or any(not isinstance(item, str) for item in (
                    held.codespace, held.effort, held.worktree, held.host, held.lease_token,
                ))
                or type(held.pid) is not int or held.pid < 0
                or any(type(item) not in (int, float) or not math.isfinite(item) or item < 0
                       for item in (held.acquired_at, held.heartbeat_at))
            ):
                raise ValueError
        return records
    except (TypeError, KeyError, ValueError, OverflowError) as error:
        raise lease.ProviderAdmissionError("Local CodeSpace return progress is malformed") from error


def pending_returns() -> dict:
    if not lease.LEASE_FILE.exists():
        return {}
    try:
        raw = json.loads(lease.LEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise lease.ProviderAdmissionError("Local CodeSpace return progress is unreadable") from error
    if not isinstance(raw, dict):
        raise lease.ProviderAdmissionError("Local CodeSpace return progress is malformed")
    return validate_pending(raw[RETURN_KEY], raw) if RETURN_KEY in raw else {}


def write_leases(records: dict, pending: dict) -> None:
    raw = {name: asdict(held) for name, held in records.items()}
    if pending:
        raw[RETURN_KEY] = {"version": 1, "returns": pending}
        validate_pending(raw[RETURN_KEY], raw)
    lease._write_private_json(lease.LEASE_FILE, raw)


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


def release_claim(name: str, owner: str, *, expected: lease.Lease | None = None) -> bool:
    """Keep local custody whenever shared return or ledger settlement is unknown."""
    with claim_guard(name):
        return _release_claim(name, owner, expected)


def _release_claim(name: str, owner: str, expected: lease.Lease | None) -> bool:
    with lease._lease_lock():
        records = lease._read_records(lease.LEASE_FILE, lease.Lease, fail_closed=True)
        held = records.get(name)
        progress = pending_returns().get(name)
    if expected is not None and held != expected:
        raise RuntimeError(f"Local ownership of '{name}' changed before return; current claim is retained")
    if held is None or lease._claim_owner(held) != owner:
        return False
    if held.lease_token:
        shared = None if progress and progress["phase"] == "released" else coordination.inspect(name)
        if progress:
            holder = progress["holder"]
        else:
            holder = _original_holder(name, held, shared) if shared else None
        if not holder:
            raise RuntimeError(f"Return of '{name}' is unconfirmed; original owner and local claim are retained")
        from .driving_worktrees import _qualified_claim_refs

        refs = _qualified_claim_refs(holder)
        if not progress and not (
            shared and (
                shared.get("state") == "leased" and shared.get("token") == held.lease_token
                or shared.get("state") == "released" and shared.get("live") is False
            )
        ):
            raise RuntimeError(f"Shared ownership of '{name}' changed; local claim is retained")
        if not progress:
            progress = {"holder": holder, "lease": asdict(held), "phase": "prepared"}
            _save_progress(name, held, progress)
        if progress["phase"] != "released":
            if not shared or not lease._same_holder_ref(shared.get("holder"), holder):
                raise RuntimeError(f"Shared ownership of '{name}' changed; local claim is retained")
            if shared.get("state") == "leased" and shared.get("token") == held.lease_token:
                if not coordination.release(name, held.lease_token).ok:
                    raise RuntimeError(f"Shared return of '{name}' is unconfirmed; local claim is retained")
            elif shared.get("state") != "released" or shared.get("live") is not False:
                raise RuntimeError(f"Shared ownership of '{name}' changed; local claim is retained")
            progress = {**progress, "phase": "released"}
            _save_progress(name, held, progress)
        if name in refs and not coordination.settle_obligation(name, holder, released=True, confirmed=True):
            raise RuntimeError(f"Original-owner settlement of '{name}' is unconfirmed; local claim is retained")
    with lease._lease_lock():
        records = lease._read_records(lease.LEASE_FILE, lease.Lease, fail_closed=True)
        if records.get(name) != held:
            raise RuntimeError(f"Local ownership of '{name}' changed during return; current claim is retained")
        del records[name]
        pending = pending_returns()
        pending.pop(name, None)
        write_leases(records, pending)
        return True


def _save_progress(name: str, held: lease.Lease, progress: dict) -> None:
    with lease._lease_lock():
        records = lease._read_records(lease.LEASE_FILE, lease.Lease, fail_closed=True)
        if records.get(name) != held:
            raise RuntimeError(f"Local ownership of '{name}' changed during return; current claim is retained")
        pending = pending_returns()
        pending[name] = progress
        write_leases(records, pending)
