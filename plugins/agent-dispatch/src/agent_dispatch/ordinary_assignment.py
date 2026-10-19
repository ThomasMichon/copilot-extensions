"""Exact ground-owner reads for an already assigned ordinary Bridge worktree."""

from __future__ import annotations

import json
import subprocess

from .bridge import BridgeCarriedSessionBusy
from .ordinary_agent_facts import owner_record
from .procutil import (
    agent_worktrees_environment,
    agent_worktrees_launch_prefix,
    no_window_kwargs,
)
from .queue_records import TaskError


def read_owner(reservation: dict, task_id: str, *, timeout: float) -> dict:
    prefix = agent_worktrees_launch_prefix()
    if not prefix:
        raise BridgeCarriedSessionBusy("exact ground owner capability is unavailable")
    try:
        result = subprocess.run(
            [*prefix, "--project", reservation["allocation_project"], "record",
             "--worktree-id", reservation["worktree"], "--json"],
            check=False, capture_output=True, text=True, timeout=timeout,
            env=agent_worktrees_environment(), **no_window_kwargs(),
        )
        if result.returncode:
            raise BridgeCarriedSessionBusy(
                result.stderr or result.stdout or "exact ground owner record is unavailable"
            )
        result_data = json.loads(result.stdout)
        if (
            not isinstance(result_data, dict)
            or type(result_data.get("version")) is not int
            or result_data["version"] != 1
            or result_data.get("ok") is not True
            or not isinstance(result_data.get("worktree"), dict)
        ):
            raise TaskError("ground owner must return successful exact-record JSON version 1")
        return owner_record(result_data["worktree"], reservation, task_id)
    except (ValueError, TaskError, OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeCarriedSessionBusy(f"exact ground owner record is unproven: {exc}") from exc


def current_task(client, task: dict) -> dict:
    """Read admission without rewriting a held, claimed, or changed task."""
    from .client import DispatchError

    if not callable(getattr(client, "get", None)):
        raise BridgeCarriedSessionBusy("coordinator cannot read current assignment facts")
    try:
        current = client.get(task["id"])
    except DispatchError as exc:
        raise BridgeCarriedSessionBusy(f"assigned task cannot be read: {exc}") from exc
    if (
        not isinstance(current, dict) or current.get("id") != task["id"]
        or current.get("generation") != task["generation"]
        or current.get("status") != "queued"
        or current.get("owner") is not None
        or current.get("hold_reason") is not None
    ):
        raise BridgeCarriedSessionBusy("ordinary assignment admission fence changed")
    return current
