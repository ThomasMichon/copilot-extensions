"""Operator-attested reconciliation of unproven legacy reservation history."""

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError
from agent_dispatch.queue_pool_accountability import reservation_accountable
from tests._helpers import RepoDefaultingQueue as TaskQueue

ATTESTED = {"permitted": True, "confirmed_no_live_holder": True, "reason": "operator verified"}


def _legacy(queue, title, *, handle=None, finish="settle", now=1000.0, concluded=True,
            driver=None, worktree=None):
    task = queue.create(title)
    reservation, _ = queue.reserve_spawn(task.id, now=now, driver=driver)
    if handle:
        queue.record_spawn(reservation.key, session_handle=handle, worktree=worktree, now=now)
    getattr(queue, "settle_spawn" if finish == "deferred" else f"{finish}_spawn")(
        reservation.key, detail="historical",
    )
    with queue._connect() as conn:  # emulate rows predating startup columns
        conn.execute(
            "UPDATE spawn_reservations SET startup_complete=0, startup_fenced=0,"
            " conversation_retired=0, release_requested=0 WHERE key=?", (reservation.key,),
        )
        if finish == "deferred":
            conn.execute("UPDATE spawn_reservations SET state='deferred' WHERE key=?", (reservation.key,))
        if concluded:
            conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (task.id,))
    return task, reservation


def _receipt(queue, reservation, native):
    from agent_dispatch.acquisition_receipts import ensure
    with queue._connect() as conn:
        ensure(conn)
        conn.execute(
            "INSERT INTO acquisition_receipts VALUES (?,?,?,?,?,?,?,?)",
            (reservation.key, reservation.task_id, 1, "wt", "s-" + reservation.key, native,
             "created", "d-" + reservation.key),
        )


def _accountable(queue, key):
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        return reservation_accountable(conn, row)


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def test_preview_is_read_only_and_apply_fences_terminal_history(queue):
    _, plain = _legacy(queue, "plain", finish="fail")
    _, handled = _legacy(queue, "handled", handle="native-old")
    assert _accountable(queue, plain.key) and _accountable(queue, handled.key)
    preview = queue.fence_legacy_history(before=2000.0)
    assert preview["applied"] is False and preview["eligible"] == 2
    assert _accountable(queue, plain.key)
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["fenced"] == 2 and result["tasks"] == 2
    assert not _accountable(queue, plain.key) and not _accountable(queue, handled.key)
    kept = queue.get_reservation(handled.key)
    assert kept.state == "settled" and kept.session_handle == "native-old" and kept.conversation_retired
    assert queue.fence_legacy_history(before=2000.0)["eligible"] == 0


def test_apply_requires_attestation_and_cutoff(queue):
    _legacy(queue, "plain")
    for missing in ("permitted", "confirmed_no_live_holder", "reason"):
        args = {**ATTESTED, missing: "" if missing == "reason" else False}
        with pytest.raises(TaskError):
            queue.fence_legacy_history(before=2000.0, apply=True, **args)
    with pytest.raises(TaskError):
        queue.fence_legacy_history(before=float("nan"))
    assert queue.fence_legacy_history(before=500.0)["eligible"] == 0


def test_live_work_and_shared_natives_are_excluded(queue):
    live = queue.create("live")
    active, _ = queue.reserve_spawn(live.id, now=1000.0)
    queue.record_spawn(active.key, session_handle="native-shared", now=1000.0)
    _, shared = _legacy(queue, "shared", handle="native-shared")
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["excluded"]["active"] == 1
    assert result["excluded"]["shared_live_native"] == 1
    assert result["fenced"] == 0
    assert queue.get_reservation(active.key).state == "spawned"
    assert _accountable(queue, shared.key)


@pytest.mark.parametrize(("status", "fenced"), [("completed", 1), ("abandoned", 1), ("submitted", 0)])
def test_terminal_task_owner_does_not_protect_its_native(queue, status, fenced):
    task, reservation = _legacy(queue, "owned", handle="native-owner")
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, owner_session_id='native-owner' WHERE id=?", (status, task.id),
        )
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["fenced"] == fenced
    assert _accountable(queue, reservation.key) is not bool(fenced)


def test_every_attempt_sharing_an_open_tasks_native_stays_resumable(queue):
    task, first = _legacy(queue, "open", handle="native-open", concluded=False)
    second, _ = queue.reserve_spawn(task.id, now=1100.0)
    queue.record_spawn(second.key, session_handle="native-open", now=1100.0)
    queue.settle_spawn(second.key, detail="historical")
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["excluded"] == {"resumable_task_native": 2} and result["fenced"] == 0
    assert _accountable(queue, first.key) and _accountable(queue, second.key)


def test_receipt_identities_are_retired_and_protected(queue):
    _, receipt_only = _legacy(queue, "receipt-only")
    _receipt(queue, receipt_only, "native-receipt")
    live = queue.create("live")
    active, _ = queue.reserve_spawn(live.id, now=1000.0)
    queue.record_spawn(active.key, session_handle="handle-live", now=1000.0)
    _receipt(queue, active, "native-live")
    _, shared = _legacy(queue, "shared-receipt", handle="handle-old")
    _receipt(queue, shared, "native-live")
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["fenced"] == 1 and result["excluded"]["shared_live_native"] == 1
    assert queue.get_reservation(receipt_only.key).conversation_retired
    assert not _accountable(queue, receipt_only.key)
    assert _accountable(queue, shared.key)


@pytest.mark.parametrize("finish", ["settle", "deferred"])
def test_fenced_conversation_is_not_carried_into_a_successor(queue, finish):
    task, old = _legacy(queue, "carried", handle="native-carried", worktree="wt-carried", finish=finish)
    assert queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)["fenced"] == 1
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (task.id,))
    successor, _ = queue.reserve_spawn(task.id, now=3000.0)
    assert successor.worktree == "wt-carried"
    assert successor.session_handle is None and successor.conversation_retired


@pytest.mark.parametrize("handle", ["local-body:lost", "bare-legacy-handle"])
def test_unproven_handle_retires_as_qualified_empty_history(queue, handle):
    from agent_dispatch.ordinary_native_bindings import native_binding_evidence
    task, row = _legacy(queue, "unproven", handle=handle, worktree="wt-unproven")
    assert queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)["fenced"] == 1
    with queue._connect() as conn:
        history = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (row.key,)).fetchone()
        evidence = native_binding_evidence(conn, history, queue.get(task.id), require_owned_workspace=False)
    assert evidence.qualified_empty and not evidence.retired


def _next_attempt(queue, task, handle, now):
    row, _ = queue.reserve_spawn(task.id, now=now)
    queue.record_spawn(row.key, session_handle=handle, now=now)
    queue.settle_spawn(row.key, detail="historical")
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET startup_complete=0, startup_fenced=0,"
            " conversation_retired=0, release_requested=0 WHERE key=?", (row.key,),
        )
    return row


@pytest.mark.parametrize("newer_at", [1000.0, 900.0])
def test_open_tasks_latest_attempt_is_chosen_numerically(queue, newer_at):
    task, newer = _legacy(queue, "attempts", handle="native-ten", concluded=False)
    older = _next_attempt(queue, task, "native-nine", 1100.0)
    with queue._connect() as conn:
        for row, attempt, at in ((newer, 10, newer_at), (older, 9, 1000.0)):
            conn.execute(
                "UPDATE spawn_reservations SET attempt=?, key=?, reserved_at=? WHERE key=?",
                (attempt, f"dispatch-task:{task.id}:{attempt}", at, row.key),
            )
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["fenced"] == 1 and result["excluded"] == {"resumable_task_native": 1}
    assert _accountable(queue, f"dispatch-task:{task.id}:10")
    assert not _accountable(queue, f"dispatch-task:{task.id}:9")


def test_unresolved_transport_protects_a_shared_native(queue):
    task, older = _legacy(queue, "transport", handle="native-shared", concluded=False)
    newer = _next_attempt(queue, task, "native-shared", 1100.0)
    with queue._connect() as conn:
        conn.execute(
            "INSERT INTO execution_send_custody VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("scope", "send", "example", "test", 1, task.id, newer.key, 1, "resume",
             "identity", "unknown", "", 1100.0, 1100.0),
        )
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (task.id,))
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["fenced"] == 0 and result["excluded"] == {"transport": 2}
    assert _accountable(queue, older.key)


def test_completed_startup_with_unproven_handle_is_unclassifiable(queue):
    _, row = _legacy(queue, "completed", handle="local-body:done")
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET startup_complete=1 WHERE key=?", (row.key,))
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["fenced"] == 0 and result["excluded"] == {"unclassifiable": 1}
    assert _accountable(queue, row.key)


@pytest.mark.parametrize("live", ["active", "release_pending", "open_latest"])
def test_conflicting_live_evidence_protects_every_recorded_identity(queue, live):
    if live == "active":
        task = queue.create("live")
        row, _ = queue.reserve_spawn(task.id, now=1000.0, driver="cli")
        queue.record_spawn(row.key, session_handle="handle-live", now=1000.0)
    else:
        _, row = _legacy(queue, "live", handle="handle-live", driver="cli",
                         concluded=live != "open_latest")
        if live == "release_pending":
            with queue._connect() as conn:
                conn.execute("UPDATE spawn_reservations SET native_release_pending=1 WHERE key=?", (row.key,))
    _receipt(queue, row, "native-conflicting")
    _, older = _legacy(queue, "older", handle="native-conflicting", driver="cli", now=900.0)
    result = queue.fence_legacy_history(before=2000.0, apply=True, **ATTESTED)
    assert result["fenced"] == 0
    assert set(result["excluded"]) & {"shared_live_native", "resumable_task_native"}
    assert _accountable(queue, older.key)


def test_http_requires_control_identity(queue, monkeypatch):
    monkeypatch.setattr("agent_dispatch.acquisition_receipts.verify_recovery_bridge", lambda: None)
    _legacy(queue, "plain")
    client = TestClient(create_app(queue, token="worker-test", control_token="control-test"))
    scheme = "Be" + "arer"
    body = {"before": 2000.0, "apply": True, **ATTESTED}
    path = "/spawn-reservations/history/fence"
    assert client.post(path, json=body, headers={"Authorization": scheme + " worker-test"}).status_code == 403
    response = client.post(path, json=body, headers={"Authorization": scheme + " control-test"})
    assert response.status_code == 200 and response.json()["fenced"] == 1


@pytest.mark.parametrize("capacity", [1, 2])
def test_adopt_legacy_binds_accountable_rows_to_their_only_pool(queue, capacity):
    from tests.test_pool_placement import place, pool, publish, task
    live = queue.create("live")
    legacy, _ = queue.reserve_spawn(live.id, now=1000.0)
    queue.record_spawn_worktree(legacy.key, "live-worktree", execution_machine="host-a")
    queue.record_spawn(legacy.key, session_handle="native-live", worktree="live-worktree", now=1000.0)
    publish(queue, pool(capacity=capacity))
    with pytest.raises(TaskError, match="legacy capacity"):
        place(queue, task(queue))
    preview = queue.adopt_legacy_capacity()
    assert preview["applied"] is False and preview["adoptable"] == 1
    with pytest.raises(TaskError):
        queue.adopt_legacy_capacity(apply=True, permitted=True)
    result = queue.adopt_legacy_capacity(apply=True, permitted=True, reason="operator migration")
    assert result["adopted"] == 1 and queue.adopt_legacy_capacity()["adoptable"] == 0
    assert queue.get_reservation(legacy.key).state == "spawned"
    if capacity == 1:
        with pytest.raises(TaskError, match="no eligible pool instance has capacity"):
            place(queue, task(queue))
    else:
        place(queue, task(queue))


def test_adopt_legacy_reports_ambiguous_rows_without_binding(queue):
    from tests.test_pool_placement import pool, publish
    live = queue.create("live")
    legacy, _ = queue.reserve_spawn(live.id, now=1000.0)
    _created_on(queue, legacy.key, "host-a")
    publish(queue, pool("a"), pool("b"))
    result = queue.adopt_legacy_capacity(apply=True, permitted=True, reason="operator migration")
    assert result["adopted"] == 0 and result["unresolved"] == {"ambiguous": [legacy.key]}
    assert _accountable(queue, legacy.key)


def test_adopt_legacy_refuses_to_infer_a_pool_from_an_incomplete_catalog(queue):
    import hashlib

    from agent_dispatch.registrar_configuration import ConfiguredSupervisorCohort, SupervisorScopeIntent
    from tests.test_pool_placement import pool
    live = queue.create("live")
    legacy, _ = queue.reserve_spawn(live.id, now=1000.0)
    queue.observe_pool_configured_cohort(ConfiguredSupervisorCohort(
        "complete", hashlib.sha256(b"configured").hexdigest(), 0,
        (SupervisorScopeIntent("host-a", "host-a", "linux"), SupervisorScopeIntent("host-b", "host-b", "linux")),
    ), expected_revision=0)
    scope = queue.begin_pool_publication(
        "host-a", "linux", "publisher-a", expected_generation=0, expected_cohort_revision=1,
    )
    queue.publish_pool_catalog_scope(
        "host-a", "linux", scope.publisher_id, generation=scope.generation, expected_revision=0,
        registration_revision=queue.pool_registration_revision(), expected_cohort_revision=1,
        input_revision="inputs", definitions=(pool(),),
    )
    catalog = queue.pool_catalog_snapshot()
    assert not catalog.ready and [d.identity for d in catalog.definitions] == [pool().identity]
    result = queue.adopt_legacy_capacity(apply=True, permitted=True, reason="operator migration")
    assert result["adopted"] == 0 and result["unresolved"] == {"catalog_not_ready": [legacy.key]}
    assert result["catalog_blockers"]
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_reservation_bindings").fetchone()[0] == 0


def test_adopted_cold_legacy_body_keeps_its_legacy_resume_owner(queue, tmp_path):
    from agent_dispatch.ordinary_agent_storage import reservation_native_facts
    from agent_dispatch.supervisor import Supervisor
    from tests.test_pool_placement import pool, publish
    from tests.test_supervisor import QueueBackedClient

    class FactsClient(QueueBackedClient):
        def get_reservation(self, key):
            return {**super().get_reservation(key), **reservation_native_facts(queue, key)}

    parked = queue.create("parked")
    reservation, _ = queue.reserve_spawn(parked.id)
    queue.record_spawn_worktree(reservation.key, "legacy-worktree", execution_machine="host-a")
    queue.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:legacy-session", worktree="legacy-worktree",
    )
    queue.claim_one("legacy-owner", task_id=parked.id)
    queue.start(parked.id, "legacy-owner")
    queue.suspend(parked.id, "legacy-owner", reason="turn ended")
    queue.record_cold(reservation.key)
    publish(queue, pool())
    assert queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")["adopted"] == 1
    facts = reservation_native_facts(queue, reservation.key)
    assert facts["reservation_pool_bound"] is True and facts["reservation_pool_assignment"] is None
    queue.submit_steer(parked.id, fields={"continue": True}, sender="producer")
    assert queue.get(parked.id).resume_requested
    resumed = []
    supervisor = Supervisor(
        FactsClient(queue), repo=parked.repo, machine="host-a",
        spawn_fn=lambda _task: pytest.fail("must resume the parked body, not replace it"),
        local_body_target_dir_fn=lambda _sid: str(tmp_path),
        local_body_verdict_fn=lambda _sid: "gone",
        local_resume_fn=lambda sid, _prompt: resumed.append(sid) or True, nudge=False,
    )
    assert supervisor.release_resumed_cold_tasks() == 1
    assert resumed == ["legacy-session"]
    assert queue.get_reservation(reservation.key).state == "spawned"
    assert queue.get(parked.id).status == "started"


def _created_on(queue, key, host):
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET creating_host=? WHERE key=?", (host, key))


def _adopted_cold_body(queue):
    from tests.test_pool_placement import pool, publish

    parked = queue.create("parked")
    reservation, _ = queue.reserve_spawn(parked.id)
    queue.record_spawn_worktree(reservation.key, "legacy-worktree", execution_machine="host-a")
    queue.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:legacy-session", worktree="legacy-worktree",
    )
    queue.claim_one("legacy-owner", task_id=parked.id)
    queue.start(parked.id, "legacy-owner")
    queue.suspend(parked.id, "legacy-owner", reason="turn ended")
    queue.record_cold(reservation.key)
    publish(queue, pool())
    assert queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")["adopted"] == 1
    return parked, reservation


def _occupy_pool(queue):
    from tests.test_pool_placement import place, record_native, task

    running, _ = place(queue, task(queue))
    record_native(queue, running)
    queue.record_spawn(running.key, session_handle="native", startup_complete=True, startup_revision=1)
    return running


def _free_lane(queue, key):
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET state='cold' WHERE key=?", (key,))


def test_adopted_cold_body_legacy_resume_waits_for_a_free_lane(queue, tmp_path):
    from agent_dispatch.ordinary_agent_storage import reservation_native_facts
    from agent_dispatch.supervisor import Supervisor
    from tests.test_supervisor import QueueBackedClient

    class FactsClient(QueueBackedClient):
        def get_reservation(self, key):
            return {**super().get_reservation(key), **reservation_native_facts(queue, key)}

    parked, reservation = _adopted_cold_body(queue)
    running = _occupy_pool(queue)
    queue.submit_steer(parked.id, fields={"continue": True}, sender="producer")
    resumed = []
    supervisor = Supervisor(
        FactsClient(queue), repo=parked.repo, machine="host-a",
        spawn_fn=lambda _task: pytest.fail("must resume the parked body, not replace it"),
        local_body_target_dir_fn=lambda _sid: str(tmp_path),
        local_body_verdict_fn=lambda _sid: "gone",
        local_resume_fn=lambda sid, _prompt: resumed.append(sid) or True, nudge=False,
    )
    supervisor.release_resumed_cold_tasks()
    assert resumed == []
    assert queue.get_reservation(reservation.key).state == "cold"
    assert queue.get_reservation(reservation.key).attempt == reservation.attempt
    assert queue.get(parked.id).resume_requested
    _free_lane(queue, running.key)
    supervisor._resume_retry_after.clear()
    assert supervisor.release_resumed_cold_tasks() == 1
    assert resumed == ["legacy-session"]
    assert queue.get_reservation(reservation.key).state == "spawned"


def test_adopted_cold_body_detached_wake_waits_for_a_free_lane(queue):
    from agent_dispatch.queue_execution_custody import ExecutionSendUnresolved
    from agent_dispatch.queue_pool_recovery import prepare_recovery_wake

    parked, reservation = _adopted_cold_body(queue)
    waiter = queue.register_run_waiter(
        parked.id, pid=123, host="host-a", start_token="waiter-start",
        resume_worktree="legacy-worktree", command=["synthetic-wait"],
    )
    running = _occupy_pool(queue)
    queue.recover_dead_run_waiter(
        parked.id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )
    wake = queue.claim_due_run_waiter_wake()
    with pytest.raises(ExecutionSendUnresolved, match="free lane"):
        prepare_recovery_wake(queue, wake)
    assert queue.get_reservation(reservation.key).state == "cold"
    _free_lane(queue, running.key)
    prepare_recovery_wake(queue, wake)
    assert queue.get_reservation(reservation.key).state == "spawned"


def test_adopt_legacy_leaves_a_launch_without_a_recorded_workspace_publishable(queue):
    from tests.test_pool_placement import pool, publish

    launching = queue.create("launching")
    reservation, _ = queue.reserve_spawn(launching.id)
    _created_on(queue, reservation.key, "host-a")
    publish(queue, pool())
    result = queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")
    assert result["adopted"] == 0
    assert result["unresolved"]["workspace_unrecorded"] == [reservation.key]
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_reservation_bindings").fetchone()[0] == 0
    queue.record_spawn(reservation.key, session_handle="local-body:launch", worktree="launch-worktree")
    assert queue.get_reservation(reservation.key).session_handle == "local-body:launch"
    assert queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")["adopted"] == 1


def test_adopt_legacy_leaves_an_excluded_recorded_workspace_unresolved(queue):
    from tests.test_pool_placement import pool, publish

    launching = queue.create("launching", excludes=["worktree:excluded-worktree"])
    reservation, _ = queue.reserve_spawn(launching.id)
    queue.record_spawn_worktree(reservation.key, "excluded-worktree", execution_machine="host-a")
    publish(queue, pool())
    result = queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")
    assert result["adopted"] == 0
    assert result["unresolved"]["workspace_conflict"] == [reservation.key]
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_reservation_bindings").fetchone()[0] == 0


def test_adopted_binding_refuses_publishing_a_newly_excluded_workspace(queue):
    from tests.test_pool_placement import pool, publish

    launching = queue.create("launching")
    reservation, _ = queue.reserve_spawn(launching.id)
    queue.record_spawn_worktree(reservation.key, "launch-worktree", execution_machine="host-a")
    publish(queue, pool())
    assert queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")["adopted"] == 1
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET excludes=? WHERE id=?", ('["worktree:launch-worktree"]', launching.id),
        )
    with pytest.raises(TaskError, match="excluded"):
        queue.record_spawn(
            reservation.key, session_handle="local-body:launch", startup_complete=True,
            startup_revision=1,
        )
    assert queue.get_reservation(reservation.key).state == "reserving"


def test_adopted_binding_refuses_replacing_its_workspace_with_an_excluded_one(queue):
    from tests.test_pool_placement import pool, publish

    launching = queue.create("launching", excludes=["worktree:blocked"])
    reservation, _ = queue.reserve_spawn(launching.id)
    queue.record_spawn_worktree(reservation.key, "allowed", execution_machine="host-a")
    publish(queue, pool())
    assert queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")["adopted"] == 1
    with pytest.raises(TaskError, match="excluded"):
        queue.record_spawn(
            reservation.key, worktree="blocked", session_handle="local-body:launch",
            startup_complete=True, startup_revision=1,
        )
    with pytest.raises(TaskError, match="excluded"):
        queue.record_spawn(reservation.key, worktree="blocked")
    current = queue.get_reservation(reservation.key)
    assert (current.state, current.worktree) == ("reserving", "allowed")


def test_client_posts_legacy_history_controls_with_control_identity():
    import httpx

    from agent_dispatch.client import DispatchClient

    seen = []

    def handler(request):
        seen.append((request.url.path, request.read(), request.headers.get("Authorization")))
        return httpx.Response(200, json={"ok": True})

    with DispatchClient(
        "http://example.test", control_token="control-test", transport=httpx.MockTransport(handler),
    ) as client:
        assert client.fence_legacy_history(before=2000.0, apply=True, **ATTESTED) == {"ok": True}
        assert client.adopt_legacy_capacity(apply=True, permitted=True, reason="r") == {"ok": True}
    import json
    scheme = "Be" + "arer"
    assert [(path, json.loads(body), auth) for path, body, auth in seen] == [
        ("/spawn-reservations/history/fence", {"before": 2000.0, "apply": True, **ATTESTED},
         scheme + " control-test"),
        ("/spawn-reservations/history/adopt", {"apply": True, "permitted": True, "reason": "r"},
         scheme + " control-test"),
    ]


def test_adopt_legacy_never_infers_an_unknown_or_conflicting_host(queue):
    from tests.test_pool_placement import pool, publish

    unknown = queue.create("unknown")
    unrecorded, _ = queue.reserve_spawn(unknown.id)
    queue.record_spawn_worktree(unrecorded.key, "unknown-wt")
    remote = queue.create("remote")
    elsewhere, _ = queue.reserve_spawn(remote.id)
    queue.record_spawn_worktree(elsewhere.key, "legacy-wt", creating_host="host-b")
    queue.record_spawn(
        elsewhere.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:remote-session", worktree="legacy-wt",
    )
    queue.claim_one("host-b/legacy-wt", task_id=remote.id)
    queue.start(remote.id, "host-b/legacy-wt")
    queue.suspend(remote.id, "host-b/legacy-wt", reason="turn ended")
    queue.record_cold(elsewhere.key)
    publish(queue, pool())  # the only pool runs on host-a
    result = queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")
    assert result["adopted"] == 0
    assert result["unresolved"]["machine_unrecorded"] == [unrecorded.key]
    assert result["unresolved"]["no_matching_pool"] == [elsewhere.key]
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_reservation_bindings").fetchone()[0] == 0
        conn.execute("UPDATE spawn_reservations SET creating_host='host-a' WHERE key=?", (elsewhere.key,))
    # The owner on host-b contradicts a creating host of host-a.
    result = queue.adopt_legacy_capacity()
    assert result["unresolved"]["machine_conflict"] == [elsewhere.key]


def test_adopt_legacy_applies_pool_filters_to_the_recorded_workspace(queue):
    from agent_dispatch.registrar import Filters
    from tests.test_pool_placement import pool, publish

    untargeted = queue.create("untargeted")
    reservation, _ = queue.reserve_spawn(untargeted.id)
    queue.record_spawn_worktree(reservation.key, "blocked", execution_machine="host-a")
    publish(queue, pool(filters=Filters(reject={"worktree": frozenset({"blocked"})})))
    result = queue.adopt_legacy_capacity(apply=True, permitted=True, reason="migration")
    assert result["adoptable"] == 0 and result["adopted"] == 0
    assert result["unresolved"]["workspace_conflict"] == [reservation.key]
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_reservation_bindings").fetchone()[0] == 0
