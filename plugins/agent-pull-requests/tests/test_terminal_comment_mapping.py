"""Isolated original preparation/receipt/acquisition through compact retention."""

from __future__ import annotations

import copy
import json
import time

import pytest

from agent_pull_requests import watch_replay_store as storage
from agent_pull_requests.attention import Cursor
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.review_comment_mapping import CommentMappingStore
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.terminal_comment_mapping import TerminalCommentMappingStore
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.terminal_recovery_executor import reconcile_terminal
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain
from test_review_comment_mapping import generation, ingest
from test_review_edit_binding import edits as edits, allocate, refresh, submit
from test_review_owner_store import prepared_intent as prepared_intent
from test_terminal_recovery import prepare, compact
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_terminal_retention import (
    environment as environment, integration as integration, workspace as workspace, load,
)

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def expires() -> int:
    return int(time.time()) + 600


def enable(owner: ReplayStore) -> TerminalCommentMappingStore:
    owner.terminal_recovery = True
    TerminalRecoveryStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    CommentMappingStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    owner.terminal_comment_mapping = True
    selected = TerminalCommentMappingStore(owner)
    selected.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    return selected


def read(owner: TerminalCommentMappingStore, intent: dict) -> dict:
    return owner.read(
        intent["operation_id"], intent["scope"], intent["actions"][0]["id"], expires_at=expires(),
    )


def ready(workspace: tuple, edits: tuple) -> TerminalCommentMappingStore:
    operations, _, _, _, initial, _ = workspace
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    (_, selected, value, attachment, _, _), _, _, _ = edits
    value.clear()
    value.update(copy.deepcopy(current))
    selected["generation_id"] = attachment["generation_id"] = current["id"]
    return enable(operations.store)


def finish(workspace: tuple, current: dict) -> tuple[dict, dict]:
    operations, _, _, _, _, _ = workspace
    evidence = {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": copy.deepcopy(current["scope"]), "sections": copy.deepcopy(current["sections"]),
    }
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:03:00Z", observed_to="2026-10-30T00:03:01Z")
    decision = ConversationStore(operations.store).observe_workspace(
        build_generation(evidence)["generation"], expires_at=expires(),
        terminal_target=operations.retention_target(current["scope"]),
    )
    policies = {
        row["intent"]["operation_id"]: {
            "owner": "original-owner", "deadline_at": expires() - 10, "escalation_ref": "original-escalation",
        } for row in json.loads(operations.store.path.read_bytes())["reviews"]
    }
    return decision, policies


def retire(workspace: tuple, current: dict, decision: dict, policies: dict) -> dict:
    return workspace[0].retention.compact_recovery(
        current["scope"], Cursor(**decision["cursor"]), policies,
        expires_at=expires(), checkpoint=lambda: None, deadline=time.monotonic() + 2,
        lifecycle_checkpoint=lambda: None,
    )


@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
def test_actual_native_mapping_survives_compaction_restart_and_reopen(
    workspace: tuple, edits: tuple, kind: str,
) -> None:
    owner = ready(workspace, edits)
    (store, selected, _, _, backend, _), _, _, _ = edits
    first = allocate(edits)
    assert submit(edits, first)["status"] == "complete"
    chosen = first
    if kind != "comment":
        refresh(edits, "later-intentional-write")
        if kind == "reply":
            selected["actions"] = [{"id": "finding", "kind": "reply", "thread_id": 100, "body": "Reply."}]
        operations, evidence, _, _, initial, _ = workspace
        for section in evidence["sections"].values():
            section.update(observed_from="2026-10-30T00:01:00Z", observed_to="2026-10-30T00:01:01Z")
        load(operations, operations.cache.lookup(initial["scope"])["current_id"])
        current = operations.cache.lookup(initial["scope"])["generation"]
        (_, _, value, attachment, _, _), _, _, _ = edits
        value.clear()
        value.update(copy.deepcopy(current))
        selected["generation_id"] = attachment["generation_id"] = current["id"]
        chosen = allocate(edits)
        assert submit(edits, chosen)["status"] == "complete"
    current = generation(edits)
    ingest(store, current, terminal_target=workspace[0].retention_target(current["scope"]))
    original = read(owner, chosen)
    assert original["representation"] == "active" and original["mapping"]["status"] == "correlated"
    decision, policies = finish(workspace, current)
    calls = list(backend.calls)
    before = json.loads(store.path.read_bytes())
    retire(workspace, current, decision, policies)
    after = json.loads(store.path.read_bytes())
    assert after["replay"] == before["replay"] and after["conversations"] == before["conversations"]
    retained = read(owner, chosen)
    assert retained == {**original, "representation": "compact"}
    assert chosen["actions"][0]["body"] not in json.dumps(retained)
    assert retire(workspace, current, decision, policies)["status"] == "compacted"
    restarted = ReplayStore(
        store.root, store.namespace, service_identity=store.service_identity,
        terminal_retention=True, terminal_recovery=True, terminal_comment_mapping=True,
    )
    before = store.path.read_bytes()
    assert read(TerminalCommentMappingStore(restarted), chosen) == retained
    assert store.path.read_bytes() == before and backend.calls == calls
    with pytest.raises(ValueError, match="scope"):
        owner.read(chosen["operation_id"], {**chosen["scope"], "number": 43}, "finding", expires_at=expires())
    reopened = copy.deepcopy(current)
    reopened["sections"]["pull_request"]["data"]["status"] = "active"
    for section in reopened["sections"].values():
        section.update(observed_from="2026-10-30T00:04:00Z", observed_to="2026-10-30T00:04:01Z")
    evidence = {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": reopened["scope"], "sections": reopened["sections"],
    }
    ingest(store, build_generation(evidence)["generation"], terminal_target=workspace[0].retention_target(current["scope"]))
    assert read(owner, chosen) == retained and backend.calls == calls


def test_missing_adoption_refuses_lossy_compaction_and_old_reader_preserves_bytes(
    workspace: tuple, edits: tuple,
) -> None:
    owner = ready(workspace, edits)
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    current = generation(edits)
    ingest(store, current, terminal_target=workspace[0].retention_target(current["scope"]))
    decision, policies = finish(workspace, current)
    store._review_transaction(lambda document: document.pop("terminal_comment_mapping_version"), expires_at=expires())
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="lossless terminal mapping adoption"):
        retire(workspace, current, decision, policies)
    # Drain is a separate valid commit; the original operation and slot stay exact.
    drained = json.loads(store.path.read_bytes())
    assert drained["reviews"] == json.loads(before)["reviews"] and not drained["terminal_recovery"]["operations"]
    before = store.path.read_bytes()
    owner.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    adopted = store.path.read_bytes()
    prior = ReplayStore(
        store.root, store.namespace, service_identity=store.service_identity,
        terminal_retention=True, terminal_recovery=True,
    )
    with pytest.raises(ReplayStoreInvalid, match="mapping reader"):
        prior.inspect_review(chosen["operation_id"], chosen["scope"], expires_at=expires())
    assert store.path.read_bytes() == adopted and before != adopted
    retire(workspace, current, decision, policies)
    assert read(owner, chosen)["mapping"] is not None


@pytest.mark.parametrize("observed", [False, True])
def test_unknown_recovery_invalidates_nullable_or_unavailable_original_slot(
    workspace: tuple, edits: tuple, observed: bool,
) -> None:
    owner = ready(workspace, edits)
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    backend.lose_reply = True
    assert submit(edits, chosen)["status"] == "unknown"
    current = generation(edits)
    if observed:
        ingest(store, current, terminal_target=workspace[0].retention_target(current["scope"]))
    prior = read(owner, chosen)
    assert prior["mapping_supported"] is True
    assert (prior["mapping"] is not None) is observed
    decision, policies = finish(workspace, current)
    retire(workspace, current, decision, policies)
    assert read(owner, chosen) == {**prior, "representation": "compact"}
    calls = len(backend.calls)
    result = reconcile_terminal(
        TerminalRecoveryStore(store), chosen["scope"], chosen["operation_id"],
        context_factory=backend.context, checkpoint=backend.checkpoint, expires_at=expires(), budget_seconds=10,
    )
    assert result["status"] == "retained" and result["entered_writes"] == 0
    retained = read(owner, chosen)
    assert retained["action_state"] == "confirmed" and retained["mapping"] is None
    assert retained["prepared"] == prior["prepared"]
    assert all(call[0] == "GET" for call in backend.calls[calls:])


@pytest.mark.parametrize("uncertain", [False, True])
def test_interrupted_compact_replacement_reconciles_original_mapping(
    workspace: tuple, edits: tuple, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    owner = ready(workspace, edits)
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    current = generation(edits)
    ingest(store, current, terminal_target=workspace[0].retention_target(current["scope"]))
    original = read(owner, chosen)
    decision, policies = finish(workspace, current)
    workspace[0].retention.drain(
        current["scope"], Cursor(**decision["cursor"]), expires_at=expires(),
        checkpoint=lambda: None, lifecycle_checkpoint=lambda: None, deadline=time.monotonic() + 2,
    )
    before, calls = store.path.read_bytes(), list(backend.calls)
    atomic = storage._atomic_write_json

    def fail(path: object, document: dict) -> None:
        if not document["reviews"]:
            if uncertain:
                atomic(path, document)
                raise StateCommitUncertain("controlled compact mapping uncertainty")
            raise OSError("controlled before compact replacement")
        atomic(path, document)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_atomic_write_json", fail)
        with pytest.raises(OSError):
            retire(workspace, current, decision, policies)
    assert (store.path.read_bytes() == before) is not uncertain
    retire(workspace, current, decision, policies)
    assert read(owner, chosen) == {**original, "representation": "compact"} and backend.calls == calls


def test_pre_extension_compact_preparation_is_explicitly_unsupported_not_empty(
    workspace: tuple, prepared_intent: dict,
) -> None:
    prior = prepare(workspace, selected=prepared_intent, attempt=False)
    compact(prior)
    owner = enable(workspace[0].store)
    result = read(owner, prior[2])
    assert result["representation"] == "compact" and result["prepared"] is not None
    assert result["mapping_supported"] is False and result["mapping"] is None
    assert result["action_state"] == "retired"


@pytest.mark.parametrize("change", ["underfunded", "invalid_digest", "extra_field", "false_confirmation"])
def test_compact_mapping_validation_and_capacity_refuse_atomically(
    workspace: tuple, edits: tuple, change: str,
) -> None:
    owner = ready(workspace, edits)
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    current = generation(edits)
    ingest(store, current, terminal_target=workspace[0].retention_target(current["scope"]))
    decision, policies = finish(workspace, current)
    retire(workspace, current, decision, policies)
    original, before = read(owner, chosen), store.path.read_bytes()

    def invalid(document: dict) -> None:
        record = document["terminal_recovery"]["operations"][0]
        action = record["actions"][0]
        if change == "underfunded":
            record["reserved_bytes"] -= 2048
        elif change == "invalid_digest":
            action["comment_observation"]["current_object_id"] = "not-a-digest"
        elif change == "extra_field":
            action["comment_observation"]["body"] = "not admitted"
        else:
            action["state"] = "unknown"

    with pytest.raises(ValueError):
        store._review_transaction(invalid, expires_at=expires())
    assert store.path.read_bytes() == before and read(owner, chosen) == original


@pytest.mark.parametrize("boundary", [1, 2])
def test_adoption_withdrawal_after_validation_preserves_original_envelope(
    workspace: tuple, edits: tuple, monkeypatch: pytest.MonkeyPatch, boundary: int,
) -> None:
    owner = ready(workspace, edits)
    store = workspace[0].store
    store._review_transaction(lambda document: document.pop("terminal_comment_mapping_version"), expires_at=expires())
    validate, before = store._validate_owner, store.path.read_bytes()
    owned, validations = True, 0

    def lifecycle() -> None:
        if not owned:
            raise PermissionError("exclusive reader custody withdrawn")

    def interrupted(document: dict) -> None:
        nonlocal owned, validations
        validate(document)
        if "terminal_comment_mapping_version" in document:
            validations += 1
            if validations == boundary:
                owned = False

    monkeypatch.setattr(store, "_validate_owner", interrupted)
    with pytest.raises(PermissionError, match="custody withdrawn"):
        owner.adopt(lifecycle_checkpoint=lifecycle, expires_at=expires())
    assert validations == boundary and store.path.read_bytes() == before


@pytest.mark.parametrize("version", [3, 4, 5, 6])
def test_mapping_extension_remains_orthogonal_to_each_supported_owner_layout(
    workspace: tuple, edits: tuple, version: int,
) -> None:
    from agent_pull_requests.attention_store import AttentionStore
    from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore

    owner = ready(workspace, edits)
    store = workspace[0].store
    if version >= 4:
        AttentionStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    if version >= 5:
        FeedbackCaptureStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    if version >= 6:
        FeedbackCaptureStore(store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires())
    chosen = allocate(edits)
    original = read(owner, chosen)
    assert original["prepared"] is not None and original["mapping_supported"] is True
    assert original["mapping"] is None
    current = generation(edits)
    decision, policies = finish(workspace, current)
    retire(workspace, current, decision, policies)
    assert json.loads(store.path.read_bytes())["version"] == version
    assert read(owner, chosen) == {**original, "representation": "compact", "action_state": "retired"}
