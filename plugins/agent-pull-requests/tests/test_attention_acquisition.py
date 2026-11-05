"""Actual workspace acquisition and one atomic attention/journal owner."""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from test_review_workspace import evidence as evidence
from test_workspace_conversation import generation

from agent_pull_requests.attention import Facts, Interest, project
from agent_pull_requests.attention_acquisition import AttentionInputs
from agent_pull_requests.attention_state import _scoped
from agent_pull_requests.attention_store import AttentionStore
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.owner_read import (
    OwnerReadBinding, OwnerReadHandler, PRINCIPAL_SEMANTICS, PROFILE,
)
from agent_pull_requests.review_workspace import _digest
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain
from agent_pull_requests.workspace_operations import WorkspaceOperations


def inputs(value: dict, **changes: object) -> AttentionInputs:
    return replace(AttentionInputs(
        value["id"], (Interest("maintainer", True, "consumer-a"),),
        True, False, False, False, False,
    ), **changes)


def rows(store: ReplayStore) -> list[dict]:
    return AttentionStore(store).snapshot(expires_at=100)["records"]


@pytest.fixture
def acquired(tmp_path: Path, evidence: dict) -> tuple:
    store = ReplayStore(tmp_path.resolve(), "owner", clock=lambda: 1)
    store.provision(conversations=True)
    AttentionStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    return store, evidence


def test_real_acquisition_preserves_meaningful_age_across_nonevents_and_restart(acquired: tuple) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    first = generation(evidence)
    result = journal.observe_workspace(first, attention=inputs(first), expires_at=100)
    assert result["attention"] == {"status": "observed", "interests": 1}
    original = rows(store)[0]["facts"]
    later = generation(evidence, 2)
    assert journal.observe_workspace(later, attention=inputs(later), expires_at=100)["status"] == "unchanged"
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    assert rows(restarted)[0]["facts"]["pending_since"] == original["pending_since"]
    assert rows(restarted)[0]["facts"]["observed_at"] == original["observed_at"] + 2
    evidence["sections"]["discussions"]["data"][0]["comments"].append({
        "id": 3, "content": "External same-head input", "author": {"id": "other"},
    })
    current = generation(evidence, 4)
    assert ConversationStore(restarted).observe_workspace(
        current, attention=inputs(current), expires_at=100,
    )["cursor"] == {"epoch": 1, "sequence": 2}
    row = rows(restarted)[0]
    facts = Facts(**_scoped(row["facts"]))
    assert facts.pending_since == original["pending_since"]
    assert project(
        facts, Interest(**row["interest"]), now=facts.observed_at, max_age_seconds=30,
    ).status == "actionable"
    assert "External same-head input" not in json.dumps(rows(restarted))


@pytest.mark.parametrize("cause", ["policy", "draft", "paths", "omitted"])
def test_missing_actual_policy_draft_or_selection_is_unavailable_not_empty_success(
    acquired: tuple, cause: str,
) -> None:
    store, evidence = acquired
    initial = generation(evidence)
    ConversationStore(store).observe_workspace(initial, attention=inputs(initial), expires_at=100)
    changes = {}
    if cause == "policy":
        changes["complete"] = False
    elif cause == "draft":
        evidence["sections"]["pull_request"]["data"].pop("isDraft")
    elif cause == "paths":
        changes["interests"] = (Interest("codeowner", None, "consumer-a"),)
    else:
        changes["interests"] = ()
    current = generation(evidence, 2)
    ConversationStore(store).observe_workspace(current, attention=inputs(current, **changes), expires_at=100)
    for row in rows(store):
        facts = Facts(**_scoped(row["facts"]))
        assert project(
            facts, Interest(**row["interest"]), now=facts.observed_at, max_age_seconds=30,
        ).status == "unavailable"


@pytest.mark.parametrize("cause", ["status", "head", "target", "terminal_partial"])
def test_missing_observation_invalidates_retained_work_without_inventing_progress(
    acquired: tuple, cause: str,
) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    if cause == "terminal_partial":
        evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    first = generation(evidence)
    journal.observe_workspace(first, attention=inputs(first), expires_at=100)
    original = copy.deepcopy(rows(store)[0])
    foreign = copy.deepcopy(evidence)
    foreign["scope"]["principal_id"] = "other-principal"
    other = generation(foreign, 2)
    journal.observe_workspace(other, attention=inputs(other), expires_at=100)
    unrelated = copy.deepcopy(rows(store)[1])
    before = json.loads(store.path.read_bytes())["conversations"]
    pull = evidence["sections"]["pull_request"]["data"]
    if cause == "status":
        pull.pop("status")
    elif cause == "head":
        pull.pop("lastMergeSourceCommit")
    elif cause == "target":
        pull.pop("lastMergeTargetCommit")
    else:
        evidence["sections"].pop("reviewers")
    current = generation(evidence, 4)
    omitted = cause in ("status", "head")
    result = journal.observe_workspace(
        current, attention=inputs(current, interests=() if omitted else inputs(current).interests, complete=False),
        expires_at=100,
    )
    assert result["status"] == "unavailable"
    assert result["attention"] == {"status": "unavailable", "interests": 1}
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    row = rows(restarted)[0]
    assert row["facts"] == {
        **original["facts"], "complete": False,
        "observed_at": original["facts"]["observed_at"] + 4,
    }
    assert row["interest"]["matches"] is (None if omitted else True)
    assert row["handled"] == original["handled"] and row["provenance"] == original["provenance"]
    assert rows(restarted)[1] == unrelated
    assert json.loads(store.path.read_bytes())["conversations"] == before
    facts = Facts(**_scoped(row["facts"]))
    assert project(
        facts, Interest(**row["interest"]), now=facts.observed_at, max_age_seconds=30,
    ).status == "unavailable"


@pytest.mark.parametrize("cause", ["status", "head", "terminal_partial"])
@pytest.mark.parametrize("second", [2, 4])
def test_stale_or_overlapping_incomplete_acquisition_cannot_invalidate_newer_work(
    acquired: tuple, cause: str, second: int,
) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    if cause == "terminal_partial":
        evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    first = generation(evidence, 4)
    journal.observe_workspace(first, attention=inputs(first), expires_at=100)
    if cause == "status":
        evidence["sections"]["pull_request"]["data"].pop("status")
    elif cause == "head":
        evidence["sections"]["pull_request"]["data"].pop("lastMergeSourceCommit")
    else:
        evidence["sections"].pop("reviewers")
    current = generation(evidence, second)
    before = store.path.read_bytes()
    result = journal.observe_workspace(
        current, attention=inputs(current, interests=(), complete=False), expires_at=100,
    )
    assert result["status"] == "stale_observation"
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("recovered", [False, True])
def test_incomplete_acquisition_watermark_fences_older_complete_and_incomplete_reads(
    acquired: tuple, recovered: bool,
) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    first = generation(evidence)
    journal.observe_workspace(first, attention=inputs(first), expires_at=100)
    evidence["sections"]["pull_request"]["data"].pop("status")
    incomplete = generation(evidence, 4)
    journal.observe_workspace(incomplete, attention=inputs(incomplete), expires_at=100)
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    if recovered:
        evidence["sections"]["pull_request"]["data"]["status"] = "active"
    older = generation(evidence, 2)
    before = store.path.read_bytes()
    result = ConversationStore(restarted).observe_workspace(
        older, attention=inputs(older, interests=()), expires_at=100,
    )
    assert result["status"] == "stale_observation"
    assert store.path.read_bytes() == before


def test_missing_intervals_cannot_prove_invalidation_order(acquired: tuple) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    first = generation(evidence, 4)
    journal.observe_workspace(first, attention=inputs(first), expires_at=100)
    current = generation(evidence, 6)
    current["sections"]["discussions"]["observed_from"] = None
    current["id"] = _digest({key: value for key, value in current.items() if key != "id"})
    before = store.path.read_bytes()
    result = journal.observe_workspace(
        current, attention=inputs(current, interests=(), complete=False), expires_at=100,
    )
    assert result == {"status": "unavailable", "reason": "observation_interval_missing"}
    assert store.path.read_bytes() == before


def test_handled_data_survives_poll_and_new_input_starts_its_own_age(acquired: tuple) -> None:
    store, evidence = acquired
    first = generation(evidence)
    ConversationStore(store).observe_workspace(first, attention=inputs(first), expires_at=100)

    def retained_baseline(document: dict) -> None:
        row = document["attention"]["records"][0]
        row["handled"] = {
            name: row["facts"][name] for name in ("key", "candidate", "cursor")
        } | {"consumer": "consumer-a", "role": "maintainer"}
        row["provenance"] = {
            "operation_id": "original", "generation_id": first["id"],
            "receipt_refs": [{"action_id": "finding", "attempt_id": "original-attempt"}],
        }

    # Isolated persisted data, not a confirmed-feedback API or proof of a send.
    store._review_transaction(retained_baseline, expires_at=100)
    later = generation(evidence, 2)
    ConversationStore(store).observe_workspace(later, attention=inputs(later), expires_at=100)
    before = rows(store)[0]
    assert before["handled"]["cursor"] == {"epoch": 1, "sequence": 1}
    evidence["sections"]["discussions"]["data"][0]["status"] = "active"
    current = generation(evidence, 4)
    ConversationStore(store).observe_workspace(current, attention=inputs(current), expires_at=100)
    row = rows(store)[0]
    assert row["facts"]["pending_since"] == before["facts"]["observed_at"] + 2
    assert row["handled"] == before["handled"] and row["provenance"] == before["provenance"]


def test_actual_terminal_and_reopen_keep_epoch_and_interests_distinct(acquired: tuple) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    first = generation(evidence)
    journal.observe_workspace(first, attention=inputs(first), expires_at=100)
    initial_age = rows(store)[0]["facts"]["pending_since"]
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    current = generation(evidence, 2)
    journal.observe_workspace(current, attention=inputs(current), expires_at=100)
    assert rows(store)[0]["facts"]["state"] == "merged"
    evidence["sections"]["pull_request"]["data"]["status"] = "active"
    reopened = generation(evidence, 4)
    result = journal.observe_workspace(reopened, attention=inputs(reopened), expires_at=100)
    assert result["cursor"] == {"epoch": 2, "sequence": 1}
    row = rows(store)[0]
    assert row["facts"]["pending_since"] == initial_age + 4
    assert row["handled"] is None and row["provenance"] is None


def test_interests_and_foreign_prs_remain_independent_with_no_feedback_inference(acquired: tuple) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    first = generation(evidence)
    admitted = (
        Interest("maintainer", True, "consumer-a"),
        Interest("codeowner", None, "consumer-a"),
        Interest("subject-matter-expert", False, "consumer-b"),
    )
    journal.observe_workspace(first, attention=inputs(first, interests=admitted), expires_at=100)
    original = copy.deepcopy(rows(store))
    foreign = copy.deepcopy(evidence)
    foreign["scope"]["principal_id"] = "other-principal"
    other = generation(foreign, 2)
    journal.observe_workspace(other, attention=inputs(other), expires_at=100)
    assert rows(store)[:3] == original
    assert all(row["handled"] is None and row["provenance"] is None for row in rows(store))
    current = generation(evidence, 4)
    journal.observe_workspace(current, attention=inputs(current, interests=(
        Interest("maintainer", True, "consumer-a"),
        Interest("subject-matter-expert", True, "consumer-b"),
    )), expires_at=100)
    refreshed = [
        row for row in rows(store) if row["facts"]["key"]["account"] == "reviewer-id"
    ]
    assert len(refreshed) == 3
    by_role = {row["interest"]["role"]: row for row in refreshed}
    assert by_role["maintainer"]["facts"]["pending_since"] == original[0]["facts"]["pending_since"]
    assert by_role["subject-matter-expert"]["facts"]["pending_since"] == original[0]["facts"]["observed_at"] + 4
    assert by_role["codeowner"]["interest"]["matches"] is None


@pytest.mark.parametrize("selection", ["unknown", "omitted", "excluded"])
def test_selection_recovery_preserves_age_unless_admission_was_confirmed_false(
    acquired: tuple, selection: str,
) -> None:
    store, evidence = acquired
    journal = ConversationStore(store)
    first = generation(evidence)
    journal.observe_workspace(first, attention=inputs(first), expires_at=100)
    initial = rows(store)[0]["facts"]
    later = generation(evidence, 2)
    interests = () if selection == "omitted" else (
        Interest("maintainer", False if selection == "excluded" else None, "consumer-a"),
    )
    journal.observe_workspace(later, attention=inputs(later, interests=interests), expires_at=100)
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    current = generation(evidence, 4)
    result = ConversationStore(restarted).observe_workspace(
        current, attention=inputs(current), expires_at=100,
    )
    assert result["status"] == "unchanged"
    facts = rows(restarted)[0]["facts"]
    assert facts["cursor"] == initial["cursor"]
    assert facts["pending_since"] == initial["pending_since"] + (4 if selection == "excluded" else 0)


def test_terminal_hint_is_unavailable_and_cannot_clear_a_live_interest(acquired: tuple) -> None:
    store, evidence = acquired
    initial = generation(evidence)
    journal = ConversationStore(store)
    journal.observe_workspace(initial, attention=inputs(initial), expires_at=100)
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    evidence["sections"].pop("reviewers")
    partial = generation(evidence, 2)
    journal.observe_workspace(partial, attention=inputs(partial), expires_at=100)
    row = rows(store)[0]
    facts = Facts(**_scoped(row["facts"]))
    assert not facts.complete and facts.cursor.epoch == 1
    assert project(
        facts, Interest(**row["interest"]), now=facts.observed_at, max_age_seconds=30,
    ).status == "unavailable"
    assert json.loads(store.path.read_bytes())["conversations"]["records"][0]["current"]["sections"]


def test_inconsistent_ahead_epoch_cannot_be_silently_reset(acquired: tuple) -> None:
    store, evidence = acquired
    first = generation(evidence)
    journal = ConversationStore(store)
    journal.observe_workspace(first, attention=inputs(first), expires_at=100)

    def ahead(document: dict) -> None:
        document["attention"]["records"][0]["facts"]["cursor"]["epoch"] = 2

    store._review_transaction(ahead, expires_at=100)
    before = store.path.read_bytes()
    current = generation(evidence, 2)
    with pytest.raises(ValueError, match="regress.*epoch"):
        journal.observe_workspace(current, attention=inputs(current), expires_at=100)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("failure", ["wrong_generation", "capacity", "withdrawal", "old_layout"])
def test_rejected_attention_update_cannot_commit_its_journal(
    acquired: tuple, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    store, evidence = acquired
    value = generation(evidence)
    supplied = inputs(value)
    checkpoint = None
    if failure == "wrong_generation":
        supplied = replace(supplied, generation_id="f" * 64)
    elif failure == "capacity":
        import agent_pull_requests.attention_acquisition as module
        monkeypatch.setattr(module, "MAX_INTERESTS", 0)
    elif failure == "withdrawal":
        def checkpoint() -> None:
            raise PermissionError("synthetic custody withdrawal")
    else:
        document = json.loads(store.path.read_bytes())
        document["version"] = 3
        document.pop("attention")
        document["capacities"].pop("attention_bytes")
        store.path.write_text(json.dumps(document))
    before = store.path.read_bytes()
    with pytest.raises((ValueError, PermissionError, ReplayStoreInvalid)):
        ConversationStore(store).observe_workspace(
            value, attention=supplied, expires_at=100, checkpoint=checkpoint,
        )
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("status", ["active", "completed", "abandoned"])
def test_lost_commit_response_reconciles_both_sections_once(
    acquired: tuple, monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    import agent_pull_requests.watch_replay_store as module

    store, evidence = acquired
    evidence["sections"]["pull_request"]["data"]["status"] = status
    value = generation(evidence)
    write = module._atomic_write_json

    def uncertain(path: Path, document: dict) -> None:
        write(path, document)
        raise StateCommitUncertain("synthetic response lost after replacement")

    monkeypatch.setattr(module, "_atomic_write_json", uncertain)
    with pytest.raises(StateCommitUncertain):
        ConversationStore(store).observe_workspace(value, attention=inputs(value), expires_at=100)
    monkeypatch.setattr(module, "_atomic_write_json", write)
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    result = ConversationStore(restarted).observe_workspace(value, attention=inputs(value), expires_at=100)
    assert result["status"] == "unchanged" and result["cursor"] == {"epoch": 1, "sequence": 1}
    assert result["attention"] == {"status": "observed", "interests": 1}
    assert len(rows(restarted)) == 1


def test_stale_observation_does_not_refresh_attention_or_adopt_storage(acquired: tuple) -> None:
    store, evidence = acquired
    value = generation(evidence, 4)
    ConversationStore(store).observe_workspace(value, attention=inputs(value), expires_at=100)
    before = store.path.read_bytes()
    stale = generation(evidence, 2)
    assert ConversationStore(store).observe_workspace(
        stale, attention=inputs(stale), expires_at=100,
    )["status"] == "stale_observation"
    assert store.path.read_bytes() == before


def test_workspace_load_uses_real_owner_and_cas_failure_never_updates_attention(acquired: tuple) -> None:
    store, evidence = acquired
    evidence["acquisition"] = {
        "source": PROFILE, "effects": "none", "principal_semantics": PRINCIPAL_SEMANTICS,
    }
    scope = dict(evidence["scope"])
    scope.pop("number")
    reader = OwnerReadHandler(
        lambda ref, remaining: OwnerReadBinding(
            scope, "existing-context", lambda: None,
            lambda number, budget: copy.deepcopy(evidence),
        ), max_work_seconds=10, max_payload_bytes=2 * 1024 * 1024,
    )
    cache = GenerationCache(store.root, store.namespace, max_bytes=4 * 1024 * 1024, max_generations=4)
    cache.provision()
    selected = []

    def actual_inputs(value: dict) -> AttentionInputs:
        selected.append(value["id"])
        return inputs(value)

    operations = WorkspaceOperations(
        store, cache, scope=scope, read_owner=reader, binding_ref="selected",
        checkpoint=lambda: None, max_work_seconds=10, conversations=True,
        attention_owner=actual_inputs,
    )
    payload = {"number": 42, "expected_current": None, "budget_seconds": 10}
    result = operations.execute("load", payload, expires_at=100, checkpoint=lambda: None)["result"]
    assert result["conversation"]["attention"]["interests"] == 1
    assert selected == [result["descriptor"]["id"]]
    before = store.path.read_bytes()
    evidence["sections"]["pull_request"]["data"]["title"] = "New current title"
    result = operations.execute("load", payload, expires_at=100, checkpoint=lambda: None)["result"]
    assert result["publication"]["status"] != "published"
    assert result["conversation"]["status"] == "not_observed"
    assert len(selected) == 1 and store.path.read_bytes() == before
    with pytest.raises(ReplayStoreInvalid, match="serving contract unavailable"):
        operations.validate_storage(expires_at=100, checkpoint=lambda: None)

    # A missing callback result is not permission to silently turn integration off.
    operations.attention_owner = lambda value: None
    payload["expected_current"] = cache.lookup(evidence["scope"])["current_id"]
    with pytest.raises(ValueError, match="actual attention owner"):
        operations.execute("load", payload, expires_at=100, checkpoint=lambda: None)
    assert store.path.read_bytes() == before
