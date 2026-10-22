"""Coordinator enrollment is retained accounting, not universal execution fencing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import coordinator_execution_runtime as routes
from agent_dispatch.coordinator import create_app
from agent_dispatch.execution_effect_adapter import EffectRevocation
from agent_dispatch.execution_scope_binding import resolve_execution_installation
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_execution_runtimes import CoordinatorExecutionRuntime
from agent_dispatch.queue_records import TaskError
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
from tests._helpers import TEST_REPO
from tests.test_execution_scope_binding import BODY, _bind, _governance, _headers, _state

ENDPOINT = "/execution-scopes/enroll-coordinator"
PROCESS = CoordinatorExecutionRuntime("incarnation-a", 1234, "process-start-a")


@pytest.fixture
def queue(tmp_path) -> TaskQueue:
    queue = TaskQueue(tmp_path / "tasks.db")
    queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-a", expected_generation=0
    )
    return queue


def _enroll(
    queue: TaskQueue, runtime: CoordinatorExecutionRuntime = PROCESS, *, generation: int = 1
) -> dict[str, object]:
    return queue.enroll_coordinator_execution_runtime(
        _bind(queue, generation=generation), resolve_execution_installation(_governance()), runtime
    )


def _close(queue: TaskQueue) -> None:
    queue.close_execution_admission(
        EffectRevocation(_bind(queue).scope, "operation-a", 1, "automatic", "manual"),
        source="scheduled",
        expected_producer_generation=1,
    )


def _client(monkeypatch, queue: TaskQueue, control: str | None = "control") -> TestClient:
    monkeypatch.setattr(routes, "LoopGovernance", _governance)
    monkeypatch.setattr(
        routes, "_own_process", lambda incarnation: replace(PROCESS, incarnation=incarnation)
    )
    return TestClient(
        create_app(
            queue, token="data", control_token=control, enable_mcp=False, verification_interval=0
        )
    )


def test_enrollment_replays_after_queue_reopen_without_task_mutation(queue) -> None:
    first = _enroll(queue)
    replay = _enroll(TaskQueue(queue.db_path))
    assert first["runtime_id"] == replay["runtime_id"]
    assert first["replayed"] is False and replay["replayed"] is True
    assert first["process"] == {
        "incarnation": "incarnation-a",
        "pid": 1234,
        "process_start": "process-start-a",
    }
    assert first["acknowledged"] is False and first["send_admission"] is False
    with queue._connect() as conn:
        for table in ("tasks", "spawn_reservations", "execution_send_custody"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_concurrent_enrollment_has_one_initial_record(queue) -> None:
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: _enroll(queue), range(2)))
    assert len({result["runtime_id"] for result in results}) == 1
    assert sorted(result["replayed"] for result in results) == [False, True]


def test_closure_retains_every_incarnation_without_inferring_exit(queue) -> None:
    first = _enroll(queue)
    second = _enroll(queue, replace(PROCESS, incarnation="incarnation-b"))
    _close(queue)
    inventory = queue.execution_closure_inventory(_bind(queue).scope)
    assert {row["runtime_id"] for row in inventory["runtimes"]} == {
        first["runtime_id"],
        second["runtime_id"],
    }
    assert inventory["acknowledged"] is False
    replay = _enroll(queue)
    assert replay["replayed"] is True and replay["send_admission"] is False
    with pytest.raises(TaskError, match="admission closed"):
        _enroll(queue, replace(PROCESS, incarnation="incarnation-c"))


def test_closure_first_refuses_even_the_first_runtime(queue) -> None:
    _close(queue)
    with pytest.raises(TaskError, match="admission closed"):
        _enroll(queue)
    assert queue.execution_closure_inventory(_bind(queue).scope)["runtimes"] == []


def test_enrollment_and_closure_have_one_transaction_order(queue) -> None:
    _bind(queue)
    with ThreadPoolExecutor(max_workers=2) as workers:
        enrolling = workers.submit(_enroll, queue)
        closing = workers.submit(_close, queue)
        closing.result()
        try:
            result = enrolling.result()
        except TaskError as exc:
            assert "admission closed" in str(exc)
            result = None
    inventory = queue.execution_closure_inventory(_bind(queue).scope)
    assert len(inventory["runtimes"]) == (0 if result is None else 1)
    assert inventory["acknowledged"] is False


def test_pid_reuse_and_application_restart_do_not_replay_the_old_runtime(queue) -> None:
    first = _enroll(queue)
    reused_pid = _enroll(queue, replace(PROCESS, process_start="process-start-b"))
    restarted = _enroll(queue, replace(PROCESS, incarnation="incarnation-b"))
    assert len({row["runtime_id"] for row in (first, reused_pid, restarted)}) == 3


def test_producer_rollover_retains_old_runtime_but_needs_current_generation(queue) -> None:
    first = _enroll(queue)
    queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-b", expected_generation=1
    )
    with pytest.raises(TaskError, match="current managed producer"):
        _enroll(queue)
    second = _enroll(queue, generation=2)
    assert first["runtime_id"] != second["runtime_id"]
    with queue._connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM execution_runtime_enrollments").fetchone()[0] == 2
        )


def test_internal_enrollment_cannot_invent_a_binding_or_installation(queue) -> None:
    installation = resolve_execution_installation(_governance())
    binding = _bind(queue)
    for wrong in (
        replace(installation, identity="foreign"),
        replace(installation, baseline="foreign"),
    ):
        with pytest.raises(TaskError, match="retained installation binding"):
            queue.enroll_coordinator_execution_runtime(binding, wrong, PROCESS)
    with queue._connect() as conn:
        conn.execute("DELETE FROM execution_scope_bindings")
    with pytest.raises(TaskError, match="retained installation binding"):
        queue.enroll_coordinator_execution_runtime(binding, installation, PROCESS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("incarnation", ""),
        ("pid", True),
        ("pid", 0),
        ("process_start", ""),
        ("process_start", None),
    ],
)
def test_unknown_process_identity_never_enrolls(field, value) -> None:
    with pytest.raises(TaskError, match="known process incarnation"):
        replace(PROCESS, **{field: value})


@pytest.mark.parametrize("credential,expected", [(None, 401), ("data", 403), ("control", 200)])
def test_only_control_can_enroll_the_server_process(
    monkeypatch, queue, credential, expected
) -> None:
    client = _client(monkeypatch, queue)
    response = client.post(
        ENDPOINT, json=BODY, headers={} if credential is None else _headers(credential)
    )
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["runtime_enrolled"] is True
        assert response.json()["acknowledged"] is False
        assert response.json()["send_admission"] is False
        replay = client.post(ENDPOINT, json=BODY, headers=_headers("control")).json()
        assert replay["runtime_id"] == response.json()["runtime_id"]
        assert replay["replayed"] is True
    else:
        with queue._connect() as conn:
            assert (
                conn.execute("SELECT count(*) FROM execution_runtime_enrollments").fetchone()[0]
                == 0
            )


def test_new_application_cannot_reuse_the_old_enrollment_after_closure(monkeypatch, queue) -> None:
    client = _client(monkeypatch, queue)
    first = client.post(ENDPOINT, json=BODY, headers=_headers("control"))
    assert first.status_code == 200
    _close(queue)
    replay = client.post(ENDPOINT, json=BODY, headers=_headers("control"))
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    replacement = _client(monkeypatch, queue)
    refused = replacement.post(ENDPOINT, json=BODY, headers=_headers("control"))
    assert refused.status_code == 409
    assert "admission closed" in refused.json()["detail"]["reason"]
    assert len(queue.execution_closure_inventory(_bind(queue).scope)["runtimes"]) == 1


def test_runtime_history_cannot_be_hidden_by_rebinding_after_missing_binding(queue) -> None:
    first = _enroll(queue)
    with queue._connect() as conn:
        conn.execute("DELETE FROM execution_scope_bindings")
    state = _state()
    state["baseline"]["context"] = "/synthetic/other/context.json"
    with pytest.raises(TaskError, match="qualified producer scope"):
        _bind(queue, _governance(state))
    assert _enroll(queue)["runtime_id"] == first["runtime_id"]


@pytest.mark.parametrize(
    "field", ["pid", "process_start", "incarnation", "runtime_id", "role", "scope", "installation"]
)
def test_caller_cannot_choose_a_runtime_or_qualification(monkeypatch, queue, field) -> None:
    response = _client(monkeypatch, queue).post(
        ENDPOINT, json={**BODY, field: "caller-value"}, headers=_headers("control")
    )
    assert response.status_code == 422


def test_control_unconfigured_and_task_scoped_auth_are_not_enrollment(monkeypatch, queue) -> None:
    client = _client(monkeypatch, queue, control=None)
    assert client.post(ENDPOINT, json=BODY, headers=_headers("data")).status_code == 503
    client = _client(monkeypatch, queue)
    credential = mint_task_scope_token(
        "control", task_id="task-a", worker_id="worker-a", owner_session_id="session-a", epoch=0
    )
    response = client.post(ENDPOINT, json=BODY, headers={TASK_SCOPE_HEADER: credential})
    assert response.status_code == 403


def test_receipt_drift_after_insertion_preserves_accounting_without_success(
    monkeypatch, queue
) -> None:
    client = _client(monkeypatch, queue)
    original = queue.enroll_coordinator_execution_runtime

    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        state = _state()
        state["baseline"]["installGeneration"] = 4
        monkeypatch.setattr(
            routes,
            "resolve_execution_installation",
            lambda _: resolve_execution_installation(_governance(state)),
        )
        return result

    monkeypatch.setattr(queue, "enroll_coordinator_execution_runtime", change)
    response = client.post(ENDPOINT, json=BODY, headers=_headers("control"))
    assert response.status_code == 409
    assert "provenance changed" in response.json()["detail"]["reason"]
    with queue._connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM execution_runtime_enrollments").fetchone()[0] == 1
        )


def test_own_process_observes_only_current_pid_and_refuses_unknown_start(monkeypatch) -> None:
    monkeypatch.setattr(routes.os, "getpid", lambda: 1234)
    seen = []
    monkeypatch.setattr(
        routes, "process_start_token", lambda pid: seen.append(pid) or "process-start-a"
    )
    assert routes._own_process("incarnation-a") == PROCESS
    assert seen == [1234]
    monkeypatch.setattr(routes, "process_start_token", lambda _: None)
    with pytest.raises(TaskError, match="start identity is unavailable"):
        routes._own_process("incarnation-a")
