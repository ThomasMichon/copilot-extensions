"""Authoritative reads and atomic requeue keep native capacity independently accountable."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import subprocess
import time
from dataclasses import asdict
from threading import Barrier, Event
from types import SimpleNamespace

import httpx
import pytest

from agent_dispatch import pool_decline_observation as observation
from agent_dispatch.client import DispatchError
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.queue_records import SpawnState, Status
from tests import test_pool_placement_client as controls
from tests import test_pool_worker_decline as worker_controls
from tests.test_pool_placement import place, pool, task
from tests.test_pool_worker_decline import prepared_worker, snapshot

q = controls.q
api = controls.api


def test_existing_supervisor_requests_independent_observation_without_finalizing(q, api, ready):
    from agent_dispatch.supervisor import Supervisor
    from agent_dispatch.supervisor_pool_startup import supervise_pool_startup

    task_id, binding, _, calls = ready
    requests = []
    with controls.connected(api, requests) as client:
        owner = Supervisor(
            client, repo="example", machine="host-a", supervisor_id="allocator",
            spawn_fn=lambda *a: pytest.fail("Decline observation must not spawn"),
        )
        reservation, assigned = asdict(q.get_reservation(binding["reservation_key"])), asdict(q.get(task_id))
        assert supervise_pool_startup(owner, reservation, assigned) is True
        assert supervise_pool_startup(owner, reservation, asdict(q.get(task_id))) is None
    assert q.get(task_id).status == Status.QUEUED
    assert q.get_reservation(binding["reservation_key"]).state == SpawnState.RELEASING
    assert len(calls) == 1 and calls[0][0][2] == "record"
    posts = [r for r in requests if r.method == "POST"]
    assert len(posts) == 1 and posts[0].url.path == f"/tasks/{task_id}/pool-decline/closeout"
    assert json.loads(posts[0].content) == {"version": 1, **binding}


@pytest.mark.parametrize("hold", ["observe-only", "allocator", "task", "admission", "governance"])
def test_decline_observation_respects_existing_supervisor_admission(q, api, ready, monkeypatch, hold):
    from agent_dispatch.supervisor import Supervisor
    from agent_dispatch import supervisor_pool_startup as startup

    task_id, binding, _, calls = ready
    requests = []
    with controls.connected(api, requests) as client:
        owner = Supervisor(
            client, repo="example", machine="host-a", supervisor_id="allocator",
            spawn_fn=lambda *a: pytest.fail("Decline observation must not spawn"),
        )
        if hold == "allocator":
            owner.supervisor_id = "other-allocator"
        elif hold == "task":
            q.set_hold(task_id, reason="Operator hold", actor="operator")
        elif hold == "admission":
            monkeypatch.setattr(startup, "action_inhibited", lambda *a, **k: True)
        elif hold == "governance":
            owner._recheck_governance = lambda *a: {"status": "held"}
        before = snapshot(q, task_id)
        assert startup.supervise_pool_startup(
            owner, asdict(q.get_reservation(binding["reservation_key"])), asdict(q.get(task_id)),
            observe_only=hold == "observe-only",
        ) is False
    assert snapshot(q, task_id) == before and not calls
    assert not any(r.method == "POST" for r in requests)


def test_old_coordinator_and_unknown_closeout_response_never_replay(q, api, ready):
    from agent_dispatch.client import DispatchUpgradeRequired

    task_id, binding, _, _ = ready
    original = asdict(q.pool_worker_decline(task_id))
    for mode in ("old-provider", "lost-reply"):
        requests = []

        def rewrite(request, response):
            if mode == "old-provider" and request.url.path == "/health":
                value = response.json()
                value["pool_placement"].pop("worker_decline_local_closeout_versions")
                return httpx.Response(200, json=value)
            if request.method == "POST":
                return httpx.Response(200, json={"protocol": "pool_worker_decline/v1", "version": True})
            return response

        with controls.connected(api, requests, rewrite=rewrite) as client:
            with pytest.raises(DispatchUpgradeRequired if mode == "old-provider" else DispatchError):
                client.close_pool_decline(task_id, original)
        assert sum(r.method == "POST" for r in requests) == (0 if mode == "old-provider" else 1)
    assert q.get(task_id).status == Status.QUEUED


def test_owner_settling_requests_extend_only_their_read_timeout(q, api, ready):
    from agent_dispatch.client_pool_decline import OWNER_SETTLEMENT_READ_SECONDS

    task_id, _, _, _ = ready
    original = asdict(q.pool_worker_decline(task_id))
    requests = []

    def rewrite(request, response):
        if request.method == "POST":
            return httpx.Response(200, json={"protocol": "pool_worker_decline/v1", "version": True})
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        default = httpx.Timeout(client._http.timeout).as_dict()
        for call in (client.close_pool_decline, client.settle_pool_decline):
            with pytest.raises(DispatchError):
                call(task_id, original)
    posts = [r for r in requests if r.method == "POST"]
    assert [r.url.path.rsplit("/", 1)[-1] for r in posts] == ["closeout", "settlement"]
    for request in posts:
        timeout = request.extensions["timeout"]
        assert timeout["read"] == max(default["read"], OWNER_SETTLEMENT_READ_SECONDS)
        assert {k: timeout[k] for k in ("connect", "write", "pool")} == {
            k: default[k] for k in ("connect", "write", "pool")
        }
    assert all(r.extensions["timeout"] == default for r in requests if r.method == "GET")


@pytest.fixture
def ready(q, monkeypatch, request):
    if getattr(request, "param", None):
        monkeypatch.setattr(
            worker_controls, "task", lambda queue, **kwargs: task(queue, dedup_key=request.param, **kwargs),
        )
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
    from agent_dispatch.task_obligations import read_snapshot
    obligation = read_snapshot(q, task_id)
    assert obligation["release_outcome"] == "unassigned_queued"
    assert obligation["unassignment"]["worker_id"] == binding["worker_id"]
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


def test_observed_allocation_driver_must_match_retained_owner(q, ready):
    task_id, binding, value, _ = ready
    value["allocation"]["driver"] = "ordinary-bridge"
    before = snapshot(q, task_id)
    with pytest.raises(TaskError, match="changed its allocation"):
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


@pytest.mark.parametrize("corruption", [None, "exhaustion_reason", "exhausted_at"])
def test_client_observes_declined_cooldown_without_accepting_invalid_budget(
    q, api, ready, corruption,
):
    from tests.test_pool_placement import headers

    task_id, binding, _, _ = ready
    response = api.post(
        f"/tasks/{task_id}/pool-decline/closeout", headers=headers(),
        json={"version": 1, **binding},
    )
    assert response.status_code == 200
    retained = q.pool_startup_snapshot(task_id)
    requests = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if corruption is None or request.url.path.endswith("/contract"):
            return response
        payload = response.json()
        payload["result"]["budget"][corruption] = (
            "unknown_reason" if corruption == "exhaustion_reason" else None
        )
        return httpx.Response(200, json=payload)

    with controls.connected(api, requests, rewrite=rewrite) as client:
        if corruption is not None:
            with pytest.raises(DispatchError, match="invalid budget observation"):
                client.pool_startup_snapshot(task_id)
        else:
            observed = client.pool_startup_snapshot(task_id)
            assert observed == json.loads(json.dumps(retained))
            assert observed["budget"]["exhaustion_reason"] == "worker_declined"
            assert observed["budget"]["cooldown_stage"] == 1
            assert observed["budget"]["startup_attempts"] == 1
            assert observed["task"]["generation"] == binding["generation"] + 1
            assert observed["reservation"]["state"] == SpawnState.RELEASING
            assert observed["native_id"] == binding["native_id"]
    assert all(request.method == "GET" for request in requests)
    assert q.pool_startup_snapshot(task_id) == retained


@pytest.mark.parametrize("original_driver", ["cli", "ordinary-bridge"])
def test_retired_decline_can_select_another_pool_without_reusing_finalized_workspace(
    q, ready, monkeypatch, original_driver,
):
    from agent_dispatch import embody

    task_id, binding, value, _ = ready
    if original_driver == "ordinary-bridge":
        info = {
            "session_id": "bridge", "ready": True, "status": "awaiting_first_prompt",
            "connection": {
                "worktree_id": "worktree", "native_session_id": "native",
                "repo": "example", "target_dir": "/worker",
            },
        }
        with q._connect() as conn:
            conn.execute(
                "UPDATE spawn_reservations SET driver=?, agent_info=? WHERE key=?",
                (original_driver, json.dumps(info), binding["reservation_key"]),
            )
        value["allocation"]["driver"] = original_driver
    original = q.get_reservation(binding["reservation_key"])
    with q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET affinity=? WHERE id=?",
            (json.dumps({"worktree": "worktree", "machine": "host-a", "topic": "preserved"}), task_id),
        )
    q.close_pool_decline(task_id, **binding)
    q.retire_spawn(binding["reservation_key"], exact_absence=True)
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
    assert reservation.allocation_revision == original.allocation_revision + 1
    assert reservation.creation_key != original.creation_key
    assert q.task_pool_assignment(task_id).registration_id == "other"
    assert q.pool_startup(task_id).cooldown_stage == 1
    assert q.pool_startup(task_id).startup_attempts == 2
    assert q.pool_startup(task_id).episode_start_attempt == 1
    assert q.get(task_id).affinity == {"machine": "host-a", "topic": "preserved"}
    assert q.get_reservation(original.key).conclusion_state == "pending"
    replay, admitted = TaskQueue(q.db_path).reserve_spawn(
        task_id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=q.pool_catalog_snapshot().revision, now=4001,
    )
    assert not admitted and replay.creation_key == reservation.creation_key
    assert q.get_reservation(original.key).creation_key == original.creation_key
    calls = []

    def create(argv, **kwargs):
        key = argv[argv.index("--creation-key") + 1]
        assert key != original.creation_key
        assert argv[argv.index("--dispatch-reservation-key") + 1] == reservation.key
        calls.append(key)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "worktree": {"id": "replacement", "path": "/replacement", "repo": "example"},
            "creation": {"version": 1, "status": "complete", "replayed": len(calls) > 1},
        }), "")

    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    monkeypatch.setattr(embody.subprocess, "run", create)
    for current in (reservation, replay):
        prepared = embody.prepare_reusable_worktree(
            asdict(q.get(task_id)), asdict(current), interface="cli", driver="cli", supervisor="allocator",
        )
        assert prepared["worktree"] == "replacement" and prepared["created"]
    assert calls == [reservation.creation_key] * 2
    q.record_spawn_worktree(
        reservation.key, "replacement", ownership="created", allocation_project="example",
        worktree_path="/replacement", creating_host="host-a", execution_machine="host-a", driver="cli",
    )
    q.record_spawn(
        reservation.key, session_handle="native-2", worktree="replacement",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    budget = q.pool_startup(task_id)
    queued = q.exhaust_pool_startup(
        reservation.key, generation=q.get(task_id).generation,
        startup_revision=reservation.startup_revision, now=budget.startup_deadline + 1,
    )
    q.retire_spawn(reservation.key, exact_absence=True, conclusion_state="skipped")
    q.publish_pool_catalog_scope(
        "host-a", "linux", "publisher-0", generation=1, expected_revision=2,
        registration_revision=q.pool_registration_revision(), expected_cohort_revision=1,
        input_revision="retry-pool", definitions=(pool(), pool("other")), now=queued.not_before,
    )
    retry, admitted = TaskQueue(q.db_path).reserve_spawn(
        task_id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=q.pool_catalog_snapshot().revision, now=queued.not_before,
    )
    assert admitted and retry.worktree == "replacement"
    assert retry.creation_key == reservation.creation_key
    assert q.get(task_id).affinity == {"machine": "host-a", "topic": "preserved"}
    assert q.get_reservation(original.key).conclusion_state == "pending"


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


@pytest.mark.parametrize("ready", ["logical-work"], indirect=True)
def test_distinct_logical_successor_does_not_replay_finalized_predecessor(q, ready):
    task_id, binding, _, _ = ready
    original = q.get_reservation(binding["reservation_key"])
    q.close_pool_decline(task_id, **binding)
    q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    q.abandon(task_id, permitted=True, reason="Explicit terminal disposition")
    successor = task(q, dedup_key="logical-work")
    replacement, admitted = q.reserve_spawn(
        successor.id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=q.pool_catalog_snapshot().revision,
    )
    assert admitted and replacement.worktree is None
    assert replacement.allocation_revision == original.allocation_revision + 1
    assert replacement.creation_key != original.creation_key
    assert replacement.allocation_reservation_key == replacement.key
    assert q.get_reservation(original.key).worktree == original.worktree


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
    assert api.app.state.drain_gate.claims == 0


def test_draining_refuses_closeout_before_observation_or_blocker(q, api, ready):
    from tests.test_pool_placement import headers

    task_id, binding, _, calls = ready
    before = snapshot(q, task_id)
    assert api.post("/drain", headers=headers("data"), json={"timeout": 1, "poll": .05}).json()["drained"]
    result = api.post(f"/tasks/{task_id}/pool-decline/closeout", headers=headers(),
                      json={"version": 1, **binding})
    assert result.status_code == 409 and "draining" in result.json()["detail"]
    assert not calls and snapshot(q, task_id) == before
    assert api.app.state.drain_gate.claims == 0


def test_drain_waits_for_observation_and_closeout_commit(q, api, ready, monkeypatch):
    from tests.test_pool_placement import headers

    task_id, binding, value, _ = ready
    entered, finish, draining = Event(), Event(), Event()
    gate = api.app.state.drain_gate
    set_draining = gate.set_draining

    def observed(*args):
        entered.set()
        assert finish.wait(5)
        assert gate.claims == 1
        return value

    def observe_drain(value):
        set_draining(value)
        if value:
            draining.set()

    monkeypatch.setattr(observation, "observe", observed)
    monkeypatch.setattr(gate, "set_draining", observe_drain)
    with ThreadPoolExecutor(max_workers=2) as executor:
        closeout = executor.submit(
            api.post, f"/tasks/{task_id}/pool-decline/closeout", headers=headers(),
            json={"version": 1, **binding},
        )
        try:
            assert entered.wait(5)
            drain = executor.submit(
                api.post, "/drain", headers=headers("data"), json={"timeout": 5, "poll": .05},
            )
            assert draining.wait(5)
            assert gate.claims == 1 and not drain.done()
            assert q.get(task_id).status == Status.CLAIMED
        finally:
            finish.set()
        assert closeout.result(timeout=5).status_code == 200
        assert drain.result(timeout=5).json()["drained"]
    assert gate.claims == 0 and q.get(task_id).status == Status.QUEUED


def test_draining_refuses_settlement_before_queue_reconciliation(q, api, ready, monkeypatch):
    from tests.test_pool_placement import headers

    task_id, binding, _, _ = ready
    monkeypatch.setattr(q, "settle_pool_decline", lambda *a, **k: pytest.fail("settled while draining"))
    assert api.post("/drain", headers=headers("data"), json={"timeout": 1, "poll": .05}).json()["drained"]
    result = api.post(f"/tasks/{task_id}/pool-decline/settlement", headers=headers("data"),
                      json={"version": 1, **binding})
    assert result.status_code == 409 and "draining" in result.json()["detail"]
    assert api.app.state.drain_gate.claims == 0


def test_drain_waits_for_admitted_settlement(q, api, ready, monkeypatch):
    from tests.test_pool_placement import headers

    task_id, binding, _, _ = ready
    closed = q.close_pool_decline(task_id, **binding)
    entered, finish, draining = Event(), Event(), Event()
    gate = api.app.state.drain_gate
    set_draining, settle = gate.set_draining, q.settle_pool_decline

    def settling(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        assert gate.claims == 1
        return settle(*args, **kwargs)

    def observe_drain(value):
        set_draining(value)
        if value:
            draining.set()

    monkeypatch.setattr(q, "settle_pool_decline", settling)
    monkeypatch.setattr(gate, "set_draining", observe_drain)
    with ThreadPoolExecutor(max_workers=2) as executor:
        settlement = executor.submit(
            api.post, f"/tasks/{task_id}/pool-decline/settlement", headers=headers("data"),
            json={"version": 1, **binding},
        )
        try:
            assert entered.wait(5)
            drain = executor.submit(
                api.post, "/drain", headers=headers("data"), json={"timeout": 5, "poll": .05},
            )
            assert draining.wait(5)
            assert gate.claims == 1 and not drain.done()
        finally:
            finish.set()
        result = settlement.result(timeout=5)
        assert result.status_code == 200 and result.json()["closeout"] == closed.receipt
        assert drain.result(timeout=5).json()["drained"]
    assert gate.claims == 0


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


@pytest.fixture
def governed(tmp_path, monkeypatch, request):
    """A Dispatch-allocated worker whose workspace retains its exact task claim."""
    from agent_dispatch import submission_readiness_observation as readiness
    from agent_dispatch.assignment_workspace import AssignmentWorkspace
    from tests.test_assignment_relationships import GroundOwner
    from tests.test_pool_placement import publish

    owner = GroundOwner()
    queue = TaskQueue(tmp_path / "queue.db", relationship_writer=owner, relationship_authority="local")
    owner.queue = queue
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    workspace = AssignmentWorkspace("host-a", "example", "tree", str(tmp_path / "tree"), "created")
    publish(queue, pool())
    requested = task(queue, require_verification=True)
    reservation, _ = place(queue, requested)
    queue.record_spawn_worktree(
        reservation.key, workspace.worktree, ownership="created", creating_host="host-a",
        execution_machine="host-a", driver="cli", allocation_project=workspace.project,
        worktree_path=workspace.path, creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(reservation.key, session_handle="native", startup_complete=True, startup_revision=1)
    assert queue.task_relationship(requested.id)["state"] == "bound"
    binding = {
        "worker_id": workspace.worker_id, "native_id": "native",
        "generation": queue.get(requested.id).generation, "startup_revision": 1,
        "reservation_key": reservation.key,
    }
    if getattr(request, "param", True):
        queue.prepare_pool_decline(
            requested.id, **binding, exclusions=["pool:worker", "worktree:tree"],
            note="The pool lacks the required tool.",
        )
    allocation = {
        "task_id": requested.id, "reservation_key": reservation.key, "attempt": reservation.attempt,
        "driver": "cli", "supervisor": "allocator", "creator_machine": "host-a", "ownership": "created",
    }
    descriptor = {
        "contract": readiness.CONTRACT, "version": 1, "operations": ["observe-ready"],
        "platforms": ["linux", "wsl"], "native_proof": "inuse-hold-v1", "paired_readiness": False,
        "finalized": False, "task_release": False, "process_retirement": False,
    }
    metadata = {"ok": True, "context": {
        "schema": "agent-worktrees.workspace-context", "version": 1,
        "project": workspace.project, "root": workspace.path,
        "worktree": {
            "id": workspace.worktree, "repo": workspace.project, "path": workspace.path,
            "creation_nonce": workspace.creation_nonce, "status": "active",
            "dispatch_attempt_opaque": False, "checkout_managed": True, "dispatch_attempt": allocation,
            "head": {
                "version": 1, "worktree_id": workspace.worktree, "tracked": True,
                "head_session": "native", "active": True, "state": "active",
                "head_revision": 7, "pending_handoffs": [],
            },
        },
    }}
    value = {
        "ok": True, "contract": readiness.CONTRACT, "version": 1, "operation": "observe-ready",
        "ready": True, "task_id": requested.id, "reservation_key": reservation.key,
        "native_id": "native", "worktree_id": workspace.worktree,
        "creation_nonce": workspace.creation_nonce, "head_revision": 7,
        "task_claim_ref": queue.task_relationship(requested.id)["receipt"]["ref"],
        "project": workspace.project, "path": workspace.path, "machine": "HOST-A",
        "allocation": allocation, "checkout": {"head": "a" * 40, "branch": "worktree/tree"},
        "landing": {"bases": ["origin/dev"], "prs": []}, "checkout_retained": True,
        "finalized": False, "task_release": False, "process_retirement": False,
    }
    calls, refusal = [], {}

    def run(argv, **kwargs):
        calls.append(argv)
        if "--describe-readiness-contract" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(descriptor), "")
        if "--cwd" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(metadata), "")
        if refusal:
            return subprocess.CompletedProcess(argv, 1, json.dumps(refusal), "")
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(readiness.remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(readiness.procutil, "agent_worktrees_launch_prefix", lambda: ["selected-worktrees"])
    monkeypatch.setattr(readiness.procutil, "agent_worktrees_environment", lambda: None)
    monkeypatch.setattr(readiness.procutil, "run_background_capture", run)
    monkeypatch.setattr(
        observation.procutil, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("A retained task claim must not require prior finalization"),
    )
    return SimpleNamespace(
        q=queue, owner=owner, task_id=requested.id, binding=binding, calls=calls, refusal=refusal,
    )


def test_readiness_closeout_requeues_then_settles_only_the_exact_claim(governed):
    g = governed
    event_id = g.q.task_relationship(g.task_id)["request"]["event_id"]
    closed = g.q.close_pool_decline(g.task_id, **g.binding, now=101)
    assert closed.applied and closed.relationship == {"event_id": event_id, "state": "closed"}
    assert closed.receipt["task_relationship_event_id"] == event_id
    assert closed.receipt["observation"]["operation"] == "observe-ready"
    current = g.q.get(g.task_id)
    assert current.status == Status.QUEUED and current.generation == g.binding["generation"] + 1
    assert current.owner is None and current.owner_session_id is None
    assert current.excludes == ["pool:worker", "worktree:tree"]
    assert g.q.task_relationship(g.task_id)["state"] == "closed"
    assert g.q.get_reservation(g.binding["reservation_key"]).state == SpawnState.RELEASING
    assert sum("observe-ready" in argv for argv in g.calls) == 1
    retained = snapshot(g.q, g.task_id)
    replay = g.q.close_pool_decline(g.task_id, **g.binding, now=9999)
    assert replay.receipt == closed.receipt and not replay.applied
    assert replay.relationship == closed.relationship
    assert snapshot(g.q, g.task_id) == retained
    assert sum("observe-ready" in argv for argv in g.calls) == 1


def test_readiness_refusal_blocks_before_any_dispatch_mutation(governed):
    g = governed
    g.refusal.update(ok=False, error="unrelated outbound obligation remains unsettled")
    before = snapshot(g.q, g.task_id)
    with pytest.raises(TaskError, match="unrelated outbound obligation"):
        g.q.close_pool_decline(g.task_id, **g.binding)
    assert snapshot(g.q, g.task_id) == before
    assert g.q.task_relationship(g.task_id)["state"] == "bound"
    with pytest.raises(TaskError, match="unconfirmed"):
        g.q.settle_pool_decline(g.task_id, **g.binding)
    assert snapshot(g.q, g.task_id) == before


@pytest.mark.parametrize("required", [False, True])
def test_governed_decline_refuses_a_pinned_workspace_before_any_mutation(governed, required):
    g = governed
    with g.q._connect() as conn:
        if required:
            conn.execute("UPDATE tasks SET requires='[\"worktree:tree\"]' WHERE id=?", (g.task_id,))
        else:
            conn.execute("UPDATE tasks SET target_worktree='tree' WHERE id=?", (g.task_id,))
    before = snapshot(g.q, g.task_id)
    with pytest.raises(TaskError, match="pinned to a worktree"):
        g.q.close_pool_decline(g.task_id, **g.binding)
    assert snapshot(g.q, g.task_id) == before
    assert g.q.task_relationship(g.task_id)["state"] == "bound"
    assert not any("observe-ready" in argv for argv in g.calls)


def test_lost_settlement_reply_retains_claim_then_converges_without_second_requeue(governed):
    g = governed
    g.owner.lose_reply = "close"
    closed = g.q.close_pool_decline(g.task_id, **g.binding, now=101)
    assert closed.applied and closed.relationship["state"] == "retained"
    assert closed.relationship["error"]
    requeued = g.q.get(g.task_id)
    assert requeued.status == Status.QUEUED
    events = len(g.q.events(g.task_id))
    settled = g.q.settle_pool_decline(g.task_id, **g.binding)
    assert not settled.applied and settled.receipt == closed.receipt
    assert settled.relationship["state"] == "closed"
    assert g.q.task_relationship(g.task_id)["state"] == "closed"
    assert g.q.get(g.task_id) == requeued and len(g.q.events(g.task_id)) >= events
    assert g.q.settle_pool_decline(g.task_id, **g.binding).relationship["state"] == "closed"
    with pytest.raises(TaskError):
        g.q.settle_pool_decline(g.task_id, **{**g.binding, "native_id": "foreign"})


def test_concurrent_closeout_and_worker_settlement_reconcile_the_claim_once(governed, monkeypatch):
    g = governed
    event_id = g.q.task_relationship(g.task_id)["request"]["event_id"]
    original, calls, entered = g.q.reconcile_task_relationship, [], Event()

    def reconcile(task_id, **kwargs):
        calls.append(kwargs)
        entered.set()
        time.sleep(0.2)
        return original(task_id, **kwargs)

    monkeypatch.setattr(g.q, "reconcile_task_relationship", reconcile)

    def settle():
        assert entered.wait(5)
        return g.q.settle_pool_decline(g.task_id, **g.binding)

    with ThreadPoolExecutor(2) as pool_:
        closing = pool_.submit(g.q.close_pool_decline, g.task_id, **g.binding, now=101)
        settling = pool_.submit(settle)
        closed, settled = closing.result(10), settling.result(10)
    expected = {"event_id": event_id, "state": "closed"}
    assert closed.applied and closed.relationship == expected
    assert not settled.applied and settled.relationship == expected
    assert calls == [{"event_id": event_id}]
    assert g.q.task_relationship(g.task_id)["state"] == "closed"
