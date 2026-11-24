"""Optional native batch codec, using the existing attributed process owner."""

from __future__ import annotations

from datetime import datetime
import json
import math
import time
from typing import TYPE_CHECKING

from agent_procutil.owned_budget import OwnedExecutionBudget

from .filtered_state import Checkpoint, Document
from .repository_native import RepositoryNativeUnavailable, RepositoryNativeUnsupported, _invoke
from .repository_projection import validate_identity
from .snapshot_batch_state import PROTOCOL, _instant
from .watch_contract import PRSnapshot
from .watch_provider import decode_snapshot
from .watch_replay_store import json_object

if TYPE_CHECKING:
    from .watch_observer import NativeObservationBinding


def qualify(value: object) -> None:
    if not isinstance(value, dict) or any(value.get(name) != expected for name, expected in {
        "protocol": PROTOCOL, "command": ["pr-watch", "snapshot-batch"], "providers": ["github"],
        "api_bases": ["https://api.github.com"], "credential": "observe/per-request",
        "source_config_api_bases": ["https://github.com", "https://api.github.com"],
        "effects": "query-only", "scalar_default": "unchanged",
    }.items()) or (
        any(value.get(name) is not True for name in (
            "caller_backoff_persistence_required", "requires_binary_qualification",
        ))
        or any(type(value.get(name)) is not int or value[name] != expected for name, expected in {
            "max_members": 10, "page_size": 100, "max_query_nodes": 8192,
            "expected_query_cost": 1, "max_requests": 64, "capture_bytes": 1_048_576,
        }.items())
    ):
        raise RepositoryNativeUnsupported("selected native snapshot batches unsupported")


def _integer(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _text(value: object, maximum: int = 128) -> bool:
    return isinstance(value, str) and len(value) <= maximum and all(ord(c) >= 32 for c in value)


def decode(
    raw: str, binding: NativeObservationBinding, numbers: tuple[int, ...],
) -> tuple[dict[int, PRSnapshot | None], Document | None, bool]:
    if len(raw.encode()) > 1_048_576:
        raise ValueError("native batch exceeds its capture bound")
    value = json_object(raw.encode())
    identity = binding.repository_identity
    assert identity is not None
    if set(value) != {
        "schema", "version", "provider", "api_base", "endpoint", "repo", "results", "quota",
        "backoff", "credential_acquisitions", "query_attempts", "validated_primary_points", "stop_reason",
    } or (
        value["schema"] != "agent-worktrees.pr-snapshot-batch"
        or type(value["version"]) is not int or value["version"] != 1
        or (value["provider"], value["api_base"], value["repo"], value["endpoint"]) != (
            identity.provider, "https://api.github.com", identity.repo, "https://api.github.com/graphql",
        )
        or not _text(value["stop_reason"])
        or any(not _integer(value[name], 64) for name in (
            "credential_acquisitions", "query_attempts", "validated_primary_points",
        ))
        or value["validated_primary_points"] > value["query_attempts"]
        or value["credential_acquisitions"] > value["query_attempts"]
    ):
        raise ValueError("invalid native batch envelope")
    quota = value["quota"]
    if quota is not None:
        if not isinstance(quota, dict) or set(quota) != {"cost", "remaining", "reset_at"} or (
            type(quota["cost"]) is not int or quota["cost"] != 1
            or not _integer(quota["remaining"], 2**31 - 1) or not _text(quota["reset_at"], 64)
        ):
            raise ValueError("invalid native batch quota")
        instant = datetime.fromisoformat(quota["reset_at"].replace("Z", "+00:00"))
        if instant.tzinfo is None or not _instant(instant.timestamp()):
            raise ValueError("invalid native batch quota reset")
    backoff = value["backoff"]
    if backoff is not None and (
        not isinstance(backoff, dict) or set(backoff) != {"resource", "not_before", "reason"}
        or backoff["resource"] not in ("graphql-primary", "shared-secondary")
        or not _instant(backoff["not_before"]) or not _text(backoff["reason"])
        or not backoff["reason"]
    ):
        raise ValueError("invalid native batch backoff")
    if quota is not None and quota["remaining"] == 0 and (
        backoff is None or backoff["resource"] != "graphql-primary"
        or backoff["not_before"] < instant.timestamp()
    ):
        raise ValueError("exhausted query quota lacks durable backoff evidence")
    rows = value["results"]
    if not isinstance(rows, list) or len(rows) != len(numbers):
        raise ValueError("native batch omitted requested members")
    result: dict[int, PRSnapshot | None] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"pr", "outcome", "reason", "snapshot"} or (
            type(row["pr"]) is not int or row["pr"] not in numbers or row["pr"] in result
            or row["outcome"] not in ("complete", "unavailable") or not _text(row["reason"])
            or (row["outcome"] == "complete") != (row["reason"] == "")
            or row["outcome"] == "unavailable" and row["snapshot"] is not None
        ):
            raise ValueError("invalid native batch member")
        snapshot = None
        if row["outcome"] == "complete":
            snapshot = decode_snapshot(row["snapshot"], binding.repo, row["pr"])
            native = validate_identity(snapshot, identity, row["pr"])
            if native.get("reviews_complete") is not True or native.get("checks_complete") is not True:
                raise ValueError("native batch member lacks complete collections")
        result[row["pr"]] = snapshot
    resolved = backoff is not None or not value["stop_reason"] and quota is not None
    return result, backoff, resolved


def acquire(
    binding: NativeObservationBinding, numbers: tuple[int, ...], *, checkpoint: Checkpoint,
    deadline: float, on_admitted: Checkpoint,
) -> tuple[dict[int, PRSnapshot | None], Document | None, bool]:
    budget = OwnedExecutionBudget(deadline - 1, 2)
    remaining = min(60, math.floor(budget.execution_deadline - time.monotonic()))
    if remaining < 1:
        raise RepositoryNativeUnavailable(qualified=True)
    if binding.snapshot_batch != PROTOCOL or not 1 <= len(numbers) <= 10 or (
        len(set(numbers)) != len(numbers)
        or any(type(number) is not int or not 1 <= number <= 2_147_483_647 for number in numbers)
    ):
        raise ValueError("invalid selected snapshot batch")
    prefix = list(binding.prefix or (binding.command,))
    result = _invoke([
        *prefix, "pr-watch", "snapshot-batch", binding.repo, *(str(number) for number in numbers),
        "--config", str(binding.inventory_config),
        "--credential-command", json.dumps(binding.credential_command),
        "--max-credential-requests", "64", "--seconds", str(remaining), "--json",
    ], timeout=remaining, checkpoint=checkpoint, qualified=True,
        budget=budget, on_admitted=on_admitted)
    if result.returncode:
        raise RepositoryNativeUnavailable(qualified=True)
    return decode(result.stdout, binding, numbers)
