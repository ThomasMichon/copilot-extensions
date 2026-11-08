"""Coordinator-owned local Worktrees reads, never worker-supplied closeout receipts."""

from __future__ import annotations

import json
import re
import subprocess

from . import procutil, remote_dispatch
from .bridge_remote import normalize_host
from .queue_records import TaskError

CONTRACT = "agent-worktrees.dispatch-worker-closeout"


def observe(task: dict, reservation: dict, decline: dict, relationship: dict | None = None) -> dict:
    """Read only from the actual allocation host's selected runtime, or retain custody.

    A retained task relationship means the workspace still holds the exact task
    claim that only this disposition can settle, so the owner is observed through
    nonfinalizing readiness instead of requiring prior finalization.
    """
    if relationship is not None:
        return _observe_ready(task, reservation, decline, relationship)
    machine = reservation["execution_machine"]
    local = remote_dispatch.local_machine()
    if not local or not machine or normalize_host(local) != normalize_host(machine):
        raise TaskError("worker decline closeout requires a qualified local owner; remote custody retained")
    project = reservation["allocation_project"]
    if (
        not project or not reservation["worktree_path"]
        or reservation["worktree_ownership"] != "created"
        or reservation["allocation_reservation_key"] not in (None, reservation["key"])
    ):
        raise TaskError("worker decline closeout lacks its original managed allocation")

    def read(*args: str) -> dict:
        try:
            result = procutil.run_agent_worktrees_capture(
                "--project", project, "record", *args, timeout=15,
            )
            value = json.loads(result.stdout) if result is not None and result.returncode == 0 else None
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise TaskError("worker decline owner observation is unavailable; custody retained") from exc
        if not isinstance(value, dict):
            raise TaskError("worker decline owner observation is unconfirmed; custody retained")
        return value

    value = read(
        "--worktree-id", reservation["worktree"], "--closeout-task", task["id"],
        "--closeout-reservation", reservation["key"], "--closeout-native", decline["native_id"],
        "--json",
    )
    if (
        value.get("contract") != CONTRACT or type(value.get("version")) is not int
        or value["version"] != 1 or value.get("operation") != "observe-finalized"
    ):
        raise TaskError("selected Worktrees runtime lacks worker closeout observation")
    expected = {
        "ok": True, "contract": CONTRACT, "version": 1, "operation": "observe-finalized",
        "task_id": task["id"], "reservation_key": reservation["key"],
        "native_id": decline["native_id"], "worktree_id": reservation["worktree"],
        "project": project, "path": reservation["worktree_path"], "status": "finalized",
        "checkout_retained": True, "task_release": False, "process_retirement": False,
    }
    allocation = value.get("allocation")
    checkout = value.get("checkout")
    if (
        any(value.get(key) != actual or type(value.get(key)) is not type(actual)
            for key, actual in expected.items())
        or not isinstance(value.get("machine"), str)
        or normalize_host(value["machine"]) != normalize_host(machine)
        or not isinstance(value.get("creation_nonce"), str) or not value["creation_nonce"]
        or type(value.get("head_revision")) is not int or value["head_revision"] < 0
        or not isinstance(allocation, dict)
        or allocation.get("task_id") != task["id"]
        or allocation.get("reservation_key") != reservation["key"]
        or type(allocation.get("attempt")) is not int or allocation["attempt"] != reservation["attempt"]
        or allocation.get("supervisor") != reservation["reserved_by"]
        or allocation.get("driver") != reservation["driver"]
        or allocation.get("ownership") != "created"
        or not isinstance(allocation.get("creator_machine"), str)
        or normalize_host(allocation["creator_machine"]) != normalize_host(machine)
        or not isinstance(checkout, dict) or set(checkout) != {"head", "branch"}
        or not isinstance(checkout.get("head"), str)
        or re.fullmatch(r"[0-9a-f]{40,64}", checkout["head"]) is None
        or not isinstance(checkout.get("branch"), str)
    ):
        raise TaskError("worker decline owner observation changed its allocation; custody retained")
    return value


def _observe_ready(task: dict, reservation: dict, decline: dict, relationship: dict) -> dict:
    """Readiness excepts only the exact bound task claim; it never finalizes."""
    from .submission_readiness_observation import observe as observe_ready

    request = relationship.get("request") if isinstance(relationship, dict) else None
    receipt = relationship.get("receipt") if isinstance(relationship, dict) else None
    workspace = request.get("workspace") if isinstance(request, dict) else None
    if (
        relationship.get("state") != "bound"
        or not isinstance(receipt, dict) or not isinstance(receipt.get("ref"), str)
        or not isinstance(workspace, dict)
        or request.get("task_id") != task["id"]
        or request.get("reservation_key") != reservation["key"]
        or reservation["worktree_ownership"] != "created"
        or reservation["allocation_reservation_key"] not in (None, reservation["key"])
        or not isinstance(workspace.get("machine"), str)
        or not isinstance(reservation["execution_machine"], str)
        or normalize_host(workspace["machine"]) != normalize_host(reservation["execution_machine"])
        or workspace.get("project") != reservation["allocation_project"]
        or workspace.get("worktree") != reservation["worktree"]
        or workspace.get("path") != reservation["worktree_path"]
        or workspace.get("creation_nonce") != reservation["creation_nonce"]
    ):
        raise TaskError("worker decline lacks its original bound task relationship; custody retained")
    return observe_ready(
        {"workspace": workspace, "task_id": task["id"], "owner_session_id": decline["native_id"]},
        {
            "task_id": task["id"], "reservation_key": reservation["key"],
            "attempt": reservation["attempt"], "driver": reservation["driver"],
            "supervisor": reservation["reserved_by"],
            "creator_machine": reservation["execution_machine"], "ownership": "created",
        },
        receipt["ref"],
    )
