"""Pending predicates precede bounded history, including mixed-provider refusal."""

from __future__ import annotations

from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnState, TaskQueue
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, record_native, task
from tests.test_supervisor import QueueBackedClient

q = placement_controls.q


def no_spawn(task: dict) -> dict:
    raise AssertionError("pending reservation inspection cannot launch a worker")


def fill_completed_history(q: TaskQueue, template_key: str) -> None:
    history_task = task(q, labels=["review"])
    with q._connect() as conn:
        template = dict(conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (template_key,),
        ).fetchone())
        template.update(
            task_id=history_task.id, creation_key=None, allocation_reservation_key=None,
            inherited_worktree=None,
            state=SpawnState.FAILED, session_handle=None, worktree=None,
            native_release_pending=0, release_requested=0, startup_complete=1,
            conclusion_state="complete", exclusive_released=1, reserved_at=200, updated_at=200,
        )
        columns = list(template)
        rows = [
            {**template, "key": f"completed:{index}", "attempt": index + 2}
            for index in range(10000)
        ]
        conn.executemany(
            f"INSERT INTO spawn_reservations ({','.join(columns)}) "
            f"VALUES ({','.join(':' + column for column in columns)})", rows,
        )
    assert all(not row.native_release_pending for row in q.list_reservations(limit=10000))


@pytest.mark.parametrize("labels", [None, {"review"}])
def test_capacity_and_native_sweep_see_pending_row_behind_full_completed_page(
    q: TaskQueue, labels: set[str] | None,
) -> None:
    publish(q, pool())
    t = task(q, labels=["review"])
    original, _ = place(q, t)
    record_native(q, original)
    q.fail_spawn(original.key)
    q.request_spawn_release(original.key, finished_startup_revision=1)
    fill_completed_history(q, original.key)
    probes = []
    supervisor = Supervisor(
        QueueBackedClient(q), spawn_fn=no_spawn, machine="host-a", labels=labels, max_concurrent=1,
        verdict_fn=lambda *args: probes.append(args) or "gone", nudge=False,
    )
    assert [row["key"] for row in supervisor._active_reservations()] == [original.key]
    assert supervisor.release_requested_bodies() == 1
    assert probes == [("worktree", "host-a", "native")]
    assert not q.get_reservation(original.key).native_release_pending
    assert supervisor._active_reservations() == []


@pytest.mark.parametrize("labels", [None, {"review"}])
def test_cleanup_sweep_keeps_pending_filter_before_history_limit(
    q: TaskQueue, labels: set[str] | None,
) -> None:
    t = task(q, labels=["review"])
    original, _ = q.reserve_spawn(t.id)
    q.record_spawn_worktree(
        original.key, "worktree", ownership="created", creating_host="host-a", driver="cli",
        allocation_project="example", worktree_path="/workspace",
    )
    q.request_spawn_release(original.key, finished_startup_revision=1)
    q.retire_spawn(original.key, exact_absence=True, conclusion_state="pending")
    fill_completed_history(q, original.key)
    conclusions = []
    supervisor = Supervisor(
        QueueBackedClient(q), spawn_fn=no_spawn, machine="host-a", labels=labels,
        attempt_conclusion_fn=lambda *args, **kw: conclusions.append((args, kw)) or {"action": "primed"},
        nudge=False,
    )
    assert supervisor.release_requested_bodies() == 0
    assert len(conclusions) == 1
    assert q.get_reservation(original.key).conclusion_state == "complete"


@pytest.mark.parametrize("pending", [False, True])
def test_real_http_query_and_client_apply_pending_before_limit(q: TaskQueue, pending: bool) -> None:
    publish(q, pool())
    original, _ = place(q, task(q, labels=["review"]))
    record_native(q, original)
    q.fail_spawn(original.key)
    q.request_spawn_release(original.key, finished_startup_revision=1)
    fill_completed_history(q, original.key)
    app = TestClient(create_app(q, token=None, control_token=None, enable_mcp=False,
                                verification_interval=0))

    def respond(request: httpx.Request) -> httpx.Response:
        response = app.get(request.url.path, params=dict(request.url.params))
        return httpx.Response(response.status_code, headers=response.headers, json=response.json())

    with DispatchClient("http://example.test", token=None, transport=httpx.MockTransport(respond)) as client:
        rows = client.list_reservations(
            state=SpawnState.FAILED, repo="example", label="review",
            native_release_pending=pending, limit=1,
        )
        assert len(rows) == 1 and rows[0]["native_release_pending"] is pending
        assert (rows[0]["key"] == original.key) is pending
        assert len(client.list_reservations(state=SpawnState.FAILED, limit=2)) == 2
        if pending:
            assert rows == [asdict(q.get_reservation(original.key))]


@pytest.mark.parametrize("header,body", [
    ({}, []),
    ({}, [{"native_release_pending": True}]),
    ({"X-Dispatch-Reservation-Query": "native-release-pending/v1"}, [{"native_release_pending": False}]),
])
def test_new_query_refuses_legacy_or_incorrect_provider(header: dict, body: list) -> None:
    with DispatchClient(
        "http://example.test", token=None,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=header, json=body)),
    ) as client:
        with pytest.raises(DispatchError, match="native-release-pending/v1"):
            client.list_reservations(native_release_pending=True)
        assert client.list_reservations() == body


def test_missing_filtered_query_contract_exhausts_supervisor_capacity(q: TaskQueue, caplog) -> None:
    class Client(QueueBackedClient):
        def list_reservations(self, **kw: object) -> list[dict]:
            if kw.get("native_release_pending") is not None:
                raise DispatchError(426, "coordinator lacks native-release-pending/v1 reservation query")
            return super().list_reservations(**kw)

    supervisor = Supervisor(Client(q), spawn_fn=no_spawn, machine="host-a", max_concurrent=2, nudge=False)
    assert supervisor._active_reservations() == [{}, {}]
    assert "treating capacity as exhausted" in caplog.text
