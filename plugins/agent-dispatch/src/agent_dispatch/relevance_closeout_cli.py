"""Let the original worker finalize before Dispatch acknowledges source closeout."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .assignment_workspace import AssignmentWorkspace, same_assignment_worker
from .client import DispatchClient
from .client_errors import DispatchError
from .loop_commands import _resolve_cli_module
from .pool_worker_decline_cli import _finalize
from .queue_records import TaskError


def _matches(value: object, expected: dict) -> bool:
    return isinstance(value, dict) and all(
        type(value.get(key)) is type(item) and value[key] == item for key, item in expected.items()
    )


def _captured(value: dict, task_id: str, worker_id: str, native: str) -> tuple[dict, dict]:
    generation, request_id = value.get("generation"), value.get("request_id")
    if (
        value.get("task_id") != task_id
        or not isinstance(request_id, str)
        or not request_id.strip()
        or len(request_id) > 4096
        or type(generation) is not int
        or not 0 <= generation <= 2**63 - 1
        or not same_assignment_worker(value.get("owner"), worker_id)
        or value.get("owner_session_id") != native
    ):
        raise DispatchError(
            409, "closeout does not name this original worker/native; custody retained"
        )
    binding = value.get("resource_binding")
    if (
        not isinstance(binding, dict)
        or binding.get("state") != "captured"
        or not isinstance(binding.get("reservation_key"), str)
        or not binding["reservation_key"]
        or type(binding.get("assignment_event_id")) is not int
        or binding["assignment_event_id"] < 1
        or type(binding.get("startup_revision")) is not int
        or binding["startup_revision"] < 0
    ):
        raise DispatchError(
            409, "closeout lacks its captured managed allocation; custody retained"
        )
    try:
        workspace = AssignmentWorkspace.from_dict(binding.get("workspace"))
    except TaskError as exc:
        raise DispatchError(409, "closeout workspace is unconfirmed; custody retained") from exc
    if (
        not same_assignment_worker(workspace.worker_id, worker_id)
        or not Path(workspace.path).is_absolute()
        or Path(workspace.path).resolve() != Path.cwd().resolve()
    ):
        raise DispatchError(
            409, "closeout is not the invoking worker's own checkout; custody retained"
        )
    return {
        "request_id": request_id,
        "worker_id": value["owner"],
        "native_id": native,
        "expected_generation": generation,
    }, binding


def _acknowledged(value: dict, task_id: str, arguments: dict, binding: dict) -> bool:
    receipt = value.get("acknowledgement")
    if receipt is None:
        return False
    if not _matches(
        receipt,
        {
            "task_id": task_id,
            "arguments": arguments,
            "binding": binding,
            "process_retirement": False,
        },
    ) or not _matches(
        receipt.get("observation"),
        {
            "status": "finalized",
            "creation_nonce": binding["workspace"]["creation_nonce"],
        },
    ):
        raise DispatchError(502, "closeout acknowledgement is unconfirmed; custody retained")
    return True


def _allocation(prepared: dict, task_id: str, arguments: dict, binding: dict) -> str:
    receipt, relationship = prepared.get("receipt"), prepared.get("relationship")
    if not _matches(
        receipt,
        {
            "task_id": task_id,
            "arguments": arguments,
            "binding": binding,
            "finalized": False,
            "process_retirement": False,
        },
    ) or not _matches(
        relationship,
        {
            "event_id": binding["assignment_event_id"],
            "state": "closed",
        },
    ):
        raise DispatchError(
            409,
            "closeout original claim is not confirmed closed; custody retained; rerun to converge",
        )
    observation = receipt.get("observation")
    workspace = binding["workspace"]
    if not _matches(
        observation,
        {
            "ready": True,
            "finalized": False,
            "process_retirement": False,
            "task_id": task_id,
            "native_id": arguments["native_id"],
            "worktree_id": workspace["worktree"],
            "creation_nonce": workspace["creation_nonce"],
            "project": workspace["project"],
            "path": workspace["path"],
        },
    ):
        raise DispatchError(502, "closeout readiness is unconfirmed; custody retained")
    original_key = observation.get("reservation_key")
    if (
        not isinstance(original_key, str)
        or not original_key
        or not _matches(
            observation.get("allocation"),
            {
                "task_id": task_id,
                "reservation_key": original_key,
                "ownership": "created",
            },
        )
    ):
        raise DispatchError(502, "closeout original allocation is unconfirmed; custody retained")
    return original_key


def closeout(client: DispatchClient, task_id: str, worker_id: str) -> dict:
    native = os.environ.get("COPILOT_AGENT_SESSION_ID")
    if not isinstance(native, str) or not native.strip():
        raise DispatchError(
            409, "closeout requires the actual native conversation; custody retained"
        )
    current = client.relevance_closeout(task_id)
    arguments, binding = _captured(current, task_id, worker_id, native)
    if not _acknowledged(current, task_id, arguments, binding):
        prepared = client.prepare_relevance_closeout(task_id, arguments)
        original_key = _allocation(prepared, task_id, arguments, binding)
        _finalize(
            {"task_id": task_id, "native_id": native, "reservation_key": original_key},
            binding["workspace"]["worktree"],
            expected_creation_nonce=binding["workspace"]["creation_nonce"],
        )
        finished = client.finish_relevance_closeout(task_id, arguments)
        if not _matches(
            finished,
            {
                "id": task_id,
                "status": "abandoned",
                "generation": arguments["expected_generation"],
            },
        ):
            raise DispatchError(
                502, "terminal closeout is unconfirmed; custody retained; rerun to converge"
            )
    return {
        "id": task_id,
        "status": "abandoned",
        "generation": arguments["expected_generation"],
        "workspace_closeout": "finalized",
        "task_relationship": "closed",
        "task_release": False,
        "process_retirement": False,
    }


def cmd_relevance_closeout(args: argparse.Namespace) -> int:
    core = _resolve_cli_module()
    machine, worktree = core._identity(args)
    if not machine or not worktree:
        raise DispatchError(409, "closeout requires the invoking worker's own machine/worktree")
    with core._client(args, allow_ssh_failover=False) as client:
        return core._emit(closeout(client, args.task_id, f"{machine}/{worktree}"))
