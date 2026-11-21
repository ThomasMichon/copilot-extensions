"""Original reservations and receipt custody survive selected active adoption."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests.review_workspace import _digest
from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.terminal_retention import TerminalAdmissionClosed, TerminalRetentionStore
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import _atomic_write_json
from test_generation_associations import generation, state
from test_generation_native_targets import capture, fresh, publish
from test_recorded_native_cache import targets
from test_review_workspace import evidence as evidence

Document = dict[str, Any]


def adopted(tmp_path: Path, evidence: Document) -> tuple[ReplayStore, ReplayStore, Document]:
    original, cache = fresh(tmp_path)
    value = generation(evidence)
    publish(original, cache, value, capture(original, value))
    selected = {
        "schema": "agent-pull-requests.review-intent", "version": 1,
        "operation_id": "original", "scope": value["scope"], "generation_id": value["id"],
        "source_id": "a" * 64,
        "revision": {name: value["revision"][name] for name in ("head_sha", "common_sha", "iteration")},
        "actions": [{"id": "finding", "kind": "comment", "body": "Original content."}],
    }
    original.admit_review(selected, expires_at=100)
    original.begin_review_action("original", selected["scope"], "finding", expires_at=100)
    operation = original.inspect_review("original", selected["scope"], expires_at=100)
    owner = ReplayStore(
        tmp_path.resolve(), "owner", clock=lambda: 1, workspace_generation_associations=True,
        native_generation_associations=True, recorded_terminal_cache=True,
        terminal_retention=True, terminal_recovery=True, terminal_cache_retirement=True,
        retained_active_reviews=True,
    )
    TerminalRetentionStore(owner).adopt(
        retained_cache=cache, retained_targets=targets(value),
        lifecycle_checkpoint=lambda: None, expires_at=100,
    )
    TerminalRecoveryStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    TerminalCacheRetirementStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    return original, owner, operation


def test_original_attributable_receipt_uses_unchanged_attempt_and_reserved_capacity(
    tmp_path: Path, evidence: Document,
) -> None:
    original, owner, operation = adopted(tmp_path, evidence)
    scope = operation["intent"]["scope"]
    action = operation["actions"][0]
    with pytest.raises(ReplayStoreInvalid):
        original.inspect_review("original", scope, expires_at=100)
    with pytest.raises(TerminalAdmissionClosed):
        owner.begin_review_action("original", scope, "finding", expires_at=100)
    owner.record_review_receipt(
        "original", scope, "finding", action["attempt_id"], {
            "origin": "attributable_readback",
            "platform_ids": {"thread_id": 100, "comment_id": 200, "event_id": "original-event"},
            "proof": {
                "operation_id": "original", "action_id": "finding",
                "scope_id": _digest(scope),
                "intent_id": operation["intent_id"],
            },
        }, expires_at=100,
    )
    result = owner.inspect_retained_review("original", scope, expires_at=100)
    assert result["recovery_only"] is True
    assert result["actions"][0]["state"] == "confirmed"
    assert result["actions"][0]["attempt_id"] == action["attempt_id"]
    for field in ("intent", "intent_id", "reserved_bytes", "created_at"):
        assert result[field] == operation[field]


@pytest.mark.parametrize("change", ["marker-bool", "flag-false", "flag-int", "no-selection"])
def test_malformed_or_unselected_recovery_metadata_refuses_without_rewriting(
    tmp_path: Path, evidence: Document, change: str,
) -> None:
    _, owner, operation = adopted(tmp_path, evidence)
    document = state(owner)
    if change == "marker-bool":
        document["retained_active_review_version"] = True
    elif change == "no-selection":
        owner.retained_active_reviews = False
    else:
        document["reviews"][0]["recovery_only"] = False if change == "flag-false" else 1
    _atomic_write_json(owner.path, document)
    before = owner.path.read_bytes()
    with pytest.raises(ValueError):
        owner.inspect_retained_review("original", operation["intent"]["scope"], expires_at=100)
    assert owner.path.read_bytes() == before
