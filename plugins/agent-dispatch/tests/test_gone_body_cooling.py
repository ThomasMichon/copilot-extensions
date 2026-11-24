"""A delivered owner wake holds a lane only while its body may still exist."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import tracking
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue_records import SpawnState, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
from agent_dispatch.supervisor import Supervisor
from tests import test_supervisor as _supervisor_tests
from tests._helpers import TEST_REPO

SCOPE = RelevanceScope(TEST_REPO, "emitter", "review-source")
_fixtures: Any = _supervisor_tests


def _report_closed(queue: Any, task_id: str) -> None:
    snapshot = queue.relevance_snapshot(task_id)
    queue.reconcile_task_relevance(
        task_id, request_id="closed-source", scope=SCOPE,
        expected_generation=snapshot["generation"], expected_revision=snapshot["revision"],
        verdict="obsolete", reason="Backing object is closed",
        observation_ref="provider-object:closed:revision-2", reporter="source-evaluator",
    )


def _suspended_closeout_body(tmp_path: Path) -> tuple[Any, Any, Any]:
    queue = _fixtures.OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create(
        "Dormant review", labels=["review"], source=SCOPE.source, origin_ref=SCOPE.origin_ref,
    )
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:bridge-a",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.claim_one("host/worker", task_id=task.id)
    queue.start(task.id, "host/worker", owner_session_id="original-native")
    queue.suspend(task.id, "host/worker", reason="Waiting on the source")
    _report_closed(queue, task.id)
    return queue, task, queue.latest_reservation(task.id)


def _claim_cooling(queue: Any, reservation: Any, **kwargs: Any) -> Any:
    return queue.claim_spawn_cooling(
        reservation.key, startup_revision=reservation.startup_revision,
        session_handle=reservation.session_handle, state=reservation.state, **kwargs,
    )


def _deliver_closeout_wake(queue: Any, task_id: str) -> None:
    wake = queue.claim_due_wake()
    assert wake is not None and wake.task_id == task_id
    queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
    assert queue.list_wakes(task_id)[0].status == "delivered"


def test_delivered_closeout_wake_excludes_cooling_only_while_its_body_may_exist(
    tmp_path: Path,
) -> None:
    queue, task, reservation = _suspended_closeout_body(tmp_path)
    with pytest.raises(TaskError, match="wake excludes physical cooling"):
        _claim_cooling(queue, reservation, body_gone=True)
    _deliver_closeout_wake(queue, task.id)
    with pytest.raises(TaskError, match="current owner wake excludes physical cooling"):
        _claim_cooling(queue, reservation)
    assert _claim_cooling(queue, reservation, body_gone=True).cooling_token


def test_gone_body_with_delivered_closeout_wake_is_cooled_without_stop(tmp_path: Path) -> None:
    queue, task, reservation = _suspended_closeout_body(tmp_path)
    _deliver_closeout_wake(queue, task.id)
    stopped: list[str] = []
    verdict = {"value": tracking.LIVE}

    def stop(session_id: str) -> bool:
        stopped.append(session_id)
        return True

    supervisor = Supervisor(
        _fixtures.QueueBackedClient(queue),
        spawn_fn=lambda _task: pytest.fail("no replacement"),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=stop,
        local_body_verdict_fn=lambda _session_id: verdict["value"],
    )

    assert supervisor.cool_dormant_bodies() == 0
    assert queue.get_reservation(reservation.key).state == SpawnState.SPAWNED
    supervisor._cold_retry_after.clear()
    verdict["value"] = tracking.GONE
    assert supervisor.cool_dormant_bodies() == 1
    assert stopped == []
    assert queue.get_reservation(reservation.key).state == SpawnState.COLD


def _supervisor(client: Any, verdicts: list[str], stopped: list[str]) -> Supervisor:
    def verdict(_session_id: str) -> str:
        return verdicts.pop(0) if len(verdicts) > 1 else verdicts[0]

    def stop(session_id: str) -> bool:
        stopped.append(session_id)
        return True

    return Supervisor(
        client, spawn_fn=lambda _task: pytest.fail("no replacement"), repo=TEST_REPO,
        labels=["review"], local_cold_fn=stop, local_body_verdict_fn=verdict,
    )


def test_gone_verdict_turning_unknown_after_claim_does_not_strand_guard(tmp_path: Path) -> None:
    queue, task, reservation = _suspended_closeout_body(tmp_path)
    _deliver_closeout_wake(queue, task.id)
    stopped: list[str] = []
    verdicts = [tracking.GONE, tracking.UNKNOWN]
    supervisor = _supervisor(_fixtures.QueueBackedClient(queue), verdicts, stopped)

    assert supervisor.cool_dormant_bodies() == 1
    cooled = queue.get_reservation(reservation.key)
    assert cooled.state == SpawnState.COLD and cooled.cooling_token is None
    assert stopped == [] and verdicts == [tracking.UNKNOWN]


def test_wake_excluded_live_probe_is_budgeted_and_backed_off(tmp_path: Path) -> None:
    queue, task, reservation = _suspended_closeout_body(tmp_path)
    _deliver_closeout_wake(queue, task.id)
    probes: list[str] = []
    supervisor = _supervisor(_fixtures.QueueBackedClient(queue), [tracking.LIVE], [])
    probe = supervisor.local_body_verdict_fn

    def counted(session_id: str) -> str:
        probes.append(session_id)
        return str(probe(session_id))

    supervisor.local_body_verdict_fn = counted
    assert supervisor.cool_dormant_bodies() == 0
    assert supervisor.cool_dormant_bodies() == 0
    assert len(probes) == 1
    assert queue.get_reservation(reservation.key).cooling_token is None


def _http_client(app: Any, seen: list[dict[str, Any]], *, legacy: bool) -> DispatchClient:
    server = TestClient(app)

    def handle(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/claim-cooling"):
            seen.append(body)
            if legacy and "body_gone" in body:
                # An older coordinator's strict body model forbids the field.
                return httpx.Response(422, json={"detail": "extra_forbidden: body_gone"})
        result = server.request(
            request.method, request.url.path, params=request.url.params, json=body,
        )
        return httpx.Response(result.status_code, json=result.json())

    return DispatchClient("http://testserver", transport=httpx.MockTransport(handle))


@pytest.mark.parametrize("legacy", [False, True])
def test_body_gone_claim_over_http_and_against_legacy_coordinator(
    tmp_path: Path, legacy: bool,
) -> None:
    queue, task, reservation = _suspended_closeout_body(tmp_path)
    _deliver_closeout_wake(queue, task.id)
    seen: list[dict[str, Any]] = []
    stopped: list[str] = []
    with _http_client(create_app(queue), seen, legacy=legacy) as client:
        supervisor = _supervisor(client, [tracking.GONE], stopped)
        assert supervisor.cool_dormant_bodies() == (0 if legacy else 1)

    # The ordinary claim omits the field, so only the gone retry needs the new peer.
    assert "body_gone" not in seen[0] and seen[1]["body_gone"] is True
    current = queue.get_reservation(reservation.key)
    assert current.cooling_token is None and stopped == []
    assert current.state == (SpawnState.SPAWNED if legacy else SpawnState.COLD)


def test_suspended_body_is_stopped_only_after_its_grace_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _fixtures.OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Dormant review", labels=["review"])
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:bridge-a",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.claim_one("host/worker", task_id=task.id)
    queue.start(task.id, "host/worker", owner_session_id="original-native")
    queue.suspend(task.id, "host/worker", reason="Waiting on the source")
    stopped: list[str] = []
    supervisor = _supervisor(_fixtures.QueueBackedClient(queue), [tracking.LIVE], stopped)
    supervisor.dormant_grace_seconds = 300.0
    clock = {"now": 1_000.0}
    monkeypatch.setattr("agent_dispatch.supervisor_startup_fence.time.time", lambda: clock["now"])

    assert supervisor.cool_dormant_bodies() == 0
    clock["now"] += 299.0
    assert supervisor.cool_dormant_bodies() == 0
    assert stopped == [] and queue.get_reservation(reservation.key).state == SpawnState.SPAWNED
    clock["now"] += 1.0
    assert supervisor.cool_dormant_bodies() == 1
    assert stopped == ["bridge-a"]
    assert queue.get_reservation(reservation.key).state == SpawnState.COLD
    assert supervisor._dormant_since == {}


def test_dormant_grace_seconds_env_falls_back_on_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch.supervise_cli import DEFAULT_DORMANT_GRACE_SECONDS, _dormant_grace_seconds

    monkeypatch.delenv("AGENT_DISPATCH_DORMANT_GRACE_SECONDS", raising=False)
    assert _dormant_grace_seconds() == DEFAULT_DORMANT_GRACE_SECONDS
    monkeypatch.setenv("AGENT_DISPATCH_DORMANT_GRACE_SECONDS", "45")
    assert _dormant_grace_seconds() == 45.0
    for bad in ("-1", "soon", "nan"):
        monkeypatch.setenv("AGENT_DISPATCH_DORMANT_GRACE_SECONDS", bad)
        assert _dormant_grace_seconds() == DEFAULT_DORMANT_GRACE_SECONDS
