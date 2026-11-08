"""Operator-attested reconciliation of unproven legacy reservation history."""

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError
from agent_dispatch.queue_pool_accountability import reservation_accountable
from tests._helpers import RepoDefaultingQueue as TaskQueue

ATTESTED = {"permitted": True, "confirmed_no_live_holder": True, "reason": "operator verified"}


def _legacy(queue, title, *, handle=None, finish="settle", now=1000.0, concluded=True):
    task = queue.create(title)
    reservation, _ = queue.reserve_spawn(task.id, now=now)
    if handle:
        queue.record_spawn(reservation.key, session_handle=handle, now=now)
    getattr(queue, f"{finish}_spawn")(reservation.key, detail="historical")
    with queue._connect() as conn:  # emulate rows predating startup columns
        conn.execute(
            "UPDATE spawn_reservations SET startup_complete=0, startup_fenced=0,"
            " conversation_retired=0, release_requested=0 WHERE key=?", (reservation.key,),
        )
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
