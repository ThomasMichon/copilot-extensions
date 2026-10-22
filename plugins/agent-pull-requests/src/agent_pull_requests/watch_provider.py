"""Bounded native snapshot bridge; provider policy remains owned by Worktrees."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from .watch_contract import PRSnapshot
from .watch_process import run_owned

NATIVE_SNAPSHOTS = "native_snapshots/v1"
CONTINUOUS_NOTIFICATIONS = "continuous_notifications/v1"
_MAX_SNAPSHOT_BYTES = 1_048_576
_FINGERPRINTS = ("votes", "approvals", "discussions", "policies", "gates")


def target_id(snapshot: PRSnapshot) -> str:
    """Bind observation to its provider endpoint, repository and PR, not a short slug."""
    value = snapshot.native_snapshot or {}
    observation = snapshot.observation or {}
    identity = [
        snapshot.provider, value.get("api_base", observation.get("api_base", "")).rstrip("/"),
        value.get("repo"), value.get("pr"),
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _run_native(argv: list[str]) -> subprocess.CompletedProcess:
    return run_owned(argv, timeout=20.0)


def command_path(value: object) -> str:
    """Require the caller's attributable executable, never a PATH substitute."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("an absolute worktrees command is required")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("the worktrees command is unavailable")
    return str(path)


def decode_snapshot(data: object, repo: str, number: int) -> PRSnapshot:
    """Validate the versioned native envelope without evaluating provider rules."""
    if (
        not isinstance(data, dict)
        or data.get("schema") != "agent-worktrees.pr-snapshot"
        or type(data.get("version")) is not int or data["version"] != 1
        or data.get("repo") != repo
        or type(data.get("pr")) is not int or data["pr"] != number
        or data.get("pr_state") not in ("open", "closed")
        or type(data.get("merged")) is not bool
        or data.get("mergeable") is not None and type(data["mergeable"]) is not bool
        or any(not isinstance(data.get(field), str) for field in (
            "provider", "head_sha", "base_ref", "checks_state", "title",
        ))
        or type(data.get("draft")) is not bool
    ):
        raise ValueError("invalid native snapshot envelope")
    observation = data.get("observation")
    if "reviews" in data and (
        not isinstance(data["reviews"], list)
        or any(not isinstance(review, dict) for review in data["reviews"])
    ):
        raise ValueError("invalid native snapshot reviews")
    if (
        any(name in data and not isinstance(data[name], str)
            for name in ("api_base", "author", "cursor"))
        or "checks_complete" in data and data["checks_complete"] is not None
        and type(data["checks_complete"]) is not bool
        or "check_failures" in data and (
            not isinstance(data["check_failures"], list)
            or any(not isinstance(failure, dict) for failure in data["check_failures"])
        )
    ):
        raise ValueError("invalid native snapshot details")
    if observation is not None:
        if (
            not isinstance(observation, dict)
            or observation.get("schema") != "agent-worktrees.pr-observation"
            or type(observation.get("version")) is not int or observation["version"] != 1
            or observation.get("repo") != repo
            or type(observation.get("number")) is not int or observation["number"] != number
            or observation.get("provider") != data["provider"]
            or observation.get("head_sha") != data["head_sha"]
            or type(observation.get("complete")) is not bool
            or not isinstance(observation.get("fingerprints"), dict)
            or any(
                not isinstance(observation["fingerprints"].get(field), str)
                or not observation["fingerprints"][field]
                for field in _FINGERPRINTS
            )
            or any(not isinstance(observation.get(field), list) for field in (
                "votes", "approval_events", "discussions", "policies", "gates", "errors",
            ))
        ):
            raise ValueError("invalid native snapshot observation")
    try:
        encoded = json.dumps(data, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("invalid native snapshot JSON") from None
    if len(encoded.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("native snapshot exceeds its size budget")
    mergeable = data["mergeable"]
    return PRSnapshot(
        pr_state=data["pr_state"], merged=data["merged"], head_sha=data["head_sha"],
        mergeable="" if mergeable is None else ("MERGEABLE" if mergeable else "CONFLICTING"),
        checks_state=data["checks_state"], provider=data["provider"],
        observation=copy.deepcopy(observation),
        native_snapshot={
            field: copy.deepcopy(data[field])
            for field in (
                "schema", "version", "repo", "pr", "provider", "pr_state", "merged",
                "head_sha", "base_ref", "mergeable", "checks_state", "title", "draft",
                "observation",
                "reviews",
                "api_base", "author", "cursor", "checks_complete", "check_failures",
            ) if field in data
        },
    )


def fetch_snapshot(
    repo: str, number: int, command: str, *, prefix: list[str] | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
) -> PRSnapshot:
    """Perform exactly one native configured read; never start another waiter."""
    if prefix is None:
        launch = [command_path(command)]
    else:
        if not prefix or any(not isinstance(argument, str) or not argument for argument in prefix):
            raise ValueError("invalid attributed native peer prefix")
        command_path(prefix[0])
        launch = list(prefix)
    if not repo or any(character in repo for character in "\0\r\n&|<>^%!"):
        raise ValueError("invalid native repository target")
    try:
        result = (runner or _run_native)(
            [*launch, "pr-watch", "snapshot", repo, str(number), "--json"],
        )
        if result.returncode or len(result.stdout.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            raise ValueError("native snapshot command failed or exceeded its budget")
        return decode_snapshot(json.loads(result.stdout), repo, number)
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError):
        raise ValueError("native snapshot command is unavailable or incompatible") from None
