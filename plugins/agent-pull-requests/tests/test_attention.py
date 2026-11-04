"""Inert source controls, not provider, persisted-cursor or service adoption."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_pull_requests.attention import (
    Attention,
    Candidate,
    Cursor,
    Facts,
    Handled,
    Interest,
    PRKey,
    project,
    rank,
)


def facts(**changes: object) -> Facts:
    return replace(
        Facts(
            PRKey(
                "gitea",
                "https://forge.example.test",
                "example/repository",
                7,
                "reader-a",
                "service-a",
            ),
            Candidate("a" * 40, "b" * 40),
            Cursor(1, 3),
            100,
            90,
            True,
        ),
        **changes,
    )


def view(value: Facts, handled: Handled | None = None, *, now: int = 100) -> Attention:
    return project(
        value, Interest("maintainer", True, "reviewer-a"), handled, now=now, max_age_seconds=60
    )


def baseline(value: Facts) -> Handled:
    return Handled(value.key, value.candidate, value.cursor, "reviewer-a", "maintainer")


def test_initial_unchanged_feedback_and_same_head_conversation() -> None:
    value = facts()
    assert view(value).reasons == ("initial-review",)
    handled = baseline(value)
    assert view(value, handled).status == "waiting"
    changed = replace(value, cursor=Cursor(1, 4), pending_since=100)
    assert view(changed, handled).reasons == ("follow-up",)
    assert view(changed, handled).candidate == value.candidate


def test_feedback_does_not_swallow_concurrent_input_or_changed_candidate() -> None:
    original = facts()
    handled = baseline(original)
    current = replace(original, cursor=Cursor(1, 5), candidate=Candidate("c" * 40, "b" * 40))
    assert view(current, handled).reasons == ("follow-up",)
    assert view(original, replace(handled, cursor=Cursor(1, 5))).status == "unavailable"
    mismatched = replace(original, candidate=current.candidate)
    assert view(mismatched, handled).reasons == ("candidate-cursor-mismatch",)


@pytest.mark.parametrize("role", ["maintainer", "codeowner", "subject-matter-expert"])
def test_interest_does_not_grant_authority_and_unknown_selection_is_explicit(role: str) -> None:
    value = facts(merge_nudge=True)
    assert project(
        value, Interest(role, True, "reviewer-a"), now=100, max_age_seconds=60
    ).reasons == ("merge-nudge",)
    assert (
        project(value, Interest(role, None, "reviewer-a"), now=100, max_age_seconds=60).status
        == "unavailable"
    )
    assert (
        project(value, Interest(role, False, "reviewer-a"), now=100, max_age_seconds=60).status
        == "excluded"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"complete": False},
        {"observed_at": 40, "pending_since": 40},
        {"observed_at": 101},
    ],
)
def test_partial_stale_and_future_observations_cannot_recommend_work(change: dict) -> None:
    assert view(facts(**change)).status == "unavailable"


@pytest.mark.parametrize("state", ["merged", "closed-unmerged"])
def test_terminal_outcomes_are_distinct_and_not_destructive(state: str) -> None:
    value = facts(state=state)
    result = view(value)
    assert result.status == "terminal" and result.reasons == (state,)
    assert rank([result], now=100, aging_seconds=60) == ()


def test_reopen_epoch_does_not_apply_old_feedback() -> None:
    original = facts()
    handled = baseline(original)
    reopened = facts(cursor=Cursor(2, 0))
    assert view(reopened, handled).reasons == ("follow-up",)
    assert view(original, replace(handled, cursor=Cursor(2, 0))).status == "unavailable"


@pytest.mark.parametrize(
    "field,value",
    [
        ("account", "reader-b"),
        ("namespace", "service-b"),
        ("repository", "example/other"),
        ("number", 8),
        ("endpoint", "https://other.example.test"),
        ("provider", "github"),
    ],
)
def test_feedback_cannot_cross_scope(field: str, value: object) -> None:
    current = facts()
    handled = replace(baseline(current), key=replace(current.key, **{field: value}))
    with pytest.raises(ValueError, match="another PR/account/namespace"):
        view(current, handled)


def test_priority_aging_tail_rotation_and_deterministic_ties() -> None:
    original = facts()
    high = view(facts(key=replace(original.key, number=8), conflict=True))
    old = view(facts(pending_since=0))
    assert rank([old, high], now=100, aging_seconds=60) == (high, old)
    assert rank([old, high], now=100, aging_seconds=1) == (old, high)
    handled = baseline(original)
    waiting = view(original, handled)
    assert rank([waiting, high], now=100, aging_seconds=60) == (high,)
    other = view(facts(key=replace(original.key, number=9), pending_since=0))
    assert rank([other, old], now=100, aging_seconds=60) == (old, other)
    with pytest.raises(ValueError, match="duplicate"):
        rank([old, old], now=100, aging_seconds=60)
    assert rank([old, high], now=160, aging_seconds=1) == ()


def test_mechanical_recommendations_and_draft_do_not_invent_effects() -> None:
    value = facts(conflict=True, ci_rekick=True, final_iteration=True)
    assert view(value).reasons == ("rebase", "ci-rekick", "final-iteration")
    handled = baseline(value)
    assert view(value, handled).status == "waiting"
    assert view(replace(value, draft=True)).reasons == ("draft",)


@pytest.mark.parametrize("provider", ["github", "gitea", "azure-devops"])
def test_provider_identity_is_retained_without_claiming_driver_qualification(
    provider: str,
) -> None:
    original = facts()
    value = replace(original, key=replace(original.key, provider=provider))
    assert view(value).key.provider == provider


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://forge.example.test",
        "https://user:secret@forge.example.test",
        "https://forge.example.test/",
        "https://forge.example.test?token=example",
    ],
)
def test_unsafe_or_ambiguous_endpoint_refuses(endpoint: str) -> None:
    with pytest.raises(ValueError):
        replace(facts().key, endpoint=endpoint)


def test_another_subscriber_or_role_does_not_inherit_handled_progress() -> None:
    value = facts()
    handled = baseline(value)
    for changed in (
        replace(handled, consumer="reviewer-b"),
        replace(handled, role="codeowner"),
    ):
        with pytest.raises(ValueError, match="interest"):
            view(value, changed)
    independent = project(
        value,
        Interest("maintainer", True, "reviewer-b"),
        now=100,
        max_age_seconds=60,
    )
    assert independent.reasons == ("initial-review",)
    assert len(rank([view(value), independent], now=100, aging_seconds=60)) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "merged"},
        {"state": "closed-unmerged"},
        {"draft": True},
    ],
)
@pytest.mark.parametrize("contradiction", ["epoch", "sequence", "candidate"])
def test_terminal_and_draft_evidence_cannot_bypass_feedback_consistency(
    changes: dict,
    contradiction: str,
) -> None:
    value = facts(**changes)
    handled = baseline(value)
    if contradiction == "epoch":
        handled = replace(handled, cursor=Cursor(2, 0))
    elif contradiction == "sequence":
        handled = replace(handled, cursor=Cursor(1, 4))
    else:
        handled = replace(handled, candidate=Candidate("c" * 40, "b" * 40))
    assert view(value, handled).status == "unavailable"
    consistent = view(value, baseline(value))
    assert consistent.status == ("terminal" if value.state != "open" else "waiting")
