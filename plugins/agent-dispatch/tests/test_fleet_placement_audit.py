"""Every fleet placement decision is recorded as a durable task event."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.fleet_placement import PlacementProviderError, run_provider
from agent_dispatch.queue import TaskError
from agent_dispatch.queue_spawn_completion import PLACEMENT_NOTE_PREFIX
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_direct_pool_definitions import _api, _client
from tests.test_fleet import _create
from tests.test_fleet_placement import FakeRunner, _ordering, _script, _spawner
from tests.test_supervisor import QueueBackedClient


def _placement_events(q: TaskQueue, task_id: str) -> list[dict[str, Any]]:
    notes = [str(e.get("note") or "") for e in q.events(task_id)]
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
        cast(DispatchClient, QueueBackedClient(q)), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    sup.poll_once()
    assert [r["host"] for r in rec] == ["W2"]
    (event,) = _placement_events(q, task.id)
    assert event["task_id"] == task.id
    assert event["provider_ordered"] == ["W2", "W1"]
    assert event["selected"] == "W2" and event["selected_provider_rank"] == 1
    assert event["spawn_ok"] is True and event["reservation"]


def test_supervisor_tolerates_an_unrecorded_decision(tmp_path: Path) -> None:
    class OldCoordinator(QueueBackedClient):
        def record_placement_decision(self, key: str, decision: dict[str, Any]) -> None:
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
    assert [r["host"] for r in rec] == ["W1"]
    assert _placement_events(q, task.id) == []


def test_queue_dedupes_and_refuses_unknown_reservations(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "tasks.db")
    task = _create(q, "work")
    reservation, _ = q.reserve_spawn(task.id)
    decision = {"decision_id": "d1", "selected": "W1"}
    assert q.record_placement_decision(reservation.key, decision)["recorded"] is True
    assert q.record_placement_decision(reservation.key, decision)["recorded"] is False
    assert len(_placement_events(q, task.id)) == 1
    with pytest.raises(TaskError):
        q.record_placement_decision("missing", decision)


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
