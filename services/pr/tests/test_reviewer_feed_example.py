"""Actual cache pages and an inert task-owner projection; no task mutations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("example source unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SERVICE = Path(__file__).resolve().parents[1]
example = load("reviewer_feed_example", SERVICE / "examples/reviewer_feed.py")
fixtures = load(
    "reviewer_feed_cache_fixture",
    SERVICE.parents[1] / "plugins/agent-pull-requests/tests/test_repository_reviewer.py",
)
QUEUE = ("queue-a", "installation-a", "machine-a")


def selection(tmp_path: Path) -> tuple[Any, Any, dict[str, Any]]:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected)
    scope = example.Scope(selected[2], "consumer", "reviewer", QUEUE)
    return selected, scope, fixtures.request(selected)


def test_reference_selection_never_changes_delivery_or_handles_an_action(tmp_path: Path) -> None:
    selected, scope, page = selection(tmp_path)
    subscription = fixtures.request(
        selected, "subscribe", request_id="consumer-proof", ttl=3600, after=0,
    )["subscription"]
    before = selected[1].path.read_bytes()
    reads = []

    def lookup(action: dict[str, Any]) -> Any:
        reads.append(action["pr"])
        return example.Task(QUEUE, "absent")

    proposed = example.propose(page, scope, lookup=lookup, notifications={}, now=1000)
    assert proposed.kind == "create" and proposed.pr == 7 and reads == [7]
    assert selected[1].path.read_bytes() == before
    retained = fixtures.request(
        selected, "inspect", registration_id=subscription["registration_id"],
    )["subscription"]
    assert retained["delivered"] == retained["acknowledged"] == 0


def test_round_robin_uses_actual_ranked_view_and_preserves_partial_coverage(tmp_path: Path) -> None:
    selected, scope, _page = selection(tmp_path)
    fixtures.observe(selected, number=7, members=(7, 8))
    fixtures.observe(selected, number=8, members=(7, 8))
    page = fixtures.request(selected)
    lookup = lambda _action: example.Task(QUEUE, "absent")
    proposal = example.propose(
        page, scope, lookup=lookup, notifications={}, now=1000, last_served_pr=7,
    )
    assert proposal.kind == "create" and proposal.pr == 8
    partial = fixtures.request(selected, limit=1)
    assert partial["more"]
    assert example.propose(
        partial, scope, lookup=lookup, notifications={}, now=1000,
    ).kind == "read-page"
    last = fixtures.request(selected, after=1, limit=1, cursor=partial["cursor"])
    assert not last["more"]
    assert example.propose(
        last, scope, lookup=lookup, notifications={}, now=1000, after=1,
    ).kind == "reconcile"
    selected[0][0] += 1801
    stale = fixtures.request(selected)
    result = example.propose(stale, scope, lookup=lookup, notifications={}, now=2801)
    assert result.kind == "noop" and result.unavailable == 2
    assert example.propose(
        page, scope, lookup=lookup, notifications={}, now=2801,
    ).kind == "reconcile"


def test_new_activity_gets_new_notification_but_duplicate_views_do_not_wake(tmp_path: Path) -> None:
    selected, scope, page = selection(tmp_path)
    task = example.Task(QUEUE, "present", "task-a", "revision-a", "suspended")
    lookup = lambda _action: task
    first = example.propose(page, scope, lookup=lookup, notifications={}, now=1000)
    assert first.kind == "steer"
    accepted = {7: example.Notification(first.action_key, "original-request", "task-a", "accepted")}
    assert example.propose(
        page, scope, lookup=lookup, notifications=accepted, now=1000,
    ).kind == "noop"
    # A later suspension/revision is not itself new PR activity.
    task = example.Task(QUEUE, "present", "task-a", "revision-b", "suspended")
    assert example.propose(
        page, scope, lookup=lookup, notifications=accepted, now=1000,
    ).kind == "noop"
    fixtures.observe(selected, head_sha="d" * 40)
    updated = example.propose(
        fixtures.request(selected), scope, lookup=lookup, notifications=accepted, now=1000,
    )
    assert updated.kind == "steer" and updated.task_id == "task-a"
    assert updated.action_key != first.action_key
    assert updated.notification_key != first.notification_key


def test_unknown_notification_survives_new_activity_and_missing_live_task(tmp_path: Path) -> None:
    selected, scope, page = selection(tmp_path)
    before = selected[1].path.read_bytes()
    pending = {7: example.Notification("older-action", "original-request", "task-a", "unknown")}

    def no_lookup(_action: dict[str, Any]) -> Any:
        raise AssertionError("reconcile the original request before another task lookup")

    assert example.propose(
        page, scope, lookup=no_lookup, notifications=pending, now=1000,
    ).kind == "reconcile"
    accepted = {7: example.Notification("older-action", "original-request", "task-a", "accepted")}
    assert example.propose(
        page, scope, lookup=lambda _action: example.Task(QUEUE, "absent"),
        notifications=accepted, now=1000,
    ).kind == "reconcile"
    assert example.propose(
        page, scope, lookup=lambda _action: example.Task(
            QUEUE, "present", "different-task", "revision-a", "suspended",
        ), notifications=accepted, now=1000,
    ).kind == "reconcile"
    assert selected[1].path.read_bytes() == before


def test_author_actions_holds_unknown_scope_and_foreign_consumers_do_not_assign(tmp_path: Path) -> None:
    selected, scope, page = selection(tmp_path)
    for task, expected in (
        (example.Task(QUEUE, "unknown"), "reconcile"),
        (example.Task(("other-queue", *QUEUE[1:]), "absent"), "reconcile"),
        (example.Task(QUEUE, "present", "task-a", "revision-a", "suspended", True), "noop"),
        (example.Task(QUEUE, "present", "task-a", "revision-a", "queued"), "noop"),
    ):
        assert example.propose(
            page, scope, lookup=lambda _action: task, notifications={}, now=1000,
        ).kind == expected
    with pytest.raises(ValueError, match="scope"):
        example.propose(
            page | {"consumer": "other"}, scope,
            lookup=lambda _action: example.Task(QUEUE, "absent"), notifications={}, now=1000,
        )
    fixtures.observe(selected, mergeable=False)

    def no_lookup(_action: dict[str, Any]) -> Any:
        raise AssertionError("a reviewer cannot take an author-owned rebase")

    result = example.propose(
        fixtures.request(selected), scope, lookup=no_lookup, notifications={}, now=1000,
    )
    assert result.kind == "noop"
