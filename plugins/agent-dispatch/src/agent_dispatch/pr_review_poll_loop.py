"""Poll-fallback loop tick for the GitHub PR-state observation pipeline.

Phase 10 item 3's wiring slice: :mod:`agent_dispatch.pr_polling_policy`
declares *when* a fallback poll is due; this module is the one function
that actually runs one such cycle -- iterate every PR the store already
tracks, refresh whichever ones are due, and persist the result.

A PR starts being tracked the moment anything (a webhook delivery via
:mod:`agent_dispatch.producers.github_pr_review_webhook`, or an initial
explicit observation) records it in the
:class:`~agent_dispatch.pr_observation_store.PRObservationStore`. This loop
never discovers new PRs on its own -- it is purely the safety net that
keeps an already-tracked PR's state from going stale if webhooks stop
flowing for it, per :mod:`agent_dispatch.pr_polling_policy`'s own
"webhooks primary, polling fallback" design.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from .github_provider_adapter import PRObservation
from .pr_observation_store import PRObservationStore, record_observation
from .pr_polling_policy import RepoTier, poll_due

#: Fetches the current raw observation for one PR. In production this is
#: :meth:`agent_dispatch.github_provider_adapter.GitHubPRAdapter.observe`;
#: tests inject a fake.
Observer = Callable[[str, int], PRObservation]


def run_poll_cycle(
    store: PRObservationStore,
    observe: Observer,
    repository_tiers: Mapping[str, RepoTier] | None = None,
    *,
    now: float | None = None,
) -> list[tuple[str, int, PRObservation]]:
    """Refresh every tracked ``(repo, number)`` whose fallback poll is due.

    Returns the ``(repo, number, observation)`` triples actually refreshed
    this cycle -- an empty list means nothing was due, not that nothing is
    tracked.
    """
    current_time = now if now is not None else time.time()
    refreshed: list[tuple[str, int, PRObservation]] = []
    for repo, number in store.tracked_keys():
        last_observed_at = store.last_observed_at(repo, number)
        if last_observed_at is None:
            # Tracked but never actually observed (should not normally
            # happen -- put() always sets last_observed_at) -- treat as
            # due rather than silently skipping it forever.
            due = True
        else:
            due = poll_due(repo, last_observed_at, current_time, repository_tiers)
        if not due:
            continue
        current = observe(repo, number)
        evaluated = record_observation(store, repo, number, current, now=current_time)
        refreshed.append((repo, number, evaluated))
    return refreshed
