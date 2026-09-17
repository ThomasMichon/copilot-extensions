"""Revision-history evaluator for the GitHub PR-state observer.

Phase 10 item 3, third slice: :mod:`agent_dispatch.github_provider_adapter`
observes a PR's *current* raw state, but a single snapshot cannot tell
whether an existing ``APPROVED`` verdict still covers the PR's current head
-- that is a function of *two* observations over time, per
:func:`agent_dispatch.provider_state_machine.classify_revision_change`. This
module is that comparison, made real against the declared
``APPROVAL_TRANSITIONS`` table rather than re-deciding the rule inline --
the same "the declared table is the actual governing data" discipline Phase
10 items 1 and 2 already established for the task machine's own transitions.

Deliberately narrow, matching the rest of item 3's slices:

- :func:`evaluate_observation` is pure: two :class:`~agent_dispatch.
  github_provider_adapter.PRObservation` values (or ``None`` for the first
  observation) in, one out. No network, no persistence.
- It does not decide *where* the ``previous`` observation is stored between
  calls -- that is the persistence slice this item still needs (a
  per-(repo, PR) store, likely a new ``queue.py`` table given the plugin's
  existing convention of one additive table per declared machine).
- It never applies ``revalidate_stale``: that transition exists for when a
  reviewer re-requests review after a stale approval, which is simply
  whatever the provider's own ``reviewDecision`` already reports on the
  next observation (e.g. ``REVIEW_REQUIRED``) -- nothing this evaluator
  needs to force, since it always recomputes from the provider's current
  raw approval status, not from a locally cached "STALE" flag.
"""

from __future__ import annotations

from dataclasses import replace

from .github_provider_adapter import PRObservation
from .provider_state_machine import (
    APPROVAL_TRANSITIONS,
    REVISION_CHANGE_APPROVAL_TRANSITION,
    classify_revision_change,
)

#: name -> DimensionTransition, built once from the declared table.
_APPROVAL_TRANSITIONS_BY_NAME = {t.name: t for t in APPROVAL_TRANSITIONS}


def evaluate_observation(previous: PRObservation | None, current: PRObservation) -> PRObservation:
    """Return ``current``, with its ``approval_status`` corrected to
    ``STALE`` if the declared ``revision_invalidates_approval`` transition
    applies against ``previous``'s revision.

    A first-ever observation (``previous is None``) has nothing to compare
    against and is returned unchanged -- staleness is a two-observation
    property, not something a lone snapshot can classify.
    """
    if previous is None:
        return current
    change_kind = classify_revision_change(previous.revision, current.revision)
    transition_name = REVISION_CHANGE_APPROVAL_TRANSITION[change_kind]
    if transition_name is None:
        return current
    transition = _APPROVAL_TRANSITIONS_BY_NAME[transition_name]
    if current.approval_status not in transition.from_states:
        # The provider's own current approval status isn't one the
        # declared transition applies from (e.g. it already reports
        # PENDING/CHANGES_REQUESTED rather than APPROVED) -- nothing to
        # invalidate.
        return current
    return replace(current, approval_status=transition.to_state)
