"""Provisional exact owner checks across the engine process boundary."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from uuid import UUID

from . import engine_client
from .restart_observer import HostEpoch


_EXPECTED_FIELDS = (
    "project", "worktree_id", "worktree_path", "native_session_id", "worktree_incarnation",
    "head_revision", "owner_revision", "activation_ordinal", "source_identity",
)


def _acknowledged(result: dict, request: dict) -> bool:
    expected = request["expected"]
    value = result.get("acknowledgement")
    return (
        result.get("state") == "seed-acknowledged"
        and result.get("seed_acknowledged") is True
        and isinstance(value, dict)
        and set(value) == {
            "native_session_id", "target_epoch", "source_identity", "native_incarnation",
            "session_incarnation", "head_revision", "owner_revision", "activation_ordinal",
            "status_reconciled",
        }
        and value["native_session_id"] == expected["native_session_id"]
        and value["target_epoch"] == expected["target_epoch"]
        and value["status_reconciled"] is True
        and type(value["head_revision"]) is int
        and value["head_revision"] == expected["head_revision"]
        and all(
            type(value[field]) is int and expected[field] < value[field] <= 2**63 - 1
            for field in ("owner_revision", "activation_ordinal")
        )
        and all(
            isinstance(value[field], str) and re.fullmatch(pattern, value[field]) is not None
            for field, pattern in (
                ("source_identity", r"native-source-v1:[0-9a-f]{64}"),
                ("native_incarnation", r"[1-9][0-9]{0,9}:[0-9]{1,32}"),
                ("session_incarnation", r"[A-Za-z0-9.$_:-]{1,128}"),
            )
        )
        and value["source_identity"] != expected["source_identity"]
    )


def _attempt(
    attempt_id: str, attempts: list[dict], epoch: HostEpoch | None, *, historical: bool = False,
) -> tuple[dict, dict]:
    if str(UUID(attempt_id)) != attempt_id:
        raise ValueError("Current owner inspection requires a canonical attempt UUID")
    attempt = next((item for item in attempts if item["attempt_id"] == attempt_id), None)
    if attempt is None:
        raise ValueError("Frozen restart attempt not found")
    fields = ("platform", "environment_id") if historical else ("platform", "environment_id", "boot_id")
    if epoch is None or any(attempt["target_epoch"][key] != asdict(epoch)[key] for key in fields):
        raise ValueError("Frozen restart attempt target epoch no longer matches")
    evidence = attempt["last_positive"]
    expected = {field: evidence[field] for field in _EXPECTED_FIELDS}
    expected["target_epoch"] = attempt["target_epoch"]
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
    attempt, expected = _attempt(attempt_id, attempts, epoch, historical=True)
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
        } | ({"acknowledgement"} if result.get("version") == 2 else set())
        or result["schema"] != "native-restart-launch"
        or type(result["version"]) is not int or result["version"] not in {1, 2}
        or json.dumps(result["request"], sort_keys=True, allow_nan=False) != payload
        or result["restored"] is not False or result["automatic_eligible"] is not False
        or (
            result["version"] == 1 and (
                not isinstance(result["state"], str)
                or result["state"] not in {"not-reserved", "launch-uncertain", "mux-created"}
                or result["seed_acknowledged"] is not False
            )
            or result["version"] == 2 and not _acknowledged(result, request)
        )
    ):
        raise ValueError("Restart inspection received an unconfirmed launch receipt")
    return result
