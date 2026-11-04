"""Worker-owned decline and finalization; the coordinator independently observes it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess

from . import procutil
from .bridge_remote import normalize_host
from .client_errors import DispatchError, DispatchUpgradeRequired
from .loop_commands import _resolve_cli_module

CONTRACT = "agent-worktrees.dispatch-worker-closeout"


def _finalize(binding: dict, worktree: str) -> None:
    prefix = procutil.agent_worktrees_launch_prefix()
    if prefix is None:
        raise DispatchUpgradeRequired("selected Worktrees runtime is unavailable for own-worker closeout")
    environment = procutil.agent_worktrees_environment()

    def run(arguments: list[str], timeout: float) -> dict:
        result = procutil.run_background_capture(
            [*prefix, *arguments], timeout=timeout, env=environment,
        )
        if result is None:
            raise DispatchError(502, "own-workspace closeout timed out or failed to launch; custody retained")
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise DispatchError(502, "own-workspace closeout response is unconfirmed; custody retained") from exc
        if result.returncode or not isinstance(value, dict):
            detail = value.get("error") if isinstance(value, dict) else None
            message = detail[:1024] if isinstance(detail, str) else "unsupported or blocked"
            raise DispatchError(409, f"own-workspace closeout {message}; custody retained")
        return value

    descriptor = run(["record", "--describe-closeout-contract", "--json"], 15)
    versions = descriptor.get("own_finalization_cli_versions")
    if (
        descriptor.get("contract") != CONTRACT or type(descriptor.get("version")) is not int
        or descriptor["version"] != 1 or not isinstance(versions, list)
        or not any(type(version) is int and version == 1 for version in versions)
        or descriptor.get("paired_closeout") is not False
        or descriptor.get("task_release") is not False
        or descriptor.get("process_retirement") is not False
    ):
        raise DispatchUpgradeRequired("selected Worktrees runtime lacks canonical own-worker finalization")
    result = run([
        "finalize", "--dispatch-task-id", binding["task_id"],
        "--dispatch-reservation-key", binding["reservation_key"],
        "--dispatch-native-id", binding["native_id"], "--json",
    ], 180)
    expected = {
        "ok": True, "contract": CONTRACT, "version": 1, "operation": "finalize-own",
        "task_id": binding["task_id"], "reservation_key": binding["reservation_key"],
        "native_id": binding["native_id"], "worktree_id": worktree, "status": "finalized",
        "checkout_retained": True, "task_release": False, "process_retirement": False,
    }
    if (
        any(result.get(key) != value or type(result.get(key)) is not type(value)
            for key, value in expected.items())
        or not isinstance(result.get("creation_nonce"), str) or not result["creation_nonce"]
        or type(result.get("head_revision")) is not int or result["head_revision"] < 0
    ):
        raise DispatchError(502, "own-workspace closeout outcome is unconfirmed; custody retained")


def cmd_decline(args: argparse.Namespace) -> int:
    core = _resolve_cli_module()
    native = args.owner_session_id
    if (
        type(args.pool_generation) is not int or args.pool_generation < 1
        or not isinstance(native, str) or not native.strip()
        or native != os.environ.get("COPILOT_AGENT_SESSION_ID")
    ):
        raise DispatchError(400, "decline requires the current generation and actual native conversation")
    machine, worktree = core._identity(args)
    if not machine or not worktree:
        raise DispatchError(409, "decline requires the invoking worker's own machine/worktree")
    with core._client(args, allow_ssh_failover=False) as client:
        task = client.get(args.task_id)
        reservation = task.get("spawn_reservation")
        if (
            task.get("id") != args.task_id or task.get("status") != "claimed"
            or task.get("owner") is not None or task.get("generation") != args.pool_generation
            or type(task.get("generation")) is not int
            or not isinstance(reservation, dict) or reservation.get("worktree") != worktree
            or not isinstance(reservation.get("execution_machine"), str)
            or normalize_host(reservation["execution_machine"]) != normalize_host(machine)
            or reservation.get("driver") not in {"cli", "ordinary-bridge"}
        ):
            raise DispatchError(409, "decline requires its unchanged allocator assignment")
        exclusions = args.exclude
        if args.exclude_self == "worktree":
            exclusions = [f"worktree:{worktree}"]
        elif args.exclude_self == "machine":
            assignment = task.get("pool_assignment")
            if (
                not isinstance(assignment, dict)
                or assignment.get("reservation_key") != reservation.get("key")
                or not isinstance(assignment.get("machine"), str)
                or normalize_host(assignment["machine"]) != normalize_host(machine)
            ):
                raise DispatchUpgradeRequired("machine self-exclusion requires the retained pool assignment")
            exclusions = [f"machine:{assignment['machine']}"]
        binding = {
            "native_id": native, "generation": args.pool_generation,
            "startup_revision": reservation.get("startup_revision"),
            "reservation_key": reservation.get("key"),
        }
        worker = f"{machine}/{worktree}"
        decline = client.prepare_pool_decline(
            args.task_id, worker, **binding, exclusions=exclusions, note=args.note,
        )
        try:
            _finalize({"task_id": args.task_id, **binding}, worktree)
        except (DispatchError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            client.record_pool_decline_blocker(
                args.task_id, worker, **binding,
                detail=f"Own-workspace closeout unconfirmed; retain custody: {exc}"[:4096],
            )
            raise
    return core._emit({
        "decline": decline, "workspace_closeout": "finalized",
        "task_release": False, "process_retirement": False,
    })
