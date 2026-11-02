"""Retained continuation startup custody, owned by the reservation transaction."""

from __future__ import annotations

import json
from typing import Any

from .ordinary_agent_facts import identity
from .queue_records import SpawnReservation, SpawnState, TaskError
from .queue_spawn_cooling import require_no_cooling


def _prompt(task_id: str, native: str) -> str:
    return (
        f"Continue your existing dispatched task {task_id} in this same native conversation. "
        "Keep the original worktree identity and use the original coordinator "
        "routing flags with each command below. "
        f"Read the current task; if it is still queued and unowned, run "
        f"`agent-dispatch claim --task {task_id} --evaluation --all-repos` "
        "(add the task's required capabilities), evaluate normally, then "
        f"`agent-dispatch start {task_id} --owner-session-id {native}`. "
        "If its owner or state has changed, do not claim or override it. "
        "Continue the original goal under the existing charter; do not create a replacement."
    )


def redrive_invocation(row: Any, machine: str) -> dict:
    """Keep accepted/UNKNOWN retry bytes even when the supervisor changes."""
    info = json.loads(row["agent_info"])
    actual = identity(info)
    key = f"{row['key']}:redrive"
    expected = {
        "version": 1, "task_id": row["task_id"], "reservation_key": row["key"],
        "session_id": actual.bridge, "native_session_id": actual.native,
        "worktree_id": actual.worktree, "machine": machine,
        "idempotency_key": key, "caller": f"agent-dispatch:{key}",
    }
    marker = info.get("retained_redrive")
    if marker is None:
        if not row["startup_complete"]:
            raise TaskError("retained native redrive has an unrelated incomplete startup")
        return {**expected, "prompt": _prompt(row["task_id"], actual.native)}
    if not isinstance(marker, dict) or set(marker) != {"invocation", "startup_revision"}:
        raise TaskError("retained native redrive startup metadata is invalid")
    invocation = marker["invocation"]
    revision = marker["startup_revision"]
    if (
        not isinstance(invocation, dict) or set(invocation) != {*expected, "prompt"}
        or type(invocation["version"]) is not int
        or any(invocation[name] != value for name, value in expected.items())
        or not isinstance(invocation["prompt"], str) or not invocation["prompt"].strip()
        or type(revision) is not int or not 1 <= revision <= row["startup_revision"]
        or not row["startup_complete"] and revision != row["startup_revision"]
    ):
        raise TaskError("retained native redrive startup identity conflicts")
    return invocation


def require_pending_redrive(before: dict, facts: dict, pending: dict) -> None:
    """Confirm the executing provider retained this operation before transport."""
    revision = before["startup_revision"] + int(before["startup_complete"])
    info = pending.get("agent_info")
    marker = info.get("retained_redrive") if isinstance(info, dict) else None
    if (
        pending.get("startup_complete") is not False
        or type(pending.get("startup_revision")) is not int
        or pending["startup_revision"] != revision
        or marker != {"startup_revision": revision, "invocation": facts["invocation"]}
        or any(pending.get(name) != before.get(name) for name in (
            "key", "task_id", "driver", "worktree", "session_handle",
        ))
        or pending.get("state") != SpawnState.SPAWNED
        or pending.get("release_requested") or pending.get("conversation_retired")
        or pending.get("startup_fenced") or pending.get("cooling_token") is not None
    ):
        raise ValueError("retained native redrive pending startup publication not confirmed")


def record_redrive(
    queue: Any, key: str, *, ordinary_redrive: dict, startup_complete: bool,
    startup_revision: int, session_handle: str | None, worktree: str | None,
    now: float | None,
) -> SpawnReservation:
    """Acquire/reconcile one pending startup, or close only its exact revision."""
    from .ordinary_native_bindings import retained_redrive_facts

    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        if (
            row is None or task is None or row["driver"] != "ordinary-bridge"
            or row["startup_revision"] != startup_revision or row["startup_fenced"]
            or row["state"] not in {SpawnState.SPAWNED, SpawnState.RELEASING}
            or row["conversation_retired"] or row["session_handle"] != session_handle
            or row["worktree"] != worktree
            or not row["agent_info"]
            or type(ordinary_redrive.get("version")) is not int
            or ordinary_redrive["version"] != 2
        ):
            raise TaskError("retained native redrive startup fence changed")
        require_no_cooling(row)
        info = json.loads(row["agent_info"])
        revision = row["startup_revision"]
        if startup_complete:
            marker = info.get("retained_redrive")
            invocation = ordinary_redrive.get("invocation")
            if (
                not isinstance(marker, dict)
                or marker.get("startup_revision") != revision
                or marker.get("invocation") != invocation
                or not isinstance(invocation, dict)
                or redrive_invocation(row, invocation.get("machine")) != invocation
            ):
                raise TaskError("retained native redrive completion does not own this startup")
        else:
            queue.require_worker_admission()
            if retained_redrive_facts(conn, row, task) != ordinary_redrive:
                raise TaskError("retained native redrive admission changed")
            if not row["startup_complete"]:
                return SpawnReservation._from_row(row)
            revision += 1
            info["retained_redrive"] = {
                "invocation": ordinary_redrive["invocation"], "startup_revision": revision,
            }
        conn.execute(
            "UPDATE spawn_reservations SET startup_complete=?,startup_revision=?,"
            "agent_info=?,updated_at=? WHERE key=?",
            (int(startup_complete), revision, json.dumps(info, sort_keys=True), queue._now(now), key),
        )
        result = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        conn.commit()
    return SpawnReservation._from_row(result)
