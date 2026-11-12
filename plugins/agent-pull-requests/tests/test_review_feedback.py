"""Actual protected synthetic submission and existing durable owner; no live writes."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import replace
import json
import time
from typing import Any

import pytest

from test_review_executor import environment as environment, writes
from test_review_owner_store import prepared_intent as prepared_intent

from agent_pull_requests.attention import Cursor, Interest
from agent_pull_requests.attention_acquisition import AttentionInputs
from agent_pull_requests.attention_store import AttentionStore
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore
from agent_pull_requests.review_executor import execute_review
from agent_pull_requests.review_feedback import FeedbackInputs
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_conversation import observation_interval


@pytest.fixture
def feedback_environment(environment: tuple) -> tuple:
    store, selected, generation, attachment, backend, _ = environment
    sections = copy.deepcopy(generation["sections"])
    for section in sections.values():
        if section["data"] is None:
            section.update(
                data=[], complete=True, errors=[], observed_from="2026-10-30T00:00:03Z",
                observed_to="2026-10-30T00:00:04Z",
            )
    generation = build_generation({
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": generation["scope"], "sections": sections,
    })["generation"]
    selected["generation_id"] = generation["id"]
    selected["actions"] = selected["actions"][:1]
    attachment["generation_id"] = generation["id"]
    now = [observation_interval(generation["sections"])[1] + 1]
    store.clock = lambda: now[0]
    expires = int(time.time()) + 600
    ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    AttentionStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    FeedbackCaptureStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    interests = (Interest("maintainer", True, "consumer-a"), Interest("codeowner", True, "consumer-b"))
    observation = ConversationStore(store).observe_workspace(
        generation, expires_at=expires,
        attention=AttentionInputs(generation["id"], interests, True, False, False, False, False),
    )
    backend.positions = {generation["id"]: Cursor(**observation["cursor"])}
    return store, selected, generation, attachment, backend, now, expires, interests


def run(value: tuple, *, interests: tuple[Interest, ...] | None = None, current=None) -> dict:
    store, selected, generation, attachment, backend, _, expires, admitted = value
    return execute_review(
        store, selected, generation, attachment, context_factory=backend.context,
        checkpoint=backend.checkpoint, expires_at=expires, budget_seconds=10,
        feedback=FeedbackInputs(generation["id"], interests or admitted[:1], 60),
        feedback_position=backend.positions.get(generation["id"]),
        feedback_current=current,
    )


@pytest.mark.parametrize("changed_readback", [False, True])
def test_prepared_receipt_correlation_does_not_replace_actual_feedback_readback(
    feedback_environment: tuple, prepared_intent: dict, changed_readback: bool,
) -> None:
    store, selected, generation, attachment, backend, _, expires, interests = feedback_environment
    store.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires)
    selected["version"] = 2
    selected["actions"][0].update({
        key: copy.deepcopy(prepared_intent["actions"][0][key]) for key in ("body", "preparation")
    })
    backend.versioned_reply = True
    if changed_readback:
        def alter(current: Any) -> None:
            current.threads[0]["comments"][0]["content"] += " Later independent edit."
        backend.after_write = alter
    result = execute_review(
        store, selected, generation, attachment, context_factory=backend.context,
        checkpoint=backend.checkpoint, expires_at=expires, budget_seconds=10,
        prepared_intent=True, feedback=FeedbackInputs(generation["id"], interests[:1], 60),
        feedback_position=backend.positions[generation["id"]],
    )
    receipt = store.prepared_review_receipt(
        selected["operation_id"], selected["scope"], selected["actions"][0]["id"],
        expires_at=expires,
    )
    assert receipt["action_state"] == "confirmed" and receipt["fresh_native_read"] is False
    assert receipt["identity"] == selected["actions"][0]["preparation"]["identity"]
    assert result["feedback"]["status"] == ("unavailable" if changed_readback else "handled")
    assert (document(feedback_environment)["attention"]["records"][0]["handled"] is None) is changed_readback
    assert len(writes(backend)) == 1


def document(value: tuple) -> dict:
    return json.loads(value[0].path.read_bytes())


def observe(value: tuple, *, change: str = "reply", second: int = 10) -> dict:
    store, _, generation, _, backend, now, expires, interests = value
    sections = copy.deepcopy(generation["sections"])
    for section in sections.values():
        section.update(
            observed_from=f"2026-10-30T00:00:{second:02d}Z",
            observed_to=f"2026-10-30T00:00:{second + 1:02d}Z",
        )
    sections["discussions"]["data"] = copy.deepcopy(backend.threads)
    if change == "reply":
        sections["discussions"]["data"].append({
            "id": 900 + second, "status": 1, "comments": [{
                "id": 1, "content": "Independent input", "author": {"id": "other-author"},
            }],
        })
        backend.threads = copy.deepcopy(sections["discussions"]["data"])
    elif change == "unavailable":
        sections["pull_request"]["data"].pop("lastMergeTargetCommit")
    elif change in ("completed", "active"):
        sections["pull_request"]["data"]["status"] = change
    elif change == "different_title":
        sections["pull_request"]["data"]["title"] = "Independent title change"
    newer = build_generation({
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": generation["scope"], "sections": sections,
    })["generation"]
    now[0] = observation_interval(newer["sections"])[1] + 1
    observation = ConversationStore(store).observe_workspace(
        newer, expires_at=expires,
        attention=AttentionInputs(newer["id"], interests, True, False, False, False, False),
    )
    if observation["status"] in ("observed", "unchanged"):
        backend.positions[newer["id"]] = Cursor(**observation["cursor"])
    return newer


def test_original_capture_restart_and_all_confirmed_retry_readback_without_resend(feedback_environment: tuple) -> None:
    value = feedback_environment
    store, selected, _, _, backend, _, expires, _ = value
    first = run(value)
    assert first["status"] == "complete" and first["feedback"]["status"] == "handled"
    original = document(value)
    assert original["attention"]["records"][0]["handled"]["cursor"] == {"epoch": 1, "sequence": 1}
    assert original["attention"]["records"][1]["handled"] is None
    assert original["attention"]["records"][0]["provenance"]["receipt_refs"] == [{
        "action_id": "finding", "attempt_id": first["actions"][0]["attempt_id"],
    }]
    restarted = ReplayStore(
        store.root, store.namespace, clock=store.clock, service_identity=store.service_identity,
    )
    value = (restarted, *value[1:])
    backend.positions.clear()
    calls = len(backend.calls)
    retry = run(value)
    assert retry["status"] == "complete" and retry["entered_writes"] == 0
    assert retry["feedback"]["status"] == "handled" and retry["feedback"]["updated"] == 0
    assert len(backend.calls) > calls and len(writes(backend)) == 1
    assert document(value) == original
    backend.threads[0]["comments"][0]["content"] = "Later edit"
    changed = run(value)
    assert changed["status"] == "complete" and changed["feedback"]["status"] == "unavailable"
    assert store.inspect_review(selected["operation_id"], selected["scope"], expires_at=expires)["actions"] == first["actions"]
    assert len(writes(backend)) == 1


@pytest.mark.parametrize("failure", [
    "stale", "future", "unselected", "incomplete", "cursor", "candidate", "watermark",
    "capacity", "journal_meaning", "namespace",
])
def test_capture_refuses_inadmissible_evidence_before_native_attempt(feedback_environment: tuple, failure: str) -> None:
    value = feedback_environment
    store, _, _, _, backend, now, expires, _ = value
    if failure == "stale":
        now[0] += 60
    elif failure == "future":
        now[0] -= 2
    elif failure == "namespace":
        store.namespace = "different-owner"
    else:
        def mutate(data: dict) -> None:
            row = data["attention"]["records"][0]
            if failure == "unselected":
                row["interest"]["matches"] = None
            elif failure == "incomplete":
                row["facts"]["complete"] = False
            elif failure == "cursor":
                row["facts"]["cursor"]["sequence"] = 0
            elif failure == "candidate":
                row["facts"]["candidate"]["base"] = "d" * 40
            elif failure == "watermark":
                data["attention"]["records"][1]["facts"]["observed_at"] += 1
                data["attention"]["records"][1]["facts"]["complete"] = False
            elif failure == "capacity":
                data["capacities"]["feedback_capture_bytes"] = 256
            elif failure == "journal_meaning":
                data["conversations"]["records"][0]["current"]["counts"]["comments"] = 99
        store._review_transaction(mutate, expires_at=expires)
    if failure == "namespace":
        # A foreign owner envelope refuses before even retaining an intent.
        with pytest.raises(ValueError):
            run(value)
    else:
        result = run(value)
        assert result["status"] == "retained" and result["feedback"]["status"] == "unavailable"
        assert result["actions"][0]["state"] == "unattempted"
        assert document(value)["feedback_captures"]["records"] == []
    assert writes(backend) == []


@pytest.mark.parametrize("attempted", ["unknown", "confirmed"])
def test_missing_capture_cannot_be_backfilled_after_attempt(feedback_environment: tuple, attempted: str) -> None:
    value = feedback_environment
    store, selected, generation, attachment, backend, _, expires, _ = value
    if attempted == "confirmed":
        execute_review(
            store, selected, generation, attachment, context_factory=backend.context,
            checkpoint=backend.checkpoint, expires_at=expires, budget_seconds=10,
        )
    else:
        store.admit_review(selected, expires_at=expires)
        store.begin_review_action(selected["operation_id"], selected["scope"], "finding", expires_at=expires)
    before = len(writes(backend))
    result = run(value)
    assert result["feedback"] == {"status": "unavailable", "reason": "capture_missing_after_attempt"}
    assert result["actions"][0]["state"] == attempted and len(writes(backend)) == before
    assert document(value)["feedback_captures"]["records"] == []


@pytest.mark.parametrize("after_effect", [False, True])
def test_actual_target_guard_is_independent_of_head_common_iteration(feedback_environment: tuple, after_effect: bool) -> None:
    value = feedback_environment
    backend = value[4]
    if after_effect:
        backend.after_write = lambda current: current.pr["lastMergeTargetCommit"].update(commitId="e" * 40)
    else:
        backend.pr["lastMergeTargetCommit"]["commitId"] = "e" * 40
    result = run(value)
    assert result["status"] == "candidate_changed" and result["feedback"]["status"] != "handled"
    assert len(writes(backend)) == int(after_effect)
    assert result["actions"][0]["state"] == ("confirmed" if after_effect else "unattempted")
    if after_effect:
        retry = run(value)
        assert retry["entered_writes"] == 0 and retry["feedback"]["status"] == "unavailable"


@pytest.mark.parametrize("target", ["changed", "missing"])
def test_fresher_dispatch_snapshot_rechecks_captured_target(
    feedback_environment: tuple, target: str,
) -> None:
    value = feedback_environment
    backend = value[4]
    original_context = backend.context

    @contextmanager
    def context():
        with original_context() as selected:
            original_perform = selected.perform

            def perform(operation, action):
                if target == "changed":
                    backend.pr["lastMergeTargetCommit"]["commitId"] = "e" * 40
                else:
                    backend.pr.pop("lastMergeTargetCommit")
                fresh = selected.read_state()
                selected.review_check(operation, action, fresh, context=selected)
                return original_perform(operation, action)

            selected.perform = perform
            yield selected

    backend.context = context
    result = run(value)
    assert result["status"] == "candidate_changed"
    assert result["actions"][0]["state"] == "unknown"
    assert result["actions"][0]["attempt_id"] and writes(backend) == []
    assert result["feedback"]["status"] != "handled"


def test_changed_interest_cannot_replace_original_capture(feedback_environment: tuple) -> None:
    value = feedback_environment
    assert run(value)["feedback"]["status"] == "handled"
    before = document(value)
    result = run(value, interests=value[7][1:])
    assert result["feedback"]["reason"] == "original_capture_binding_changed"
    assert document(value) == before and len(writes(value[4])) == 1


def test_new_external_input_remains_pending_with_its_actual_age(feedback_environment: tuple) -> None:
    value = feedback_environment
    backend = value[4]
    backend.after_write = lambda current: observe(value)
    result = run(value)
    assert result["status"] == "candidate_changed" and result["actions"][0]["state"] == "confirmed"
    assert result["feedback"]["status"] == "handled"
    data = document(value)
    original, other = data["attention"]["records"]
    assert original["handled"]["cursor"] == {"epoch": 1, "sequence": 1}
    assert original["facts"]["cursor"] == {"epoch": 1, "sequence": 2}
    assert original["facts"]["pending_since"] == data["conversations"]["records"][0]["events"][1]["observed_at"]
    assert other["handled"] is None and other["facts"]["pending_since"] < original["facts"]["pending_since"]
    assert data["feedback_captures"]["records"][0]["cursor"] == {"epoch": 1, "sequence": 1}


@pytest.mark.parametrize("failure", ["readback", "storage", "unavailable", "epoch", "gap"])
def test_confirmed_effect_survives_tracking_failure_and_later_input(feedback_environment: tuple, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    value = feedback_environment
    store, _, _, _, backend, _, expires, _ = value
    from agent_pull_requests.review_feedback import ReviewFeedback
    original = ReviewFeedback._record_handled

    def after_write(current: object) -> None:
        if failure == "readback":
            current.read_late = True
        elif failure == "unavailable":
            observe(value, change="unavailable")
        elif failure == "epoch":
            observe(value, change="completed", second=10)
            observe(value, change="active", second=12)
        elif failure == "gap":
            observe(value)
            observe(value, second=12)
            store._review_transaction(
                lambda data: data["conversations"]["records"][0].update(
                    events=data["conversations"]["records"][0]["events"][-1:],
                ), expires_at=expires,
            )
    backend.after_write = after_write
    if failure == "storage":
        def refuse(self: ReviewFeedback, operation: dict) -> dict:
            raise OSError("synthetic attention replacement failure")
        monkeypatch.setattr(ReviewFeedback, "_record_handled", refuse)
    result = run(value)
    assert result["actions"][0]["state"] == "confirmed" and len(writes(backend)) == 1
    row = document(value)["attention"]["records"][0]
    if failure in ("readback", "storage", "epoch"):
        assert row["handled"] is None and result["feedback"]["status"] == "unavailable"
    else:
        assert result["feedback"]["status"] == "handled"
        if failure == "unavailable":
            assert row["facts"]["complete"] is False
        else:
            assert row["facts"]["pending_since"] < document(value)["conversations"]["records"][0]["events"][0]["observed_at"]
    backend.read_late = False
    monkeypatch.setattr(ReviewFeedback, "_record_handled", original)
    retry = run(value)
    assert retry["entered_writes"] == 0 and len(writes(backend)) == 1


def test_current_vote_and_matching_vote_event_count_are_not_exact_readback(feedback_environment: tuple) -> None:
    value = feedback_environment
    value[1]["actions"].append({"id": "verdict", "kind": "verdict", "verdict": "APPROVED"})
    value[4].emit_vote_thread = True
    result = run(value)
    assert result["status"] == "complete" and all(row["state"] == "confirmed" for row in result["actions"])
    assert result["feedback"]["reason"] == "exact_vote_readback_unavailable"
    assert document(value)["attention"]["records"][0]["handled"] is None
    assert run(value)["entered_writes"] == 0 and len(writes(value[4])) == 2


def test_lost_reply_reconciles_original_capture_without_another_send(feedback_environment: tuple) -> None:
    value = feedback_environment
    value[4].lose_reply = True
    first = run(value)
    assert first["actions"][0]["state"] == "unknown" and first["feedback"]["status"] == "pending"
    captured = document(value)["feedback_captures"]
    value[4].lose_reply = False
    retry = run(value)
    assert retry["actions"][0]["state"] == "confirmed" and retry["feedback"]["status"] == "handled"
    assert retry["entered_writes"] == 0 and len(writes(value[4])) == 1
    assert document(value)["feedback_captures"] == captured


def test_older_retry_does_not_regress_newer_handled_proof_or_age(feedback_environment: tuple) -> None:
    value = feedback_environment
    assert run(value)["feedback"]["status"] == "handled"
    observe(value)
    store, _, _, _, _, _, expires, _ = value

    def newer_handled(data: dict) -> None:
        row = data["attention"]["records"][0]
        row["handled"]["cursor"] = copy.deepcopy(row["facts"]["cursor"])
        row["provenance"]["operation_id"] = "newer-confirmed-operation"
    store._review_transaction(newer_handled, expires_at=expires)
    before = document(value)
    assert run(value)["feedback"]["updated"] == 0
    assert document(value) == before


def test_feedback_inputs_are_bound_and_positive(feedback_environment: tuple) -> None:
    generation = feedback_environment[2]
    with pytest.raises(ValueError):
        FeedbackInputs(generation["id"], (Interest("maintainer", None, "consumer-a"),), 60)
    with pytest.raises(ValueError):
        replace(FeedbackInputs(generation["id"], feedback_environment[7], 60), max_age_seconds=True)


@pytest.mark.parametrize("replaced", [False, True])
def test_tracking_replacement_failure_reconciles_without_resending(
    feedback_environment: tuple, monkeypatch: pytest.MonkeyPatch, replaced: bool,
) -> None:
    import agent_pull_requests.watch_replay_store as module
    from agent_pull_requests.watch_storage import StateCommitUncertain

    value = feedback_environment
    write = module._atomic_write_json

    def fail(path: object, data: dict) -> None:
        if data["attention"]["records"][0]["handled"] is not None:
            if replaced:
                write(path, data)
                raise StateCommitUncertain("synthetic response lost after handled replacement")
            raise OSError("synthetic handled replacement unavailable")
        write(path, data)

    monkeypatch.setattr(module, "_atomic_write_json", fail)
    first = run(value)
    assert first["status"] == "complete" and first["feedback"]["status"] == "unavailable"
    assert first["actions"][0]["state"] == "confirmed"
    assert (document(value)["attention"]["records"][0]["handled"] is not None) is replaced
    before = document(value)["reviews"]
    monkeypatch.setattr(module, "_atomic_write_json", write)
    retry = run(value)
    assert retry["feedback"]["status"] == "handled" and retry["entered_writes"] == 0
    assert document(value)["reviews"] == before and len(writes(value[4])) == 1


@pytest.mark.parametrize("kind", ["reply", "edit_owned_comment"])
def test_original_reply_and_owned_edit_use_exact_native_object_readback(
    feedback_environment: tuple, kind: str,
) -> None:
    from agent_pull_requests.review_workspace import _digest

    value = feedback_environment
    store, selected, _, attachment, backend, now, expires, interests = value
    backend.threads = [{
        "id": 10, "status": 1, "comments": [{
            "id": 1, "content": "Original owned comment", "parentCommentId": 0, "commentType": 1,
            "author": {"id": selected["scope"]["principal_id"]},
        }],
    }]
    generation = observe(value, change="baseline")
    selected["generation_id"] = generation["id"]
    attachment["generation_id"] = generation["id"]
    selected["actions"] = [{
        "id": "finding", "kind": kind, "body": "Selected response", "thread_id": 10,
        **({"comment_id": 1, "expected_content_id": _digest("Original owned comment")}
           if kind == "edit_owned_comment" else {}),
    }]
    value = store, selected, generation, attachment, backend, now, expires, interests
    result = run(value)
    assert result["status"] == "complete" and result["feedback"]["status"] == "handled"
    assert result["feedback"]["cursor"] == {"epoch": 1, "sequence": 2}
    assert run(value)["entered_writes"] == 0 and len(writes(backend)) == 1
    later = observe(value, change="echo", second=12)
    retry = run(value, current=lambda: (later, backend.positions[later["id"]]))
    assert retry["feedback"]["echo"] == {
        "status": "unavailable", "reason": "action_response_version_unavailable",
    }


@pytest.mark.parametrize("mode", ["normal", "reopen", "unbound", "echo", "echo_timeout", "echo_setup_timeout"])
def test_workspace_selects_feedback_from_embedding_not_request_json(
    feedback_environment: tuple, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from agent_pull_requests.generation_cache import GenerationCache
    from agent_pull_requests.owner_read import OwnerReadBinding, OwnerReadHandler, PROFILE, PRINCIPAL_SEMANTICS
    from agent_pull_requests.workspace_operations import WorkspaceOperations

    value = feedback_environment
    store, selected, generation, attachment, backend, _, expires, interests = value
    cache = GenerationCache(
        store.root, store.namespace, max_bytes=4 * 1024 * 1024, max_generations=4,
        service_identity=store.service_identity,
    )
    cache.provision()
    scope = dict(generation["scope"])
    scope.pop("number")
    evidence = {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": generation["scope"], "sections": copy.deepcopy(generation["sections"]),
        "acquisition": {"source": PROFILE, "effects": "none", "principal_semantics": PRINCIPAL_SEMANTICS},
    }
    reader = OwnerReadHandler(
        lambda ref, remaining: OwnerReadBinding(
            scope, "existing-context", backend.checkpoint,
            lambda number, budget: copy.deepcopy(evidence),
        ), max_work_seconds=10, max_payload_bytes=2 * 1024 * 1024,
    )
    called = []

    def actual_inputs(current: dict) -> FeedbackInputs:
        called.append(current["id"])
        return FeedbackInputs(current["id"], interests[:1], 60)

    def make_operations() -> WorkspaceOperations:
        return WorkspaceOperations(
            store, cache, scope=scope, read_owner=reader, binding_ref="selected",
            write_context=lambda number: backend.context(), checkpoint=backend.checkpoint,
            max_work_seconds=10, conversations=True, feedback_owner=actual_inputs,
            attention_owner=lambda current: AttentionInputs(
                current["id"], interests, True, False, False, False, False,
            ),
        )

    operations = make_operations()
    if mode in ("echo", "echo_timeout", "echo_setup_timeout"):
        backend.versioned_reply = True
        backend.pr["reviewers"] = copy.deepcopy(generation["sections"]["reviewers"]["data"])
    if mode in ("echo_timeout", "echo_setup_timeout"):
        import agent_pull_requests.workspace_operations as workspace_module
        import agent_worktrees.providers.azure_owned_evidence as owned_module
        started = time.monotonic()
        elapsed = [0.0]
        clock = lambda: started + elapsed[0]
        executor = workspace_module.execute_review
        acquire = owned_module.acquire_owned_evidence
        monkeypatch.setattr(workspace_module.time, "monotonic", clock)
        monkeypatch.setattr(
            owned_module, "acquire_owned_evidence",
            lambda *args, **kwargs: acquire(*args, **kwargs, clock=clock),
        )
        def delayed_executor(*args, **kwargs):
            if mode == "echo_setup_timeout":
                elapsed[0] += 0.2
            return executor(*args, **kwargs, clock=clock)
        monkeypatch.setattr(workspace_module, "execute_review", delayed_executor)
    payload = {
        "number": selected["scope"]["number"], "intent": selected,
        "source_attachment": attachment, "budget_seconds": 10,
    }
    with pytest.raises(ValueError, match="workspace fields"):
        operations.execute(
            "submit", {**payload, "feedback": {"consumer": "foreign", "role": "maintainer"}},
            expires_at=expires, checkpoint=backend.checkpoint,
        )
    assert called == [] and writes(backend) == []
    with pytest.raises(ValueError, match="serving contract unavailable"):
        operations.validate_storage(expires_at=expires, checkpoint=backend.checkpoint)
    loaded = operations.execute(
        "load", {"number": 42, "expected_current": None, "budget_seconds": 10},
        expires_at=expires, checkpoint=backend.checkpoint,
    )
    assert loaded["result"]["descriptor"]["id"] == generation["id"]
    if mode == "reopen":
        for state, second in (("completed", 10), ("active", 12)):
            evidence["sections"]["pull_request"]["data"]["status"] = state
            for section in evidence["sections"].values():
                section.update(
                    observed_from=f"2026-10-30T00:00:{second:02d}Z",
                    observed_to=f"2026-10-30T00:00:{second + 1:02d}Z",
                )
            value[5][0] = observation_interval(evidence["sections"])[1] + 1
            loaded = operations.execute(
                "load", {
                    "number": 42, "expected_current": loaded["result"]["descriptor"]["id"],
                    "budget_seconds": 10,
                }, expires_at=expires, checkpoint=backend.checkpoint,
            )
        refused = operations.execute("submit", payload, expires_at=expires, checkpoint=backend.checkpoint)
        assert refused["result"]["feedback"]["reason"] == "generation_position_unavailable"
        assert writes(backend) == []
        generation = cache.lookup(selected["scope"])["generation"]
        selected.update(generation_id=generation["id"], operation_id="reopened-selected-operation")
        attachment["generation_id"] = generation["id"]
    elif mode == "unbound":
        operations = make_operations()
        refused = operations.execute("submit", payload, expires_at=expires, checkpoint=backend.checkpoint)
        assert refused["result"]["feedback"]["reason"] == "generation_position_unavailable"
        assert writes(backend) == []
        operations.execute(
            "load", {"number": 42, "expected_current": generation["id"], "budget_seconds": 10},
            expires_at=expires, checkpoint=backend.checkpoint,
        )
    if mode in ("echo_timeout", "echo_setup_timeout"):
        def acquire_echo(current: object) -> None:
            evidence["sections"]["discussions"]["data"] = copy.deepcopy(backend.threads)
            for section in evidence["sections"].values():
                section.update(observed_from="2026-10-30T00:00:10Z", observed_to="2026-10-30T00:00:11Z")
            value[5][0] = observation_interval(evidence["sections"])[1] + 1
            operations.execute(
                "load", {"number": 42, "expected_current": generation["id"], "budget_seconds": 10},
                expires_at=expires, checkpoint=backend.checkpoint,
            )
            if mode == "echo_setup_timeout":
                elapsed[0] += 9.7

        def exhaust_optional_budget(current: object, remaining: float) -> None:
            assert document(value)["attention"]["records"][0]["handled"]["cursor"]["sequence"] == 1
            assert 0 < remaining <= 5
            if mode == "echo_setup_timeout":
                assert remaining < 0.1
            elapsed[0] += remaining + 0.001
            backend.on_changes = None

        backend.after_write = acquire_echo
        backend.on_changes = exhaust_optional_budget
    result = operations.execute("submit", payload, expires_at=expires, checkpoint=backend.checkpoint)
    assert result["result"]["feedback"]["status"] == "handled", result
    assert called[-1] == generation["id"]
    assert result["result"]["feedback"]["cursor"]["epoch"] == (2 if mode == "reopen" else 1)
    if mode in ("echo_timeout", "echo_setup_timeout"):
        assert result["result"]["status"] == "complete"
        assert result["result"]["feedback"]["echo"] == {
            "status": "unavailable", "reason": "echo_readback_unavailable",
        }
        assert document(value)["attention"]["records"][0]["handled"]["cursor"]["sequence"] == 1
        recovered = operations.execute("submit", payload, expires_at=expires, checkpoint=backend.checkpoint)
        assert recovered["result"]["feedback"]["echo"]["status"] == "covered"
        assert recovered["result"]["entered_writes"] == 0
    if mode == "echo":
        evidence["sections"]["discussions"]["data"] = copy.deepcopy(backend.threads)
        for section in evidence["sections"].values():
            section.update(observed_from="2026-10-30T00:00:10Z", observed_to="2026-10-30T00:00:11Z")
        value[5][0] = observation_interval(evidence["sections"])[1] + 1
        operations.execute(
            "load", {"number": 42, "expected_current": generation["id"], "budget_seconds": 10},
            expires_at=expires, checkpoint=backend.checkpoint,
        )
        covered = operations.execute("submit", payload, expires_at=expires, checkpoint=backend.checkpoint)
        assert covered["result"]["feedback"]["echo"]["status"] == "covered", covered
        assert covered["result"]["entered_writes"] == 0
        assert document(value)["attention"]["records"][1]["handled"] is None
    operations = make_operations()
    retry = operations.execute("submit", payload, expires_at=expires, checkpoint=backend.checkpoint)
    assert retry["result"]["entered_writes"] == 0 and retry["result"]["feedback"]["status"] == "handled"
    operations.feedback_owner = lambda current: None
    with pytest.raises(ValueError, match="actual feedback owner"):
        operations.execute("submit", payload, expires_at=expires, checkpoint=backend.checkpoint)
    assert len(writes(backend)) == 1


@pytest.mark.parametrize("change", ["reopen", "roundtrip"])
def test_old_generation_cannot_capture_a_later_equal_meaning_position(
    feedback_environment: tuple, change: str,
) -> None:
    value = feedback_environment
    if change == "reopen":
        observe(value, change="completed", second=10)
        observe(value, change="active", second=12)
    else:
        observe(value, change="different_title", second=10)
        observe(value, change="baseline", second=12)
    result = run(value)
    assert result["status"] == "retained" and result["feedback"]["status"] == "unavailable"
    assert writes(value[4]) == []
    assert document(value)["feedback_captures"]["records"] == []
    assert document(value)["attention"]["records"][0]["handled"] is None


def test_target_motion_does_not_block_original_unknown_receipt_recovery(feedback_environment: tuple) -> None:
    value = feedback_environment
    backend = value[4]
    backend.lose_reply = True
    assert run(value)["actions"][0]["state"] == "unknown"
    backend.lose_reply = False
    backend.pr["lastMergeTargetCommit"]["commitId"] = "e" * 40
    result = run(value)
    assert result["actions"][0]["state"] == "confirmed"
    assert result["status"] == "candidate_changed" and result["feedback"]["status"] == "unavailable"
    assert result["entered_writes"] == 0 and len(writes(backend)) == 1


def test_missing_acquisition_binding_refuses_new_capture(feedback_environment: tuple) -> None:
    value = feedback_environment
    value[4].positions.clear()
    result = run(value)
    assert result["status"] == "retained" and result["feedback"]["reason"] == "generation_position_unavailable"
    assert writes(value[4]) == []


def test_missing_capture_allows_receipt_recovery_but_not_remaining_effects(feedback_environment: tuple) -> None:
    value = feedback_environment
    store, selected, generation, attachment, backend, _, expires, _ = value
    selected["actions"].append({"id": "verdict", "kind": "verdict", "verdict": "APPROVED"})
    backend.lose_reply = True
    execute_review(
        store, selected, generation, attachment, context_factory=backend.context,
        checkpoint=backend.checkpoint, expires_at=expires, budget_seconds=10,
    )
    backend.lose_reply = False
    result = run(value)
    assert result["actions"][0]["state"] == "confirmed"
    assert result["actions"][1]["state"] == "unattempted" and backend.vote is None
    assert result["feedback"]["reason"] == "capture_missing_after_attempt"
    assert result["entered_writes"] == 0 and len(writes(backend)) == 1


@pytest.mark.parametrize("older_first", [False, True])
def test_historical_readback_is_independent_of_newer_handled_frontier(
    feedback_environment: tuple, older_first: bool,
) -> None:
    value = feedback_environment
    store, selected, _, attachment, backend, _, expires, _ = value
    FeedbackCaptureStore(store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires)
    original_selection = copy.deepcopy(selected)
    if not older_first:
        backend.after_write = lambda current: setattr(current, "read_late", True)
    first = run(value)
    assert first["actions"][0]["state"] == "confirmed"
    assert (document(value)["feedback_captures"]["records"][0]["readback"] is not None) is older_first
    backend.read_late = False
    backend.after_write = None
    newer = observe(value, change="echo")
    selected.update(operation_id="newer-operation", generation_id=newer["id"])
    selected["actions"] = [{"id": "second", "kind": "comment", "body": "Second selected comment"}]
    attached = {**attachment, "generation_id": newer["id"]}
    second_value = store, selected, newer, attached, *value[4:]
    assert run(second_value)["feedback"]["status"] == "handled"
    before = document(value)
    assert before["feedback_captures"]["records"][1]["readback"] is not None
    assert before["attention"]["records"][0]["provenance"]["operation_id"] == "newer-operation"
    selected.clear()
    selected.update(original_selection)
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    retry = run((restarted, *value[1:]))
    assert retry["entered_writes"] == 0 and retry["feedback"]["updated"] == 0
    after = document(value)
    assert after["attention"] == before["attention"] and after["reviews"] == before["reviews"]
    first_capture = after["feedback_captures"]["records"][0]
    assert first_capture["readback"]["capture_id"]
    assert first_capture["cursor"] == {"epoch": 1, "sequence": 1}
    assert first_capture["readback"] != after["feedback_captures"]["records"][1]["readback"]
    assert len(writes(backend)) == 2
    stable = document(value)
    assert run((restarted, *value[1:]))["entered_writes"] == 0
    assert document(value) == stable


@pytest.mark.parametrize("failure", ["lost", "readback", "storage_before", "storage_after", "verdict"])
def test_historical_proof_needs_its_own_confirmed_readback_and_atomic_commit(
    feedback_environment: tuple, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    import agent_pull_requests.watch_replay_store as module
    from agent_pull_requests.watch_storage import StateCommitUncertain

    value = feedback_environment
    store, selected, _, _, backend, _, expires, _ = value
    FeedbackCaptureStore(store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires)
    write = module._atomic_write_json
    if failure == "lost":
        backend.lose_reply = True
    elif failure == "readback":
        backend.after_write = lambda current: setattr(current, "read_late", True)
    elif failure == "verdict":
        selected["actions"].append({"id": "verdict", "kind": "verdict", "verdict": "APPROVED"})
    else:
        def fail(path: object, data: dict) -> None:
            if any(row["readback"] is not None for row in data["feedback_captures"]["records"]):
                if failure == "storage_after":
                    write(path, data)
                    raise StateCommitUncertain("synthetic original-proof reply loss")
                raise OSError("synthetic original-proof commit unavailable")
            write(path, data)
        monkeypatch.setattr(module, "_atomic_write_json", fail)
    first = run(value)
    assert first["feedback"]["status"] in ("pending", "unavailable")
    assert (document(value)["feedback_captures"]["records"][0]["readback"] is not None) is (
        failure == "storage_after"
    )
    reviews = document(value)["reviews"]
    sent = len(writes(backend))
    backend.lose_reply = False
    backend.read_late = False
    backend.after_write = None
    monkeypatch.setattr(module, "_atomic_write_json", write)
    retry = run(value)
    assert retry["entered_writes"] == 0 and len(writes(backend)) == sent
    if failure == "verdict":
        assert retry["feedback"]["reason"] == "exact_vote_readback_unavailable"
        assert document(value)["feedback_captures"]["records"][0]["readback"] is None
    else:
        assert retry["feedback"]["status"] == "handled"
        assert document(value)["feedback_captures"]["records"][0]["readback"] is not None
    if failure != "lost":
        assert document(value)["reviews"] == reviews


@pytest.mark.parametrize("reopen", [False, True])
def test_adopted_original_readback_never_backfills_from_old_handled_state(
    feedback_environment: tuple, reopen: bool,
) -> None:
    value = feedback_environment
    store, _, _, _, backend, _, expires, _ = value
    assert run(value)["feedback"]["status"] == "handled"
    FeedbackCaptureStore(store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires)
    assert document(value)["feedback_captures"]["records"][0]["readback"] is None
    if reopen:
        observe(value, change="completed", second=10)
        observe(value, change="active", second=12)
    before = document(value)
    calls = len(backend.calls)
    retry = run(value)
    assert retry["feedback"]["status"] == "handled" and retry["feedback"]["updated"] == 0
    assert retry["entered_writes"] == 0 and len(writes(backend)) == 1 and len(backend.calls) > calls
    after = document(value)
    assert after["feedback_captures"]["records"][0]["readback"] is not None
    assert after["attention"] == before["attention"] and after["reviews"] == before["reviews"]
    if reopen:
        assert after["attention"]["records"][0]["facts"]["cursor"]["epoch"] == 2
        assert after["attention"]["records"][0]["handled"] is None


def test_historical_capture_capacity_refuses_before_native_attempt(feedback_environment: tuple) -> None:
    value = feedback_environment
    store, _, _, _, backend, _, expires, _ = value
    FeedbackCaptureStore(store).adopt_readback(
        lifecycle_checkpoint=lambda: None, expires_at=expires, readback_bytes=1024,
    )
    result = run(value)
    assert result["feedback"]["status"] == "unavailable" and not writes(backend)
    assert document(value)["feedback_captures"]["records"] == []
    assert all(row["state"] == "unattempted" for row in result["actions"])


def test_historical_proof_and_original_b3_echo_keep_distinct_positions(feedback_environment: tuple) -> None:
    value = feedback_environment
    store, _, _, _, backend, _, expires, _ = value
    FeedbackCaptureStore(store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires)
    backend.versioned_reply = True
    backend.pr["reviewers"] = copy.deepcopy(value[2]["sections"]["reviewers"]["data"])
    assert run(value)["feedback"]["status"] == "handled"
    original = document(value)["feedback_captures"]
    newer = observe(value, change="echo")
    result = run(value, current=lambda: (newer, backend.positions[newer["id"]]))
    assert result["feedback"]["echo"]["status"] == "covered"
    assert result["entered_writes"] == 0 and len(writes(backend)) == 1
    data = document(value)
    assert data["feedback_captures"] == original
    assert data["attention"]["records"][0]["handled"]["cursor"]["sequence"] == 2
    assert data["feedback_captures"]["records"][0]["cursor"]["sequence"] == 1
    assert data["attention"]["records"][1]["handled"] is None


def test_same_frontier_operation_still_records_its_own_original_readback(feedback_environment: tuple) -> None:
    value = feedback_environment
    store, selected, _, attachment, backend, _, expires, _ = value
    FeedbackCaptureStore(store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires)
    backend.versioned_reply = True
    backend.pr["reviewers"] = copy.deepcopy(value[2]["sections"]["reviewers"]["data"])
    assert run(value)["feedback"]["status"] == "handled"
    newer = observe(value, change="echo")
    assert run(value, current=lambda: (newer, backend.positions[newer["id"]]))[
        "feedback"
    ]["echo"]["status"] == "covered"
    before = document(value)
    selected.update(operation_id="same-frontier-operation", generation_id=newer["id"])
    selected["actions"] = [{"id": "second", "kind": "comment", "body": "Second selected comment"}]
    attached = {**attachment, "generation_id": newer["id"]}
    result = run((store, selected, newer, attached, *value[4:]))
    assert result["feedback"]["status"] == "handled" and result["feedback"]["updated"] == 0
    after = document(value)
    assert after["attention"] == before["attention"] and len(writes(backend)) == 2
    assert after["feedback_captures"]["records"][0] == before["feedback_captures"]["records"][0]
    assert after["feedback_captures"]["records"][1]["cursor"] == {"epoch": 1, "sequence": 2}
    assert after["feedback_captures"]["records"][1]["readback"] is not None
