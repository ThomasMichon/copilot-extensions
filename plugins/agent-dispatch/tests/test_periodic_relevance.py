"""Periodic source judgment, durable cadence and the actual relevance receiver."""

from __future__ import annotations

import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from subprocess import CompletedProcess
from unittest.mock import Mock
import sys

import pytest

from agent_dispatch.periodic_relevance import reconsider_unbound_tasks
from agent_dispatch.producers.evaluator import (
    Abandon, EvaluatorError, apply_decisions, decision_from_dict,
)
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.queue_relevance_registration import RelevanceRegistration
from agent_dispatch.relevance_evaluation import PeriodicRelevancePolicy
from agent_dispatch.relevance_source import RelevanceSource
from tests._helpers import TEST_REPO
from tests.test_task_relevance_effective_scope import declaration, venue as venue
from tests.test_task_relevance_registration import SPEC, emitted, q as q


@pytest.fixture
def periodic(q, venue, monkeypatch):
    _, trusted, _ = venue
    declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    spec = {
        "repo": TEST_REPO, "evaluator_ref": "review-loop",
        "evaluator_spec": {"scripts": {"review-loop": ["source-reader"]}},
        "periodic_relevance": {
            "min_age_seconds": 10, "interval_seconds": 100, "retry_base": 5,
            "max_attempts": 2, "batch_size": 2,
        },
    }
    rid = declaration(trusted, "review-evaluator", "evaluator", spec)
    clock = [100.0]
    monkeypatch.setattr(q, "_now", lambda value: clock[0] if value is None else value)
    # Use the same registrar roots as the real HTTP fixture, not runtime discovery.
    from agent_dispatch.registrar_discovery import RegistrarSources

    source = RelevanceSource(RegistrarSources(
        base=trusted.parent / "registrar", dropins=trusted.parent / "absent-dropins",
        activation_source=lambda: None,
    ))
    return source, spec, rid, clock


def task(q, **changes):
    return emitted(q, evaluator_ref="review-loop", now=80, **changes)


def runner(verdict="obsolete", *, callback=None):
    def run(argv, **kwargs):
        event = json.loads(kwargs["input"])
        assert event["type"] == "task.reconsider"
        if callback is not None:
            callback(event)
        return CompletedProcess(argv, 0, stdout=json.dumps({
            "decision": "relevance", "verdict": verdict,
            "reason": "authoritative source observation", "observation_ref": "object:revision-2",
        }))
    return Mock(side_effect=run)


def tick(q, periodic, **kwargs):
    return reconsider_unbound_tasks(q, periodic[0], **kwargs)


def test_periodic_closure_uses_receiver_and_only_eligible_source_tasks(q, periodic):
    eligible = task(q)
    young = emitted(q, evaluator_ref="review-loop", now=99)
    manual = task(q, source="manual")
    other_repo = task(q, repo="other/repo")
    bound = task(q)
    q.claim_one("host/worker", task_id=bound.id, now=90)
    check = runner()
    for _ in range(8):
        tick(q, periodic, runner=check)
    assert q.get(eligible.id).status == Status.ABANDONED
    assert all(q.get(t.id).status == Status.QUEUED for t in (young, manual, other_repo))
    assert q.get(bound.id).status == Status.CLAIMED
    assert check.call_count == 1
    assert q.relevance_evaluations(eligible.id)[0]["status"] == "finished"


@pytest.mark.parametrize("verdict", ["needed", "unknown"])
def test_due_and_retry_state_survives_restart_without_age_abandonment(q, periodic, verdict):
    t = task(q)
    check = runner(verdict)
    assert tick(q, periodic, runner=check)["accepted"]
    row = q.relevance_evaluations(t.id)[0]
    assert row["next_due"] == (200 if verdict == "needed" else 105)
    assert q.get(t.id).status == Status.QUEUED
    assert not tick(q, periodic, runner=check)["accepted"]
    from tests._helpers import RepoDefaultingQueue

    restarted = RepoDefaultingQueue(q.db_path)
    restarted._now = q._now
    periodic[3][0] = row["next_due"]
    for _ in range(2):
        tick(restarted, periodic, runner=check)
    assert check.call_count == 2
    assert restarted.get(t.id).status == Status.QUEUED
    if verdict == "unknown":
        assert restarted.relevance_evaluations(t.id)[0]["status"] == "failed"


@pytest.mark.parametrize("change", ["claim", "hold", "definition", "not_active"])
def test_changes_during_source_check_cannot_abandon(q, periodic, venue, change):
    t = task(q)
    active = [True]

    def changed(_event):
        if change == "claim":
            q.claim_one("host/worker", task_id=t.id)
        elif change == "hold":
            from agent_dispatch.overrides import set_override
            set_override(venue[2], periodic[2], reason="maintenance")
        elif change == "definition":
            declaration(venue[1], "review-evaluator", "evaluator", {
                **periodic[1], "evaluator_spec": {"scripts": {"review-loop": ["new-reader"]}},
            })
        else:
            active[0] = False

    report = tick(q, periodic, runner=runner(callback=changed), can_continue=lambda: active[0])
    assert not report["accepted"]
    assert q.get(t.id).status != Status.ABANDONED
    assert not q.steer_log(t.id)


@pytest.mark.parametrize("value", [
    {"decision": "abandon"}, {"decision": "noop"}, {"decision": "confirm"},
])
def test_legacy_or_failed_judgment_never_uses_ordinary_abandon(q, periodic, value):
    t = task(q)
    check = Mock(return_value=CompletedProcess(["source-reader"], 0, stdout=json.dumps(value)))
    assert tick(q, periodic, runner=check)["errors"]
    assert q.get(t.id).status == Status.QUEUED
    assert q.relevance_evaluations(t.id)[0]["next_due"] == 105


def test_saved_report_recovers_after_crash_without_another_source_check(q, periodic):
    t = task(q)
    policy = PeriodicRelevancePolicy.from_spec(periodic[1])
    selector = RelevanceRegistration(periodic[2], "host-a", "default")
    entry = q.claim_relevance_evaluation(
        selector, policy, admission=periodic[0].prepare(), lease_seconds=20,
    )
    snapshot = json.loads(entry["snapshot_json"])
    report = {
        "request_id": "retained-report", "expected_generation": snapshot["generation"],
        "expected_revision": snapshot["revision"],
        "registration_revision": snapshot["registration_revision"],
        "effective_revision": snapshot["effective_revision"], "verdict": "obsolete",
        "reason": "closed source", "observation_ref": "object:2",
    }
    q.save_relevance_evaluation(entry, report)
    periodic[3][0] = 121
    with pytest.raises(TaskError, match="ownership changed"):
        q.reconcile_task_relevance(
            t.id, **{k: v for k, v in report.items()
                     if k not in {"registration_revision", "effective_revision"}},
            scope=RelevanceRegistration(
                periodic[2], "host-a", "default", snapshot["registration_revision"],
                snapshot["effective_revision"],
            ), reporter=f"registration:{periodic[2]}",
            registration_admission=periodic[0].prepare(), evaluation_token=entry["token"],
        )
    check = Mock(side_effect=AssertionError("must recover saved report"))
    assert tick(q, periodic, runner=check)["accepted"][0]["decision"] == "abandoned"
    check.assert_not_called()
    assert q.get(t.id).status == Status.ABANDONED


def test_relevance_decision_cannot_escape_through_generic_lifecycle_apply():
    decision = decision_from_dict({
        "decision": "relevance", "verdict": "obsolete",
        "reason": "closed source", "observation_ref": "object:2",
    })
    abandon = Mock()
    with pytest.raises(EvaluatorError, match="periodic"):
        apply_decisions([decision, Abandon()], creator=Mock(), abandoner=abandon, task_id="task")
    abandon.assert_not_called()


def test_prior_claim_is_owner_closeout_even_when_now_unbound(q, periodic):
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    q.yield_task(t.id, "host/worker")
    accepted = tick(q, periodic, runner=runner())["accepted"][0]
    assert accepted["decision"] == "closeout_pending"
    assert q.get(t.id).status == Status.QUEUED
    assert len(q.steer_log(t.id)) == 1
    assert q.claim_one("host/replacement", task_id=t.id) is None


def test_concurrent_due_selection_has_one_delivery_lease(q, periodic):
    t = task(q)
    policy = PeriodicRelevancePolicy.from_spec(periodic[1])
    selector = RelevanceRegistration(periodic[2], "host-a", "default")
    admission = periodic[0].prepare()

    def claim(_index):
        return q.claim_relevance_evaluation(
            selector, policy, admission=admission, lease_seconds=20,
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        entries = list(workers.map(claim, range(2)))
    assert sum(entry is not None for entry in entries) == 1
    assert len(q.relevance_evaluations(t.id)) == 1


@pytest.mark.parametrize("failure", ["timeout", "malformed", "nonzero"])
def test_check_failure_exhaustion_and_supported_owner_retry(q, periodic, failure):
    t = task(q)
    if failure == "timeout":
        from subprocess import TimeoutExpired
        check = Mock(side_effect=TimeoutExpired(["reader"], 30))
    else:
        check = Mock(return_value=CompletedProcess(
            ["reader"], 1 if failure == "nonzero" else 0, stdout="{invalid",
        ))
    assert tick(q, periodic, runner=check)["errors"]
    periodic[3][0] = 105
    for _ in range(2):
        tick(q, periodic, runner=check)
    row = q.relevance_evaluations(t.id)[0]
    assert row["status"] == "failed"
    assert row["attempts"] == check.call_count == 2
    assert q.get(t.id).status == Status.QUEUED
    q.retry_relevance_evaluation(
        t.id, registration_id=periodic[2], generation=t.generation,
    )
    periodic[3][0] = 110
    for _ in range(2):
        accepted = tick(q, periodic, runner=runner())["accepted"]
        if accepted:
            break
    assert accepted[0]["decision"] == "abandoned"


def test_acceptance_callback_failure_is_recovered_without_duplicate_effect(q, periodic, monkeypatch):
    t = task(q)
    publish = Mock(side_effect=RuntimeError("post-commit callback failure"))
    monkeypatch.setattr("agent_dispatch.periodic_relevance._publish", publish)
    result = tick(q, periodic, runner=runner(), bus=Mock())
    assert result["accepted"][0]["decision"] == "abandoned"
    assert q.get(t.id).status == Status.ABANDONED
    assert publish.call_count == 1
    tick(q, periodic, runner=Mock(side_effect=AssertionError("not rechecked")))
    assert len([e for e in q.events(t.id) if e["to_status"] == Status.ABANDONED]) == 1


@pytest.mark.parametrize("gate", ["inactive", "draining", "governance"])
def test_coordinator_loop_honors_owner_gates_and_stops_cooperatively(q, periodic, monkeypatch, gate):
    from agent_dispatch.coordinator_loops import DrainGate
    from agent_dispatch.loop_governance import LoopGovernance
    from agent_dispatch.periodic_relevance import drain_periodic_relevance

    calls = Mock()
    monkeypatch.setattr("agent_dispatch.periodic_relevance.reconsider_unbound_tasks", calls)

    async def exercise():
        stop = asyncio.Event()
        drain_gate = DrainGate()
        drain_gate.set_draining(gate == "draining")
        governance = LoopGovernance(helper=lambda _point: {
            "status": "backoff" if gate == "governance" else "ready",
        })
        loop = asyncio.create_task(drain_periodic_relevance(
            q, periodic[0], bus=Mock(), interval=60, is_active=lambda: gate != "inactive",
            gate=drain_gate, stop_event=stop, governance=governance,
        ))
        await asyncio.sleep(0.02)
        stop.set()
        await asyncio.wait_for(loop, timeout=1)

    asyncio.run(exercise())
    calls.assert_not_called()


def test_coordinator_lifespan_owns_periodic_loop(q, periodic, venue, monkeypatch):
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app

    seen = []

    async def drain(*args, stop_event, **kwargs):
        seen.append("started")
        await stop_event.wait()
        seen.append("stopped")

    monkeypatch.setattr("agent_dispatch.periodic_relevance.drain_periodic_relevance", drain)
    app = create_app(
        q, token="data", control_token="control", enable_mcp=False,
        verification_interval=0, cohort_sources=periodic[0].sources,
        wake_is_active=lambda: True, periodic_relevance_interval=60,
    )
    with TestClient(app):
        assert seen == ["started"]
    assert seen == ["started", "stopped"]


@pytest.mark.parametrize("config", [
    {"batch_size": True}, {"batch_size": 101}, {"retry_base": 0},
    {"min_age_seconds": float("nan")}, {"max_attempts": 0}, {"unknown": 1},
])
def test_registration_rejects_invalid_periodic_policy_before_persistence(q, periodic, config):
    with pytest.raises(TaskError):
        q.register_registration(
            "evaluator", {**periodic[1], "periodic_relevance": config},
            reg_id="invalid", machine="host-a",
        )
    assert q.get_registration("invalid") is None


def test_actual_trusted_script_receives_reconsider_event(q, periodic, venue, tmp_path):
    script = tmp_path / "source-check.py"
    script.write_text(
        "import json, sys\n"
        "event = json.load(sys.stdin)\n"
        "assert event['type'] == 'task.reconsider'\n"
        "assert event['task']['evaluator_ref'] == 'review-loop'\n"
        "assert event['relevance_snapshot']['effective_revision']\n"
        "json.dump({'decision': 'relevance', 'verdict': 'obsolete',\n"
        " 'reason': 'authoritative source is closed', 'observation_ref': 'source:2'}, sys.stdout)\n",
        encoding="utf-8",
    )
    declaration(venue[1], "review-evaluator", "evaluator", {
        **periodic[1], "evaluator_spec": {
            "scripts": {"review-loop": [sys.executable, str(script)]},
        },
    })
    t = task(q)
    assert tick(q, periodic)["accepted"][0]["decision"] == "abandoned"
    assert q.get(t.id).status == Status.ABANDONED


def test_bounded_scan_rotates_past_unattributed_tasks(q, periodic):
    for _ in range(6):
        task(q, source="manual")
    eligible = [task(q) for _ in range(3)]
    check = runner()
    for _ in range(20):
        tick(q, periodic, runner=check)
    assert all(q.get(t.id).status == Status.ABANDONED for t in eligible)
    assert check.call_count == len(eligible)


@pytest.mark.parametrize("kind", ["emitter", "evaluator"])
def test_held_registration_does_not_spend_source_check_attempts(q, periodic, venue, kind):
    from agent_dispatch.overrides import set_override

    t = task(q)
    held = periodic[2] if kind == "evaluator" else "declared:example:review-source"
    set_override(venue[2], held, reason="maintenance")
    check = Mock(side_effect=AssertionError("disabled registration cannot check"))
    for _ in range(3):
        assert not tick(q, periodic, runner=check)["accepted"]
    assert q.relevance_evaluations(t.id) == []
    assert q.get(t.id).status == Status.QUEUED
    check.assert_not_called()


def test_claim_race_rechecks_source_and_routes_closeout_to_original_owner(q, periodic):
    t = task(q)
    first = tick(q, periodic, runner=runner(
        callback=lambda _event: q.claim_one("host/worker", task_id=t.id),
    ))
    assert first["errors"] and not first["accepted"]
    periodic[3][0] = 105
    refreshed = runner()
    result = tick(q, periodic, runner=refreshed)
    assert result["accepted"][0]["decision"] == "closeout_pending"
    assert refreshed.call_count == 1
    assert q.get(t.id).status == Status.CLAIMED
    assert q.get(t.id).owner == "host/worker"
    assert len(q.steer_log(t.id)) == 1


def test_registration_rejects_nonfinite_script_timeout(q, periodic):
    with pytest.raises(TaskError, match="timeout_seconds"):
        q.register_registration("evaluator", {
            **periodic[1], "evaluator_spec": {
                "scripts": {"review-loop": ["reader"]}, "timeout_seconds": float("nan"),
            },
        }, reg_id="invalid", machine="host-a")
    assert q.get_registration("invalid") is None
