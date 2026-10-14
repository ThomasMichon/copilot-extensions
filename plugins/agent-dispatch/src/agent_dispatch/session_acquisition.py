"""Session acquisition is bridge-owned; allocation and terminal cleanup are not."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .procutil import agent_bridge_launch_prefix, no_window_kwargs


def spawn_snapshot(task: dict, reservation: dict, worktree: str, prepared: dict, ownership: str, replaced: bool) -> dict:
    return {
        **task, "spawn_worktree": worktree, "spawn_reservation_key": reservation["key"],
        "spawn_worktree_path": prepared["path"], "spawn_worktree_ownership": ownership,
        "spawn_allocation_project": prepared.get("project"),
        "spawn_allocation_reservation_key": reservation.get("allocation_reservation_key") or reservation["key"],
        "spawn_session_handle": None if replaced else reservation.get("session_handle"),
        "spawn_conversation_retired": reservation.get("conversation_retired"),
    }


def acquire(
    task: dict[str, Any], *, mode: str, prompt: str,
    agent: str = "task-worker", project: str | None = None,
    charter: str | None = None, driver: str = "agent-dispatch",
    verify_timeout: float = 0,
    wait: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    from .bridge import BridgeUnavailable, BridgeCarriedSessionBusy

    prefix = agent_bridge_launch_prefix()
    if prefix is None:
        raise BridgeUnavailable("agent-bridge acquisition capability is unavailable")
    key = task.get("spawn_reservation_key")
    worktree = task.get("spawn_worktree")
    ownership = task.get("spawn_worktree_ownership")
    if not key or not worktree or ownership not in {"created", "reused", "targeted"}:
        raise BridgeUnavailable("acquisition requires recorded worktree/reservation owner facts")
    execution_project = task.get("spawn_allocation_project")
    worktree_path = task.get("spawn_worktree_path")
    if not execution_project or not worktree_path:
        raise BridgeCarriedSessionBusy("acquisition requires owner-qualified workspace provenance")
    if project is not None and project != execution_project:
        raise BridgeCarriedSessionBusy("acquisition project conflicts with recorded workspace")
    carried = task.get("spawn_session_handle")
    prior = carried.removeprefix("local-body:") if isinstance(carried, str) else None
    if prior and prior.startswith("fleet-body:"):
        raise BridgeUnavailable("local acquisition cannot consume a fleet session handle")
    retired = task.get("spawn_conversation_retired")
    if retired is True:
        prior = None
    payload = {
        "version": 2, "target": agent, "mode": mode, "task_id": task["id"],
        "task_generation": task.get("generation"), "worktree_id": worktree,
        "allocation": ownership, "conversation_retired": retired,
        "intent": "automatic", "prior_session_id": prior,
        "delivery_id": f"{key}:acquire", "prompt": prompt, "charter": charter,
        "project": execution_project, "worktree_path": worktree_path,
        "reservation_key": key, "driver": driver,
        "verify_timeout": verify_timeout,
    }
    try:
        result = subprocess.run(
            [*prefix, "acquire", "--request-file", "-"], input=json.dumps(payload),
            capture_output=True, text=True, check=False, timeout=timeout,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeCarriedSessionBusy("acquisition transport timed out; outcome requires reconciliation") from exc
    except OSError as exc:
        raise BridgeUnavailable(f"acquisition executable could not be launched: {exc}") from exc
    if result.returncode:
        try:
            failure = json.loads(result.stdout or "{}")
        except ValueError:
            failure = {}
        detail = failure.get("detail") if isinstance(failure, dict) else None
        if (
            result.returncode == 2
            and not result.stdout.strip()
            and result.stderr.startswith("usage: agent-bridge ")
            and any(line.startswith(
                "agent-bridge: error: argument command: invalid choice: 'acquire' (choose from "
            ) for line in result.stderr.splitlines())
        ):
            detail = {
                "code": "unsupported_acquisition_cli",
                "message": "bridge executable does not support session acquisition",
                "effect": "not_started",
            }
        if (
            isinstance(failure, dict) and failure.get("status") == 426
            and detail == "daemon does not support strict session acquisition"
        ):
            # The previous acquisition CLI emits this exact pre-request gate.
            # Arbitrary string errors are still ambiguous.
            detail = {"code": "unsupported_acquisition_protocol", "message": detail, "effect": "not_started"}
        if isinstance(detail, dict):
            result.stderr = str(detail.get("message") or detail.get("code") or result.stderr)
        if isinstance(detail, dict) and detail.get("code") in {
            "session_busy", "live_holder", "foreign_live_holder", "launch_gate_held",
            "acquisition_pending", "cli_launch_pending", "native_registration_pending",
            "owner_operation_pending",
        }:
            raise BridgeCarriedSessionBusy(str(detail.get("message") or detail["code"]))
        if not isinstance(detail, dict) or detail.get("effect") != "not_started":
            raise BridgeCarriedSessionBusy(
                result.stderr or "bridge acquisition outcome requires reconciliation",
            )
        return result
    try:
        response = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise BridgeCarriedSessionBusy("bridge acquisition returned invalid JSON; native launch is unproven") from exc
    if not isinstance(response, dict) or not response.get("native_session_id"):
        raise BridgeCarriedSessionBusy("bridge acquisition did not prove native conversation identity")
    for field in ("mode", "task_id", "task_generation", "worktree_id", "delivery_id"):
        if response.get(field) != payload[field]:
            raise BridgeCarriedSessionBusy(f"bridge acquisition returned mismatched {field}")
    session_id = response.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise BridgeCarriedSessionBusy("bridge acquisition returned no session handle")
    if wait and mode == "acp":
        observation_error = None
        try:
            settled = subprocess.run(
                [*prefix, "wait", session_id, "--json"], capture_output=True,
                text=True, check=False, timeout=timeout, **no_window_kwargs(),
            )
            if settled.returncode:
                observation_error = settled.stderr or settled.stdout or f"wait exited {settled.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            observation_error = str(exc)
        if observation_error:
            response["observation_error"] = observation_error
            result.stdout = json.dumps(response)
            result.stderr = f"acquisition succeeded; observation failed: {observation_error}"
    return result


def handle(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    response = json.loads(result.stdout)
    mode = response["mode"]
    handle = {
        "session": f"local-body:{response['session_id']}" if mode == "acp" else response["native_session_id"],
        "worktree": response["worktree_id"],
        "native_session_id": response["native_session_id"],
        "mode": mode,
    }
    if response.get("observation_error"):
        handle["observation_error"] = response["observation_error"]
    return handle
