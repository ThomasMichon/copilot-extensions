"""Accountable terminal attempts remain visible, with bounded history work."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnState, Status
from agent_dispatch.queue_pool_accountability import ReservationAccountability
from agent_dispatch.queue_pool_placement import select_pool
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement as placement_controls
from tests.test_pool_pending_queries import no_spawn
from tests.test_pool_placement import place, pool, publish, record_native, task
from tests.test_supervisor import QueueBackedClient

q = placement_controls.q


def failed_native(q, **task_kwargs):
    publish(q, pool())
    original, _ = place(q, task(q, labels=["review"], **task_kwargs))
    record_native(q, original)
    q.record_spawn(original.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.fail_spawn(original.key)
    return q.get_reservation(original.key)


def history(q, original, count, *, retired=True, distinct_tasks=False):
    task_ids = [original.task_id] * count
    if distinct_tasks:
        q.abandon(original.task_id, permitted=True, reason="synthetic historical request")
        task_ids = []
        for _ in range(count):
            successor = task(q, dedup_key=q.get(original.task_id).dedup_key)
            task_ids.append(successor.id)
            q.abandon(successor.id, permitted=True, reason="synthetic successor request")
    with q._connect() as conn:
        template = dict(conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (original.key,),
        ).fetchone())
        template.update(
            state=SpawnState.FAILED, native_release_pending=0,
            release_requested=int(retired), conversation_retired=int(retired),
            startup_complete=1, reserved_at=200, updated_at=200,
            allocation_reservation_key=original.key,
        )
        columns = list(template)
        conn.executemany(
            f"INSERT INTO spawn_reservations ({','.join(columns)}) "
            f"VALUES ({','.join(':' + column for column in columns)})",
            [{**template, "key": f"history:{index}", "attempt": index + 2, "task_id": task_ids[index],
              "session_handle": f"retired:{index}" if retired else "native"}
             for index in range(count)],
        )
        conn.execute(
            "INSERT INTO pool_reservation_bindings"
            " SELECT s.key,p.registration_id,p.machine,p.env,p.capabilities"
            " FROM spawn_reservations s JOIN pool_reservation_bindings p ON p.reservation_key=?"
            " WHERE s.key LIKE 'history:%'", (original.key,),
        )


@pytest.mark.parametrize("labels", [None, {"review"}])
@pytest.mark.parametrize("verdict", ["live", "unknown", "gone"])
def test_failed_native_without_release_intent_blocks_actual_legacy_spawn(q, labels, verdict):
    original = failed_native(q)
    assert not original.release_requested and not original.native_release_pending
    queued = task(q, labels=["review"])
    supervisor = Supervisor(
        QueueBackedClient(q), spawn_fn=no_spawn, machine="host-a", labels=labels,
        verdict_fn=lambda *args: verdict, max_concurrent=1, nudge=False,
    )
    assert [row["key"] for row in supervisor._active_reservations()] == [original.key]
    assert supervisor.poll_once() == []
    assert q.get(queued.id).status == Status.QUEUED
    q.request_spawn_release(original.key)
    q.retire_spawn(original.key, exact_absence=True)
    assert supervisor._active_reservations() == []


@pytest.mark.parametrize("state", ["pending", "unknown"])
@pytest.mark.parametrize("outbox", ["wake_outbox", "run_waiter_wakes"])
def test_active_suspended_pool_wake_blocks_actual_legacy_spawn(q, state, outbox):
    publish(q, pool())
    t = task(q)
    original, _ = place(q, t)
    record_native(q, original)
    q.record_spawn(original.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.suspend(t.id, "host-a/worktree", reason="external wait")
    if outbox == "wake_outbox":
        q.resume(t.id, "host-a/worktree", wake_requested=True, wake_message="continue")
    else:
        waiter = q.register_run_waiter(
            t.id, pid=123, host="host-a", start_token="synthetic-waiter",
            resume_worktree="host-a/worktree", command=["synthetic-wait"],
        )
        q.recover_dead_run_waiter(
            t.id, generation=waiter["generation"], reason="external",
            message="continue", sender="recovery",
        )
    with q._connect() as conn:
        conn.execute(f"UPDATE {outbox} SET execution_send_state=? WHERE task_id=?", (state, t.id))
    queued = task(q)
    supervisor = Supervisor(
        QueueBackedClient(q), spawn_fn=no_spawn, machine="host-a", max_concurrent=1, nudge=False,
    )
    assert supervisor._reservation_has_live_process(
        asdict(q.get_reservation(original.key)), asdict(q.get(t.id)),
    ) is (outbox == "wake_outbox")
    assert [row["key"] for row in supervisor._active_reservations()] == [original.key]
    assert supervisor.poll_once() == []
    assert q.get(queued.id).status == Status.QUEUED


def test_real_http_accountability_filters_pool_history_before_limit(q):
    original = failed_native(q)
    history(q, original, 10001)
    app = TestClient(create_app(q, token=None, control_token=None, enable_mcp=False,
                                verification_interval=0))

    def respond(request):
        response = app.get(request.url.path, params=dict(request.url.params))
        return httpx.Response(response.status_code, headers=response.headers, json=response.json())

    with DispatchClient("http://example.test", token=None, transport=httpx.MockTransport(respond)) as client:
        rows = client.list_reservations(
            state=SpawnState.FAILED, repo="example", label="review", pool_accountable=True, limit=1,
        )
        assert rows == [{**asdict(original), "pool_accountable": True}]
        assert client.list_reservations(state=SpawnState.FAILED, limit=1)[0]["key"] != original.key
        assert client.list_reservations(pool_accountable=True, limit=0) == []
        q.request_spawn_release(original.key)
        q.retire_spawn(original.key, exact_absence=True)
        assert client.list_reservations(pool_accountable=True, limit=1) == []


@pytest.mark.parametrize("header,body", [
    ({}, []),
    ({}, [{"pool_accountable": True}]),
    ({"X-Dispatch-Pool-Accountability": "pool-accountable/v1"}, [{"pool_accountable": False}]),
])
def test_accountability_client_refuses_legacy_or_unfiltered_provider(header, body):
    with DispatchClient(
        "http://example.test", token=None,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=header, json=body)),
    ) as client:
        with pytest.raises(DispatchError, match="pool-accountable/v1"):
            client.list_reservations(pool_accountable=True)
        assert client.list_reservations() == body


def test_missing_accountability_support_exhausts_actual_spawn_capacity(q, caplog):
    class LegacyClient(QueueBackedClient):
        def list_reservations(self, **kw):
            if kw.get("pool_accountable"):
                raise DispatchError(426, "coordinator lacks pool-accountable/v1 reservation query")
            return super().list_reservations(**kw)

    queued = task(q)
    supervisor = Supervisor(
        LegacyClient(q), spawn_fn=no_spawn, machine="host-a", max_concurrent=1, nudge=False,
    )
    assert supervisor.poll_once() == []
    assert q.get(queued.id).status == Status.QUEUED
    assert "treating capacity as exhausted" in caplog.text


def test_accountability_filter_preserves_unassigned_legacy_behavior(q):
    original, _ = q.reserve_spawn(task(q).id, driver="cli")
    record_native(q, original)
    q.fail_spawn(original.key)
    assert q.list_reservations(pool_accountable=True) == []
    assert [row.key for row in q.list_reservations()] == [original.key]
    supervisor = Supervisor(QueueBackedClient(q), spawn_fn=no_spawn, machine="host-a", nudge=False)
    assert supervisor._active_reservations() == []


@pytest.mark.parametrize("count", [100, 1000])
@pytest.mark.parametrize("retired", [False, True])
@pytest.mark.parametrize("distinct_tasks", [False, True])
def test_history_work_is_batched_not_one_lineage_scan_per_row(q, monkeypatch, count, retired, distinct_tasks):
    from agent_dispatch import queue_pool_accountability as accounting
    from agent_dispatch import ordinary_native_bindings as natives
    original = failed_native(q, dedup_key="logical-history")
    history(q, original, count, retired=retired, distinct_tasks=distinct_tasks)
    q.request_spawn_release(original.key)
    q.retire_spawn(original.key, exact_absence=True)
    queries, classifications, identities = [], [], []
    native_bindings = accounting.native_bindings
    native_identity = accounting.reservation_native_identity

    def classify(*args, **kw):
        result = native_bindings(*args, **kw)
        classifications.append(len(result[0]))
        return result

    def identify(*args, **kw):
        identities.append(1)
        return native_identity(*args, **kw)

    monkeypatch.setattr(accounting, "native_bindings", classify)
    monkeypatch.setattr(accounting, "reservation_native_identity", identify)
    monkeypatch.setattr(natives, "reservation_native_identity", identify)
    connect = q._connect

    @contextmanager
    def counted_connect():
        with connect() as conn:
            conn.set_trace_callback(queries.append)
            yield conn

    monkeypatch.setattr(q, "_connect", counted_connect)
    assert q.list_reservations(pool_accountable=True, limit=1) == []
    assert len(queries) <= 11
    assert len(classifications) <= 1
    assert sum(classifications) <= count + 1
    assert len(identities) <= 3 * (count + 1)
    queries.clear()
    classifications.clear()
    identities.clear()
    candidate = task(q)
    queries.clear()
    with q._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        definition = pool(capacity=count + 2)
        assert select_pool(conn, candidate, (definition,)) == definition
    assert len(queries) <= 13
    assert len(classifications) <= 1
    assert sum(classifications) <= count + 1
    assert len(identities) <= 3 * (count + 1)


@pytest.mark.parametrize("extra_creation", [None, "other-creation"])
def test_distinct_task_cache_preserves_task_specific_extra_history(q, extra_creation):
    original = failed_native(q, dedup_key="logical-history")
    history(q, original, 2, retired=False, distinct_tasks=True)
    with q._connect() as conn:
        first = dict(conn.execute(
            "SELECT * FROM spawn_reservations WHERE key='history:0'",
        ).fetchone())
        extra = {
            **first, "key": "extra-history", "attempt": 100,
            "creation_key": extra_creation, "conversation_retired": 1,
            "allocation_reservation_key": "extra-history",
        }
        conn.execute(
            f"INSERT INTO spawn_reservations ({','.join(extra)}) "
            f"VALUES ({','.join(':' + column for column in extra)})", extra,
        )
    visible = {r.key for r in q.list_reservations(pool_accountable=True, limit=-1)}
    assert "history:0" not in visible
    assert "history:1" in visible and original.key in visible
    with q._connect() as conn:
        accounting = ReservationAccountability(conn)
        rows = conn.execute("SELECT * FROM spawn_reservations WHERE key LIKE 'history:%'").fetchall()
        assert [accounting.accountable(row) for row in rows] == [False, True]


def test_transport_snapshot_matches_existing_native_and_workspace_owner_rules(q):
    from agent_dispatch.queue_execution_custody import reservation_transport_accountable
    original = failed_native(q)
    history(q, original, 20)
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO wake_outbox"
            " (id,task_id,generation,wake_seq,owner,owner_session_id,"
            " status,attempts,created_at,updated_at,execution_send_state)"
            " VALUES ('wake',?,0,1,' HOST-A /worktree','native',"
            " 'pending',0,1,1,'unknown')", (original.task_id,),
        )
        snapshot = ReservationAccountability(conn)
        for row in conn.execute("SELECT * FROM spawn_reservations"):
            assert snapshot.transport.accountable(
                {**dict(row), "native_session_id": None},
            ) is reservation_transport_accountable(conn, row)
