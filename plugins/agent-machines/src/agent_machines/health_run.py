"""Policy-approved one-shot collection, sharing admission and transaction owners."""

from __future__ import annotations

import hmac
import re
import time
from pathlib import Path

from . import health_collect as collector, health_slots as slots

SCHEMA = "agent-machines.health.run"
CONTRACT = "agent-machines.health.collection/v1"
RESPONSE_MARGIN = .25


def policy_preview(store: Path) -> tuple[dict, str, str]:
    store = slots._plain(store, directory=True)
    policy, policy_hash = slots._read(store / "policy.json", slots.validate_policy)
    if policy["version"] != 2:
        raise slots.HealthError("collection-store-unsupported")
    info = store.stat()
    token = slots._digest({
        "contract": CONTRACT, "store": str(store), "identity": [info.st_dev, info.st_ino],
        "policy": policy_hash,
    })
    return policy, policy_hash, token


def _reason(error):
    from agent_procutil.owned_process import OwnedCleanupError

    if isinstance(error, OwnedCleanupError):
        return "collection-cleanup-unconfirmed"
    if isinstance(error, slots.HealthError):
        return str(error)
    if isinstance(error, TimeoutError):
        return "collection-timeout"
    return "collection-unavailable"


def _finish(result):
    requested = [row for row in result["targets"] if row["requested"]]
    completed = sum(row["transaction"] == "recorded" for row in requested)
    result["status"] = (
        "completed" if completed == len(requested) and result["qualification"] == "within-budget"
        else "partial" if completed else "failed"
    )
    return result


def run(store: Path, *, apply=False, expected="") -> dict:
    if expected and not apply:
        raise slots.HealthError("confirmation-requires-apply")
    if apply and re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise slots.HealthError("policy-token-required")
    deadline = time.monotonic() + collector.COLLECTION_SECONDS
    started_ms = time.time_ns() // 1_000_000
    current_hour = started_ms // slots.HOUR_MS
    policy, policy_hash, token = policy_preview(store)
    if apply and not hmac.compare_digest(expected, token):
        raise slots.HealthError("store-policy-changed")
    targets = [{
        "hour": hour, "component": component, "requested": requested,
        "collection": "not-attempted" if requested else "not-requested",
        "transaction": "not-attempted" if requested else "not-requested",
        "applied": False, "changed": False, "reason": "none",
    } for hour, component, requested in (
        (current_hour, "observation", policy["collection"]["bridge_port"] is not None),
        (current_hour - 1, "services", bool(policy["collection"]["services"])),
    )]
    result = {
        "schema": SCHEMA, "version": 1, "status": "preview", "policy_token": token,
        "current_hour": current_hour, "targets": targets, "qualification": "not-executed",
        "carrier_publication": "not-requested",
    }
    if not apply:
        return result
    from agent_procutil.owned_process import OwnedCleanupError

    def window():
        now_ms = time.time_ns() // 1_000_000
        if now_ms < started_ms or now_ms // slots.HOUR_MS != current_hour:
            raise slots.HealthError("collection-hour-changed")
        if time.monotonic() >= deadline - RESPONSE_MARGIN:
            raise slots.HealthError("collection-timeout")

    def guard():
        window()
        if not hmac.compare_digest(expected, policy_preview(store)[2]):
            raise slots.HealthError("store-policy-changed")
        window()

    active = cleanup_error = None
    result["qualification"] = "within-budget"
    try:
        with collector.admission(
            store, uncertain=lambda: any(row["transaction"] == "unknown" for row in targets),
        ) as begin_exports:
            guard()
            for active in targets:
                if not active["requested"]:
                    continue
                try:
                    guard()
                    candidate = collector.collect_target(
                        store, active["hour"], current_hour=current_hour, started_ms=started_ms,
                        deadline=deadline, policy=policy, policy_hash=policy_hash, begin_exports=begin_exports,
                    )
                    active["collection"] = candidate["coverage"][active["component"]]
                    guard()
                    # A crash/unknown commit must fence automatic invocations too.
                    begin_exports()
                    active.update(transaction="unknown", applied=None, changed=None)
                    transaction = slots.record_collected(
                        store, candidate, now_ms=time.time_ns() // 1_000_000, guard=guard,
                        deadline=deadline - RESPONSE_MARGIN,
                    )
                    active.update(
                        transaction=transaction["status"], applied=transaction["applied"],
                        changed=transaction["changed"],
                    )
                    if transaction["status"] == "unknown":
                        result["qualification"] = "write-unconfirmed"
                        break
                except OwnedCleanupError:
                    raise
                except (ValueError, OSError, OverflowError) as error:
                    active["reason"] = _reason(error)
                    if active["collection"] == "not-attempted":
                        active["collection"] = "failed"
                    else:
                        active.update(transaction="refused", applied=False, changed=False)
                    guard()
            guard()
    except (ValueError, OSError, OverflowError, OwnedCleanupError) as error:
        reason = _reason(error)
        if isinstance(error, OwnedCleanupError):
            cleanup_error = error
        result["qualification"] = reason
        if active is not None and active["requested"]:
            active["reason"] = reason
            if active["collection"] == "not-attempted":
                active["collection"] = "failed"
    except BaseException as error:
        result["qualification"] = "collection-interrupted"
        error.health_run_result = _finish(result)
        raise
    try:
        guard()
    except (ValueError, OSError, OverflowError) as error:
        # Settlement and filesystem calls may finish late; retain committed facts.
        result["qualification"] = _reason(error)
    _finish(result)
    if cleanup_error is not None:
        cleanup_error.health_run_result = result
        raise cleanup_error
    return result
