"""Tests for the Phase 10 item 3 polling-fallback cadence policy.

Pure data/functions only -- no network, no repository names committed
anywhere in the module under test or here; fixtures use the same
``example.com``-style placeholder repo names the rest of this suite uses.
"""

from __future__ import annotations

import pytest

from agent_dispatch.pr_polling_policy import (
    DEFAULT_TIER,
    POLL_INTERVAL_SECONDS,
    RepoTier,
    poll_due,
    polling_interval_seconds,
    tier_for,
)


def test_undeclared_repository_defaults_to_the_most_conservative_tier():
    assert tier_for("example/unlisted") == DEFAULT_TIER
    assert DEFAULT_TIER == RepoTier.PUBLIC_UNOWNED


def test_no_mapping_at_all_still_defaults_conservatively():
    assert tier_for("example/unlisted", None) == RepoTier.PUBLIC_UNOWNED


def test_declared_mapping_resolves_the_assigned_tier():
    tiers = {
        "example/owned-private": RepoTier.OWNED_PRIVATE,
        "example/quick-collab": RepoTier.QUICK_COLLAB,
    }
    assert tier_for("example/owned-private", tiers) == RepoTier.OWNED_PRIVATE
    assert tier_for("example/quick-collab", tiers) == RepoTier.QUICK_COLLAB
    # Still falls back for a repo missing from the mapping.
    assert tier_for("example/other", tiers) == RepoTier.PUBLIC_UNOWNED


def test_explicit_default_override_is_honored():
    assert (
        tier_for("example/unlisted", {}, default=RepoTier.OWNED_PRIVATE) == RepoTier.OWNED_PRIVATE
    )


@pytest.mark.parametrize(
    ("tier", "expected_seconds"),
    [
        (RepoTier.OWNED_PRIVATE, 5 * 60),
        (RepoTier.QUICK_COLLAB, 30 * 60),
        (RepoTier.PUBLIC_UNOWNED, 60 * 60),
    ],
)
def test_declared_tier_intervals_match_the_operator_s_stated_cadence(tier, expected_seconds):
    assert POLL_INTERVAL_SECONDS[tier] == expected_seconds


def test_private_tier_polls_tighter_than_quick_collab_which_polls_tighter_than_public():
    private = POLL_INTERVAL_SECONDS[RepoTier.OWNED_PRIVATE]
    quick_collab = POLL_INTERVAL_SECONDS[RepoTier.QUICK_COLLAB]
    public = POLL_INTERVAL_SECONDS[RepoTier.PUBLIC_UNOWNED]
    assert private < quick_collab < public


def test_polling_interval_seconds_resolves_via_the_repository_mapping():
    tiers = {"example/owned-private": RepoTier.OWNED_PRIVATE}
    assert polling_interval_seconds("example/owned-private", tiers) == 5 * 60
    assert polling_interval_seconds("example/unlisted", tiers) == 60 * 60


def test_polling_interval_seconds_honors_interval_overrides():
    assert (
        polling_interval_seconds(
            "example/unlisted",
            interval_overrides={RepoTier.PUBLIC_UNOWNED: 15 * 60},
        )
        == 15 * 60
    )


def test_interval_overrides_only_replace_the_named_tier():
    result = polling_interval_seconds(
        "example/owned-private",
        {"example/owned-private": RepoTier.OWNED_PRIVATE},
        interval_overrides={RepoTier.PUBLIC_UNOWNED: 15 * 60},
    )
    assert result == POLL_INTERVAL_SECONDS[RepoTier.OWNED_PRIVATE]


# --- poll_due ----------------------------------------------------------------


def test_poll_not_due_before_the_interval_elapses():
    tiers = {"example/owned-private": RepoTier.OWNED_PRIVATE}
    assert not poll_due(
        "example/owned-private", last_observed_at=1000.0, now=1000.0 + 299, repository_tiers=tiers
    )


def test_poll_due_once_the_interval_elapses():
    tiers = {"example/owned-private": RepoTier.OWNED_PRIVATE}
    assert poll_due(
        "example/owned-private", last_observed_at=1000.0, now=1000.0 + 300, repository_tiers=tiers
    )


def test_a_fresh_webhook_observation_pushes_the_next_poll_out():
    tiers = {"example/owned-private": RepoTier.OWNED_PRIVATE}
    now = 2000.0
    # A poll would be due against the old observation...
    assert poll_due(
        "example/owned-private", last_observed_at=1000.0, now=now, repository_tiers=tiers
    )
    # ...but a fresh webhook-delivered observation resets the clock.
    assert not poll_due(
        "example/owned-private", last_observed_at=now - 1, now=now, repository_tiers=tiers
    )


def test_undeclared_repo_uses_the_conservative_public_interval_for_poll_due():
    now_before_interval = 3000.0 + (60 * 60) - 1
    now_at_interval = 3000.0 + (60 * 60)
    assert not poll_due("example/unlisted", last_observed_at=3000.0, now=now_before_interval)
    assert poll_due("example/unlisted", last_observed_at=3000.0, now=now_at_interval)


def test_now_before_last_observed_at_raises():
    with pytest.raises(ValueError, match="now must not precede"):
        poll_due("example/owned-private", last_observed_at=1000.0, now=999.0)
