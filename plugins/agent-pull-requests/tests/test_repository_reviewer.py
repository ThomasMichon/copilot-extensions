"""Versioned activity adoption, stable causes and actual cache-only reviewer reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.repository_projection import RepositoryProjection
from agent_pull_requests.repository_subscriptions import RepositorySubscriptions
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_replay_store import ReplayCallerExpired, ReplayStore, ReplayStoreInvalid

HEAD, BASE, OLD = "a" * 40, "b" * 40, "c" * 40
Selection = tuple[
    list[float], ReplayStore, RepositoryIdentity, RepositoryProjection, RepositorySubscriptions,
]


def setup(tmp_path: Path, *, activity: bool = True, provider: str = "gitea") -> Selection:
    now = [1000.0]
    store = ReplayStore(
        tmp_path, "test", clock=lambda: now[0], repository_cache=True,
        repository_continuing=True, repository_activity=activity,
    )
    store.provision()
    identity = RepositoryIdentity("test", provider, "https://forge.example", "reader", "owner/repo")
    projection = RepositoryProjection(store, (identity,))
    return now, store, identity, projection, RepositorySubscriptions(projection)


def observe(
    selected: Selection, *, number: int = 7, members: tuple[int, ...] | None = None,
    **changes: Any,
) -> None:
    _now, _store, identity, projection, _registry = selected
    snapshot = decode_snapshot({
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "provider": identity.provider, "api_base": identity.endpoint, "repo": identity.repo,
        "pr": number, "pr_state": "open", "merged": False,
        "head_sha": HEAD, "base_sha": BASE, "base_ref": "main",
        "mergeable": True, "checks_state": "", "title": "Synthetic", "draft": False,
        "author": "author", "reviews": [], "reviews_complete": True,
        "updated_at": "1970-01-01T00:01:00Z", **changes,
    }, identity.repo, number)
    projection.apply(
        {identity.key: {
            "outcome": "complete", "numbers": list(members or (number,)), "pages": 1, "error": "",
        }},
        {(identity.key, identity.repo, number): snapshot}, expires_at=10000,
    )


def request(
    selected: Selection, operation: str = "next-actions", **payload: Any,
) -> dict[str, Any]:
    return selected[-1].execute(
        selected[2].key, "consumer", operation,
        ({"reviewer": "reviewer"} if operation == "next-actions" else {}) | payload,
        expires_at=10000,
    )


def verdict(**changes: Any) -> dict[str, Any]:
    return {
        "id": 9, "state": "APPROVED", "user": "reviewer", "commit_id": OLD,
        "dismissed": False, "submitted_at": "1970-01-01T00:01:00Z", **changes,
    }


def test_activity_selection_cannot_reinterpret_filtered_storage(tmp_path: Path) -> None:
    filtered = ReplayStore(
        tmp_path, "test", repository_cache=True,
        repository_continuing=True, repository_filtered=True,
    )
    filtered.provision()
    before = filtered.path.read_bytes()
    with pytest.raises(ValueError, match="unfiltered continuing cache"):
        ReplayStore(
            tmp_path, "test", repository_cache=True, repository_continuing=True,
            repository_filtered=True, repository_activity=True,
        )
    unfiltered = ReplayStore(
        tmp_path, "test", repository_cache=True, repository_continuing=True,
        repository_activity=True,
    )
    with pytest.raises(ReplayStoreInvalid):
        unfiltered.adopt_repository_activity(lifecycle_checkpoint=lambda: None, expires_at=2**52)
    assert filtered.path.read_bytes() == before


def test_github_app_reviewer_identity_matches_the_actual_cached_actor(tmp_path: Path) -> None:
    selected = setup(tmp_path, provider="github")
    reviewer = "review-app[bot]"
    observe(selected, reviews=[verdict(user=reviewer, commit_id=HEAD)])
    result = request(selected, reviewer=reviewer)
    assert not result["next_actions"]
    assert result["unavailable"] == [{"pr": 7, "reason": "reviewed-base-unknown"}]
    assert request(selected, reviewer="review-app")["next_actions"][0]["action"] == "initial_verdict"
    observe(selected, reviews=[])
    assert request(selected, reviewer=reviewer)["next_actions"][0]["action"] == "initial_verdict"


@pytest.mark.parametrize("reviewer", ["[bot]", "review-app[other]", "a" * 124 + "[bot]"])
def test_github_app_reviewer_still_requires_a_bounded_identity(tmp_path: Path, reviewer: str) -> None:
    selected = setup(tmp_path, provider="github")
    with pytest.raises(ValueError, match="reviewer identity"):
        request(selected, reviewer=reviewer)


def test_adoption_retains_complete_envelope_and_original_unacknowledged_delivery(tmp_path: Path) -> None:
    selected = setup(tmp_path, activity=False)
    observe(selected, reviews=[verdict()])
    subscription = request(selected, "subscribe", request_id="original", ttl=3600, after=0)["subscription"]
    batch = request(selected, "events", registration_id=subscription["registration_id"])
    old = selected[1]
    before = json.loads(old.path.read_bytes())
    store = ReplayStore(
        old.root, "test", clock=old.clock, repository_cache=True,
        repository_continuing=True, repository_activity=True,
    )
    with pytest.raises(ReplayStoreInvalid):
        store.observation_targets(expires_at=10000)
    checkpoints = []
    assert store.adopt_repository_activity(
        lifecycle_checkpoint=lambda: checkpoints.append(True), expires_at=10000,
    ) == {"status": "adopted"}
    after = json.loads(store.path.read_bytes())
    assert checkpoints == [True, True] and after["repository_activity_version"] == 1
    for key in before.keys() - {"repository_cache"}:
        assert after[key] == before[key]
    assert after["repository_cache"]["registrations"] == before["repository_cache"]["registrations"]
    assert after["repository_cache"]["next_sequence"] == before["repository_cache"]["next_sequence"]
    for previous, adopted in zip(before["repository_cache"]["events"], after["repository_cache"]["events"]):
        assert {k: v for k, v in previous.items() if k != "facts"} == {
            k: v for k, v in adopted.items() if k != "facts"
        }
    projection = RepositoryProjection(store, (selected[2],))
    restored: Selection = (selected[0], store, selected[2], projection, RepositorySubscriptions(projection))
    assert request(restored)["unavailable"][0]["reason"] == "activity-unavailable"
    pending = request(restored, "events", registration_id=subscription["registration_id"])
    assert [row["event_id"] for row in pending["events"]] == [row["event_id"] for row in batch["events"]]
    request(
        restored, "acknowledge", registration_id=subscription["registration_id"],
        event_id=pending["events"][-1]["event_id"],
    )
    original = store.path.read_bytes()
    assert store.adopt_repository_activity(lifecycle_checkpoint=lambda: None, expires_at=10000) == {
        "status": "already_adopted",
    }
    with pytest.raises(ReplayStoreInvalid):
        old.observation_targets(expires_at=10000)
    assert store.path.read_bytes() == original
    observe(restored, reviews=[verdict()])
    assert request(restored)["unavailable"][0]["reason"] == "review-order-unavailable"
    observe(restored, head_sha=OLD, reviews=[verdict()])
    observe(restored, reviews=[verdict()])
    assert request(restored)["next_actions"][0]["action"] == "new_verdict"


def test_expired_adoption_does_not_replace_original_bytes(tmp_path: Path) -> None:
    now, old, *_rest = setup(tmp_path, activity=False)
    store = ReplayStore(
        old.root, "test", clock=old.clock, repository_cache=True,
        repository_continuing=True, repository_activity=True,
    )
    original = old.path.read_bytes()
    checks = []

    def lifecycle() -> None:
        checks.append(True)
        if len(checks) == 2:
            now[0] = 10001

    with pytest.raises(ReplayCallerExpired):
        store.adopt_repository_activity(lifecycle_checkpoint=lifecycle, expires_at=10000)
    assert store.path.read_bytes() == original


def test_refresh_restart_candidate_and_conflict_keep_their_actual_cause_ages(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected)
    first = request(selected)["next_actions"][0]
    selected[0][0] = 1100
    observe(selected, updated_at="1970-01-01T00:02:00Z")
    refreshed = request(selected)["next_actions"][0]
    assert refreshed["cause"] == first["cause"] and refreshed["pending_since"] == 1000
    store = ReplayStore(
        selected[1].root, "test", clock=selected[1].clock, repository_cache=True,
        repository_continuing=True, repository_activity=True,
    )
    projection = RepositoryProjection(store, (selected[2],))
    selected = (selected[0], store, selected[2], projection, RepositorySubscriptions(projection))
    assert request(selected)["next_actions"][0]["cause"] == first["cause"]
    selected[0][0] = 1200
    observe(selected, head_sha=OLD)
    moved = request(selected)["next_actions"][0]
    assert moved["pending_since"] == 1200 and moved["cause"] != first["cause"]
    selected[0][0] = 1300
    observe(selected, head_sha=OLD, mergeable=False)
    conflict = request(selected)["next_actions"][0]
    assert conflict["pending_since"] == 1300 and conflict["action"] == "rebase"
    assert conflict["actor_role"] == "author" and conflict["actor"] == "author"
    assert conflict["reasons"] == ["rebase", "initial-review"]
    selected[0][0] = 1400
    observe(selected, head_sha=OLD, mergeable=None)
    selected[0][0] = 1500
    observe(selected, head_sha=OLD, mergeable=False)
    assert request(selected)["next_actions"][0]["cause"] == conflict["cause"]


@pytest.mark.parametrize(("changes", "action", "unavailable"), [
    ({}, "initial_verdict", None),
    ({"reviews": [verdict()]}, None, "review-order-unavailable"),
    ({"reviews": [verdict(user="other")]}, "initial_verdict", None),
    ({"reviews": [verdict(state="PENDING", submitted_at=None)]}, "initial_verdict", None),
    ({"reviews": [verdict(state="REQUEST_REVIEW", commit_id="", submitted_at=None)]}, "initial_verdict", None),
    ({"reviews": [verdict(user="")]}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(user=" \t")]}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(state="PENDING", user=" \t", submitted_at=None)]}, "initial_verdict", None),
    ({"reviews": [verdict(state="REQUEST_REVIEW", user="", submitted_at=None)]}, "initial_verdict", None),
    ({"reviews": [verdict(commit_id=HEAD), verdict(id=10, user="")]}, None, "review-activity-unavailable"),
    ({"reviews_complete": False}, None, "review-activity-unavailable"),
    ({"reviews_complete": None}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(commit_id=HEAD)]}, None, "reviewed-base-unknown"),
    ({"reviews": [verdict(dismissed=True)]}, None, "review-dismissed"),
    ({"reviews": [verdict(submitted_at="unknown")]}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(state="unknown")]}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(commit_id="")]}, None, "review-activity-unavailable"),
    ({"base_sha": ""}, None, "candidate-unavailable"),
    ({"draft": True}, None, None),
    ({"pr_state": "closed", "merged": True}, None, None),
    ({"author": "reviewer"}, None, None),
    ({"author": ""}, None, "author-unavailable"),
    ({"author": " \t"}, None, "author-unavailable"),
    ({"author": " \t", "mergeable": False}, None, "author-unavailable"),
    ({"reviews_complete": False, "mergeable": False}, "rebase", None),
])
def test_only_qualified_review_causes_become_actions(
    tmp_path: Path, changes: dict[str, Any], action: str | None, unavailable: str | None,
) -> None:
    selected = setup(tmp_path)
    observe(selected, **changes)
    before = selected[1].path.read_bytes()
    result = request(selected)
    assert [row["action"] for row in result["next_actions"]] == ([action] if action else [])
    assert [row["reason"] for row in result["unavailable"]] == ([unavailable] if unavailable else [])
    assert selected[1].path.read_bytes() == before


@pytest.mark.parametrize("reviews", [
    [verdict(dismissed=True, commit_id=HEAD)],
    [verdict(dismissed=True, state="DISMISSED", commit_id=HEAD)],
])
def test_dismissed_review_diagnostic_does_not_invent_a_verdict_or_order(
    tmp_path: Path, reviews: list[dict[str, Any]],
) -> None:
    selected = setup(tmp_path)
    observe(selected, head_sha=OLD, reviews=reviews)
    observe(selected, reviews=reviews)
    before = selected[1].path.read_bytes()
    result = request(selected)
    assert result["unavailable"] == [{"pr": 7, "reason": "review-dismissed"}]
    assert not result["next_actions"]
    assert selected[1].path.read_bytes() == before


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("matching_head", [False, True])
@pytest.mark.parametrize("history_state", ["APPROVED", "DISMISSED"])
def test_independent_verdict_survives_dismissed_history_refresh_and_restart(
    tmp_path: Path, reverse: bool, matching_head: bool, history_state: str,
) -> None:
    selected = setup(tmp_path, provider="github" if history_state == "DISMISSED" else "gitea")
    historical = verdict(dismissed=True, state=history_state)
    independent = verdict(id=10, commit_id=HEAD if matching_head else OLD)
    reviews = [historical, independent]
    if reverse:
        reviews.reverse()
    observe(selected, head_sha=OLD, reviews=reviews)
    selected[0][0] = 1100
    observe(selected, reviews=reviews)
    before = selected[1].path.read_bytes()
    first = request(selected)
    assert selected[1].path.read_bytes() == before
    if matching_head:
        assert first["unavailable"] == [{"pr": 7, "reason": "reviewed-base-unknown"}]
        assert not first["next_actions"]
    else:
        row = first["next_actions"][0]
        assert row["action"] == "new_verdict" and row["review"]["id"] == 10
        assert row["review_basis"] == "observed-before-candidate"
        assert row["pending_since"] == 1100 and row["cause"]["since"] == 1100
    selected[0][0] = 1200
    observe(selected, reviews=reviews)
    store = ReplayStore(
        selected[1].root, "test", clock=selected[1].clock, repository_cache=True,
        repository_continuing=True, repository_activity=True,
    )
    projection = RepositoryProjection(store, (selected[2],))
    restored: Selection = (
        selected[0], store, selected[2], projection, RepositorySubscriptions(projection),
    )
    before = store.path.read_bytes()
    current = request(restored)
    assert current["unavailable"] == first["unavailable"]
    assert [(row["review"], row["cause"], row["pending_since"])
            for row in current["next_actions"]] == [
        (row["review"], row["cause"], row["pending_since"]) for row in first["next_actions"]
    ]
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("changes", [
    {"submitted_at": "unknown"}, {"commit_id": ""}, {"state": "unknown"},
    {"id": 0}, {"submitted_at": "1970-01-01T01:00:00Z"},
    {"state": "DISMISSED", "commit_id": ""},
])
def test_malformed_selected_dismissed_review_still_blocks_independent_verdict(
    tmp_path: Path, reverse: bool, changes: dict[str, Any],
) -> None:
    selected = setup(tmp_path)
    reviews = [verdict(dismissed=True, **changes), verdict(id=10, commit_id=HEAD)]
    if reverse:
        reviews.reverse()
    observe(selected, reviews=reviews)
    result = request(selected)
    assert not result["next_actions"]
    assert result["unavailable"] == [{"pr": 7, "reason": "review-activity-unavailable"}]


@pytest.mark.parametrize("reverse", [False, True])
def test_new_independent_verdict_cannot_borrow_dismissed_candidate_basis(
    tmp_path: Path, reverse: bool,
) -> None:
    selected = setup(tmp_path)
    historical = verdict(dismissed=True)
    observe(selected, head_sha=OLD, reviews=[historical])
    reviews = [historical, verdict(id=10)]
    if reverse:
        reviews.reverse()
    observe(selected, reviews=reviews)
    result = request(selected)
    assert not result["next_actions"]
    assert result["unavailable"] == [{"pr": 7, "reason": "review-order-unavailable"}]


def test_every_independent_verdict_needs_original_candidate_evidence(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    historical, known = verdict(dismissed=True), verdict(id=10)
    observe(selected, head_sha=OLD, reviews=[historical, known])
    observe(selected, reviews=[
        historical, known,
        verdict(id=11, commit_id="d" * 40, submitted_at="1970-01-01T00:00:30Z"),
    ])
    before = selected[1].path.read_bytes()
    result = request(selected)
    assert not result["next_actions"]
    assert result["unavailable"] == [{"pr": 7, "reason": "review-order-unavailable"}]
    assert selected[1].path.read_bytes() == before


@pytest.mark.parametrize("reverse", [False, True])
def test_same_review_identity_cannot_supply_independent_dismissal_evidence(
    tmp_path: Path, reverse: bool,
) -> None:
    selected = setup(tmp_path)
    reviews = [verdict(dismissed=True), verdict(commit_id=HEAD)]
    if reverse:
        reviews.reverse()
    observe(selected, reviews=reviews)
    result = request(selected)
    assert not result["next_actions"]
    assert result["unavailable"] == [{"pr": 7, "reason": "review-activity-unavailable"}]


@pytest.mark.parametrize("changes, action, unavailable", [
    ({"reviews": [verdict(dismissed=True, user="other")]}, "initial_verdict", None),
    ({"reviews": [verdict(dismissed=True, state="PENDING")]}, "initial_verdict", None),
    ({"reviews": [verdict(dismissed=True)], "mergeable": False}, "rebase", None),
    ({"reviews": [verdict(dismissed=True)], "reviews_complete": False},
     None, "review-activity-unavailable"),
    ({"reviews": [verdict(dismissed=True, state="DISMISSED")], "reviews_complete": False},
     None, "review-activity-unavailable"),
    ({"reviews": [verdict(dismissed=True)], "author": ""}, None, "author-unavailable"),
])
def test_dismissal_diagnostic_preserves_selection_and_other_evidence_precedence(
    tmp_path: Path, changes: dict[str, Any], action: str | None, unavailable: str | None,
) -> None:
    selected = setup(tmp_path)
    observe(selected, **changes)
    result = request(selected)
    assert [row["action"] for row in result["next_actions"]] == ([action] if action else [])
    assert [row["reason"] for row in result["unavailable"]] == ([unavailable] if unavailable else [])


def test_observed_review_set_orders_candidate_without_relying_on_creation_order(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    reviews = [
        verdict(id=10, commit_id="d" * 40),
        verdict(id=9, submitted_at="1970-01-01T00:02:00Z"),
    ]
    observe(selected, head_sha=OLD, reviews=reviews)
    observe(selected, reviews=reviews)
    row = request(selected)["next_actions"][0]
    assert row["action"] == "new_verdict" and row["review_basis"] == "observed-before-candidate"
    selected[0][0] = 3000
    result = request(selected)
    assert not result["next_actions"] and result["unavailable"][0]["reason"] == "evidence-incomplete-or-stale"


def test_review_ahead_of_cached_head_cannot_prove_newer_author_activity(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected, reviews=[verdict(commit_id=OLD)])
    result = request(selected)
    assert not result["next_actions"]
    assert result["unavailable"] == [{"pr": 7, "reason": "review-order-unavailable"}]


def test_newly_observed_review_with_older_reported_time_cannot_hide_behind_known_feedback(
    tmp_path: Path,
) -> None:
    selected = setup(tmp_path)
    known = verdict(submitted_at="1970-01-01T00:02:00Z")
    observe(selected, head_sha=OLD, reviews=[known])
    observe(selected, reviews=[
        known, verdict(id=10, commit_id="d" * 40, submitted_at="1970-01-01T00:01:00Z"),
    ])
    result = request(selected)
    assert not result["next_actions"]
    assert result["unavailable"] == [{"pr": 7, "reason": "review-order-unavailable"}]


def test_observed_prior_review_basis_survives_refresh_restart_and_settled_event_retirement(
    tmp_path: Path,
) -> None:
    selected = setup(tmp_path)
    observe(selected, head_sha=OLD, reviews=[verdict()])
    selected[0][0] = 1100
    observe(selected, reviews=[verdict()])
    first = request(selected)["next_actions"][0]
    assert first["action"] == "new_verdict" and first["review"]["id"] == 9
    selected[0][0] = 1200
    observe(selected, reviews=[verdict()])
    store = ReplayStore(
        selected[1].root, "test", clock=selected[1].clock, repository_cache=True,
        repository_continuing=True, repository_activity=True,
    )
    projection = RepositoryProjection(store, (selected[2],))
    selected = (selected[0], store, selected[2], projection, RepositorySubscriptions(projection))

    def retire_settled(document: dict[str, Any]) -> None:
        assert not document["repository_cache"]["registrations"]
        document["repository_cache"]["events"] = []

    store._review_transaction(retire_settled, expires_at=10000)
    current = request(selected)["next_actions"][0]
    assert current["review"] == first["review"] and current["cause"] == first["cause"]


def test_page_fence_rejects_freshness_expiry_without_an_event(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected, number=7, members=(7, 8))
    selected[0][0] = 1001
    observe(selected, number=8, members=(7, 8))
    first = request(selected, limit=1, max_age_seconds=10)
    assert [row["pr"] for row in first["next_actions"]] == [7] and first["more"]
    selected[0][0] = 1010
    with pytest.raises(ValueError, match="page changed"):
        request(selected, after=1, limit=1, max_age_seconds=10, cursor=first["cursor"])
    current = request(selected, limit=1, max_age_seconds=10)
    assert current["event_cursor"] == first["event_cursor"]
    assert [row["pr"] for row in current["next_actions"]] == [8]


def test_page_fence_rejects_no_event_snapshot_error_and_recovery(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected, number=7, members=(7, 8))
    observe(selected, number=8, members=(7, 8))
    first = request(selected, limit=1)
    identity, projection = selected[2], selected[3]
    projection.apply_facts(
        {identity.key: {"outcome": "complete", "numbers": [7, 8], "pages": 1, "error": ""}},
        {(identity.key, identity.repo, 7): None}, expires_at=10000,
    )
    with pytest.raises(ValueError, match="page changed"):
        request(selected, after=1, limit=1, cursor=first["cursor"])
    failed = request(selected, limit=1)
    assert failed["event_cursor"] == first["event_cursor"]
    assert [row["pr"] for row in failed["next_actions"]] == [8]
    observe(selected, number=7, members=(7, 8))
    with pytest.raises(ValueError, match="page changed"):
        request(selected, after=1, limit=1, cursor=failed["cursor"])
    recovered = request(selected)
    assert recovered["event_cursor"] == first["event_cursor"]
    assert [row["pr"] for row in recovered["next_actions"]] == [7, 8]


def test_query_page_cursor_and_actual_scope_are_not_persistent_effects(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected)
    first = request(selected, limit=1)
    selected[0][0] = 1100
    observe(selected, head_sha=OLD)
    with pytest.raises(ValueError, match="page changed"):
        request(selected, cursor=first["cursor"])
    with pytest.raises(ValueError):
        request(selected, limit=True)
    with pytest.raises(ValueError, match="projection cursor"):
        request(selected, after=1)
    with pytest.raises(ValueError):
        selected[-1].execute("foreign", "consumer", "next-actions", {"reviewer": "reviewer"}, expires_at=10000)
