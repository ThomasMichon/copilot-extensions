"""Real coordinator CI send boundaries with synthetic authority and transport."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_dispatch import bridge, coordinator_execution_sender as sender
from agent_dispatch.ci_repair_spawn import make_ci_repair_spawn
from agent_dispatch.coordinator_execution_runtime import coordinator_incarnation
from agent_dispatch.coordinator_spawn import register_spawn_routes
from agent_dispatch.events import EventBus
from agent_dispatch.execution_effect_adapter import EffectRevocation
from agent_dispatch.execution_scope_binding import resolve_execution_installation
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_execution_runtimes import CoordinatorExecutionRuntime
from agent_dispatch.queue_records import TaskError
from tests.test_ci_repair import EVIDENCE, EXECUTION, verdict
from tests.test_execution_scope_binding import _governance, _state

REPO = "github.com/example/project"
SOURCE = "ci-repair"
PROCESS = CoordinatorExecutionRuntime("server", 1234, "start-a")


@pytest.fixture
def cohort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    producer = queue.handoff_producer_scope(
        REPO, SOURCE, producer_id="producer-a", expected_generation=0
    )
    task = queue.create(
        "repair",
        repo=REPO,
        source=SOURCE,
        target_repo=EVIDENCE["repository"],
        origin_ref="repair-lane",
        labels=["ci-repair"],
        require_verification=True,
        dedup_key=f"ci-repair:{EVIDENCE['repository']}:{EVIDENCE['signature']}",
        prompt=json.dumps(
            {"schema": "agent-dispatch.ci-repair.v1", "failure": EVIDENCE, "execution": EXECUTION}
        ),
        producer_scope={"repo": REPO, "source": SOURCE},
        producer_id="producer-a",
        producer_generation=1,
        producer_capability=producer.producer_capability,
        producer_request_id="task",
    )
    queue.register_registration(
        "supervised-lane",
        {
            "repo": REPO,
            "labels": ["ci-repair"],
            "ci_repair_bindings": [EXECUTION],
            "ci_repair_admission_command": ["synthetic-admission"],
        },
        reg_id="repair-lane",
    )
    binding = queue.bind_execution_scope(
        resolve_execution_installation(_governance()),
        repo=REPO,
        source=SOURCE,
        expected_producer_generation=1,
    )
    monkeypatch.setattr(sender, "LoopGovernance", _governance)
    monkeypatch.setattr(
        sender.runtime, "_own_process", lambda incarnation: replace(PROCESS, incarnation=incarnation)
    )
    monkeypatch.setattr(
        "agent_dispatch.ci_repair_spawn._invoke",
        lambda *args, **kwargs: {**verdict(), **kwargs["request"]},
    )
    monkeypatch.setattr(
        bridge, "spawn_worker",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"session_id":"body"}', stderr=""),
    )
    monkeypatch.setattr(bridge, "resume_worker", lambda *args, **kwargs: True)
    app = FastAPI()
    register_spawn_routes(app, queue, EventBus())
    return queue, task, binding, app


def _post(cohort):
    queue, task, _, app = cohort
    with TestClient(app) as client:
        response = client.post(
            "/spawn-reservations", json={"task_id": task.id, "reserved_by": "repair-lane"}
        )
    assert response.status_code == 200, response.text
    return response.json()


def _sends(queue: TaskQueue) -> list[dict]:
    with queue._connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM execution_send_custody")]


def _close(queue, binding) -> None:
    queue.close_execution_admission(
        EffectRevocation(binding.scope, "operation-a", 1, "automatic", "manual"),
        source=SOURCE,
        expected_producer_generation=1,
    )


def test_actual_launch_and_delivery_are_admitted_before_transport(cohort, monkeypatch) -> None:
    queue, task, binding, app = cohort
    observed = []

    def transport(kind: str):
        rows = _sends(queue)
        assert rows[-1]["kind"] == kind and rows[-1]["state"] == "pending"
        observed.append(kind)
        # Neither installation validation nor transport holds the queue write lock.
        queue.create("unrelated", repo="example/other")
        if kind == "launch":
            return SimpleNamespace(returncode=0, stdout='{"session_id":"body"}', stderr="")
        return True

    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: transport("launch"))
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **k: transport("deliver"))
    result = _post(cohort)
    assert result["coordinator_launch"]["ok"] is True and result["reserved"] is False
    assert observed == ["launch", "deliver"]
    rows = _sends(queue)
    assert [row["state"] for row in rows] == ["sent", "sent"]
    assert len({json.loads(row["identity"])["runtime_id"] for row in rows}) == 1
    with queue._connect() as conn:
        runtime = conn.execute("SELECT * FROM execution_runtime_enrollments").fetchone()
        assert runtime["role"] == "coordinator"
        assert json.loads(runtime["process"])["incarnation"] == coordinator_incarnation(app)
    replay = _post(cohort)
    assert replay["reserved"] is False and "coordinator_launch" not in replay
    assert observed == ["launch", "deliver"]
    _close(queue, binding)
    assert queue.execution_closure_inventory(binding.scope)["acknowledged"] is False


@pytest.mark.parametrize("stage", ["launch", "deliver"])
@pytest.mark.parametrize("failure", ["timeout", "oserror", "nonzero", "missing_identity"])
def test_ambiguous_transport_is_retained_and_never_retried(
    cohort, monkeypatch, stage: str, failure: str
) -> None:
    queue, _, _, _ = cohort
    calls = []

    def transport(*args, **kwargs):
        calls.append(stage)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("transport", 30)
        if failure == "oserror":
            raise OSError("transport unavailable")
        if stage == "deliver":
            return False
        return SimpleNamespace(
            returncode=1 if failure == "nonzero" else 0, stdout="{}", stderr="lost reply"
        )

    monkeypatch.setattr(bridge, "spawn_worker" if stage == "launch" else "resume_worker", transport)
    assert _post(cohort)["coordinator_launch"]["ok"] is False
    assert _sends(queue)[-1]["kind"] == stage
    assert _sends(queue)[-1]["state"] == "unknown"
    with TestClient(cohort[3]) as client:
        replay = client.post(
            "/spawn-reservations",
            json={"task_id": cohort[1].id, "reserved_by": "repair-lane"},
        )
    # Active release obligations may be observed; no new send is allowed.
    assert replay.status_code in {200, 409}
    assert calls == [stage]
    assert _sends(TaskQueue(queue.db_path))[-1]["state"] == "unknown"


def test_final_launch_admission_sees_closure_during_independent_policy_probe(
    cohort, monkeypatch
) -> None:
    queue, _, binding, _ = cohort

    def admission(*args, **kwargs):
        if kwargs["request"]["phase"] == "spawn":
            _close(queue, binding)
        return {**verdict(), **kwargs["request"]}

    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admission)
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("closed launch"))
    result = _post(cohort)
    assert result["coordinator_launch"]["ok"] is False
    assert "closed" in result["coordinator_launch"]["error"]
    assert _sends(queue) == []


@pytest.mark.parametrize("close_at", ["launch_reply", "publication"])
def test_closure_between_idle_launch_and_task_delivery_preserves_cleanup(
    cohort, monkeypatch, close_at: str
) -> None:
    queue, task, binding, _ = cohort
    if close_at == "launch_reply":
        def transport(*args, **kwargs):
            _close(queue, binding)
            return SimpleNamespace(returncode=0, stdout='{"session_id":"body"}', stderr="")
        monkeypatch.setattr(bridge, "spawn_worker", transport)
    else:
        original = queue.record_spawn

        def publish(*args, **kwargs):
            result = original(*args, **kwargs)
            _close(queue, binding)
            return result
        monkeypatch.setattr(queue, "record_spawn", publish)
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **k: pytest.fail("closed delivery"))
    assert _post(cohort)["coordinator_launch"]["ok"] is False
    assert [(row["kind"], row["state"]) for row in _sends(queue)] == [("launch", "sent")]
    reservation = queue.latest_reservation(task.id)
    assert reservation.release_requested and reservation.session_handle == "local-body:body"


@pytest.mark.parametrize("drift", ["legacy", "receipt", "process"])
def test_bound_sender_never_falls_back_when_provenance_is_unavailable(
    cohort, monkeypatch, drift: str
) -> None:
    queue, _, _, _ = cohort
    state = _state()
    if drift == "legacy":
        state["baseline"]["actualMode"] = "legacy"
    elif drift == "receipt":
        state["baseline"]["installGeneration"] = 4
    else:
        def unknown(incarnation):
            raise TaskError("process identity unavailable")
        monkeypatch.setattr(sender.runtime, "_own_process", unknown)
    monkeypatch.setattr(sender, "LoopGovernance", lambda: _governance(state))
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("unqualified send"))
    assert _post(cohort)["coordinator_launch"]["ok"] is False
    assert _sends(queue) == []


def test_pretransport_policy_refusal_does_not_create_ambiguous_custody(cohort, monkeypatch) -> None:
    queue, _, _, _ = cohort

    def admission(*args, **kwargs):
        return {**verdict(), **kwargs["request"], "allowed": kwargs["request"]["phase"] != "spawn"}

    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admission)
    assert _post(cohort)["coordinator_launch"]["ok"] is False
    assert _sends(queue) == []


def test_stage_reentry_after_response_loss_is_observation_not_resend(cohort) -> None:
    queue, task, _, app = cohort
    reservation, reserved = queue.reserve_spawn(task.id, reserved_by="repair-lane")
    assert reserved
    first = sender.CoordinatorExecutionSender(queue, task, reservation, coordinator_incarnation(app))
    with pytest.raises(OSError):
        with first.attempt("launch"):
            raise OSError("reply lost")
    replacement = sender.CoordinatorExecutionSender(queue, task, reservation, "restarted-server")
    with pytest.raises(TaskError, match="already has custody"):
        with replacement.attempt("launch"):
            pytest.fail("must not send")
    assert len(_sends(queue)) == 1 and _sends(queue)[0]["state"] == "unknown"


def test_historical_enrollment_cannot_disappear_into_legacy_fallback(cohort, monkeypatch) -> None:
    queue, _, binding, _ = cohort
    installation = resolve_execution_installation(_governance())
    queue.enroll_coordinator_execution_runtime(binding, installation, PROCESS)
    with queue._connect() as conn:
        conn.execute("DELETE FROM execution_scope_bindings")
    state = _state()
    state["baseline"]["actualMode"] = "legacy"
    monkeypatch.setattr(sender, "LoopGovernance", lambda: _governance(state))
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("legacy fallback"))
    assert _post(cohort)["coordinator_launch"]["ok"] is False
    assert _sends(queue) == []


def test_final_provenance_recheck_refuses_drift_before_custody(cohort, monkeypatch) -> None:
    queue, _, _, _ = cohort
    first = resolve_execution_installation(_governance())
    reads = iter([first, replace(first, baseline="changed")])
    monkeypatch.setattr(sender, "resolve_execution_installation", lambda _: next(reads))
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("stale provenance"))
    result = _post(cohort)
    assert result["coordinator_launch"]["ok"] is False
    assert "provenance changed" in result["coordinator_launch"]["error"]
    assert _sends(queue) == []


def test_producer_rollover_cannot_adopt_an_inflight_coordinator_delivery(cohort, monkeypatch) -> None:
    queue, task, _, _ = cohort
    original = queue.record_spawn

    def publish(*args, **kwargs):
        result = original(*args, **kwargs)
        queue.handoff_producer_scope(
            REPO, SOURCE, producer_id="producer-b", expected_generation=1
        )
        return result

    monkeypatch.setattr(queue, "record_spawn", publish)
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **k: pytest.fail("retired producer"))
    result = _post(cohort)
    assert result["coordinator_launch"]["ok"] is False
    assert "producer generation changed" in result["coordinator_launch"]["error"]
    assert [(row["kind"], row["state"]) for row in _sends(queue)] == [("launch", "sent")]
    assert queue.latest_reservation(task.id).release_requested


def test_failed_settlement_leaves_pending_custody_and_no_new_transport(cohort, monkeypatch) -> None:
    queue, task, _, app = cohort
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="repair-lane")
    owner = sender.CoordinatorExecutionSender(queue, task, reservation, coordinator_incarnation(app))

    def unavailable(*args, **kwargs):
        raise TaskError("settlement unavailable")

    monkeypatch.setattr(queue, "settle_execution_send", unavailable)
    with pytest.raises(TaskError, match="settlement unavailable"):
        with owner.attempt("launch") as attempt:
            attempt.sent()
    assert _sends(queue)[0]["state"] == "pending"
    with pytest.raises(TaskError, match="already has custody"):
        with owner.attempt("launch"):
            pytest.fail("pending custody cannot authorize another transport")


@pytest.mark.parametrize("error_type", [TaskError, OSError, sqlite3.OperationalError])
def test_launch_settlement_failure_retains_known_session_and_cleanup(
    cohort, monkeypatch, error_type
) -> None:
    queue, task, _, _ = cohort

    def unavailable(*args, **kwargs):
        raise error_type("settlement unavailable")

    monkeypatch.setattr(queue, "settle_execution_send", unavailable)
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **k: pytest.fail("no task turn"))
    result = _post(cohort)
    assert result["coordinator_launch"]["ok"] is False
    assert result["coordinator_launch"]["session"] == "local-body:body"
    assert result["coordinator_launch"]["error"] == "settlement unavailable"
    reservation = queue.latest_reservation(task.id)
    assert reservation.session_handle == "local-body:body"
    assert reservation.release_requested is True
    assert _sends(queue)[0]["state"] == "pending"


@pytest.mark.parametrize("error_type", [TaskError, OSError, sqlite3.OperationalError])
def test_delivery_settlement_failure_retains_existing_session_and_pending_custody(
    cohort, monkeypatch, error_type
) -> None:
    queue, task, _, _ = cohort
    original = queue.settle_execution_send

    def settle(identity, **kwargs):
        if identity.kind == "deliver":
            raise error_type("settlement unavailable")
        return original(identity, **kwargs)

    monkeypatch.setattr(queue, "settle_execution_send", settle)
    assert _post(cohort)["coordinator_launch"]["ok"] is False
    reservation = queue.latest_reservation(task.id)
    assert reservation.session_handle == "local-body:body" and reservation.release_requested
    assert [(row["kind"], row["state"]) for row in _sends(queue)] == [
        ("launch", "sent"), ("deliver", "pending")
    ]


def test_direct_factory_without_sender_preserves_unqualified_behavior(cohort) -> None:
    _, task, _, _ = cohort
    ok, handle = make_ci_repair_spawn(
        [EXECUTION], admission_command=["synthetic-admission"]
    )(asdict(task))
    assert ok and handle["session"] == "local-body:body"
