"""Confirm shared return and original-owner settlement before local cleanup."""
from __future__ import annotations

import base64
import json
import math
import os
from dataclasses import asdict
from datetime import datetime

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
                or not isinstance(progress, dict)
                or not {"holder", "lease", "phase"} <= set(progress) <= {
                    "holder", "lease", "phase", "obligation_active", "lease_id",
                }
                or type(progress.get("obligation_active", False)) is not bool
                or "lease_id" in progress and not _generation(progress)
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


def _generation(record: dict) -> str | None:
    value = record.get("lease_id")
    return value if (
        isinstance(value, str) and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    ) else None


def _resource_matches(record: dict, name: str) -> bool:
    item = record.get("resource")
    encoded = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")
    return isinstance(item, dict) and item == {
        "kind": coordination.KIND, "key": name, "identity": f"{coordination.KIND}:{encoded}",
    }


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) != 20:
        raise ValueError
    result = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    if result.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError
    return result


def _confirmed_generation_return(record: dict | None, name: str, generation: str | None, holder: str) -> bool:
    if not record or not generation or not _resource_matches(record, name):
        return False
    current = _generation(record)
    version = record.get("schema_version")
    if (
        not current or type(version) is not int or version not in (1, 2)
        or record.get("state") not in ("leased", "released") or type(record.get("live")) is not bool
        or not lease._same_holder_ref(record.get("holder"), record.get("holder"))
        or record["state"] == "released" and record["live"]
    ):
        return False
    try:
        renewed = _timestamp(record.get("renewed_at"))
        if version == 1:
            return record["state"] == "released" and current == generation and record["holder"] == holder
        proofs = record.get("returned_generations")
        if not isinstance(proofs, list) or len(proofs) > 16:
            return False
        seen, previous, matched = set(), None, False
        for proof in proofs:
            if not isinstance(proof, dict) or set(proof) != {"lease_id", "holder", "released_at"}:
                return False
            identity = _generation(proof)
            released = _timestamp(proof["released_at"])
            if (
                not identity or identity in seen or released > renewed
                or previous is not None and released < previous
                or not lease._same_holder_ref(proof["holder"], proof["holder"])
            ):
                return False
            seen.add(identity)
            previous = released
            matched |= identity == generation and proof["holder"] == holder
        if record["state"] == "leased" and current in seen:
            return False
        if record["state"] == "released" and (
            not proofs or proofs[-1] != {
                "lease_id": current, "holder": record["holder"], "released_at": record["renewed_at"],
            }
        ):
            return False
        return matched
    except (ValueError, TypeError, OverflowError):
        return False


def _original_holder(name: str, held: lease.Lease, shared: dict) -> str | None:
    holder = shared.get("holder")
    if not isinstance(holder, str) or not lease._same_holder_ref(holder, holder):
        return None
    owner = lease._claim_owner(held)
    same_epoch = (
        shared.get("state") == "leased" and shared.get("token") == held.lease_token
        and len(held.lease_token) in (40, 64)
        and all(character in "0123456789abcdef" for character in held.lease_token)
    )
    if (
        held.host == lease._this_host()
        and (lease.resolve_owner_worktree() == owner or same_epoch)
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
    if not isinstance(owners, list):
        return None
    matches = [
        item for item in owners if isinstance(item, dict)
        and (
            same_epoch
            or not os.path.isabs(owner) and os.path.normpath(owner) == item.get("worktree_id")
            or isinstance(path := item.get("worktree_path") or item.get("path"), str)
            and os.path.isabs(path) == os.path.isabs(owner)
            and os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(owner))
        )
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
        shared = None if progress else coordination.inspect(name)
        if progress:
            holder = progress["holder"]
        else:
            holder = _original_holder(name, held, shared) if shared else None
        if not holder:
            raise RuntimeError(f"Return of '{name}' is unconfirmed; original owner and local claim are retained")
        from .driving_worktrees import _qualified_claim_states

        states = _qualified_claim_states(holder)
        obligation_active = states.get(name) in ("active", "at-rest")
        released = progress and progress["phase"] == "released"
        same_epoch = shared and (
            shared.get("state") == "leased" and shared.get("token") == held.lease_token
            and lease._same_holder_ref(shared.get("holder"), holder)
        )
        if not progress and not same_epoch:
            raise RuntimeError(f"Shared ownership of '{name}' changed; local claim is retained")
        if not progress:
            progress = {"holder": holder, "lease": asdict(held), "phase": "prepared"}
            if _resource_matches(shared, name) and (generation := _generation(shared)):
                progress["lease_id"] = generation
            _save_progress(name, held, progress)
        if not released:
            shared = shared if shared is not None else coordination.inspect(name)
            if not _confirmed_generation_return(shared, name, progress.get("lease_id"), holder):
                if not shared or not (
                    shared.get("state") == "leased" and shared.get("token") == held.lease_token
                    and lease._same_holder_ref(shared.get("holder"), holder)
                ):
                    raise RuntimeError(f"Shared ownership of '{name}' changed; local claim is retained")
                if _resource_matches(shared, name) and (generation := _generation(shared)):
                    if progress.get("lease_id") not in (None, generation):
                        raise RuntimeError(f"Shared generation of '{name}' changed; local claim is retained")
                    if "lease_id" not in progress:
                        progress = {**progress, "lease_id": generation}
                        _save_progress(name, held, progress)
                readiness = coordination.preflight(holder) if progress.get("lease_id") else None
                if readiness is not None and readiness.ready and (
                    coordination.RETURN_PROOF_CAPABILITY in readiness.capabilities
                ):
                    result = coordination.release(
                        name, held.lease_token, retain_return_proof=True, preflight_result=readiness,
                    )
                else:
                    result = coordination.release(name, held.lease_token)
                if not result.ok:
                    raise RuntimeError(f"Shared return of '{name}' is unconfirmed; local claim is retained")
            progress = {**progress, "phase": "released"}
            try:
                _save_progress(name, held, progress)
            except OSError:
                # The release ACK is still known in this invocation. Finish
                # through the original native ledger and durable local cleanup.
                pass
        if obligation_active and not coordination.settle_obligation(name, holder, released=True, confirmed=True):
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
