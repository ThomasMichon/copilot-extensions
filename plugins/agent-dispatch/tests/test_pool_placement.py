"""Contained allocator claim/start transactions; no installed lane qualification."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
from threading import Barrier, Event

import pytest
import httpx
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.pool_instances import PoolInstanceDefinition
from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.registrar import Filters
from agent_dispatch.registrar_configuration import ConfiguredSupervisorCohort, SupervisorScopeIntent
from agent_dispatch.queue_pool_placement import select_pool
from agent_dispatch.workspace_binding import record_prepared


@pytest.fixture
def q(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "queue.db")
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    return queue


def pool(name="worker", machine="host-a", env="linux", priority=0, capacity=1, filters=None):
    return PoolInstanceDefinition(name, machine, env, priority, capacity, filters or Filters())


def publish(q, *definitions):
    scopes = sorted({(p.machine, p.env) for p in definitions})
    cohort = ConfiguredSupervisorCohort(
        "complete", hashlib.sha256(b"configured").hexdigest(), 0,
        tuple(SupervisorScopeIntent(machine, machine, env) for machine, env in scopes),
    )
    q.observe_pool_configured_cohort(cohort, expected_revision=0)
    for index, (machine, env) in enumerate(scopes):
        scope = q.begin_pool_publication(
            machine, env, f"publisher-{index}", expected_generation=0, expected_cohort_revision=1,
        )
        q.publish_pool_catalog_scope(
            machine, env, scope.publisher_id, generation=scope.generation,
            expected_revision=0, registration_revision=q.pool_registration_revision(),
            expected_cohort_revision=1, input_revision="inputs",
            definitions=tuple(p for p in definitions if (p.machine, p.env) == (machine, env)),
        )
    return q.pool_catalog_snapshot().revision


def place(q, task, revision=None, **kw):
    return q.reserve_spawn(
        task.id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=q.pool_catalog_snapshot().revision if revision is None else revision,
        **kw,
    )


def task(q, **kw):
    return q.create("work", repo="example", **kw)


def record_native(q, r, *, worktree="worktree", native="native"):
    ownership = r.worktree_ownership or "created"
    q.record_spawn_worktree(
        r.key, worktree, ownership=ownership,
        creating_host="host-a" if ownership == "created" else None,
        execution_machine="host-a", driver="cli",
    )
    return q.record_spawn(r.key, session_handle=native, worktree=worktree)


def test_claim_and_slot_are_committed_before_workspace_or_native_exists(q):
    revision = publish(q, pool())
    t = task(q)
    r, reserved = place(q, t, revision)
    current = q.get(t.id)
    assert reserved and current.status == Status.CLAIMED
    assert current.owner is None and current.owner_session_id is None
    assert current.claimed_at == 100 and current.generation == t.generation + 1
    assert r.worktree is None and r.session_handle is None
    assignment = q.task_pool_assignment(t.id)
    assert assignment.reservation_key == r.key
    assert assignment.task_generation == current.generation
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    assert q.claim_one("other/worktree", task_id=t.id) is None


def test_replay_observes_one_slot_without_renewing_or_reclaiming(q):
    revision = publish(q, pool())
    t = task(q)
    r, _ = place(q, t, revision)
    before = q.get(t.id)
    assert place(q, t, revision - 1) == (r, False)
    assert q.get(t.id) == before
    assert len(q.list_reservations(task_id=t.id)) == 1
    with pytest.raises(TaskError, match="pool placement operation"):
        q.reserve_spawn(t.id)


def test_priority_and_equal_priority_ties_are_not_registration_order(q):
    publish(q, pool("z", priority=9), pool("a", priority=1), pool("b", priority=9))
    first, second, third = task(q), task(q), task(q)
    for t in (first, second, third):
        place(q, t)
    assert [q.task_pool_assignment(t.id).registration_id for t in (first, second, third)] == [
        "b", "z", "a",
    ]


def test_machine_environment_instances_have_separate_capacity(q):
    publish(q, pool(), pool(env="other"), pool(machine="host-b"))
    tasks = [task(q) for _ in range(3)]
    for t in tasks:
        place(q, t)
    assert len({(q.task_pool_assignment(t.id).machine, q.task_pool_assignment(t.id).env)
                for t in tasks}) == 3
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


@pytest.mark.parametrize("change", [
    {"not_before": 101}, {"target_machine": "other"},
    {"excludes": ["pool:worker"]}, {"excludes": ["machine:host-a"]},
    {"excludes": ["env:linux"]},
    {"excludes": ["repo:example"]},
    {"target_worktree": "excluded", "excludes": ["worktree:excluded"]},
])
def test_routing_and_due_time_refusals_leave_no_claim_or_slot(q, change):
    publish(q, pool())
    t = task(q, **change)
    with pytest.raises(TaskError):
        place(q, t)
    assert q.get(t.id).status == Status.QUEUED
    assert q.task_pool_assignment(t.id) is None
    assert q.list_reservations(task_id=t.id) == []


def test_full_permit_and_reject_filters_select_the_first_real_match(q):
    publish(q, pool("rejected", priority=20, filters=Filters(
        permit={"task-type": frozenset({"review"})},
        reject={"task-type": frozenset({"blocked"})},
    )), pool("wrong-repo", priority=10, filters=Filters(
        permit={"repo": frozenset({"another"})},
    )), pool("accepted"))
    t = task(q, labels=["review", "blocked"])
    place(q, t)
    assert q.task_pool_assignment(t.id).registration_id == "accepted"


@pytest.mark.parametrize("reason", ["stale", "expired", "hold", "unconfirmed"])
def test_admission_refusal_does_not_mutate_task_or_reservation(q, reason):
    revision = publish(q, pool())
    t = task(q)
    kw = {}
    if reason == "stale":
        revision -= 1
    elif reason == "expired":
        kw["now"] = 130
    elif reason == "hold":
        q.set_hold(t.id, reason="operator hold", actor="operator")
    else:
        q.observe_pool_configured_cohort(
            ConfiguredSupervisorCohort("unconfirmed", None, None, (), "unknown"),
            expected_revision=1,
        )
        revision = q.pool_catalog_snapshot().revision
    before = q.get(t.id)
    with pytest.raises(TaskError):
        place(q, t, revision, **kw)
    assert q.get(t.id) == before and q.list_reservations(task_id=t.id) == []


def test_legacy_reservations_are_preserved_and_not_guessed_into_pool_capacity(q):
    publish(q, pool())
    legacy = task(q)
    r, _ = q.reserve_spawn(legacy.id)
    with pytest.raises(TaskError, match="legacy capacity"):
        place(q, task(q))
    with pytest.raises(TaskError, match="cannot be adopted"):
        place(q, legacy)
    assert q.get_reservation(r.key) == r and q.get(legacy.id).status == Status.QUEUED


def test_concurrent_allocators_commit_only_one_slot(q):
    revision = publish(q, pool())
    tasks = [task(q), task(q)]
    barrier = Barrier(2)

    def attempt(t):
        barrier.wait(timeout=5)
        try:
            return place(q, t, revision)[1]
        except TaskError as exc:
            assert "capacity" in str(exc)
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, tasks))
    assert sorted(results) == [False, True]
    assert sorted(q.get(t.id).status for t in tasks) == [Status.CLAIMED, Status.QUEUED]


def test_start_requires_current_affirmative_worker_and_exact_recorded_native(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    current = q.get(t.id)
    with pytest.raises(TaskError, match="current reservation"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=current.generation)
    record_native(q, r)
    assert q.get(t.id).status == Status.CLAIMED
    for worker, native, generation in [
        ("host-b/worktree", "native", current.generation),
        ("host-a/other", "native", current.generation),
        ("host-a/worktree", "other-native", current.generation),
        ("host-a/worktree", "native", current.generation - 1),
        ("host-a/worktree", "native", None),
        ("host-a/worktree", "native", True),
    ]:
        with pytest.raises(TaskError, match="pool start"):
            q.start(t.id, worker, owner_session_id=native, expected_generation=generation)
        assert q.get(t.id).status == Status.CLAIMED
    started = q.start(
        t.id, "host-a/worktree", owner_session_id="native", expected_generation=current.generation,
    )
    assert started.status == Status.STARTED and started.owner == "host-a/worktree"
    assert started.owner_session_id == "native" and started.generation == current.generation
    assert q.get_reservation(r.key).session_handle == "native"


def test_startup_release_prevents_late_acceptance_and_retains_capacity(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r)
    q.request_spawn_release(r.key, detail="startup failed")
    with pytest.raises(TaskError, match="pool start"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)
    assert q.get(t.id).status == Status.CLAIMED
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


def test_catalog_without_configured_coverage_cannot_allocate(q):
    pending = q.begin_pool_publication("host-a", "linux", "publisher", expected_generation=0)
    q.publish_pool_catalog_scope(
        pending.machine, pending.env, pending.publisher_id, generation=pending.generation,
        expected_revision=0, registration_revision=0, input_revision="inputs",
        definitions=(pool(),),
    )
    assert q.pool_catalog_snapshot().ready and not q.pool_catalog_snapshot().configured_ready
    with pytest.raises(TaskError, match="complete configured"):
        place(q, task(q))


def test_retained_assignment_waits_instead_of_switching_to_free_lower_priority_pool(q):
    publish(q, pool("first", priority=9), pool("second"))
    t = task(q)
    place(q, t)
    with q._connect() as conn:
        with pytest.raises(TaskError, match="retained.*waiting"):
            select_pool(conn, q.get(t.id), q.pool_catalog_snapshot().definitions)
        with pytest.raises(TaskError, match="unsatisfiable"):
            select_pool(conn, replace(q.get(t.id), excludes=["pool:first"]),
                        q.pool_catalog_snapshot().definitions)
    assert q.task_pool_assignment(t.id).registration_id == "first"


def test_failed_commit_rolls_back_assignment_claim_and_slot(q, monkeypatch):
    publish(q, pool())
    t = task(q)

    def reject_audit(*args, **kw):
        raise TaskError("injected claim commit failure")

    monkeypatch.setattr(q, "_audit", reject_audit)
    with pytest.raises(TaskError, match="commit failure"):
        place(q, t)
    assert q.get(t.id).status == Status.QUEUED
    assert q.task_pool_assignment(t.id) is None and q.list_reservations(task_id=t.id) == []


def test_unaccepted_allocator_claim_cannot_be_yielded_or_bound_by_an_arbitrary_worker(q):
    publish(q, pool())
    t = task(q)
    place(q, t)
    before = q.get(t.id)
    with pytest.raises(TaskError, match="no accepted worker"):
        q.yield_task(t.id, "host-a/worktree")
    with pytest.raises(TaskError, match="owned"):
        q.bind_owner_session(t.id, "host-a/worktree", "native")
    assert q.get(t.id) == before


def headers(credential="control"):
    scheme = "Bear" + "er "
    return {"Authorization": scheme + credential}


@pytest.mark.parametrize("credential", [None, "data", "wrong"])
def test_http_placement_requires_control_authority_without_mutating(q, credential):
    revision = publish(q, pool())
    t = task(q)
    app = create_app(q, token=None, control_token="control", enable_mcp=False,
                     verification_interval=0)
    response = TestClient(app).post(
        "/pool-placements", headers={} if credential is None else headers(credential),
        json={"task_id": t.id, "reserved_by": "allocator", "catalog_revision": revision},
    )
    assert response.status_code == 403
    assert q.get(t.id).status == Status.QUEUED and q.task_pool_assignment(t.id) is None


def test_http_placement_and_affirmative_start_use_real_transactions(q):
    revision = publish(q, pool())
    t = task(q)
    client = TestClient(create_app(q, token="data", control_token="control", enable_mcp=False,
                                  verification_interval=0))
    contract = client.get("/pool-placements/contract", headers=headers())
    assert contract.status_code == 200, contract.text
    assert contract.json() == {
        "protocol": "pool_placement/v1", "supported_versions": [1],
        "supported_drivers": ["cli"],
        "atomic_claim_before_spawn": True, "affirmative_worker_start": True,
        "automatic_lane_migration": False, "startup_retry_policy_complete": False,
    }
    body = {"task_id": t.id, "reserved_by": "allocator", "catalog_revision": revision, "driver": "cli"}
    placed = client.post("/pool-placements", headers=headers(), json=body)
    assert placed.status_code == 200, placed.text
    placement = placed.json()
    assert placement["reserved"] and placement["assignment"]["registration_id"] == "worker"
    assert placement["protocol"] == "pool_placement/v1" and placement["version"] == 1
    assert q.get(t.id).status == Status.CLAIMED
    record_native(q, q.get_reservation(placement["reservation"]["key"]))
    start = {
        "worker_id": "host-a/worktree", "owner_session_id": "native",
        "expected_generation": placement["assignment"]["task_generation"],
    }
    response = client.post(f"/tasks/{t.id}/start", headers=headers(), json=start)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == Status.STARTED
    replay = client.post(
        f"/tasks/{t.id}/start", headers=headers(),
        json={**start, "expected_owner_session_id": "native"},
    )
    assert replay.status_code == 200, replay.text
    assert q.get(t.id).generation == placement["assignment"]["task_generation"]
    assert client.post("/pool-placements", headers=headers(), json=body).json()["reserved"] is False


def test_client_emits_control_placement_and_incarnation_bound_start(q):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    with DispatchClient(
        "http://example.test", token="data", control_token="control",
        transport=httpx.MockTransport(respond),
    ) as client:
        client.place_pool_task("task", reserved_by="allocator", catalog_revision=3, driver="cli")
        client.start("task", "host/worktree", owner_session_id="native", expected_generation=7)
    scheme = "Bear" + "er "
    assert requests[0].headers["Authorization"] == scheme + "control"
    assert requests[0].url.path == "/pool-placements"
    assert json.loads(requests[0].content)["catalog_revision"] == 3
    assert json.loads(requests[1].content) == {
        "worker_id": "host/worktree", "owner_session_id": "native",
        "expected_generation": 7, "expected_owner_session_id": None,
    }


def test_recorded_execution_host_must_match_the_selected_pool(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    with pytest.raises(TaskError, match="execution machine"):
        q.record_spawn_worktree(
            r.key, "worktree", ownership="created", creating_host="host-b",
            execution_machine="host-b", driver="cli",
        )
    with pytest.raises(TaskError, match="execution machine"):
        q.record_spawn(r.key, session_handle="native-b", worktree="worktree")
    assert q.get_reservation(r.key).worktree is None
    assert q.get(t.id).status == Status.CLAIMED


def test_target_worktree_is_carried_and_cannot_be_replaced_by_preparation(q):
    publish(q, pool())
    t = task(q, target_worktree="wanted", affinity={"worktree": "other"})
    r, _ = place(q, t)
    assert r.worktree == "wanted" and r.worktree_ownership == "targeted"
    with pytest.raises(TaskError, match="target worktree"):
        record_native(q, r, worktree="other")
    record_native(q, r, worktree="wanted")
    started = q.start(t.id, "host-a/wanted", owner_session_id="native",
                      expected_generation=q.get(t.id).generation)
    assert started.owner == "host-a/wanted"


def test_sibling_exclusive_contention_is_a_controlled_wait_not_another_task_replay(q):
    revision = publish(q, pool(capacity=2))
    a, b = task(q, exclusive_key="shared"), task(q, exclusive_key="shared")
    r, _ = place(q, a)
    with pytest.raises(TaskError, match="exclusive key"):
        place(q, b)
    client = TestClient(create_app(q, token=None, control_token="control", enable_mcp=False,
                                  verification_interval=0))
    response = client.post(
        "/pool-placements", headers=headers(),
        json={"task_id": b.id, "reserved_by": "allocator", "catalog_revision": revision},
    )
    assert response.status_code == 409 and "exclusive key" in response.json()["detail"]
    assert q.get(b.id).status == Status.QUEUED and q.task_pool_assignment(b.id) is None
    assert q.get_reservation(r.key).task_id == a.id


def test_owner_liveness_does_not_requeue_an_unaccepted_allocator_claim(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r, native="local-body:unaccepted")
    calls = []

    def gone(native):
        calls.append(native)
        return "gone"

    counts = q.reconcile_liveness(headless_local_verdict=gone)
    assert counts["requeued"] == 0 and calls == []
    assert q.get(t.id).status == Status.CLAIMED


@pytest.mark.parametrize("version", [True, 0, 2, 1.0])
def test_unsupported_placement_version_refuses_before_claim(q, version):
    revision = publish(q, pool())
    t = task(q)
    client = TestClient(create_app(q, token=None, control_token="control", enable_mcp=False,
                                  verification_interval=0))
    response = client.post(
        "/pool-placements", headers=headers(),
        json={"task_id": t.id, "reserved_by": "allocator", "catalog_revision": revision,
              "version": version},
    )
    assert response.status_code == 422
    assert q.get(t.id).status == Status.QUEUED and q.task_pool_assignment(t.id) is None


def test_hard_requirements_are_not_invented_from_an_unconstrained_pool_filter(q):
    publish(q, pool("unqualified", priority=20), pool("wrong", priority=10, filters=Filters(
        permit={"capabilities": frozenset({"other"})},
    )), pool("qualified", filters=Filters(
        permit={"capabilities": frozenset({"agent:worker", "gpu"})},
    )))
    t = task(q, requires=["agent:worker", "gpu"])
    place(q, t)
    assert q.task_pool_assignment(t.id).registration_id == "qualified"


def test_admission_time_is_refreshed_after_entering_the_write_transaction(q, monkeypatch):
    revision = publish(q, pool())
    t = task(q)
    observations = iter([100.0, 131.0])
    monkeypatch.setattr(q, "_now", lambda now=None: next(observations))
    with pytest.raises(TaskError, match="complete configured"):
        place(q, t, revision)
    assert q.get(t.id).status == Status.QUEUED and q.task_pool_assignment(t.id) is None


def test_nonfinite_admission_time_cannot_create_a_claim(q):
    revision = publish(q, pool())
    t = task(q)
    with pytest.raises(TaskError, match="finite admission"):
        place(q, t, revision, now=float("nan"))
    assert q.get(t.id).status == Status.QUEUED and q.task_pool_assignment(t.id) is None


def test_draining_refuses_placement_before_any_claim_or_slot(q):
    revision = publish(q, pool())
    t = task(q)
    app = create_app(q, token=None, control_token="control", enable_mcp=False,
                     verification_interval=0)
    client = TestClient(app)
    assert client.post("/drain", json={"timeout": 1, "poll": .05}).json()["drained"]
    response = client.post("/pool-placements", headers=headers(), json={
        "task_id": t.id, "reserved_by": "allocator", "catalog_revision": revision,
    })
    assert response.status_code == 409 and "draining" in response.json()["detail"]
    assert q.get(t.id).status == Status.QUEUED
    assert q.task_pool_assignment(t.id) is None and q.list_reservations(task_id=t.id) == []


def test_drain_waits_for_an_admitted_placement_transaction(q, monkeypatch):
    revision = publish(q, pool())
    t = task(q)
    app = create_app(q, token=None, control_token="control", enable_mcp=False,
                     verification_interval=0)
    client = TestClient(app)
    entered, release, draining = Event(), Event(), Event()
    reserve = q.place_pool_task
    set_draining = app.state.drain_gate.set_draining

    def blocked(*args, **kw):
        entered.set()
        assert release.wait(5)
        return reserve(*args, **kw)

    def observe(value):
        set_draining(value)
        if value:
            draining.set()

    monkeypatch.setattr(q, "place_pool_task", blocked)
    monkeypatch.setattr(app.state.drain_gate, "set_draining", observe)
    with ThreadPoolExecutor(max_workers=2) as executor:
        placement = executor.submit(client.post, "/pool-placements", headers=headers(), json={
            "task_id": t.id, "reserved_by": "allocator", "catalog_revision": revision,
        })
        try:
            assert entered.wait(5)
            drain = executor.submit(client.post, "/drain", json={"timeout": 5, "poll": .05})
            assert draining.wait(5)
            assert app.state.drain_gate.claims == 1 and not drain.done()
        finally:
            release.set()
        assert placement.result(timeout=5).status_code == 200
        assert drain.result(timeout=5).json()["drained"]
    assert app.state.drain_gate.claims == 0


@pytest.mark.parametrize("driver", [None, "", "ordinary-bridge", "restricted-bridge", "unknown"])
def test_unqualified_drivers_cannot_create_pool_claims(q, driver):
    revision = publish(q, pool())
    t = task(q)
    with pytest.raises(TaskError, match="qualified cli"):
        q.reserve_spawn(t.id, reserved_by="allocator", driver=driver, pool_catalog_revision=revision)
    client = TestClient(create_app(q, token=None, control_token="control", enable_mcp=False,
                                  verification_interval=0))
    response = client.post("/pool-placements", headers=headers(), json={
        "task_id": t.id, "reserved_by": "allocator", "catalog_revision": revision, "driver": driver,
    })
    assert response.status_code == 422
    assert q.get(t.id).status == Status.QUEUED and q.task_pool_assignment(t.id) is None


def test_excluded_supplied_capability_is_not_just_a_required_token(q):
    publish(q, pool("gpu", priority=5, filters=Filters(
        permit={"capabilities": frozenset({"gpu"})},
    )), pool("cpu"))
    t = task(q, excludes=["gpu"])
    place(q, t)
    assert q.task_pool_assignment(t.id).registration_id == "cpu"


@pytest.mark.parametrize("excluded", ["gpu", "repo:example"])
def test_new_exclusions_are_checked_at_workspace_and_start_boundaries(q, excluded):
    publish(q, pool(filters=Filters(permit={"capabilities": frozenset({"gpu"})})))
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET excludes = ? WHERE id = ?", (json.dumps([excluded]), t.id))
    with pytest.raises(TaskError, match="excluded"):
        q.record_spawn(r.key, session_handle="native", worktree="worktree")
    with pytest.raises(TaskError, match="excluded"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)
    assert q.get(t.id).status == Status.CLAIMED


@pytest.mark.parametrize("with_native", [False, True])
def test_failed_startup_holds_capacity_until_qualified_retirement(q, with_native):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    if with_native:
        record_native(q, r)
    q.fail_spawn(r.key, detail="failed launch observation, not retirement")
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.request_spawn_release(r.key, finished_startup_revision=1)
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.retire_spawn(r.key, exact_absence=True)
    assert place(q, task(q))[1]


def test_failed_native_remains_counted_after_the_current_assignment_moves(q):
    publish(q, pool(capacity=2))
    t = task(q)
    old, _ = place(q, t)
    record_native(q, old)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(old.key)
    q.yield_task(t.id, "host-a/worktree")
    current, _ = place(q, q.get(t.id))
    assert current.key != old.key
    assert q.task_pool_assignment(t.id).reservation_key == current.key
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with q._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_reservation_bindings").fetchone()[0] == 2


def test_later_exact_same_native_retirement_releases_historical_completed_startup(q):
    publish(q, pool(capacity=2))
    t = task(q)
    old, _ = place(q, t)
    record_native(q, old)
    q.record_spawn(old.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(old.key)
    q.yield_task(t.id, "host-a/worktree")
    current, _ = place(q, q.get(t.id))
    record_native(q, current)
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.request_spawn_release(current.key, finished_startup_revision=1)
    q.retire_spawn(current.key, exact_absence=True)
    assert place(q, task(q))[1]
    assert place(q, task(q))[1]


def test_force_absence_failure_does_not_prove_original_startup_closure(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r)
    q.request_spawn_release(r.key)
    forced = q.fail_spawn(r.key, force=True, confirmed_absent=True)
    assert forced.release_requested and not forced.startup_complete and not forced.startup_fenced
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.request_spawn_release(r.key, finished_startup_revision=1)
    q.retire_spawn(r.key, exact_absence=True)
    assert place(q, task(q))[1]


def test_rearmed_retirement_marker_does_not_prove_original_startup_closure(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r)
    q.fail_spawn(r.key)
    with q._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state = 'rearmed', conversation_retired = 1 WHERE key = ?",
            (r.key,),
        )
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.request_spawn_release(r.key, finished_startup_revision=1)
    q.retire_spawn(r.key, exact_absence=True)
    assert place(q, task(q))[1]


def test_retired_native_with_unknown_transport_custody_still_holds_capacity(q):
    publish(q, pool())
    t = task(q, source="test-producer")
    r, _ = place(q, t)
    record_native(q, r)
    q.request_spawn_release(r.key, finished_startup_revision=1)
    q.retire_spawn(r.key, exact_absence=True)
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO execution_send_custody"
            " (scope_key, send_id, repo, source, producer_generation, task_id, reservation_key,"
            " task_generation, kind, identity, state, detail, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("scope", "send", t.repo, t.source, 1, t.id, r.key, q.get(t.id).generation,
             "launch", "{}", "unknown", "unresolved transport outcome", 100, 100),
        )
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with q._connect() as conn:
        conn.execute("UPDATE execution_send_custody SET state = 'sent' WHERE send_id = 'send'")
    assert place(q, task(q))[1]


@pytest.mark.parametrize("ownership", ["targeted", "reused"])
def test_real_preparation_preserves_non_created_ownership_and_execution_machine(q, ownership):
    publish(q, pool())
    t = task(q, target_worktree="wanted")
    r, _ = place(q, t)

    class Client:
        def record_spawn_worktree(self, key, worktree, **kw):
            return asdict(q.record_spawn_worktree(key, worktree, **kw))

    prepared = {
        "worktree": "wanted", "ownership": ownership, "project": "example", "path": "/workspace",
    }
    record_prepared(Client(), asdict(r), prepared, driver="cli", creating_host="host-a")
    bound = q.get_reservation(r.key)
    assert bound.worktree_ownership == ownership and bound.creating_host is None
    assert bound.execution_machine == "host-a"
    q.record_spawn(r.key, session_handle="native", worktree="wanted")
    assert q.start(t.id, "host-a/wanted", owner_session_id="native",
                   expected_generation=q.get(t.id).generation).status == Status.STARTED


@pytest.mark.parametrize("ownership", ["targeted", "reused"])
def test_non_created_preparation_cannot_bind_the_wrong_execution_machine(q, ownership):
    publish(q, pool())
    t = task(q, target_worktree="wanted")
    r, _ = place(q, t)
    with pytest.raises(TaskError, match="execution machine"):
        q.record_spawn_worktree(r.key, "wanted", ownership=ownership,
                                execution_machine="host-b", driver="cli")
    assert q.get_reservation(r.key).execution_machine is None
    assert q.get_reservation(r.key).worktree_ownership == "targeted"


@pytest.mark.parametrize("replay", [False, True])
def test_http_response_retains_its_transactional_assignment_after_retry_advances(q, monkeypatch, replay):
    revision = publish(q, pool(capacity=2))
    t = task(q)
    if replay:
        place(q, t)
    client = TestClient(create_app(q, token=None, control_token="control", enable_mcp=False,
                                  verification_interval=0))
    captured, release = Event(), Event()
    placements = []
    place_task = q.place_pool_task

    def blocked(*args, **kw):
        result = place_task(*args, **kw)
        placements.append(result)
        captured.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(q, "place_pool_task", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(client.post, "/pool-placements", headers=headers(), json={
            "task_id": t.id, "reserved_by": "allocator", "catalog_revision": revision,
        })
        try:
            assert captured.wait(5)
            original = placements[0]
            record_native(q, original.reservation)
            q.start(t.id, "host-a/worktree", owner_session_id="native",
                    expected_generation=original.assignment.task_generation)
            q.fail_spawn(original.reservation.key)
            q.yield_task(t.id, "host-a/worktree")
            q.request_spawn_release(original.reservation.key, finished_startup_revision=1)
            q.retire_spawn(original.reservation.key, exact_absence=True, conclusion_state="skipped")
            current = place_task(t.id, reserved_by="allocator", catalog_revision=revision)
            assert current.assignment.task_generation > original.assignment.task_generation
        finally:
            release.set()
        result = response.result(timeout=5)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["reserved"] is not replay
    assert body["reservation"] == asdict(original.reservation)
    assert body["assignment"] == asdict(original.assignment)
    assert body["assignment"]["reservation_key"] == body["reservation"]["key"]
    assert current.reservation.key != body["reservation"]["key"]


def retired_predecessor_retry(q):
    publish(q, pool(capacity=2))
    t = task(q)
    old, _ = place(q, t)
    record_native(q, old, native="old-native")
    q.start(t.id, "host-a/worktree", owner_session_id="old-native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(old.key)
    q.yield_task(t.id, "host-a/worktree")
    q.request_spawn_release(old.key, finished_startup_revision=1)
    q.retire_spawn(old.key, exact_absence=True, conclusion_state="skipped")
    retry, _ = place(q, q.get(t.id))
    assert retry.conversation_retired and retry.session_handle is None
    return t, old, retry


def test_replacement_native_clears_only_inherited_retirement_and_can_start(q):
    t, old, retry = retired_predecessor_retry(q)
    recorded = record_native(q, retry, native="replacement")
    assert not recorded.conversation_retired
    assert q.get_reservation(old.key).release_requested
    assert q.start(t.id, "host-a/worktree", owner_session_id="replacement",
                   expected_generation=q.get(t.id).generation).status == Status.STARTED


def test_completed_failed_replacement_holds_capacity_until_its_own_retirement(q):
    t, old, retry = retired_predecessor_retry(q)
    record_native(q, retry, native="replacement")
    q.record_spawn(retry.key, session_handle="replacement", startup_complete=True, startup_revision=1)
    q.fail_spawn(retry.key)
    assert place(q, task(q))[1]
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    assert q.get_reservation(old.key).release_requested
    q.request_spawn_release(retry.key)
    q.retire_spawn(retry.key, exact_absence=True)
    assert place(q, task(q))[1]


def test_retired_predecessor_cannot_be_republished_as_a_replacement(q):
    _, _, retry = retired_predecessor_retry(q)
    before = q.get_reservation(retry.key)
    q.record_spawn_worktree(retry.key, "worktree", ownership="reused",
                            execution_machine="host-a", driver="cli")
    with pytest.raises(TaskError, match="retired native"):
        q.record_spawn(retry.key, session_handle="old-native", worktree="worktree")
    current = q.get_reservation(retry.key)
    assert current.conversation_retired and current.session_handle == before.session_handle
    assert current.state == before.state and current.startup_revision == before.startup_revision


@pytest.mark.parametrize("selector", ["target", "required", "none"])
def test_ordinary_bridge_to_cli_cannot_discard_explicit_worktree_before_claim(q, selector):
    original_task = task(q, dedup_key="logical-driver-transition")
    original, _ = q.reserve_spawn(original_task.id, driver="ordinary-bridge")
    q.record_spawn_worktree(
        original.key, "worktree", ownership="created", creating_host="host-a",
        execution_machine="host-a", driver="ordinary-bridge",
    )
    q.request_spawn_release(original.key, finished_startup_revision=1)
    q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    q.abandon(original_task.id, permitted=True, reason="synthetic prior request")
    publish(q, pool())
    successor = task(
        q, dedup_key="logical-driver-transition",
        **({"target_worktree": "worktree"} if selector == "target" else
           {"requires": ["worktree:worktree"]} if selector == "required" else {}),
    )
    if selector == "none":
        replacement, reserved = place(q, successor)
        assert reserved and replacement.worktree is None
        assert q.get(successor.id).status == Status.CLAIMED
    else:
        with pytest.raises(TaskError, match="driver transition cannot discard"):
            place(q, successor)
        assert q.get(successor.id) == successor
        assert q.task_pool_assignment(successor.id) is None
        assert q.list_reservations(task_id=successor.id) == []


def test_late_exact_retirement_of_a_carried_native_prevents_retry_acceptance(q):
    publish(q, pool(capacity=2))
    t = task(q)
    old, _ = place(q, t)
    record_native(q, old)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(old.key)
    q.yield_task(t.id, "host-a/worktree")
    retry, _ = place(q, q.get(t.id))
    record_native(q, retry)
    assert not q.get_reservation(retry.key).conversation_retired
    q.request_spawn_release(old.key, finished_startup_revision=1)
    q.retire_spawn(old.key, exact_absence=True)
    with pytest.raises(TaskError, match="retired native"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)
    with pytest.raises(TaskError, match="retired native"):
        q.record_spawn(retry.key, session_handle="native")
    assert q.get(t.id).status == Status.CLAIMED


def test_live_native_identity_cannot_be_overwritten_without_preserved_history(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r)
    before = q.get_reservation(r.key)
    with pytest.raises(TaskError, match="retained native"):
        q.record_spawn(r.key, session_handle="different-native")
    assert q.get_reservation(r.key) == before


def test_unproven_retirement_marker_cannot_authorize_a_replacement(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    q.record_spawn_worktree(r.key, "worktree", ownership="created", creating_host="host-a",
                            execution_machine="host-a", driver="cli")
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET conversation_retired=1 WHERE key=?", (r.key,))
    with pytest.raises(TaskError, match="proven replacement"):
        q.record_spawn(r.key, session_handle="new-native")
    assert q.get_reservation(r.key).conversation_retired
    assert q.get_reservation(r.key).session_handle is None


@pytest.mark.parametrize("selector", [
    "machine:host-a", "repo:example", "env:linux", "pool:worker", "worktree:wanted",
])
def test_intrinsic_requirements_match_actual_execution_without_capability_declarations(q, selector):
    publish(q, pool())
    t = task(q, requires=[selector], target_worktree="wanted")
    r, _ = place(q, t)
    record_native(q, r, worktree="wanted")
    assert q.start(t.id, "host-a/wanted", owner_session_id="native",
                   expected_generation=q.get(t.id).generation).status == Status.STARTED


@pytest.mark.parametrize("selector", [
    "machine:wrong", "repo:wrong", "env:wrong", "pool:wrong", "worktree:wrong",
])
def test_declared_capabilities_cannot_impersonate_intrinsic_execution_identity(q, selector):
    publish(q, pool(filters=Filters(permit={"capabilities": frozenset({selector})})))
    t = task(q, requires=[selector], target_worktree="wanted")
    with pytest.raises(TaskError, match="eligible"):
        place(q, t)
    assert q.get(t.id).status == Status.QUEUED and q.task_pool_assignment(t.id) is None


def test_worktree_requirement_cannot_invent_a_future_workspace(q):
    publish(q, pool())
    t = task(q, requires=["worktree:future"])
    with pytest.raises(TaskError, match="eligible"):
        place(q, t)
    assert q.list_reservations(task_id=t.id) == []


@pytest.mark.parametrize("selector", [
    "machine:wrong", "repo:wrong", "env:wrong", "pool:wrong", "worktree:wrong", "gpu",
])
def test_changed_requirements_are_rechecked_at_publication_and_acceptance(q, selector):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET requires=? WHERE id=?", (json.dumps([selector]), t.id))
    with pytest.raises(TaskError, match="requirements"):
        q.record_spawn(r.key, session_handle="native", worktree="worktree")
    with pytest.raises(TaskError, match="requirements"):
        q.record_spawn(r.key, session_handle="native")
    with pytest.raises(TaskError, match="requirements"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)
    assert q.get(t.id).status == Status.CLAIMED


def test_releasing_retry_records_a_late_replacement_without_inheriting_its_retirement(q):
    t, _, retry = retired_predecessor_retry(q)
    q.record_spawn_worktree(retry.key, "worktree", ownership="reused",
                            execution_machine="host-a", driver="cli")
    q.request_spawn_release(retry.key)
    result = q.record_spawn(
        retry.key, session_handle="late-replacement", startup_complete=True, startup_revision=1,
    )
    assert result.release_requested and result.state == "releasing"
    assert result.session_handle == "late-replacement" and not result.conversation_retired
    with pytest.raises(TaskError, match="pool start"):
        q.start(t.id, "host-a/worktree", owner_session_id="late-replacement",
                expected_generation=q.get(t.id).generation)
    assert place(q, task(q))[1]
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.retire_spawn(retry.key, exact_absence=True)
    assert place(q, task(q))[1]
