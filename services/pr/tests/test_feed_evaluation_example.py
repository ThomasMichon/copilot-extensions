"""Comparison source controls, not provider acquisition or live agreement."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest

from agent_pull_requests.watch_provider import decode_snapshot


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("example source unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SERVICE = Path(__file__).resolve().parents[1]
example = load("feed_evaluation_example", SERVICE / "examples/evaluate_feed.py")
fixtures = load(
    "feed_evaluation_cache_fixture",
    SERVICE.parents[1] / "plugins/agent-pull-requests/tests/test_repository_reviewer.py",
)


def reference(selected: Any, *, observed_at: int = 1000, **changes: Any) -> Any:
    identity = selected[2]
    raw = {
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "provider": identity.provider, "api_base": identity.endpoint, "repo": identity.repo,
        "pr": 7, "pr_state": "open", "merged": False,
        "head_sha": "a" * 40, "base_sha": "b" * 40, "base_ref": "main",
        "mergeable": True, "checks_state": "", "title": "Synthetic", "draft": False,
        "author": "author", "reviews": [], "reviews_complete": True,
        "updated_at": "1970-01-01T00:01:00Z", **changes,
    }
    return example.Reference(identity, decode_snapshot(raw, identity.repo, 7), observed_at)


def compare(selected: Any, direct: Any, **changes: Any) -> Any:
    arguments = {
        "identity": selected[2], "consumer": "consumer", "reviewer": "reviewer",
        "number": 7, "now": 1000, **changes,
    }
    return example.compare(
        fixtures.request(selected, "query", number=7), fixtures.request(selected), direct,
        **arguments,
    )


def test_actual_cache_initial_and_conflict_comparison_has_no_effects(tmp_path: Path) -> None:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected)
    before = selected[1].path.read_bytes()
    initial = compare(selected, reference(selected))
    assert set(initial.facts.values()) == {"agree"} and initial.action == "agree"
    assert selected[1].path.read_bytes() == before
    fixtures.observe(selected, mergeable=False)
    conflict = compare(selected, reference(selected, mergeable=False))
    assert conflict.action == "agree" and "author-owned" in conflict.reason
    wrong = compare(selected, reference(selected, mergeable=True))
    assert wrong.facts["mergeable"] == "different" and wrong.action == "different"


def test_current_fact_differences_are_not_fabricated_causal_verdicts(tmp_path: Path) -> None:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected)
    moved = compare(selected, reference(selected, head_sha="d" * 40))
    assert moved.facts["head_sha"] == "different" and moved.action == "unavailable"
    closed = compare(selected, reference(selected, pr_state="closed"))
    assert closed.facts["pr_state"] == "different" and closed.action == "different"
    conflict = compare(selected, reference(selected, mergeable=False))
    assert conflict.action == "different" and "author-owned" in conflict.reason
    feed = fixtures.request(selected)
    feed["next_actions"][0]["candidate"]["base"] = "e" * 40
    candidate = example.compare(
        fixtures.request(selected, "query", number=7), feed, reference(selected),
        identity=selected[2], consumer="consumer", reviewer="reviewer", number=7, now=1000,
    )
    assert candidate.action == "unavailable" and "candidate" in candidate.reason


def test_partial_review_history_and_followup_ordering_remain_unavailable(tmp_path: Path) -> None:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected)
    assert compare(selected, reference(selected, reviews_complete=None)).action == "unavailable"
    missing = reference(selected)
    raw = dict(missing.snapshot.native_snapshot)
    del raw["reviews"]
    missing = replace(missing, snapshot=decode_snapshot(raw, selected[2].repo, 7))
    assert compare(selected, missing).action == "unavailable"
    fixtures.observe(selected, reviews=[fixtures.verdict()])
    fixtures.observe(selected, head_sha="c" * 40, reviews=[fixtures.verdict()])
    fixtures.observe(selected, reviews=[fixtures.verdict()])
    assert fixtures.request(selected)["next_actions"][0]["action"] == "new_verdict"
    result = compare(selected, reference(selected, reviews=[fixtures.verdict()]))
    assert set(result.facts.values()) == {"agree"}
    assert result.action == "unavailable" and "causal" in result.reason


def test_expired_reference_buffered_action_and_incoherent_pages_are_not_disagreement(tmp_path: Path) -> None:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected)
    stale = compare(selected, reference(selected, observed_at=0), now=1801)
    assert stale.action == "stale" and set(stale.facts.values()) == {"stale"}
    assert compare(selected, reference(selected, observed_at=1001)).action == "unavailable"
    pulls = fixtures.request(selected, "query", number=7)
    buffered = fixtures.request(selected, max_age_seconds=2)
    expired = example.compare(
        pulls, buffered, reference(selected), identity=selected[2],
        consumer="consumer", reviewer="reviewer", number=7, now=1003,
    )
    assert expired.action == "stale"
    changed = example.compare(
        pulls | {"cursor": pulls["cursor"] + 1}, buffered, reference(selected),
        identity=selected[2], consumer="consumer", reviewer="reviewer", number=7, now=1000,
    )
    assert changed.action == "unavailable" and "moved" in changed.reason


def test_reference_and_cached_scope_are_fenced_before_comparison(tmp_path: Path) -> None:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected)
    direct = reference(selected)
    with pytest.raises(ValueError, match="scope"):
        compare(selected, replace(direct, identity=replace(selected[2], account="other")))
    with pytest.raises(ValueError, match="identity"):
        compare(selected, reference(selected, api_base="https://other.example"))
    with pytest.raises(ValueError, match="scope"):
        compare(selected, direct, consumer="other")


@pytest.mark.parametrize("state", ["open", "closed"])
def test_terminal_continuation_cannot_establish_absence_of_an_earlier_action(
    tmp_path: Path, state: str,
) -> None:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected, number=7, members=(7, 8))
    fixtures.observe(selected, number=8, members=(7, 8))
    first = fixtures.request(selected, limit=1)
    assert first["next_actions"][0]["pr"] == 7
    terminal = fixtures.request(selected, after=1, limit=1, cursor=first["cursor"])
    assert not terminal["more"] and terminal["next_actions"][0]["pr"] == 8
    result = example.compare(
        fixtures.request(selected, "query", number=7), terminal,
        reference(selected, pr_state=state), identity=selected[2],
        consumer="consumer", reviewer="reviewer", number=7, now=1000, after=1,
    )
    assert result.action == "unavailable" and "incomplete" in result.reason
