"""Select CLI session-origin transport before reservation; replay sealed facts."""

from __future__ import annotations

import copy
import json
import os
import subprocess

from .bridge_owner_launch import retained_selection
from .procutil import OutputLimitExceeded, run_background_capture
from .worktree_cohort import _carrier_json

FIELDS = ("session_origin", "session_origin_reservation_key", "session_origin_transport")


def snapshot(reservation: dict) -> dict:
    return {f"spawn_{name}": copy.deepcopy(reservation.get(name)) for name in FIELDS}


def _read(prefix: tuple[str, ...], *arguments: str) -> dict:
    result = run_background_capture(
        [*prefix, "--json", *arguments], timeout=5, max_output=64 * 1024,
        env={**os.environ, "AGENT_BRIDGE_CLIENT_NO_ENSURE": "1"},
    )
    value = json.loads(result.stdout) if result is not None and not result.returncode else None
    if not isinstance(value, dict):
        raise ValueError("Bridge origin support is unavailable")
    return value


def require_support(prefix: tuple[str, ...]) -> None:
    support = _read(prefix, "version", "--contracts-json")
    contracts = support.get("contracts")
    acquire = contracts.get("agent-bridge.acquire") if isinstance(contracts, dict) else None
    if not (
        support.get("schema") == "agent-bridge.cli-contracts"
        and type(support.get("version")) is int and support["version"] == 1
        and isinstance(acquire, dict)
        and type(acquire.get("version")) is int and acquire["version"] == 1
        and isinstance(acquire.get("capabilities"), list)
        and "dispatch-cli-session-origin" in acquire["capabilities"]
    ):
        raise ValueError("executing Bridge CLI lacks CLI session-origin support")
    health = _read(prefix, "status")
    capabilities = health.get("capabilities")
    if not (
        health.get("service") == "agent-bridge" and health.get("status") == "ok"
        and health.get("ready") is True and health.get("draining") is False
        and isinstance(capabilities, dict)
        and type(capabilities.get("dispatch_cli_session_origin")) is int
        and capabilities["dispatch_cli_session_origin"] == 1
    ):
        raise ValueError("executing Bridge daemon lacks CLI session-origin support")


def reservation_options(client) -> dict:
    """Old or unavailable providers retain the unchanged baseline before effects."""
    from .client_errors import DispatchError

    observe = getattr(client, "pool_placement_contract", None)
    if not callable(observe):
        return {}
    try:
        versions = observe().get("session_origin_reservation_versions")
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            return {}
        require_support(retained_selection())
    except (DispatchError, OSError, subprocess.SubprocessError, OutputLimitExceeded,
            ValueError, TypeError, ImportError):
        return {}
    return {"session_origin_transport": "origin-v1"}


def acquisition(task: dict, mode: str) -> tuple[list[str] | None, dict | None]:
    """A selected attempt never downgrades after lost replies or provider rollback."""
    from .bridge import BridgeCarriedSessionBusy

    transport = task.get("spawn_session_origin_transport")
    if transport in (None, "baseline"):
        return None, None
    origin = task.get("spawn_session_origin")
    key = task.get("spawn_reservation_key")
    if not (
        transport == "origin-v1" and mode == "cli"
        and isinstance(origin, dict) and set(origin) == {"task_id", "reservation_key", "cohort"}
        and origin["task_id"] == task.get("id") and origin["reservation_key"] == key
        and task.get("spawn_session_origin_reservation_key") == key
        and _carrier_json(origin["cohort"]) is not None
        and (origin["cohort"]["emitter"] is None or origin["cohort"]["emitter"]["task_id"] == origin["task_id"])
        and (origin["cohort"]["pool"] is None or (
            origin["cohort"]["pool"]["task_id"] == origin["task_id"]
            and origin["cohort"]["pool"]["reservation_key"] == key
        ))
    ):
        raise BridgeCarriedSessionBusy("sealed CLI session origin is unconfirmed; preserve the reservation")
    try:
        prefix = retained_selection()
        require_support(prefix)
    except (OSError, subprocess.SubprocessError, OutputLimitExceeded, ValueError, TypeError, ImportError) as exc:
        raise BridgeCarriedSessionBusy(f"sealed CLI session origin cannot be replayed: {exc}") from exc
    return list(prefix), copy.deepcopy(origin)
