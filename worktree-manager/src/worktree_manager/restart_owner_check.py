"""Provisional exact owner checks across the engine process boundary."""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import UUID

from . import engine_client
from .restart_observer import HostEpoch


_EXPECTED_FIELDS = (
    "project", "worktree_id", "worktree_path", "native_session_id", "worktree_incarnation",
    "head_revision", "owner_revision", "activation_ordinal", "source_identity",
)


def _attempt(attempt_id: str, attempts: list[dict], epoch: HostEpoch | None) -> tuple[dict, dict]:
    if str(UUID(attempt_id)) != attempt_id:
        raise ValueError("Current owner inspection requires a canonical attempt UUID")
    attempt = next((item for item in attempts if item["attempt_id"] == attempt_id), None)
    if attempt is None:
        raise ValueError("Frozen restart attempt not found")
    if epoch is None or attempt["target_epoch"] != asdict(epoch):
        raise ValueError("Frozen restart attempt target epoch no longer matches")
    evidence = attempt["last_positive"]
    expected = {field: evidence[field] for field in _EXPECTED_FIELDS}
    expected["target_epoch"] = asdict(epoch)
    return attempt, expected


def inspect_attempt(attempt_id: str, attempts: list[dict], epoch: HostEpoch | None) -> dict:
    """Check one already-frozen identity; never interpret a snapshot as admission."""
    _, expected = _attempt(attempt_id, attempts, epoch)
    payload = json.dumps(expected, sort_keys=True, allow_nan=False)
    if len(payload.encode("utf-8")) > 8192:
        raise ValueError("Restart worktree expectations exceed their budget")
    result = engine_client.run_json(
        expected["project"], ["session-role", "--restart-check", payload, "--json"],
        timeout=4,
        environment={"WORKTREE_NO_UPDATE": "1", "WORKTREE_NO_RECONCILE": "1"},
    )
    if (
        result.get("schema") != "native-restart-worktree-check"
        or type(result.get("version")) is not int or result["version"] != 1
        or json.dumps(result.get("expected"), sort_keys=True, allow_nan=False) != payload
        or type(result.get("worktree_qualified")) is not bool
        or result.get("launch_authorized") is not False
        or result.get("automatic_eligible") is not False
        or not isinstance(result.get("reason"), str) or not result["reason"]
        or len(result["reason"]) > 256
        or result["worktree_qualified"] and result["reason"] != "current-worktree-matches"
    ):
        raise ValueError("Restart inspection received an unconfirmed owner response")
    return result


def inspect_launch(attempt_id: str, attempts: list[dict], epoch: HostEpoch | None) -> dict:
    """Compose the frozen Manager identity with its durable owner launch outcome.

    No Manager attempt is reset or advanced by this inspection. An absent or
    uncertain owner receipt cannot authorize a retry.
    """
    attempt, expected = _attempt(attempt_id, attempts, epoch)
    evidence = attempt["last_positive"]
    request = {
        "request_id": attempt_id, "expected": expected,
        "source_epoch": {field: evidence[field] for field in (
            "platform", "environment_id", "boot_id",
        )},
        "native_incarnation": evidence["native_incarnation"],
    }
    payload = json.dumps(request, sort_keys=True, allow_nan=False)
    if len(payload.encode("utf-8")) > 8192:
        raise ValueError("Restart launch request exceeds its budget")
    result = engine_client.run_json(
        expected["project"], [
            "embody", "--worktree-id", expected["worktree_id"],
            "--restart-request", payload, "--restart-receipt", "--json",
        ],
        timeout=4,
        environment={"WORKTREE_NO_UPDATE": "1", "WORKTREE_NO_RECONCILE": "1"},
    )
    if (
        set(result) != {
            "schema", "version", "request", "state", "seed_acknowledged",
            "restored", "automatic_eligible",
        }
        or result["schema"] != "native-restart-launch"
        or type(result["version"]) is not int or result["version"] != 1
        or json.dumps(result["request"], sort_keys=True, allow_nan=False) != payload
        or not isinstance(result["state"], str)
        or result["state"] not in {"not-reserved", "launch-uncertain", "mux-created"}
        or any(result[field] is not False for field in (
            "seed_acknowledged", "restored", "automatic_eligible",
        ))
    ):
        raise ValueError("Restart inspection received an unconfirmed launch receipt")
    return result
