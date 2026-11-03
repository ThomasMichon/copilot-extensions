"""Authoritative reads and atomic requeue keep native capacity independently accountable."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import subprocess
from threading import Barrier

import pytest

from agent_dispatch import pool_decline_observation as observation
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.queue_records import SpawnState, Status
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import place, pool, task
from tests.test_pool_worker_decline import prepared_worker, snapshot

q = controls.q
api = controls.api


@pytest.fixture
def ready(q, monkeypatch):
    task_id, binding = prepared_worker(q)
    q.prepare_pool_decline(task_id, **binding)
    with q._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET allocation_project='example', worktree_path='/worker'"
            " WHERE key=?", (binding["reservation_key"],),
        )
    arguments = {key: value for key, value in binding.items() if key not in {"exclusions", "note"}}
    value = {
        "ok": True, "contract": observation.CONTRACT, "version": 1, "operation": "observe-finalized",
        "task_id": task_id, "reservation_key": binding["reservation_key"], "native_id": "native",
        "worktree_id": "worktree", "creation_nonce": "created", "project": "example",
        "path": "/worker", "machine": "host-a", "status": "finalized", "head_revision": 0,
        "checkout_retained": True, "task_release": False, "process_retirement": False,
        "checkout": {"head": "a" * 40, "branch": "worktree/worker"},
        "allocation": {
            "task_id": task_id, "reservation_key": binding["reservation_key"], "attempt": 1,
            "driver": "cli", "supervisor": "allocator", "creator_machine": "host-a", "ownership": "created",
        },
    }
    calls = []

    def run(*argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(observation.remote_dispatch, "local_machine", lambda: "HOST-A")
    monkeypatch.setattr(observation.procutil, "run_agent_worktrees_capture", run)
    return task_id, arguments, value, calls


def test_closeout_atomically_requeues_but_retains_native_capacity_and_history(q, ready):
    task_id, binding, value, calls = ready
    before = snapshot(q, task_id)
    closed = q.close_pool_decline(task_id, **binding, now=101)
    receipt = closed.receipt
    assert closed.applied
    current = q.get(task_id)
    reservation = q.get_reservation(binding["reservation_key"])
    assert current.status == Status.QUEUED and current.generation == binding["generation"] + 1
    assert current.excludes == ["capability:unrelated", "pool:worker", "worktree:worktree"]
    assert current.not_before == 3701 and q.task_pool_assignment(task_id) is None
    assert reservation.state == SpawnState.RELEASING and reservation.release_requested
    assert reservation.startup_fenced and not reservation.conversation_retired
    assert reservation.session_handle == "native" and reservation.worktree == "worktree"
    assert q.pool_startup(task_id).startup_attempts == before[2].startup_attempts
    assert q.pool_startup(task_id).cooldown_stage == 1
    assert len(q.events(task_id)) == len(before[5]) + 1
    assert receipt["observation"] == value and receipt["process_retirement"] is False
    assert len(calls) == 1 and calls[0][1]["timeout"] == 15
    retained = snapshot(q, task_id)
    reopened = TaskQueue(q.db_path)
    replay = reopened.close_pool_decline(task_id, **binding, now=9999)
    assert replay.receipt == receipt and not replay.applied
    assert snapshot(q, task_id) == retained and len(calls) == 1
    with pytest.raises(TaskError, match="allocator"):
        q.claim_one("other/worker", task_id=task_id, now=9999)
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with pytest.raises(TaskError, match="native retirement pending"):
        place(q, current)
    assert not q.get_reservation(binding["reservation_key"]).conversation_retired


@pytest.mark.parametrize("change", [
    {"ok": 1}, {"version": True}, {"operation": "finalize-own"}, {"task_id": "foreign"},
    {"reservation_key": "foreign"}, {"native_id": "foreign"}, {"worktree_id": "foreign"},
    {"path": "/foreign"}, {"project": "foreign"}, {"machine": "host-b"},
    {"creation_nonce": ""}, {"status": "active"}, {"head_revision": True},
    {"checkout_retained": False}, {"process_retirement": True}, {"allocation": {}},
    {"checkout": {}}, {"checkout": {"head": "not-a-commit", "branch": "worktree/worker"}},
])
def test_unqualified_owner_reply_never_changes_claim_or_routing(q, ready, change):
    task_id, binding, value, _ = ready
    value.update(change)
    before = snapshot(q, task_id)
    with pytest.raises(TaskError):
        q.close_pool_decline(task_id, **binding)
    assert snapshot(q, task_id) == before


@pytest.mark.parametrize("kind", ["remote", "missing-host", "unsupported", "timeout", "malformed"])
def test_missing_or_old_owner_never_falls_back_to_copied_worker_json(q, ready, monkeypatch, kind):
    task_id, binding, _, calls = ready
    if kind in {"remote", "missing-host"}:
        monkeypatch.setattr(observation.remote_dispatch, "local_machine",
                            lambda: "host-b" if kind == "remote" else None)
    else:
        def run(*a, **k):
            if kind == "timeout":
                raise subprocess.TimeoutExpired("probe", 15)
            return subprocess.CompletedProcess(a, 0, "{}" if kind == "unsupported" else "not-json", "")
        monkeypatch.setattr(observation.procutil, "run_agent_worktrees_capture", run)
    before = snapshot(q, task_id)
    with pytest.raises(TaskError):
        q.close_pool_decline(task_id, **binding)
    assert snapshot(q, task_id) == before
    assert not calls


@pytest.mark.parametrize("kind", ["hold", "steer", "native", "revision", "routing", "blocker"])
def test_change_during_owner_read_refuses_stale_observation(q, ready, monkeypatch, kind):
    task_id, binding, value, _ = ready

    def raced(*args):
        with q._connect() as conn:
            if kind == "hold":
                conn.execute("UPDATE tasks SET hold_reason='operator hold' WHERE id=?", (task_id,))
            elif kind == "steer":
                conn.execute("UPDATE tasks SET awaiting_steer=1 WHERE id=?", (task_id,))
            elif kind == "routing":
                conn.execute("UPDATE tasks SET excludes='[\"pool:other\"]' WHERE id=?", (task_id,))
            elif kind == "blocker":
                conn.execute("UPDATE pool_worker_declines SET blocker='new blocker' WHERE task_id=?", (task_id,))
            else:
                conn.execute(
                    "UPDATE spawn_reservations SET " + (
                        "session_handle='foreign'" if kind == "native" else "startup_revision=2"
                    ) + " WHERE key=?", (binding["reservation_key"],),
                )
        return value

    monkeypatch.setattr(observation, "observe", raced)
    with pytest.raises(TaskError):
        q.close_pool_decline(task_id, **binding)
    assert q.get(task_id).status == Status.CLAIMED
    assert not q.get_reservation(binding["reservation_key"]).release_requested


def test_two_observers_commit_one_disposition_and_one_cooldown(q, ready, monkeypatch):
    task_id, binding, value, _ = ready
    barrier = Barrier(2)

    def observed(*args):
        barrier.wait(timeout=5)
        return deepcopy(value)

    monkeypatch.setattr(observation, "observe", observed)
    before = len(q.events(task_id))
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _: q.close_pool_decline(task_id, **binding), range(2)))
    assert receipts[0].receipt == receipts[1].receipt
    assert sorted(result.applied for result in receipts) == [False, True]
    assert len(q.events(task_id)) == before + 1
    assert q.pool_startup(task_id).cooldown_stage == 1


def test_later_due_time_and_cooldown_stage_survive_decline(q, ready):
    task_id, binding, _, _ = ready
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET not_before=100000 WHERE id=?", (task_id,))
        conn.execute("UPDATE task_pool_assignments SET cooldown_stage=4 WHERE task_id=?", (task_id,))
    q.close_pool_decline(task_id, **binding)
    assert q.get(task_id).not_before == 100000
    assert q.pool_startup(task_id).cooldown_stage == 4


def test_retired_decline_can_select_another_pool_without_reusing_finalized_workspace(q, ready):
    task_id, binding, _, _ = ready
    q.close_pool_decline(task_id, **binding)
    q.retire_spawn(binding["reservation_key"], exact_absence=True, conclusion_state="skipped")
    q.publish_pool_catalog_scope(
        "host-a", "linux", "publisher-0", generation=1, expected_revision=1,
        registration_revision=q.pool_registration_revision(), expected_cohort_revision=1,
        input_revision="second-pool", definitions=(pool(), pool("other")), now=4000,
    )
    reservation, reserved = q.reserve_spawn(
        task_id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=q.pool_catalog_snapshot().revision, now=4000,
    )
    assert reserved and reservation.key != binding["reservation_key"]
    assert reservation.worktree is None and reservation.session_handle is None
    assert reservation.allocation_reservation_key == reservation.key
    assert q.task_pool_assignment(task_id).registration_id == "other"
    assert q.pool_startup(task_id).cooldown_stage == 1
    assert q.pool_startup(task_id).startup_attempts == 2
    assert q.pool_startup(task_id).episode_start_attempt == 1


@pytest.mark.parametrize("required", [False, True])
def test_decline_does_not_unpin_an_explicit_or_required_workspace(q, ready, required):
    task_id, binding, _, _ = ready
    with q._connect() as conn:
        if required:
            conn.execute("UPDATE tasks SET requires='[\"worktree:worktree\"]' WHERE id=?", (task_id,))
        else:
            conn.execute("UPDATE tasks SET target_worktree='worktree' WHERE id=?", (task_id,))
    q.close_pool_decline(task_id, **binding)
    q.retire_spawn(binding["reservation_key"], exact_absence=True, conclusion_state="skipped")
    q.publish_pool_catalog_scope(
        "host-a", "linux", "publisher-0", generation=1, expected_revision=1,
        registration_revision=q.pool_registration_revision(), expected_cohort_revision=1,
        input_revision="fresh-pool", definitions=(pool(),), now=4000,
    )
    before = snapshot(q, task_id)
    with pytest.raises(TaskError, match="unsatisfiable explicit target"):
        q.reserve_spawn(
            task_id, reserved_by="allocator", driver="cli",
            pool_catalog_revision=q.pool_catalog_snapshot().revision, now=4000,
        )
    assert snapshot(q, task_id) == before


def test_only_control_can_request_coordinator_observation_and_no_receipt_is_accepted(q, api, ready):
    task_id, binding, _, _ = ready
    url = f"/tasks/{task_id}/pool-decline/closeout"
    from tests.test_pool_placement import headers
    assert api.post(url, json={"version": 1, **binding}).status_code == 401
    assert api.post(url, headers=headers("data"), json={"version": 1, **binding}).status_code == 403
    assert api.post(url, headers=headers(), json={
        "version": 1, **binding, "finalized": True,
    }).status_code == 422
    result = api.post(url, headers=headers(), json={"version": 1, **binding})
    assert result.status_code == 200 and result.json()["closeout"]["observation"]["status"] == "finalized"
    assert q.get(task_id).status == Status.QUEUED


def test_failed_owner_read_records_a_visible_closeout_blocker(q, api, ready, monkeypatch):
    task_id, binding, _, _ = ready
    monkeypatch.setattr(observation.remote_dispatch, "local_machine", lambda: "host-b")
    from tests.test_pool_placement import headers
    result = api.post(f"/tasks/{task_id}/pool-decline/closeout", headers=headers(),
                      json={"version": 1, **binding})
    assert result.status_code == 409
    assert q.get(task_id).status == Status.CLAIMED
    assert "remote custody retained" in q.pool_worker_decline(task_id).blocker


def test_historical_api_replay_does_not_announce_a_later_task_as_queued(q, api, ready, monkeypatch):
    from agent_dispatch.events import EventBus
    from tests.test_pool_placement import headers

    task_id, binding, _, calls = ready
    events = []
    monkeypatch.setattr(EventBus, "publish", lambda self, event: events.append(event))
    url = f"/tasks/{task_id}/pool-decline/closeout"
    first = api.post(url, headers=headers(), json={"version": 1, **binding})
    assert first.status_code == 200 and len(events) == 2
    q.abandon(task_id, permitted=True, reason="Explicit later disposition")
    events.clear()
    before = snapshot(q, task_id)
    replay = api.post(url, headers=headers(), json={"version": 1, **binding})
    assert replay.json() == first.json()
    assert not events and len(calls) == 1 and snapshot(q, task_id) == before
