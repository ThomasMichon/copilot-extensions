"""Empty allocation history and real receipt-before-publication ordering."""

from __future__ import annotations

from typing import Any
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import acquisition_receipts, tracking
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnState, Status, TaskError, TaskQueue
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import (
    place, pool, publish, record_native, retired_predecessor_retry, task,
)
from tests.test_supervisor import QueueBackedClient

q = placement_controls.q


def empty_predecessor_successor(q: TaskQueue, *, failed_first: bool = False) -> tuple[Any, Any, Any]:
    publish(q, pool())
    original_task = task(q, exclusive_key="shared", dedup_key="logical-work")
    original, _ = place(q, original_task)
    q.record_spawn_worktree(
        original.key, "worktree", ownership="created", creating_host="host-a",
        execution_machine="host-a", driver="cli",
    )
    if failed_first:
        q.fail_spawn(original.key)
    q.request_spawn_release(original.key, finished_startup_revision=1)
    q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    q.abandon(original_task.id, permitted=True, reason="synthetic obsolete task")
    successor_task = task(q, exclusive_key="shared", dedup_key="logical-work")
    successor, reserved = place(q, successor_task)
    assert reserved and successor.worktree == "worktree"
    assert successor.allocation_reservation_key == original.key
    assert successor.conversation_retired == q.get_reservation(original.key).conversation_retired
    assert successor.session_handle is None
    prepared = q.record_spawn_worktree(
        successor.key, "worktree", ownership="reused", execution_machine="host-a", driver="cli",
    )
    return successor_task, original, prepared


@pytest.mark.parametrize("failed_first", [False, True])
@pytest.mark.parametrize("operation", ["publish", "release"])
def test_qualified_empty_root_does_not_retire_successor_native(
    q: TaskQueue, failed_first: bool, operation: str,
) -> None:
    t, original, successor = empty_predecessor_successor(q, failed_first=failed_first)
    if operation == "publish":
        current = record_native(q, successor, native="successor-native")
        assert not current.conversation_retired
        assert q.start(
            t.id, "host-a/worktree", owner_session_id="successor-native",
            expected_generation=q.get(t.id).generation,
        ).status == Status.STARTED
    else:
        current = q.request_spawn_release(
            successor.key, session_handle="successor-native", finished_startup_revision=1,
        )
        assert current.release_requested and not current.conversation_retired
        with pytest.raises(TaskError, match="capacity"):
            place(q, task(q))
        q.retire_spawn(successor.key, exact_absence=True)
        assert place(q, task(q))[1]
    root = q.get_reservation(original.key)
    assert root.session_handle is None and not root.native_release_pending


@pytest.mark.parametrize("broken", ["pending", "unclosed"])
def test_incomplete_empty_root_cannot_authorize_successor_publication(q: TaskQueue, broken: str) -> None:
    _, original, successor = empty_predecessor_successor(q)
    with q._connect() as conn:
        if broken == "pending":
            conn.execute(
                "UPDATE spawn_reservations SET native_release_pending=1 WHERE key=?", (original.key,),
            )
        else:
            conn.execute(
                "UPDATE spawn_reservations SET startup_complete=0,startup_fenced=0 WHERE key=?",
                (original.key,),
            )
    before = q.get_reservation(successor.key)
    with pytest.raises(TaskError, match="no proven native|pending retirement"):
        record_native(q, successor, native="successor-native")
    assert q.get_reservation(successor.key) == before


def receipt_for(q: TaskQueue, t: Any, reservation: Any, native: str = "replacement") -> dict[str, Any]:
    q.record_spawn_worktree(
        reservation.key, "worktree", ownership="reused", execution_machine="host-a", driver="cli",
    )
    return {
        "reservation_key": reservation.key, "task_id": t.id,
        "task_generation": q.get(t.id).generation, "worktree_id": "worktree",
        "session_id": native, "native_session_id": native,
        "mode": "cli", "delivery_id": "delivery",
    }


def authority(receipt: dict[str, Any], *, admitted: bool = False) -> dict[str, Any]:
    return {
        "delivery_id": receipt["delivery_id"], "stage": "delivering" if admitted else "launched",
        "state": "pending", "delivery_admitted": admitted,
        "recorded_result": {key: value for key, value in receipt.items() if key != "reservation_key"},
    }


@pytest.mark.parametrize("predecessor", ["empty", "native"])
def test_real_cli_receipt_precedes_publication_and_requires_affirmative_start(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch, predecessor: str,
) -> None:
    t, original, retry = (
        empty_predecessor_successor(q) if predecessor == "empty" else retired_predecessor_retry(q)
    )
    receipt = receipt_for(q, t, retry)
    before = q.get(t.id)
    acquired = authority(receipt)
    assert acquisition_receipts.bind(q, receipt, evidence=acquired) == receipt
    assert not q.get_reservation(retry.key).conversation_retired
    assert q.get(t.id) == before and before.status == Status.CLAIMED
    assert acquisition_receipts.bind(q, receipt, evidence=acquired) == receipt
    current = q.record_spawn(retry.key, session_handle="replacement", worktree="worktree")
    assert current.session_handle == "replacement" and not current.conversation_retired
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _delivery: acquired)
    with pytest.raises(TaskError, match="has not established"):
        q.start(t.id, "host-a/worktree", owner_session_id="replacement",
                expected_generation=before.generation)
    monkeypatch.setattr(
        acquisition_receipts, "bridge_evidence", lambda _delivery: authority(receipt, admitted=True),
    )
    with pytest.raises(TaskError):
        q.start(t.id, "host-a/worktree", owner_session_id="old-native",
                expected_generation=before.generation)
    assert q.start(
        t.id, "host-a/worktree", owner_session_id="replacement",
        expected_generation=before.generation,
    ).status == Status.STARTED
    assert q.get_reservation(original.key).release_requested


@pytest.mark.parametrize("change", ["old-native", "generation", "workspace", "authority"])
def test_receipt_refusal_preserves_inherited_marker_and_durable_binding(q: TaskQueue, change: str) -> None:
    t, _, retry = retired_predecessor_retry(q)
    receipt = receipt_for(q, t, retry)
    if change == "old-native":
        receipt.update(session_id="old-native", native_session_id="old-native")
    elif change == "generation":
        receipt["task_generation"] -= 1
    elif change == "workspace":
        receipt["worktree_id"] = "foreign"
    evidence = authority(receipt)
    if change == "authority":
        evidence["recorded_result"]["native_session_id"] = "foreign"
    before = q.get_reservation(retry.key)
    with pytest.raises(TaskError):
        acquisition_receipts.bind(q, receipt, evidence=evidence)
    assert q.get_reservation(retry.key) == before
    with q._connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM acquisition_receipts WHERE reservation_key=?", (retry.key,),
        ).fetchone() is None


def test_receipt_replay_cannot_replace_or_revive_its_retired_native(q: TaskQueue) -> None:
    t, _, retry = retired_predecessor_retry(q)
    receipt = receipt_for(q, t, retry)
    acquisition_receipts.bind(q, receipt, evidence=authority(receipt))
    changed = {**receipt, "session_id": "foreign", "native_session_id": "foreign"}
    with pytest.raises(TaskError, match="already bound"):
        acquisition_receipts.bind(q, changed, evidence=authority(changed))
    q.record_spawn(retry.key, session_handle="replacement", startup_complete=True, startup_revision=1)
    q.fail_spawn(retry.key)
    q.request_spawn_release(retry.key)
    q.retire_spawn(retry.key, exact_absence=True)
    before = q.get_reservation(retry.key)
    with pytest.raises(TaskError, match="admission fence"):
        acquisition_receipts.bind(q, receipt, evidence=authority(receipt))
    assert q.get_reservation(retry.key) == before


@pytest.mark.parametrize("count", [100, 1000])
def test_empty_origin_and_unbound_inherited_markers_use_linear_retirement_work(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    from agent_dispatch import ordinary_native_bindings as natives

    t, _, successor = empty_predecessor_successor(q)
    calls = []
    retired = natives.is_native_retired

    def classify(row: Any) -> bool:
        calls.append(row["key"])
        return retired(row)

    monkeypatch.setattr(natives, "is_native_retired", classify)
    with q._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (successor.key,)).fetchone()
        template = {**dict(row), "state": "failed", "conversation_retired": 1, "startup_fenced": 1}
        columns = list(template)
        conn.executemany(
            f"INSERT INTO spawn_reservations ({','.join(columns)}) "
            f"VALUES ({','.join(':' + column for column in columns)})",
            [{**template, "key": f"empty-history:{index}", "attempt": index + 2}
             for index in range(count)],
        )
        evidence = natives.native_binding_evidence(conn, row, q.get(t.id))
    assert evidence.qualified_empty and evidence.known == evidence.retired == ()
    assert len(calls) <= 4 * (count + 2)


def bound_handleless_release(q: TaskQueue, *, startup_closed: bool = True) -> tuple[Any, Any]:
    publish(q, pool())
    t = task(q)
    reservation, _ = place(q, t)
    receipt = receipt_for(q, t, reservation, native="receipt-native")
    acquisition_receipts.bind(q, receipt, evidence=authority(receipt))
    released = q.request_spawn_release(
        reservation.key, finished_startup_revision=1 if startup_closed else None,
    )
    assert released.session_handle is None
    return t, released


@pytest.mark.parametrize("verdict", [tracking.LIVE, tracking.UNKNOWN, tracking.GONE])
@pytest.mark.parametrize("startup_closed", [False, True])
@pytest.mark.parametrize("sweep", ["stranded", "ordinary", "historical"])
def test_stranded_receipt_native_is_probed_not_age_force_failed(
    q: TaskQueue, verdict: str, startup_closed: bool, sweep: str,
) -> None:
    if sweep == "historical":
        publish(q, pool())
        t = task(q)
        reservation, _ = place(q, t)
        receipt = receipt_for(q, t, reservation, native="receipt-native")
        acquisition_receipts.bind(q, receipt, evidence=authority(receipt))
        q.fail_spawn(reservation.key)
        released = q.request_spawn_release(
            reservation.key, finished_startup_revision=1 if startup_closed else None,
        )
        assert released.native_release_pending
    else:
        t, released = bound_handleless_release(q, startup_closed=startup_closed)
    probes = []
    supervisor = Supervisor(
        QueueBackedClient(q), machine="observer", supervisor_id="allocator",
        spawn_fn=lambda _: pytest.fail("no spawn"),
        verdict_fn=lambda *args: probes.append(args) or verdict,
        attempt_conclusion_fn=lambda *args, **kw: pytest.fail("no workspace conclusion"),
    )
    # An unfinished caller can only be fenced by its existing startup protocol.
    supervisor._retain_terminal_startup = lambda *args: None
    retired = startup_closed and verdict == tracking.GONE
    recover = (
        supervisor.recover_stranded_releasing_reservations if sweep == "stranded"
        else supervisor.release_requested_bodies
    )
    assert recover(now=701) == int(retired)
    assert probes == ([("worktree", "host-a", "receipt-native")] if startup_closed else [])
    current = q.get_reservation(released.key)
    assert current.session_handle is None and current.state == (
        SpawnState.FAILED if retired or sweep == "historical" else SpawnState.RELEASING
    )
    assert current.native_release_pending is (sweep == "historical" and not retired)
    assert q.get(t.id).status == Status.CLAIMED
    if retired:
        assert place(q, task(q))[1]
    else:
        with pytest.raises(TaskError, match="capacity"):
            place(q, task(q))


@pytest.mark.parametrize("legacy", [False, True])
def test_unknown_receipt_lookup_retains_stranded_release(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch, caplog: Any, legacy: bool,
) -> None:
    _, released = bound_handleless_release(q)
    client = QueueBackedClient(q)

    def unknown(_key: str) -> dict:
        if legacy:
            return asdict(released)
        raise DispatchError(503, "identity read unavailable")

    monkeypatch.setattr(client, "get_reservation", unknown)
    supervisor = Supervisor(client, spawn_fn=lambda _: pytest.fail("no spawn"))
    assert supervisor.recover_stranded_releasing_reservations(now=701) == 0
    assert q.get_reservation(released.key) == released
    assert "identity" in caplog.text


def test_force_fail_rechecks_receipt_identity_after_stale_empty_snapshot(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, released = bound_handleless_release(q)
    client = QueueBackedClient(q)
    monkeypatch.setattr(client, "get_reservation", lambda _key: {
        **asdict(released), "reservation_native_session_id": None,
        "reservation_native_identity_error": None,
        "reservation_native_identity_protocol": "reservation-native-identity/v1",
    })

    def fail(key: str, **kwargs: Any) -> dict:
        try:
            return asdict(q.fail_spawn(key, **kwargs))
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    monkeypatch.setattr(client, "fail_spawn", fail)
    supervisor = Supervisor(client, spawn_fn=lambda _: pytest.fail("no spawn"))
    assert supervisor.recover_stranded_releasing_reservations(now=701) == 0
    assert q.get_reservation(released.key) == released
    with pytest.raises(TaskError, match="recorded native identity"):
        q.fail_spawn(released.key, force=True)
    assert q.get_reservation(released.key) == released


def test_receipt_identity_is_exposed_and_force_guarded_over_real_http(q: TaskQueue) -> None:
    _, released = bound_handleless_release(q)
    app = create_app(q)
    with TestClient(app) as http:
        dispatch = DispatchClient.__new__(DispatchClient)
        dispatch._http = http
        snapshot = dispatch.get_reservation(released.key)
        assert snapshot["reservation_native_identity_protocol"] == "reservation-native-identity/v1"
        assert snapshot["reservation_native_session_id"] == "receipt-native"
        assert "receipt-native" in snapshot["known_native_session_ids"]
        assert snapshot["reservation_native_identity_error"] is None
        assert snapshot["session_handle"] is None
        response = http.post(f"/spawn-reservations/{released.key}/fail", json={"force": True})
        assert response.status_code == 409 and "native identity" in response.json()["detail"]
    assert q.get_reservation(released.key) == released


def test_receipt_retirement_does_not_drop_unsettled_transport_accountability(q: TaskQueue) -> None:
    t, released = bound_handleless_release(q)
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO wake_outbox"
            " (id,task_id,generation,wake_seq,owner,owner_session_id,"
            " status,attempts,created_at,updated_at,execution_send_state)"
            " VALUES ('wake',?,0,1,'host-a/worktree','receipt-native',"
            " 'pending',0,1,1,'unknown')", (t.id,),
        )
    supervisor = Supervisor(
        QueueBackedClient(q), verdict_fn=lambda *args: tracking.GONE,
        spawn_fn=lambda _: pytest.fail("no spawn"),
    )
    assert supervisor.recover_stranded_releasing_reservations(now=701) == 1
    assert q.get_reservation(released.key).state == SpawnState.FAILED
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
