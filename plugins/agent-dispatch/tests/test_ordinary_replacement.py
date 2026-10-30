"""An explicit replacement grant retains the allocation and retired history."""

from dataclasses import asdict
import json

import pytest

from agent_dispatch import ordinary_replacement as replacement
from agent_dispatch.ordinary_native_bindings import native_bindings
from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue

OLD = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def recovery(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("recover work", exclusive_key="worker-slot")
    attempt, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    path = str(tmp_path / "worktree-a")
    queue.record_spawn_worktree(
        attempt.key, "worktree-a", ownership="created",
        allocation_project="example-project", worktree_path=path,
    )
    queue.defer_spawn(attempt.key, detail="retired native head")
    record = {
        "id": "worktree-a", "repo": "example-project", "path": path,
        "status": "active", "creation_nonce": "original-allocation",
        "session_count": 1, "sessions_indexed": True, "last_session_id": OLD,
        "head_revision": 3, "lifecycle_revision": 5,
        "execution_leg": None, "execution_leg_opaque": False,
        "session_backend": None, "session_backend_opaque": False,
        "dispatch_attempt": {"task_id": task.id}, "dispatch_attempt_opaque": False,
    }
    args = {
        "reservation_key": attempt.key, "worktree": "worktree-a",
        "expected_generation": task.generation, "expected_head": OLD,
        "expected_head_revision": 3, "expected_lifecycle_revision": 5,
        "worktree_record": record, "permitted": True, "confirmed_no_live_holder": True,
        "reason": "operator confirmed the predecessor has stopped",
    }
    return queue, task, attempt, args


def current(queue, key):
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        return replacement.current_authorization(conn, row, queue._fetch(conn, row["task_id"]))


def history(queue, key):
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        return native_bindings(conn, row, queue._fetch(conn, row["task_id"]))


@pytest.mark.parametrize("already_retired", [False, True])
def test_grant_preserves_allocation_and_survives_retry(recovery, already_retired):
    queue, task, original, args = recovery
    if already_retired:
        with queue._connect() as conn:
            conn.execute("UPDATE spawn_reservations SET conversation_retired=1 WHERE key=?", (original.key,))
    before = queue.get_reservation(original.key)
    grant = replacement.authorize(queue, task.id, **args)
    assert queue.get(task.id).generation == task.generation + 1
    assert grant["expected_head"] == OLD
    assert grant["task_generation"] == task.generation + 1
    assert grant["authorized_reservation_key"] == original.key
    assert grant["creation_key"] == before.creation_key
    assert grant["replacement_creation_key"] != before.creation_key
    saved = queue.get_reservation(original.key)
    assert saved.conversation_retired
    assert saved.detail == before.detail
    assert saved.worktree_path == before.worktree_path
    assert saved.agent_info == before.agent_info
    assert saved.session_handle == before.session_handle
    retry, created = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    assert created
    assert current(queue, retry.key) == grant
    assert history(queue, retry.key) == ([OLD], [OLD])
    queue.defer_spawn(retry.key, detail="temporary unavailable receiving capability")
    again, created = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    assert created
    assert current(queue, again.key) == grant
    assert again.creation_key == before.creation_key
    assert again.allocation_reservation_key == before.allocation_reservation_key
    assert history(queue, again.key) == ([OLD], [OLD])
    with pytest.raises(TaskError):
        current(queue, retry.key)


@pytest.mark.parametrize("changes", [
    {"permitted": False}, {"permitted": 1}, {"confirmed_no_live_holder": False},
    {"confirmed_no_live_holder": 1}, {"reason": " "},
    {"expected_generation": True}, {"expected_generation": -1},
    {"expected_generation": 2**63 - 1}, {"expected_generation": 1},
    {"reservation_key": "other"}, {"worktree": "other"},
    {"expected_head": "not-a-native-session"}, {"expected_head_revision": True},
    {"expected_head_revision": 2}, {"expected_lifecycle_revision": 4},
    {"expected_lifecycle_revision": 2**63 - 1},
])
def test_bad_request_rolls_back_without_retiring_history(recovery, changes):
    queue, task, original, args = recovery
    with pytest.raises(TaskError):
        replacement.authorize(queue, task.id, **{**args, **changes})
    assert queue.get(task.id).generation == task.generation
    assert not queue.get_reservation(original.key).conversation_retired
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM ordinary_native_replacements").fetchone()[0] == 0


@pytest.mark.parametrize("changes", [
    {"creation_nonce": None}, {"creation_nonce": ""},
    {"head_revision": None}, {"lifecycle_revision": None}, {"session_count": 0},
    {"head_session_id": "different"}, {"session_live": True},
    {"native_liveness": "active"}, {"native_liveness": "unknown"},
    {"sessions_indexed": False}, {"execution_leg": {"state": "stopped"}},
    {"session_backend": {}}, {"execution_leg_opaque": True},
    {"session_backend_opaque": True}, {"execution_leg_live": True},
    {"dispatch_attempt_opaque": True}, {"dispatch_attempt": {"task_id": "foreign"}},
    {"id": "different"}, {"repo": "different"}, {"path": "relative"},
    {"status": "finalizing"},
])
def test_unproven_ground_record_is_not_authority(recovery, changes):
    queue, task, original, args = recovery
    with pytest.raises(TaskError):
        replacement.authorize(
            queue, task.id, **{**args, "worktree_record": {**args["worktree_record"], **changes}},
        )
    assert queue.get(task.id).generation == task.generation
    assert not queue.get_reservation(original.key).conversation_retired


@pytest.mark.parametrize("mutation", [
    "hold", "owner", "native-owner", "cleanup", "active", "handle", "release",
    "bound", "legacy-leg", "foreign-cleanup",
])
def test_queue_authority_changes_refuse_grant(recovery, mutation):
    queue, task, original, args = recovery
    with queue._connect() as conn:
        if mutation == "hold":
            conn.execute("UPDATE tasks SET hold_reason='hold' WHERE id=?", (task.id,))
        elif mutation == "owner":
            conn.execute("UPDATE tasks SET owner='other' WHERE id=?", (task.id,))
        elif mutation == "native-owner":
            conn.execute("UPDATE tasks SET owner_session_id=? WHERE id=?", (OLD, task.id))
        elif mutation == "legacy-leg":
            conn.execute("UPDATE tasks SET source='ci-repair' WHERE id=?", (task.id,))
        else:
            column, value = {
                "cleanup": ("conclusion_state", "pending"),
                "active": ("state", "reserving"), "handle": ("session_handle", OLD),
                "release": ("release_requested", 1), "bound": ("agent_info", "{}"),
                "foreign-cleanup": ("conclusion_state", None),
            }[mutation]
            conn.execute(f"UPDATE spawn_reservations SET {column}=? WHERE key=?", (value, original.key))
    if mutation == "foreign-cleanup":
        foreign = queue.create("other task")
        attempt, _ = queue.reserve_spawn(foreign.id)
        queue.record_spawn_worktree(attempt.key, "worktree-a", ownership="targeted")
        queue.defer_spawn(attempt.key, detail="pending cleanup")
        with queue._connect() as conn:
            conn.execute(
                "UPDATE spawn_reservations SET conclusion_state='pending' WHERE key=?", (attempt.key,),
            )
    with pytest.raises(TaskError):
        replacement.authorize(queue, task.id, **args)
    assert queue.get(task.id).generation == task.generation


def test_lost_authorization_response_cannot_generate_second_key(recovery):
    queue, task, original, args = recovery
    grant = replacement.authorize(queue, task.id, **args)
    with pytest.raises(TaskError):
        replacement.authorize(queue, task.id, **args)
    with queue._connect() as conn:
        grants = conn.execute("SELECT authorization FROM ordinary_native_replacements").fetchall()
    assert [json.loads(row[0]) for row in grants] == [grant]


@pytest.mark.parametrize("mutation", [
    "hold", "generation", "nonce-origin", "path", "project", "creation-key",
    "exclusive-key", "target", "handle", "bound", "owner-session",
])
def test_grant_cannot_authorize_changed_attempt(recovery, mutation):
    queue, task, original, args = recovery
    replacement.authorize(queue, task.id, **args)
    retry, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    with queue._connect() as conn:
        if mutation == "hold":
            conn.execute("UPDATE tasks SET hold_reason='hold' WHERE id=?", (task.id,))
        elif mutation == "generation":
            conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (task.id,))
        elif mutation == "target":
            conn.execute("UPDATE tasks SET target_worktree='other' WHERE id=?", (task.id,))
        elif mutation == "owner-session":
            conn.execute("UPDATE tasks SET owner_session_id=? WHERE id=?", (OLD, task.id))
        else:
            column, value = {
                "nonce-origin": ("allocation_reservation_key", "dispatch-task:other:1"),
                "path": ("worktree_path", "/other"),
                "project": ("allocation_project", "other"),
                "creation-key": ("creation_key", "other"),
                "exclusive-key": ("exclusive_key", "other"),
                "handle": ("session_handle", "some-bridge"),
                "bound": ("agent_info", "{}"),
            }[mutation]
            conn.execute(f"UPDATE spawn_reservations SET {column}=? WHERE key=?", (value, retry.key))
    if mutation == "generation":
        assert current(queue, retry.key) is None
        assert history(queue, retry.key) == ([OLD], [OLD])
    else:
        with pytest.raises(TaskError):
            current(queue, retry.key)


def test_grant_reopen_is_durable_and_read_only(recovery):
    queue, task, original, args = recovery
    grant = replacement.authorize(queue, task.id, **args)
    retry, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    before = asdict(queue.get_reservation(retry.key))
    reopened = TaskQueue(queue.db_path)
    assert current(reopened, retry.key) == grant
    assert asdict(reopened.get_reservation(retry.key)) == before
