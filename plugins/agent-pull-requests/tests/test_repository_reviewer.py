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


def setup(tmp_path: Path, *, activity: bool = True) -> Selection:
    now = [1000.0]
    store = ReplayStore(
        tmp_path, "test", clock=lambda: now[0], repository_cache=True,
        repository_continuing=True, repository_activity=activity,
    )
    store.provision()
    identity = RepositoryIdentity("test", "gitea", "https://forge.example", "reader", "owner/repo")
    projection = RepositoryProjection(store, (identity,))
    return now, store, identity, projection, RepositorySubscriptions(projection)


def observe(selected: Selection, *, number: int = 7, **changes: Any) -> None:
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
        {identity.key: {"outcome": "complete", "numbers": [number], "pages": 1, "error": ""}},
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
    ({"reviews": [verdict()]}, "new_verdict", None),
    ({"reviews": [verdict(user="other")]}, "initial_verdict", None),
    ({"reviews": [verdict(state="PENDING", submitted_at=None)]}, "initial_verdict", None),
    ({"reviews_complete": False}, None, "review-activity-unavailable"),
    ({"reviews_complete": None}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(commit_id=HEAD)]}, None, "reviewed-base-unknown"),
    ({"reviews": [verdict(dismissed=True)]}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(submitted_at="unknown")]}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(state="unknown")]}, None, "review-activity-unavailable"),
    ({"reviews": [verdict(commit_id="")]}, None, "review-activity-unavailable"),
    ({"base_sha": ""}, None, "candidate-unavailable"),
    ({"draft": True}, None, None),
    ({"pr_state": "closed", "merged": True}, None, None),
    ({"author": "reviewer"}, None, None),
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


def test_late_submitted_review_wins_over_draft_creation_order_and_stale_evidence_refuses(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected, reviews=[
        verdict(id=10, commit_id=HEAD),
        verdict(id=9, submitted_at="1970-01-01T00:02:00Z"),
    ])
    assert request(selected)["next_actions"][0]["review"]["id"] == 9
    selected[0][0] = 3000
    result = request(selected)
    assert not result["next_actions"] and result["unavailable"][0]["reason"] == "evidence-incomplete-or-stale"


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
    with pytest.raises(ValueError):
        selected[-1].execute("foreign", "consumer", "next-actions", {"reviewer": "reviewer"}, expires_at=10000)
