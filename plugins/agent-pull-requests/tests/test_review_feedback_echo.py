"""Exact protected response versions, acquisition positions and per-interest coverage."""

import copy

import pytest

from test_review_feedback import feedback_environment as feedback_environment, document, observe, run
from test_review_executor import environment as environment, writes

from agent_pull_requests.attention import Cursor


def versioned(value):
    backend = value[4]
    backend.versioned_reply = True
    backend.pr["reviewers"] = copy.deepcopy(value[2]["sections"]["reviewers"]["data"])
    return backend


def current(value, generation):
    position = value[4].positions.get(generation["id"])
    return lambda: (generation, position) if position is not None else None


def test_original_response_version_covers_only_captured_interest_and_survives_restart(feedback_environment):
    value = feedback_environment
    backend = versioned(value)
    first = run(value)
    ids = first["actions"][0]["receipt"]["platform_ids"]
    assert len(ids["thread_state_id"]) == 64
    assert value[1]["actions"][0]["body"] not in str(first["actions"][0]["receipt"])
    original = copy.deepcopy(document(value)["feedback_captures"])
    later = observe(value, change="echo")
    result = run(value, current=current(value, later))
    assert result["entered_writes"] == 0 and result["feedback"]["cursor"] == {"epoch": 1, "sequence": 1}
    assert result["feedback"]["echo"] == {
        "status": "covered", "cursor": {"epoch": 1, "sequence": 2},
        "generation_id": later["id"], "updated": 1,
    }
    data = document(value)
    captured, other = data["attention"]["records"]
    assert captured["handled"]["cursor"] == {"epoch": 1, "sequence": 2}
    assert captured["provenance"]["generation_id"] == value[2]["id"]
    assert other["handled"] is None and other["facts"]["cursor"]["sequence"] == 2
    assert data["feedback_captures"] == original
    from agent_pull_requests.watch_replay_store import ReplayStore
    store = value[0]
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    value = (restarted, *value[1:])
    retry = run(value, current=current(value, later))
    assert retry["feedback"]["echo"]["updated"] == 0 and document(value) == data
    assert len(writes(backend)) == 1
    observe(value, second=12)
    newer = document(value)
    captured, other = newer["attention"]["records"]
    assert captured["facts"]["pending_since"] == newer["conversations"]["records"][0]["events"][-1]["observed_at"]
    assert other["facts"]["pending_since"] < captured["facts"]["pending_since"]


@pytest.mark.parametrize("change", ["reply", "different_title", "unavailable", "edit", "thread", "delete", "version"])
def test_mixed_or_changed_echo_does_not_consume_new_input(feedback_environment, change):
    value = feedback_environment
    backend = versioned(value)
    run(value)
    if change == "edit":
        backend.threads[0]["comments"][0]["content"] = "Independent edit"
    elif change == "thread":
        backend.threads[0]["status"] = 2
    elif change == "delete":
        backend.threads[0]["isDeleted"] = True
    elif change == "version":
        backend.threads[0]["lastUpdatedDate"] = "2026-10-30T00:00:08Z"
    later = observe(value, change=change)
    result = run(value, current=current(value, later))
    assert result["feedback"].get("echo", {"status": "unavailable"})["status"] == "unavailable"
    data = document(value)
    assert data["attention"]["records"][0]["handled"]["cursor"]["sequence"] == 1
    assert data["attention"]["records"][1]["handled"] is None
    assert len(writes(backend)) == 1


@pytest.mark.parametrize("failure", [
    "old_provider", "lost_response", "unbound", "gap", "roundtrip", "stale", "future",
    "fresh_policy", "fresh_title", "read_failure", "watermark", "reopen", "interest",
])
def test_missing_or_unproven_evidence_preserves_original_handled(feedback_environment, failure):
    value = feedback_environment
    backend = versioned(value)
    if failure == "old_provider":
        backend.versioned_reply = False
    if failure == "lost_response":
        backend.lose_reply = True
        assert run(value)["actions"][0]["state"] == "unknown"
        backend.lose_reply = False
    first = run(value)
    assert first["feedback"]["status"] == "handled"
    if failure == "roundtrip":
        observe(value, change="different_title", second=8)
    if failure == "reopen":
        observe(value, change="completed", second=8)
        later = observe(value, change="active", second=10)
    else:
        later = observe(value, change="echo")
    if failure == "gap":
        value[0]._review_transaction(
            lambda data: data["conversations"]["records"][0].update(
                events=[{**data["conversations"]["records"][0]["events"][-1], "kind": "opened"}],
            ), expires_at=value[6],
        )
    elif failure == "stale":
        value[5][0] += 60
    elif failure == "future":
        value[5][0] -= 2
    elif failure == "fresh_policy":
        backend.readback_policies = [{"evaluationId": "new-evaluation", "status": "failed"}]
    elif failure == "fresh_title":
        backend.pr["title"] = "Independent metadata after acquisition"
    elif failure == "read_failure":
        backend.singleton_continuation = "identity"
    elif failure in ("watermark", "interest"):
        def mutate(data):
            row = data["attention"]["records"][0]
            if failure == "watermark":
                row["facts"]["observed_at"] += 1
                row["facts"]["complete"] = False
            else:
                row["interest"]["matches"] = None
        value[0]._review_transaction(mutate, expires_at=value[6])
    acquired = (lambda: None) if failure == "unbound" else current(value, later)
    result = run(value, current=acquired)
    assert result["feedback"].get("echo", {"status": "unavailable"})["status"] == "unavailable", result
    data = document(value)
    handled = data["attention"]["records"][0]["handled"]
    assert handled is None if failure == "reopen" else handled["cursor"]["sequence"] == 1
    assert len(writes(backend)) == 1
    if failure in ("old_provider", "lost_response"):
        assert "thread_state_id" not in result["actions"][0]["receipt"]["platform_ids"]


def test_echo_transaction_rechecks_actual_position_without_consuming_newer_input(feedback_environment, monkeypatch):
    value = feedback_environment
    versioned(value)
    run(value)
    later = observe(value, change="echo")
    from agent_pull_requests.review_feedback import ReviewFeedback
    original = ReviewFeedback._record_echo

    def raced(self, operation, generation, position):
        observe(value, second=12)
        return original(self, operation, generation, position)

    monkeypatch.setattr(ReviewFeedback, "_record_echo", raced)
    result = run(value, current=current(value, later))
    assert result["feedback"]["echo"] == {"status": "unavailable", "reason": "echo_position_changed"}
    row = document(value)["attention"]["records"][0]
    assert row["facts"]["cursor"]["sequence"] == 3 and row["handled"]["cursor"]["sequence"] == 1
    assert len(writes(value[4])) == 1


@pytest.mark.parametrize("selected", [True, False])
def test_distinct_comments_and_captured_interests_commit_coverage_atomically(feedback_environment, selected):
    value = feedback_environment
    backend = versioned(value)
    value[1]["actions"].append({"id": "second", "kind": "comment", "body": "Second selected finding"})
    assert run(value, interests=value[7])["status"] == "complete"
    later = observe(value, change="echo")
    if not selected:
        value[0]._review_transaction(
            lambda data: data["attention"]["records"][1]["interest"].update(matches=None),
            expires_at=value[6],
        )
    result = run(value, interests=value[7], current=current(value, later))
    assert result["feedback"]["echo"]["status"] == ("covered" if selected else "unavailable")
    assert all(
        row["handled"]["cursor"]["sequence"] == (2 if selected else 1)
        for row in document(value)["attention"]["records"]
    )
    assert len(writes(backend)) == 2 and result["entered_writes"] == 0


@pytest.mark.parametrize("replaced", [True, False])
def test_echo_storage_uncertainty_reconciles_without_resending(feedback_environment, monkeypatch, replaced):
    value = feedback_environment
    backend = versioned(value)
    run(value)
    later = observe(value, change="echo")
    import agent_pull_requests.watch_replay_store as module
    from agent_pull_requests.watch_storage import StateCommitUncertain
    original = module._atomic_write_json

    def uncertain(path, data):
        if data["attention"]["records"][0]["handled"]["cursor"]["sequence"] == 2:
            if replaced:
                original(path, data)
                raise StateCommitUncertain("synthetic lost echo replacement reply")
            raise OSError("synthetic echo storage unavailable")
        original(path, data)

    monkeypatch.setattr(module, "_atomic_write_json", uncertain)
    result = run(value, current=current(value, later))
    assert result["feedback"]["status"] == "handled" and result["feedback"]["echo"]["status"] == "unavailable"
    assert document(value)["attention"]["records"][0]["handled"]["cursor"]["sequence"] == (2 if replaced else 1)
    monkeypatch.setattr(module, "_atomic_write_json", original)
    retry = run(value, current=current(value, later))
    assert retry["feedback"]["echo"]["status"] == "covered"
    assert retry["entered_writes"] == 0 and len(writes(backend)) == 1


def test_echo_requires_position_from_actual_acquisition_not_equal_meaning(feedback_environment):
    value = feedback_environment
    versioned(value)
    run(value)
    observe(value, change="different_title", second=8)
    later = observe(value, change="echo")
    result = run(value, current=lambda: (later, Cursor(1, 2)))
    assert result["feedback"]["echo"]["status"] == "unavailable"
    assert document(value)["attention"]["records"][0]["handled"]["cursor"]["sequence"] == 1


@pytest.mark.parametrize("old_peer", ["provider", "caller"])
def test_optional_response_evidence_preserves_mixed_peer_native_behavior(feedback_environment, monkeypatch, old_peer):
    value = feedback_environment
    backend = versioned(value)
    if old_peer == "provider":
        from agent_worktrees.providers.azure_owned_review import AdoReviewContext
        from types import SimpleNamespace
        original = AdoReviewContext.perform

        def legacy(self, operation, action):
            outcome = original(self, operation, action)
            return SimpleNamespace(receipt=outcome.receipt, confirmed=outcome.confirmed)
        monkeypatch.setattr(AdoReviewContext, "perform", legacy)
        result = run(value)
        assert result["feedback"]["status"] == "handled"
    else:
        from agent_pull_requests.review_executor import execute_review
        result = execute_review(
            value[0], value[1], value[2], value[3], context_factory=backend.context,
            checkpoint=backend.checkpoint, expires_at=value[6], budget_seconds=10,
        )
        assert "feedback" not in result
    assert result["status"] == "complete" and len(writes(backend)) == 1
    assert set(result["actions"][0]["receipt"]["platform_ids"]) == {"thread_id", "comment_id"}
