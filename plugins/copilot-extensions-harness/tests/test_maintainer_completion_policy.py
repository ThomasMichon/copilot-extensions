"""The repository's completion policy is stricter than its direct-submit flow."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "maintainer_pr_completion_policy", ROOT / "tools" / "maintainer_pr_completion_policy.py",
)
assert SPEC is not None and SPEC.loader is not None
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEAD = "a" * 40
CONFIG = policy.CompletionPolicy(repository="example/project")


def ready() -> policy.CompletionSnapshot:
    return policy.CompletionSnapshot(
        repository="example/project", number=7, node_id="PR_example",
        state="open", head_sha=HEAD, head_ref="pr/example", base_ref="dev",
        base_sha="b" * 40, same_repository=True, draft=False, title="Ready change",
        labels=("auto-merge",), invited_collaborator=True, root_codeowner=True,
        reviews=policy.ReviewSummary(
            complete=True, change_requests=(), copilot_state="APPROVED",
            copilot_head=HEAD, copilot_submitted_at=EPOCH + timedelta(seconds=1),
            epoch=EPOCH,
        ),
        required_checks_verified=True, mergeable=True, update_required=False,
        rebase_supported=True,
    )


def test_ready_candidate_does_not_update_merely_because_base_advanced() -> None:
    snapshot = ready()
    assert policy.decide(snapshot, CONFIG).action == "merge"
    assert policy.decide(replace(snapshot, base_sha="c" * 40), CONFIG).action == "merge"


def test_required_update_rebases_instead_of_merging_with_stale_evidence() -> None:
    assert policy.decide(replace(ready(), update_required=True), CONFIG).action == "rebase"
    snapshot = replace(ready(), update_required=True, head_sha="c" * 40)
    assert policy.decide(snapshot, CONFIG).action == "wait"


@pytest.mark.parametrize("field,value", [
    ("repository", "example/other"), ("base_ref", "main"), ("state", "closed"),
    ("same_repository", False), ("head_ref", "release/promote-example"),
    ("labels", ()), ("labels", ("auto-merge", "HOLD")), ("draft", True),
    ("title", "WIP: unfinished"), ("title", "Draft: unfinished"),
    ("invited_collaborator", False), ("root_codeowner", False),
])
def test_consent_never_expands_scope_or_overrides_holds(field: str, value: object) -> None:
    assert policy.decide(replace(ready(), **{field: value}), CONFIG).action == "skip"


@pytest.mark.parametrize("field,value", [
    ("invited_collaborator", None), ("root_codeowner", None),
    ("required_checks_verified", None), ("required_checks_verified", False),
    ("mergeable", None), ("update_required", None),
])
def test_unknown_or_unsuccessful_evidence_does_not_authorize_action(
    field: str, value: object,
) -> None:
    assert policy.decide(replace(ready(), **{field: value}), CONFIG).action == "wait"


@pytest.mark.parametrize("changes", [
    {"complete": False},
    {"change_requests": ("another-reviewer",)},
    {"copilot_state": "COMMENTED"},
    {"copilot_state": "DISMISSED"},
    {"copilot_state": ""},
    {"copilot_head": "c" * 40},
    {"copilot_submitted_at": None},
    {"copilot_submitted_at": EPOCH},
    {"copilot_submitted_at": EPOCH - timedelta(seconds=1)},
    {"copilot_submitted_at": EPOCH.replace(tzinfo=None)},
    {"epoch": EPOCH + timedelta(seconds=2)},
])
def test_current_approval_cannot_hide_negative_incomplete_or_wrong_epoch_review(
    changes: dict[str, object],
) -> None:
    snapshot = ready()
    snapshot = replace(snapshot, reviews=replace(snapshot.reviews, **changes))
    assert policy.decide(snapshot, CONFIG).action == "wait"


@pytest.mark.parametrize("supported", [False, None])
def test_required_rebase_never_falls_back_to_a_merge_update(supported: bool | None) -> None:
    snapshot = replace(ready(), update_required=True, rebase_supported=supported)
    assert policy.decide(snapshot, CONFIG).action == "wait"


def test_only_positive_conflict_evidence_returns_resolution_to_submitter() -> None:
    assert policy.decide(replace(ready(), mergeable=False), CONFIG).action == "conflict"
    assert policy.decide(replace(ready(), mergeable=None), CONFIG).action == "wait"
    held = replace(ready(), mergeable=False, labels=("auto-merge", "hold"))
    assert policy.decide(held, CONFIG).action == "skip"
