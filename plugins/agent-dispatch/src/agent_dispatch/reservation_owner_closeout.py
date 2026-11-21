"""Original review worker closeout, using its current assigned reservation, not a task claim."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import pool_decline_observation, procutil, remote_dispatch
from .bridge_remote import normalize_host
from .current_driver_lookup import DriverMatch, current_driver_match
from .ordinary_agent_facts import identity as bridge_identity
from .ordinary_native_bindings import (
    native_bindings,
    reservation_native_identity,
    retained_start_generation,
)
from .pending_workspace_conclusion import pending_workspace_conclusion
from .queue_common import CompletionOutcome, Task
from .queue_execution_custody import require_execution_send_reconciliation
from .queue_pool_placement import pool_worker_matches
from .queue_records import TaskError
from .queue_submission_preparation import require_unprepared_submission
from .queue_task_relationships import latest_relationship
from .review_closeout_submission import SUBMIT, eligible
from .task_relevance_contract import RESERVATION_OWNER

if TYPE_CHECKING:
    from .queue import TaskQueue

Json = dict[str, Any]


def candidate(binding: object) -> bool:
    return (
        binding is None
        or binding == {"state": "unconfirmed", "reason": "original_relationship_unavailable"}
        or (isinstance(binding, dict) and binding.get("state") == RESERVATION_OWNER)
    )


def binding_for(queue: TaskQueue, conn: sqlite3.Connection, task_id: str, args: Json) -> Json:
    task = queue._fetch(conn, task_id)
    row = conn.execute(
        "SELECT * FROM task_relevance_closeouts WHERE task_id=?", (task_id,)
    ).fetchone()
    if (
        task is None
        or row is None
        or not eligible(task)
        or args
        != {
            "request_id": row["request_id"],
            "expected_generation": task.generation,
            "worker_id": task.owner,
            "native_id": task.owner_session_id,
        }
        or (task.generation, task.owner, task.owner_session_id)
        != (
            row["generation"],
            row["owner"],
            row["owner_session_id"],
        )
        or task.awaiting_steer
        or any(v is not None for v in (task.hold_at, task.hold_actor, task.hold_reason))
    ):
        raise TaskError("reservation closeout original review owner changed or is held")
    stored = json.loads(row["resource_binding"] or "null")
    if not candidate(stored) or latest_relationship(conn, task_id) is not None:
        raise TaskError("reservation closeout cannot replace a protected relationship")
    require_unprepared_submission(conn, task_id)
    return _assigned_reservation(conn, task, row, stored)


def _assigned_reservation(
    conn: sqlite3.Connection,
    task: Task,
    row: sqlite3.Row,
    stored: Json | None,
) -> Json:
    """Check retained custody, not permission to place or start new work."""
    task_id = task.id
    res = conn.execute(
        "SELECT r.*,a.registration_id,a.env,a.machine,a.catalog_revision,a.task_generation"
        " FROM spawn_reservations r"
        " JOIN task_pool_assignments a ON a.reservation_key=r.key AND a.task_id=r.task_id"
        " JOIN pool_reservation_bindings p ON p.reservation_key=r.key AND p.env=a.env"
        " AND p.machine=a.machine AND p.registration_id=a.registration_id"
        " WHERE r.task_id=? AND a.task_generation=? AND a.assignment_released=0",
        (task_id, task.generation),
    ).fetchone()
    if (
        res is None
        or res["state"] != "spawned"
        or not res["startup_complete"]
        or res["worktree_ownership"] != "created"
        or not res["creation_key"]
        or res["reserved_at"] > row["created_at"]
        or res["allocation_reservation_key"] not in (None, res["key"])
        or not res["allocation_project"]
        or not res["worktree_path"]
        or not res["execution_machine"]
        or normalize_host(res["machine"]) != normalize_host(res["execution_machine"])
        or not pool_worker_matches(task.owner, res["machine"], res["worktree"])
        or res["driver"] not in {"cli", "ordinary-bridge"}
        or not res["reserved_by"]
        or res["startup_fenced"]
        or res["release_requested"]
        or res["conversation_retired"]
        or res["cooling_token"] is not None
        or res["native_release_pending"]
        or res["conclusion_state"] not in (None, "")
        or res["task_relationship_event_id"] is not None
        or res["task_relationship_required"]
        or reservation_native_identity(conn, res) != task.owner_session_id
        or conn.execute(
            "SELECT 1 FROM spawn_reservations WHERE task_id=? AND "
            "(attempt>? OR task_relationship_required=1 OR (key<>? AND state IN "
            "('reserving','spawned','cold','releasing')))",
            (task_id, res["attempt"], res["key"]),
        ).fetchone()
    ):
        raise TaskError("reservation closeout lacks an unambiguous original allocation")
    cohort = json.loads(res["allocation_cohort"] or "null")
    if cohort is not None:
        expected_pool = {
            key: res[key]
            for key in (
                "registration_id",
                "env",
                "machine",
                "catalog_revision",
                "task_generation",
                "task_id",
            )
        }
        expected_pool["reservation_key"] = res["key"]
        if not isinstance(cohort, dict) or cohort.get("pool") != expected_pool:
            raise TaskError("reservation closeout pool differs from its original allocation")
    known, retired = native_bindings(conn, res, task, reject_pending_native=task.owner_session_id)
    if task.owner_session_id in retired or set(known).difference(retired) != {
        task.owner_session_id
    }:
        raise TaskError("reservation closeout native is retired or ambiguous")
    require_execution_send_reconciliation(conn, task_id, exclusive_key=task.exclusive_key)
    if res["driver"] == "ordinary-bridge":
        agent = json.loads(res["agent_info"] or "null")
        if not isinstance(agent, dict):
            raise TaskError("ordinary reservation closeout is missing the original agent")
        actual = bridge_identity(agent)
        if (
            not retained_start_generation(conn, task, agent)
            or (actual.worktree, actual.project, actual.path, actual.native)
            != (
                res["worktree"],
                res["allocation_project"],
                res["worktree_path"],
                task.owner_session_id,
            )
            or res["session_handle"]
            not in (
                f"local-body:{actual.bridge}",
                f"fleet-body:{res['execution_machine']}:{actual.bridge}",
            )
        ):
            raise TaskError("ordinary reservation closeout agent or handle changed")
    elif res["session_handle"] != task.owner_session_id:
        raise TaskError("CLI reservation closeout requires the original native")
    if (
        pending_workspace_conclusion(
            conn, machine=res["execution_machine"], worktree=res["worktree"]
        )
        != DriverMatch.CLEAR
    ):
        raise TaskError("reservation closeout has pending workspace conclusion")
    if (
        current_driver_match(
            conn,
            task_id=task_id,
            machine=res["execution_machine"],
            worktree=res["worktree"],
            native_id=task.owner_session_id,
        )
        != DriverMatch.CLEAR
    ):
        raise TaskError("reservation closeout driver has competing custody")
    binding = {
        "state": RESERVATION_OWNER,
        "reservation_key": res["key"],
        "startup_revision": res["startup_revision"],
        "creation_key": res["creation_key"],
        "attempt": res["attempt"],
        "driver": res["driver"],
        "session_handle": res["session_handle"],
        "supervisor": res["reserved_by"],
        "pool": [res["registration_id"], res["machine"], res["env"]],
        "catalog_revision": res["catalog_revision"],
        "allocation_cohort": cohort,
        "allocation_cohort_transport": res["allocation_cohort_transport"],
        "workspace": {
            "machine": res["execution_machine"],
            "project": res["allocation_project"],
            "path": res["worktree_path"],
            "worktree": res["worktree"],
            "creation_nonce": res["creation_nonce"],
        },
    }
    if isinstance(stored, dict) and stored.get("state") == RESERVATION_OWNER and stored != binding:
        raise TaskError("prepared reservation closeout changed")
    return binding


def observe_workspace(task_id: str, args: Json, binding: Json) -> Json:
    w = binding["workspace"]
    if normalize_host(remote_dispatch.local_machine() or "") != normalize_host(w["machine"]):
        raise TaskError("reservation closeout requires the workspace's local owner")
    try:
        result = procutil.run_agent_worktrees_capture(
            "--project", w["project"], "record", "--cwd", w["path"], "--json", timeout=15
        )
        value = json.loads(result.stdout) if result and result.returncode == 0 else None
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise TaskError("reservation closeout workspace observation unavailable") from exc
    context = value.get("context") if isinstance(value, dict) and value.get("ok") is True else None
    tree = context.get("worktree") if isinstance(context, dict) else None
    if not isinstance(context, dict) or not isinstance(tree, dict):
        raise TaskError("reservation closeout requires an observed managed worktree")
    stamp, head, nonce = tree.get("dispatch_attempt"), tree.get("head"), tree.get("creation_nonce")
    expected = {
        "task_id": task_id,
        "reservation_key": binding["reservation_key"],
        "attempt": binding["attempt"],
        "driver": binding["driver"],
        "supervisor": binding["supervisor"],
        "ownership": "created",
    }
    if (
        context.get("schema") != "agent-worktrees.workspace-context"
        or type(context.get("version")) is not int
        or context["version"] != 1
        or context.get("project") != w["project"]
        or context.get("root") != w["path"]
        or tree.get("id") != w["worktree"]
        or tree.get("repo") != w["project"]
        or tree.get("path") != w["path"]
        or tree.get("status") != "active"
        or (tree.get("checkout_managed") is not None and tree["checkout_managed"] is not True)
        or tree.get("dispatch_attempt_opaque") is not False
        or not isinstance(nonce, str)
        or not nonce.strip()
        or w["creation_nonce"] not in (None, nonce)
        or not isinstance(stamp, dict)
        or type(stamp.get("attempt")) is not int
        or any(stamp.get(k) != v for k, v in expected.items())
        or (
            ("cohort" in stamp or binding["allocation_cohort_transport"] == "cohort-v1")
            and (
                not isinstance(binding["allocation_cohort"], dict)
                or stamp.get("cohort") != binding["allocation_cohort"]
            )
        )
        or not isinstance(stamp.get("creator_machine"), str)
        or normalize_host(stamp["creator_machine"]) != normalize_host(w["machine"])
        or not isinstance(head, dict)
        or head.get("worktree_id") != w["worktree"]
        or head.get("head_session") != args["native_id"]
        or head.get("state") != "active"
        or head.get("active") is not True
        or head.get("tracked") is not True
        or head.get("pending_handoffs") != []
        or type(head.get("head_revision")) is not int
        or head["head_revision"] < 0
    ):
        raise TaskError("reservation closeout workspace or native head changed")
    return {
        **expected,
        "allocation": stamp,
        "ready": True,
        "finalized": False,
        "process_retirement": False,
        "native_id": args["native_id"],
        "worktree_id": w["worktree"],
        "project": w["project"],
        "path": w["path"],
        "creation_nonce": nonce,
        "head_revision": head["head_revision"],
    }


def prepare(queue: TaskQueue, task_id: str, args: Json, fence: Callable[[], None]) -> Json:
    def saved(conn: sqlite3.Connection, binding: Json) -> Json | None:
        raw = conn.execute(
            "SELECT readiness FROM task_relevance_closeouts WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        if raw is None:
            return None
        receipt: Json = json.loads(raw)
        if receipt["binding"] != binding or receipt["arguments"] != args:
            raise TaskError("reservation closeout preparation belongs to another incarnation")
        return receipt

    with queue._connect() as conn:
        conn.execute("BEGIN")
        binding = binding_for(queue, conn, task_id, args)
        receipt = saved(conn, binding)
    if receipt is None:
        observation = observe_workspace(task_id, args, binding)
        with queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            fence()
            if binding != binding_for(queue, conn, task_id, args):
                raise TaskError("reservation changed during closeout preparation")
            receipt = {
                "task_id": task_id,
                "binding": binding,
                "arguments": args,
                "observation": observation,
                "finalized": False,
                "process_retirement": False,
            }
            existing = saved(conn, binding)
            if existing is not None and existing != receipt:
                raise TaskError("conflicting reservation closeout preparation")
            conn.execute(
                "UPDATE task_relevance_closeouts SET readiness=?, resource_binding=?"
                " WHERE task_id=?",
                (json.dumps(receipt), json.dumps(binding), task_id),
            )
            conn.commit()
    return {"receipt": receipt, "relationship": {"state": "not-required"}}


def finish(
    queue: TaskQueue, task_id: str, args: Json, fence: Callable[[], None]
) -> CompletionOutcome:
    from .queue_relevance_acknowledgement import _replay

    def snapshot(conn: sqlite3.Connection) -> tuple[Json, Json, Json]:
        binding = binding_for(queue, conn, task_id, args)
        row = conn.execute(
            "SELECT readiness FROM task_relevance_closeouts WHERE task_id=?", (task_id,)
        ).fetchone()
        receipt = json.loads(row[0] or "null")
        if (
            not isinstance(receipt, dict)
            or receipt.get("arguments") != args
            or receipt.get("binding") != binding
        ):
            raise TaskError("reservation closeout is not prepared")
        res = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (binding["reservation_key"],)
        ).fetchone()
        return binding, receipt, dict(res)

    with queue._connect() as conn:
        conn.execute("BEGIN")
        replay = _replay(queue, conn, task_id, args)
        if replay:
            return replay
        binding, ready, res = snapshot(conn)
    observation = pool_decline_observation.observe(
        {"id": task_id}, res, {"native_id": args["native_id"]}
    )
    before = ready["observation"]
    if (
        observation.get("creation_nonce") != before["creation_nonce"]
        or observation.get("head_revision") != before["head_revision"]
        or observation.get("allocation") != before["allocation"]
    ):
        raise TaskError("finalized reservation closeout differs from its prepared incarnation")
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        fence()
        replay = _replay(queue, conn, task_id, args)
        if replay:
            return replay
        if snapshot(conn) != (binding, ready, res):
            raise TaskError("reservation changed during finalization observation")
        task = queue._fetch(conn, task_id)
        if task is None:
            raise TaskError("reservation closeout lost its task before submission")
        ts = queue._now(None)
        result_ref = "relevance-closeout:" + args["request_id"]
        ack = {
            "task_id": task_id,
            "arguments": args,
            "binding": binding,
            "observation": observation,
            "process_retirement": False,
            "finish_action": SUBMIT,
            "at": ts,
            "submission_result_ref": result_ref,
            "task_attachment": {
                "generation": task.generation,
                "owner_session_id": task.owner_session_id,
            },
        }
        conn.execute(
            "UPDATE task_relevance_closeouts SET acknowledgement=? WHERE task_id=?",
            (json.dumps(ack), task_id),
        )
        outcome, wake = queue._write_submission(
            conn,
            task,
            worker_id=args["worker_id"],
            encoded_result=None,
            result_ref=result_ref,
            ts=ts,
        )
        conn.commit()
    queue._notify_submission(outcome, wake_enqueued=wake)
    return outcome
