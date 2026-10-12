"""Per-repository PR-observation polling-fallback cadence.

Phase 10 item 3 (``review-automation-reliability`` effort,
``efforts/active/review-automation-reliability/phase-10-live-wiring.md``):
:mod:`agent_dispatch.github_provider_adapter` observes PR state on demand,
but says nothing about *when* to observe it. The operator's own resolution
of that design fork: webhooks are the primary, low-latency trigger; polling
is only a **fallback** that fires when nothing -- webhook or otherwise --
has refreshed a PR's observed state within a declared interval. That
interval is not one global number: it scopes by how formal/collaborative a
repository is and how much throttling risk polling it carries.

Three declared tiers capture that risk profile, each with its own default
interval:

- :attr:`RepoTier.OWNED_PRIVATE` -- a private repository the deploying
  identity owns outright. State changes matter fast and there is no shared
  rate-limit risk from a single-tenant repo, so this tier polls tightest.
- :attr:`RepoTier.QUICK_COLLAB` -- a repository the deploying identity owns
  and iterates on quickly alongside others, sitting between the two.
- :attr:`RepoTier.PUBLIC_UNOWNED` -- a repository this identity does not
  own or control. Polling it must stay conservative to avoid tripping a
  rate limit shared with every other consumer of that repository's API
  quota. This is also the **default** for any repository with no explicit
  tier assignment -- the safest assumption when formality/ownership has not
  been declared.

This module is deliberately data-only and organization-neutral: it declares
the tiers and their default intervals, but the actual repository -> tier
mapping is supplied by the caller (a deployer's own config, following the
same shape :mod:`agent_dispatch.producers.webhook`'s ``load_config`` already
uses) rather than committed here. No specific repository name belongs in
this plugin's source.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum


class RepoTier(Enum):
    """A repository's formality/ownership tier for polling-fallback cadence."""

    OWNED_PRIVATE = "owned_private"
    QUICK_COLLAB = "quick_collab"
    PUBLIC_UNOWNED = "public_unowned"


#: Default fallback polling interval per tier, in seconds. Only consulted
#: when a PR's last observed state -- from any source, webhook or poll --
#: is older than this; see :func:`poll_due`.
POLL_INTERVAL_SECONDS: dict[RepoTier, int] = {
    RepoTier.OWNED_PRIVATE: 5 * 60,
    RepoTier.QUICK_COLLAB: 30 * 60,
    RepoTier.PUBLIC_UNOWNED: 60 * 60,
}

#: The tier assumed for any repository with no explicit assignment in the
#: caller-supplied mapping. Deliberately the most conservative tier: an
#: undeclared repository must not be assumed safe to poll aggressively.
DEFAULT_TIER = RepoTier.PUBLIC_UNOWNED


def tier_for(
    repo: str,
    repository_tiers: Mapping[str, RepoTier] | None = None,
    *,
    default: RepoTier = DEFAULT_TIER,
) -> RepoTier:
    """Resolve ``repo``'s tier from a caller-supplied mapping, falling back
    to ``default`` (the most conservative tier) when undeclared."""
    if repository_tiers is None:
        return default
    return repository_tiers.get(repo, default)


def polling_interval_seconds(
    repo: str,
    repository_tiers: Mapping[str, RepoTier] | None = None,
    *,
    interval_overrides: Mapping[RepoTier, int] | None = None,
) -> int:
    """The fallback polling interval, in seconds, for ``repo``."""
    tier = tier_for(repo, repository_tiers)
    intervals = (
        POLL_INTERVAL_SECONDS
        if interval_overrides is None
        else {
            **POLL_INTERVAL_SECONDS,
            **interval_overrides,
        }
    )
    return intervals[tier]


def poll_due(
    repo: str,
    last_observed_at: float,
    now: float,
    repository_tiers: Mapping[str, RepoTier] | None = None,
    *,
    interval_overrides: Mapping[RepoTier, int] | None = None,
) -> bool:
    """Whether a fallback poll should fire for ``repo``.

    ``last_observed_at`` is the last time *any* source -- a webhook
    delivery or a prior poll -- successfully refreshed this PR's observed
    state. As long as webhooks keep that timestamp fresh, a poll never
    fires; polling only kicks in once the declared interval has elapsed
    with no fresher observation from any source.
    """
    if now < last_observed_at:
        raise ValueError("now must not precede last_observed_at")
    interval = polling_interval_seconds(
        repo, repository_tiers, interval_overrides=interval_overrides
    )
    return (now - last_observed_at) >= interval
