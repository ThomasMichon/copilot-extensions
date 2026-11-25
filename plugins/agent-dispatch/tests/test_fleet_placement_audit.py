"""Every fleet placement decision is recorded as a durable task event."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.client_spawn_reservations import PLACEMENT_DECISION_TIMEOUT_SECONDS
from agent_dispatch.fleet_placement import PlacementProviderError, run_provider
from agent_dispatch.placement_audit_outbox import PlacementAuditOutbox
from agent_dispatch.queue_placement_audit import PLACEMENT_NOTE_PREFIX, record_placement_decision
from agent_dispatch.queue_records import TaskError
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_direct_pool_definitions import _api, _client
from tests.test_fleet import _create
from tests.test_fleet_placement import FakeRunner, _ordering, _script, _spawner
from tests.test_supervisor import QueueBackedClient


class RecordingClient(QueueBackedClient):
    def record_placement_decision(self, key: str, decision: dict[str, Any]) -> dict[str, object]:
        return record_placement_decision(self._q, key, decision)


def _placement_events(q: TaskQueue, task_id: str) -> list[dict[str, Any]]:
    notes = [str(e.get("note") or "") for e in cast(list[dict[str, Any]], q.events(task_id))]
    return [json.loads(n[len(PLACEMENT_NOTE_PREFIX):]) for n in notes if n.startswith(PLACEMENT_NOTE_PREFIX)]


def test_run_provider_returns_optional_scores(tmp_path: Path) -> None:
    command = _script(
        tmp_path,
        "sys.stdin.read()\n"
        "print(json.dumps({'version': 1, 'ordered': ['W2', 'W1'], 'scores': {'W2': 9.5, 'W1': 3}}))\n",
    )
    result = run_provider(command, ["W1", "W2"])
    assert result == ("W2", "W1")
    assert result.scores == {"W2": 9.5, "W1": 3.0}


@pytest.mark.parametrize("scores", ["{'W9': 1}", "{'W1': 'high'}", "{'W1': True}", "[1, 2]"])
def test_run_provider_rejects_bad_scores(tmp_path: Path, scores: str) -> None:
    command = _script(
        tmp_path,
        f"sys.stdin.read()\nprint(json.dumps({{'version': 1, 'ordered': ['W1'], 'scores': {scores}}}))\n",
    )
    with pytest.raises(PlacementProviderError):
        run_provider(command, ["W1", "W2"])


def test_decision_selects_provider_rank_one() -> None:
    f = _spawner(["W1", "CDE", "W2"], _ordering(["W1", "W2"], FakeRunner(["W2", "W1"])))
    assert f.select({"id": "t"}) == "W2"
    d = f.take_placement_decision("t")
    assert d is not None
    assert d["candidates_sent"] == ["W1", "W2"]
    assert d["provider_ordered"] == ["W2", "W1"]
    assert d["selected"] == "W2" and d["selected_provider_rank"] == 1
    assert d["skipped"] == []
    assert f.take_placement_decision("t") is None


def test_decision_explains_why_rank_one_was_skipped() -> None:
    f = _spawner(["W1", "W2", "W3"], _ordering(["W1", "W2", "W3"], FakeRunner(["W1", "W2", "W3"])),
                 live={"W2", "W3"})
    assert f.select({"id": "a"}) == "W2"
    d = f.take_placement_decision("a")
    assert d is not None and d["selected_provider_rank"] == 2
    assert d["skipped"] == [{"host": "W1", "rank": 1, "reason": "not_live"}]

    assert f.select({"id": "b", "excludes": ["machine:W1"]}) == "W2"
    d = f.take_placement_decision("b")
    assert d is not None
    assert d["skipped"] == [{"host": "W1", "rank": 1, "reason": "excluded_by_task"}]


def test_decision_records_provider_error() -> None:
    f = _spawner(["W1", "CDE"], _ordering(["W1"], FakeRunner(fail=True)))
    assert f.select({"id": "t"}) == "CDE"
    d = f.take_placement_decision("t")
    assert d is not None
    assert "exited 3" in d["provider_error"] and d["provider_ordered"] is None
    assert d["omitted_by_provider"] == ["W1"]


def test_supervisor_records_the_dispatch_decision_as_a_task_event(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "tasks.db")
    task = _create(q, "work")
    rec: list[dict[str, Any]] = []
    f = _spawner(["W1", "W2"], _ordering(["W1", "W2"], FakeRunner(["W2", "W1"])), rec=rec)
    sup = Supervisor(
        cast(DispatchClient, RecordingClient(q)), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    sup.poll_once()
    assert sup.placement_audit is not None and sup.placement_audit.drain(5)
    assert [r["host"] for r in rec] == ["W2"]
    (event,) = _placement_events(q, task.id)
    assert event["task_id"] == task.id
    assert event["provider_ordered"] == ["W2", "W1"]
    assert event["selected"] == "W2" and event["selected_provider_rank"] == 1
    assert event["spawn_ok"] is True and event["reservation"]


def test_supervisor_tolerates_an_unrecorded_decision(tmp_path: Path) -> None:
    class OldCoordinator(RecordingClient):
        def record_placement_decision(self, key: str, decision: dict[str, Any]) -> dict[str, object]:
            raise DispatchError(404, "Not Found")

    q = TaskQueue(tmp_path / "tasks.db")
    task = _create(q, "work")
    rec: list[dict[str, Any]] = []
    f = _spawner(["W1"], _ordering(["W1"], FakeRunner(["W1"])), rec=rec)
    sup = Supervisor(
        cast(DispatchClient, OldCoordinator(q)), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    sup.poll_once()
    assert sup.placement_audit is not None and sup.placement_audit.drain(5)
    assert [r["host"] for r in rec] == ["W1"]
    assert _placement_events(q, task.id) == []


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("audit sink unreachable"),
    httpx.ReadTimeout("audit sink stalled"),
])
def test_unavailable_audit_sink_never_gates_spawn_settlement(tmp_path: Path, failure: Exception) -> None:
    order: list[str] = []

    class UnreachableAudit(RecordingClient):
        def record_spawn(self, key: str, **kwargs: Any) -> dict[str, object]:
            order.append("record_spawn")
            base: Any = super()
            return cast(dict[str, object], base.record_spawn(key, **kwargs))

        def record_placement_decision(self, key: str, decision: dict[str, Any]) -> dict[str, object]:
            order.append("audit")
            raise failure

    q = TaskQueue(tmp_path / "tasks.db")
    task = _create(q, "work")
    f = _spawner(["W1"], _ordering(["W1"], FakeRunner(["W1"])), rec=[])
    sup = Supervisor(
        cast(DispatchClient, UnreachableAudit(q)), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    assert sup.poll_once() == [task.id]
    assert sup.placement_audit is not None and sup.placement_audit.drain(5)
    assert order == ["record_spawn", "audit"]
    assert _placement_events(q, task.id) == []


def test_client_bounds_the_placement_audit_request() -> None:
    seen: dict[str, Any] = {}

    class Http:
        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            seen.update(kwargs, url=url)
            return httpx.Response(200, json={"recorded": True}, request=httpx.Request("POST", url))

    client = DispatchClient("http://127.0.0.1:1")
    client._http = cast(httpx.Client, Http())
    assert client.record_placement_decision("k", {"selected": "W1"}) == {"recorded": True}
    assert seen["timeout"] == PLACEMENT_DECISION_TIMEOUT_SECONDS


def test_queue_dedupes_and_refuses_unknown_reservations(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "tasks.db")
    task = _create(q, "work")
    reservation, _ = q.reserve_spawn(task.id)
    decision = {"decision_id": "d1", "selected": "W1"}
    assert record_placement_decision(q, reservation.key, decision)["recorded"] is True
    assert record_placement_decision(q, reservation.key, decision)["recorded"] is False
    assert len(_placement_events(q, task.id)) == 1
    with pytest.raises(TaskError):
        record_placement_decision(q, "missing", decision)


def test_coordinator_route_records_and_rejects_unknown_keys(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "tasks.db")
    task = _create(q, "work")
    reservation, _ = q.reserve_spawn(task.id)
    client = _client(_api(q), [])
    assert client.record_placement_decision(reservation.key, {"selected": "W1"})["recorded"] is True
    assert _placement_events(q, task.id)[0]["selected"] == "W1"
    with pytest.raises(DispatchError) as raised:
        client.record_placement_decision("missing", {"selected": "W1"})
    assert raised.value.status_code == 404


def test_slow_audit_sink_never_stalls_the_supervisor_cycle(tmp_path: Path) -> None:
    release = threading.Event()
    calls: list[str] = []

    class StalledAudit(RecordingClient):
        def record_placement_decision(self, key: str, decision: dict[str, Any]) -> dict[str, object]:
            calls.append(key)
            release.wait(10)
            raise httpx.ReadTimeout("audit sink stalled")

    q = TaskQueue(tmp_path / "tasks.db")
    tasks = [_create(q, f"work-{i}") for i in range(3)]
    f = _spawner(["W1"], _ordering(["W1"], FakeRunner(["W1"])), rec=[])
    sup = Supervisor(
        cast(DispatchClient, StalledAudit(q)), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    def writers() -> int:
        return [t.name for t in threading.enumerate()].count("placement-audit-writer")

    baseline = writers()
    try:
        started = time.monotonic()
        spawned = sup.poll_once() + sup.poll_once()
        elapsed = time.monotonic() - started
        assert sorted(spawned) == sorted(t.id for t in tasks)
        assert elapsed < 2.0, f"stalled audit sink delayed the cycle by {elapsed:.2f}s"
        outbox = sup.placement_audit
        assert outbox is not None
        assert writers() == baseline + 1
    finally:
        release.set()
    assert outbox.drain(5)
    assert len(calls) == 1  # one transport failure backs off instead of retrying the rest
    assert outbox.dropped == 2 and outbox.delivered == 0
    outbox.close()


def test_full_outbox_drops_and_counts_without_blocking() -> None:
    gate = threading.Event()
    seen: list[str] = []

    def record(key: str, decision: dict[str, Any]) -> object:
        seen.append(key)
        gate.wait(10)
        return {}

    outbox = PlacementAuditOutbox(record, capacity=2)
    try:
        assert outbox.submit("k0", {})
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
        started = time.monotonic()
        results = [outbox.submit(f"k{i}", {}) for i in range(1, 6)]
        assert time.monotonic() - started < 0.5
        assert results == [True, True, False, False, False]
        assert outbox.dropped == 3
    finally:
        gate.set()
    assert outbox.drain(5)
    assert seen == ["k0", "k1", "k2"] and outbox.delivered == 3
    outbox.close()
    assert not outbox.submit("late", {})


@pytest.mark.parametrize(("error", "disabled"), [
    (DispatchError(404, "Not Found"), True),
    (DispatchError(405, "Method Not Allowed"), True),
    (DispatchError(404, "no such reservation"), False),
])
def test_missing_audit_route_disables_the_outbox(error: DispatchError, disabled: bool) -> None:
    calls: list[str] = []

    def record(key: str, decision: dict[str, Any]) -> object:
        calls.append(key)
        raise error

    outbox = PlacementAuditOutbox(record)
    assert outbox.submit("a", {}) and outbox.drain(5)
    assert outbox.submit("b", {}) is not disabled
    assert outbox.drain(5)
    assert calls == (["a"] if disabled else ["a", "b"])
    outbox.close()