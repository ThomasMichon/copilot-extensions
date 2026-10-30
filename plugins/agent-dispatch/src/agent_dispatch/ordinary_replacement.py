"""Explicit queue authority for a new native conversation in the same allocation.

This grant does not prove native death or change the ground owner's head.
Bridge must recheck the exact allocation and use the ground owner's conditional
replacement operation before recording an identity or sending work.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from .ordinary_agent_facts import owner_record
from .queue_records import SpawnState, Status, TaskError

CONTRACT = "agent-dispatch/ordinary-native-replacement"
VERSION = 1
BRIDGE_PROTOCOL = 52
MAX_GENERATION = 2**63 - 1


def ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ordinary_native_replacements ("
        "task_id TEXT NOT NULL, task_generation INTEGER NOT NULL,"
        "reservation_key TEXT NOT NULL UNIQUE, worktree TEXT NOT NULL,"
        "allocation_reservation_key TEXT NOT NULL, creation_key TEXT NOT NULL,"
        "authorization TEXT NOT NULL, created_at REAL NOT NULL,"
        "PRIMARY KEY(task_id, task_generation))"
    )


def _exact_head(record: dict, expected_head: str, head_revision: int, lifecycle_revision: int) -> None:
    try:
        canonical = str(UUID(expected_head))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TaskError("replacement requires an exact canonical native session UUID") from exc
    if (
        canonical != expected_head
        or type(head_revision) is not int or not 0 < head_revision <= MAX_GENERATION
        or type(lifecycle_revision) is not int
        or not head_revision <= lifecycle_revision < MAX_GENERATION
        or record.get("last_session_id") != expected_head
        or record.get("head_revision") != head_revision
        or record.get("lifecycle_revision") != lifecycle_revision
        or type(record.get("head_revision")) is not int
        or type(record.get("lifecycle_revision")) is not int
        or record.get("status") != "active"
        or record.get("sessions_indexed") is not True
        or type(record.get("session_count")) is not int or record["session_count"] < 1
        or not isinstance(record.get("creation_nonce"), str) or not record["creation_nonce"].strip()
        or record.get("execution_leg_opaque") is not False
        or record.get("session_backend_opaque") is not False
        or record.get("execution_leg") is not None or record.get("session_backend") is not None
        or record.get("execution_leg_live") is True
    ):
        raise TaskError("replacement native head, revision or ordinary allocation changed")


def authorize(
    queue, task_id: str, *, reservation_key: str, worktree: str,
    expected_generation: int, expected_head: str, expected_head_revision: int,
    expected_lifecycle_revision: int, worktree_record: dict,
    permitted: bool = False, confirmed_no_live_holder: bool = False, reason: str,
) -> dict:
    """Persist one generation-bound control grant, preserving historical evidence.

    The control route obtains ``worktree_record`` from the ground owner itself;
    a worker-supplied record or a boolean attestation alone is not authority.
    """
    from .ordinary_native_bindings import native_bindings
    from .queue_spawn_rearm import recovery_reservation

    if permitted is not True or confirmed_no_live_holder is not True or not reason.strip():
        raise TaskError("native replacement requires explicit permission, no-live-holder attestation and reason")
    if type(expected_generation) is not int or not 0 <= expected_generation < MAX_GENERATION:
        raise TaskError("native replacement requires an exact bounded task generation")
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task, row = recovery_reservation(
            queue, conn, task_id, reservation_key=reservation_key, worktree=worktree,
            expected_generation=expected_generation, allow_retired=True,
        )
        if task.source == "ci-repair" or row["agent_info"]:
            raise TaskError("native replacement requires an unbound ordinary recovery attempt")
        record = owner_record(worktree_record, dict(row), task_id)
        _exact_head(record, expected_head, expected_head_revision, expected_lifecycle_revision)
        if any(not isinstance(row[name], str) or not row[name] for name in (
            "allocation_reservation_key", "creation_key", "allocation_project", "worktree_path",
        )):
            raise TaskError("native replacement requires retained original allocation provenance")
        known, retired = native_bindings(conn, row, task)
        if any(native != expected_head and native not in retired for native in known):
            raise TaskError("native replacement conflicts with retained unretired identity")
        generation = expected_generation + 1
        authorization = {
            "contract": CONTRACT, "version": VERSION, "task_id": task_id,
            "task_generation": generation, "authorized_reservation_key": reservation_key,
            "exclusive_key": task.exclusive_key,
            "allocation_reservation_key": row["allocation_reservation_key"],
            "creation_key": row["creation_key"], "worktree_id": worktree,
            "project": row["allocation_project"], "worktree_path": row["worktree_path"],
            "creation_nonce": record["creation_nonce"], "expected_head": expected_head,
            "expected_head_revision": expected_head_revision,
            "expected_lifecycle_revision": expected_lifecycle_revision,
            "replacement_creation_key": f"ordinary-replacement:{uuid4()}",
        }
        timestamp = queue._now(None)
        conn.execute(
            "INSERT INTO ordinary_native_replacements VALUES (?,?,?,?,?,?,?,?)",
            (task_id, generation, reservation_key, worktree, row["allocation_reservation_key"],
             row["creation_key"], json.dumps(authorization, sort_keys=True), timestamp),
        )
        conn.execute(
            "UPDATE tasks SET generation=?,updated_at=? WHERE id=?", (generation, timestamp, task_id),
        )
        conn.execute(
            "UPDATE spawn_reservations SET conversation_retired=1,updated_at=? WHERE key=?",
            (timestamp, reservation_key),
        )
        queue._audit(
            conn, task_id, ts=timestamp, from_status=Status.QUEUED, to_status=Status.QUEUED,
            worker="operator",
            note=(
                f"ordinary native replacement authorized: reservation={reservation_key}; "
                f"worktree={worktree}; generation={expected_generation}->{generation}; "
                f"previous native={expected_head}; history preserved; reason={reason.strip()}"
            ),
        )
        conn.commit()
    return authorization


def retired_heads(conn, row) -> list[str]:
    """Retirement survives generation changes; this is evidence, never a grant."""
    return [
        json.loads(item["authorization"])["expected_head"]
        for item in conn.execute(
            "SELECT authorization FROM ordinary_native_replacements "
            "WHERE worktree=? AND allocation_reservation_key=? AND creation_key=?",
            (row["worktree"], row["allocation_reservation_key"], row["creation_key"]),
        )
    ]


def current_authorization(conn, row, task) -> dict | None:
    """Return authority only for the current unbound attempt and exact generation."""
    item = conn.execute(
        "SELECT * FROM ordinary_native_replacements WHERE task_id=? AND task_generation=?",
        (task.id, task.generation),
    ).fetchone()
    if item is None:
        return None
    from .ordinary_agent_storage import _admission_error

    grant = json.loads(item["authorization"])
    if error := _admission_error(conn, row, task):
        raise TaskError(error)
    original = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (item["reservation_key"],),
    ).fetchone()
    if (
        original is None or original["attempt"] >= row["attempt"]
        or original["state"] not in {SpawnState.DEFERRED, SpawnState.FAILED, SpawnState.REARMED}
        or not original["conversation_retired"]
        or row["agent_info"] or row["session_handle"]
        or task.owner_session_id or not row["conversation_retired"]
        or row["worktree"] != grant["worktree_id"]
        or row["allocation_reservation_key"] != grant["allocation_reservation_key"]
        or row["creation_key"] != grant["creation_key"]
        or row["allocation_project"] != grant["project"]
        or row["worktree_path"] != grant["worktree_path"]
        or row["exclusive_key"] != grant["exclusive_key"]
        or task.target_worktree not in (None, grant["worktree_id"])
    ):
        raise TaskError("ordinary native replacement authorization no longer matches the allocation")
    return grant


def validate_replacement(conn, row, task, info: dict, record: dict | None) -> None:
    """Accept the new identity only after exact owner readback under queue fences."""
    from .ordinary_agent_facts import identity

    grant = current_authorization(conn, row, task)
    if grant is None or record is None:
        raise TaskError("retired history requires a proven current native head")
    actual = identity(info)
    creation = info.get("creation")
    proof = creation.get("native_replacement") if isinstance(creation, dict) else None
    if not isinstance(proof, dict) or not isinstance(creation, dict):
        raise TaskError("native replacement requires its receiving-owner proof")
    revision = proof.get("head_revision")
    if (
        proof != {**grant, "replacement_session": actual.native, "head_revision": revision}
        or type(revision) is not int or not grant["expected_head_revision"] < revision <= MAX_GENERATION
        or actual.native == grant["expected_head"]
        or creation.get("version") != 1 or type(creation.get("version")) is not int
        or creation.get("status") != "complete" or creation.get("turn_index") is not None
        or creation.get("native_session_id") != actual.native
        or record.get("last_session_id") != actual.native
        or record.get("creation_nonce") != grant["creation_nonce"]
        or type(record.get("head_revision")) is not int or record["head_revision"] != revision
        or type(record.get("lifecycle_revision")) is not int or record["lifecycle_revision"] < revision
        or record.get("sessions_indexed") is not True
        or type(record.get("session_count")) is not int or record["session_count"] < 2
        or record.get("execution_leg") is not None or record.get("session_backend") is not None
        or record.get("execution_leg_opaque") is not False or record.get("session_backend_opaque") is not False
    ):
        raise TaskError("native replacement proof or exact ground head changed")
