"""Negotiated, read-only readiness from the selected local Worktrees owner."""

from __future__ import annotations

import json
import re
import subprocess

from . import procutil, remote_dispatch
from .assignment_workspace import AssignmentWorkspace
from .bridge_remote import normalize_host
from .queue_records import TaskError

CONTRACT = "agent-worktrees.dispatch-worker-readiness"


def _matches(value: object, expected: dict) -> bool:
    return isinstance(value, dict) and all(
        type(value.get(key)) is type(item) and value[key] == item for key, item in expected.items()
    )


def observe(request: dict, allocation: dict, task_claim_ref: str) -> dict:
    """Acquire independent evidence; never accept a worker's readiness receipt."""
    workspace = AssignmentWorkspace.from_dict(request["workspace"])
    local = remote_dispatch.local_machine()
    if not local or normalize_host(local) != normalize_host(workspace.machine):
        raise TaskError("submission readiness requires a qualified local owner; custody retained")
    try:
        prefix = procutil.agent_worktrees_launch_prefix()
        environment = procutil.agent_worktrees_environment()
    except (OSError, RuntimeError, ValueError) as exc:
        raise TaskError(
            "selected Worktrees readiness runtime is unavailable; custody retained",
        ) from exc
    if not prefix:
        raise TaskError("selected Worktrees runtime lacks submission readiness; custody retained")

    def read(*args: str) -> dict:
        try:
            result = procutil.run_background_capture(
                [*prefix, "--project", workspace.project, "record", *args, "--json"],
                timeout=15,
                env=environment,
            )
            value = json.loads(result.stdout) if result is not None else None
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise TaskError(
                "Worktrees readiness observation is unavailable; custody retained",
            ) from exc
        if result is None or result.returncode or not isinstance(value, dict):
            detail = value.get("error") if isinstance(value, dict) else None
            message = detail[:1024] if isinstance(detail, str) else "unsupported or unconfirmed"
            raise TaskError(f"Worktrees readiness {message}; custody retained")
        return value

    descriptor = read("--describe-readiness-contract")
    operations, platforms = descriptor.get("operations"), descriptor.get("platforms")
    if (
        not _matches(
            descriptor,
            {
                "contract": CONTRACT,
                "version": 1,
                "native_proof": "inuse-hold-v1",
                "paired_readiness": False,
                "finalized": False,
                "task_release": False,
                "process_retirement": False,
            },
        )
        or not isinstance(operations, list)
        or "observe-ready" not in operations
        or not isinstance(platforms, list)
        or not {"linux", "wsl"} <= set(item for item in platforms if isinstance(item, str))
    ):
        raise TaskError(
            "selected Worktrees runtime lacks independent readiness v1; custody retained",
        )

    metadata = read("--cwd", workspace.path)
    context = metadata.get("context")
    tree = context.get("worktree") if isinstance(context, dict) else None
    head = tree.get("head") if isinstance(tree, dict) else None
    if (
        metadata.get("ok") is not True
        or not _matches(
            context,
            {
                "schema": "agent-worktrees.workspace-context",
                "version": 1,
                "project": workspace.project,
                "root": workspace.path,
            },
        )
        or not _matches(
            tree,
            {
                "id": workspace.worktree,
                "repo": workspace.project,
                "path": workspace.path,
                "creation_nonce": workspace.creation_nonce,
                "status": "active",
                "dispatch_attempt_opaque": False,
                "checkout_managed": True,
            },
        )
        or not _matches(tree.get("dispatch_attempt"), allocation)
        or not _matches(
            head,
            {
                "version": 1,
                "worktree_id": workspace.worktree,
                "tracked": True,
                "head_session": request["owner_session_id"],
                "active": True,
                "state": "active",
                "pending_handoffs": [],
            },
        )
        or type(head.get("head_revision")) is not int
        or head["head_revision"] < 0
    ):
        raise TaskError("Worktrees readiness workspace or native head changed; custody retained")

    revision = head["head_revision"]
    value = read(
        "--worktree-id",
        workspace.worktree,
        "--readiness-operation",
        "observe-ready",
        "--readiness-task",
        request["task_id"],
        "--readiness-reservation",
        allocation["reservation_key"],
        "--readiness-native",
        request["owner_session_id"],
        "--readiness-creation-nonce",
        workspace.creation_nonce,
        "--readiness-head-revision",
        str(revision),
        "--readiness-task-claim-ref",
        task_claim_ref,
    )
    checkout, landing = value.get("checkout"), value.get("landing")
    if (
        not _matches(
            value,
            {
                "ok": True,
                "contract": CONTRACT,
                "version": 1,
                "operation": "observe-ready",
                "ready": True,
                "task_id": request["task_id"],
                "reservation_key": allocation["reservation_key"],
                "native_id": request["owner_session_id"],
                "worktree_id": workspace.worktree,
                "creation_nonce": workspace.creation_nonce,
                "head_revision": revision,
                "task_claim_ref": task_claim_ref,
                "project": workspace.project,
                "path": workspace.path,
                "checkout_retained": True,
                "finalized": False,
                "task_release": False,
                "process_retirement": False,
            },
        )
        or not isinstance(value.get("machine"), str)
        or normalize_host(value["machine"]) != normalize_host(workspace.machine)
        or not _matches(value.get("allocation"), allocation)
        or not isinstance(checkout, dict)
        or set(checkout) != {"head", "branch"}
        or not isinstance(checkout.get("head"), str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", checkout["head"]) is None
        or not isinstance(checkout.get("branch"), str)
        or not isinstance(landing, dict)
        or not isinstance(landing.get("bases"), list)
        or not landing["bases"]
        or any(not isinstance(base, str) or not base for base in landing["bases"])
        or not isinstance(landing.get("prs"), list)
    ):
        raise TaskError("Worktrees readiness outcome changed or is unconfirmed; custody retained")
    return value
