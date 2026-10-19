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


def inspect_attempt(attempt_id: str, attempts: list[dict], epoch: HostEpoch | None) -> dict:
    """Check one already-frozen identity; never interpret a snapshot as admission."""
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
    payload = json.dumps(expected, sort_keys=True, allow_nan=False)
    if len(payload.encode("utf-8")) > 8192:
        raise ValueError("Restart worktree expectations exceed their budget")
    result = engine_client.run_json(
        evidence["project"], ["session-role", "--restart-check", payload, "--json"],
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
