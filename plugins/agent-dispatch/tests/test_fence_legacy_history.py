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
