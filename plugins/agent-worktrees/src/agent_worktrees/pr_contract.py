"""Provider-neutral PR event/verdict contract for the ``pr-*`` command family.

This is the single **pure** seam that ``pr-watch`` (transition events) and
``pr-status`` (glance verdict / conflict / merge state) both build on.  It
unifies the pure cores of the two facility tools -- ``tools/pr-watch`` (the
transition diff + cursor) and ``tools/pr-consent`` (the head-aware verdict
reduction + consent eligibility) -- into one place so the family speaks one
vocabulary regardless of provider.

Design constraints that keep it a *contract*, not an implementation:

- **No network.**  Every function is a pure transform of its inputs; the
  provider fetches a :class:`PRSnapshot` and hands it in.
- **No config import.**  The facility binding (auto-merge label, hold labels, WIP
  title prefixes) is passed in as explicit arguments, so this module never
  couples to ``config`` or a specific hosting service.  Binding-absent (empty
  arguments) degrades cleanly -- no holds, no WIP, verdict/mergeability still
  classify.
- **Stdlib only.**  No new dependency.

The heavier machinery -- polling a provider, the CLI surface, moving the
facility tools onto this seam -- lands in later phases of the
``pr-command-family`` effort.  Phase 1 ships only this contract + its tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Transition vocabulary (shared by pr-watch's --until and pr-status)
# ---------------------------------------------------------------------------

#: Every transition name a caller may select.  Review states map onto the
#: ``*_requested`` / ``approved`` / ``commented`` names; PR lifecycle maps onto
#: ``merged`` / ``closed``; the provider mergeability flag maps onto
#: ``conflict`` / ``mergeable``.
ALL_TRANSITIONS = (
    "changes_requested",  # a request-changes review was submitted (not the author's)
    "approved",           # an approving review was submitted (not the author's)
    "commented",          # a comment-only review was submitted (not the author's)
    "conflict",           # the PR became un-mergeable (mergeable true -> false)
    "mergeable",          # the PR became mergeable again (mergeable false -> true)
    "merged",             # the PR became merged
    "closed",             # the PR closed without merging
)

#: The actionable default: everything that needs the author's attention -- a
#: review by someone else, a merge-state change, or the PR landing/closing.
#: Bare ``commented`` is excluded (noisy) but available via ``any``.
DEFAULT_UNTIL = (
    "changes_requested", "approved", "conflict", "mergeable", "merged", "closed",
)

#: Provider-neutral review state (uppercased) -> transition name.  A provider
#: normalizes its own review vocabulary onto these three canonical states.
_REVIEW_STATE_EVENT = {
    "REQUEST_CHANGES": "changes_requested",
    "CHANGES_REQUESTED": "changes_requested",
    "APPROVED": "approved",
    "COMMENT": "commented",
    "COMMENTED": "commented",
    # A pending/draft review is not submitted, so it is never a transition.
}

#: Review states that carry a merge-relevant verdict.  A comment is not a
#: verdict; a pending draft is not submitted.
VERDICT_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Review:
    """One submitted PR review, normalized across providers."""

    id: int
    state: str            # canonical: APPROVED | CHANGES_REQUESTED | COMMENT | ...
    user: str
    submitted_at: str = ""
    commit_id: str = ""
    dismissed: bool = False


@dataclass(frozen=True)
class PRSnapshot:
    """A point-in-time view of a PR, sufficient to diff and classify.

    Carries both the fields ``pr-watch`` needs (reviews, mergeable, lifecycle)
    and the fields ``pr-consent`` / ``pr-status`` need (labels, title, draft),
    so one snapshot feeds every member of the family.
    """

    pr_state: str = "open"       # "open" | "closed"
    merged: bool = False
    head_sha: str = ""
    base_ref: str = ""
    reviews: tuple[Review, ...] = ()
    author: str = ""             # the PR creator's login (its own reviews never fire)
    mergeable: bool | None = None
    """Provider mergeability flag: True (ready), False (conflict/blocked), or
    None when the provider hasn't computed it yet (some compute it async, so a
    just-opened PR can briefly report None)."""
    labels: tuple[str, ...] = ()
    title: str = ""
    draft: bool = False

    @property
    def max_review_id(self) -> int:
        """High-water mark over **submitted** (verdict/comment) reviews only.

        Excluding non-submitted reviews from the cursor means a draft visible at
        arm time can't silently absorb its own later submission.
        """
        return max(
            (r.id for r in self.reviews if r.state.upper() in _REVIEW_STATE_EVENT),
            default=0,
        )

    @property
    def closed_unmerged(self) -> bool:
        return self.pr_state == "closed" and not self.merged


@dataclass(frozen=True)
class Baseline:
    """The arm-time reference a wait diffs against ("notify me of changes from
    here on"), serializable as an opaque cursor."""

    max_review_id: int = 0
    merged: bool = False
    closed: bool = False
    mergeable: bool | None = None
    """The arm-time mergeable flag a ``conflict`` / ``mergeable`` transition
    diffs against.  ``None`` means "not yet known" -- the wait loop adopts the
    first concrete value without firing.  Deliberately **not** encoded in the
    cursor (tri-state, recomputed cheaply next poll)."""

    @classmethod
    def from_snapshot(cls, snap: PRSnapshot) -> Baseline:
        return cls(
            max_review_id=snap.max_review_id,
            merged=snap.merged,
            closed=snap.pr_state == "closed",
            mergeable=snap.mergeable,
        )

    def to_cursor(self) -> str:
        """Compact, opaque, ASCII cursor (machine-facing -- stays ASCII)."""
        flags = ("m" if self.merged else "") + ("c" if self.closed else "")
        return f"r{self.max_review_id}" + (f".{flags}" if flags else "")

    @classmethod
    def from_cursor(cls, cursor: str) -> Baseline:
        """Parse a cursor produced by :meth:`to_cursor` (or a bare review id).

        A bare int (e.g. ``"13"``) means "review high-water 13, not yet
        merged/closed", so a PR already merged when such a cursor is passed
        counts the merge as a fresh transition.
        """
        s = cursor.strip()
        if not s:
            return cls()
        flags = ""
        if "." in s:
            s, flags = s.split(".", 1)
        s = s.lstrip("r") or "0"
        try:
            rid = int(s)
        except ValueError as exc:
            raise ValueError(f"invalid cursor: {cursor!r}") from exc
        return cls(max_review_id=rid, merged="m" in flags, closed="c" in flags)


# ---------------------------------------------------------------------------
# Pure transition logic (pr-watch's core)
# ---------------------------------------------------------------------------

def compute_events(
    baseline: Baseline, snap: PRSnapshot, until: Iterable[str]
) -> list[dict]:
    """Return the target transitions present in ``snap`` relative to ``baseline``.

    Pure and deterministic: the wait loop calls this each poll and exits on the
    first non-empty result.  A review by the PR author never fires (they armed
    the watch) but still advances the cursor; a ``None`` (not-yet-computed)
    mergeable baseline is adopted by the caller without firing.
    """
    want = set(until)
    if "any" in want:
        want = set(ALL_TRANSITIONS)

    events: list[dict] = []

    for review in sorted(snap.reviews, key=lambda r: r.id):
        if review.id <= baseline.max_review_id:
            continue
        if snap.author and review.user == snap.author:
            continue
        name = _REVIEW_STATE_EVENT.get(review.state.upper())
        if name is None or name not in want:
            continue
        events.append(
            {
                "event": name,
                "review": {
                    "id": review.id,
                    "state": review.state,
                    "user": review.user,
                    "submitted_at": review.submitted_at,
                    "commit_id": review.commit_id,
                },
            }
        )

    # Merge state change -- only meaningful while open + unmerged, and only on a
    # concrete True<->False flip (a None baseline is adopted without firing).
    if snap.pr_state == "open" and not snap.merged:
        if baseline.mergeable is True and snap.mergeable is False and "conflict" in want:
            events.append({"event": "conflict"})
        elif baseline.mergeable is False and snap.mergeable is True and "mergeable" in want:
            events.append({"event": "mergeable"})

    if snap.merged and not baseline.merged and "merged" in want:
        events.append({"event": "merged"})

    if (
        snap.closed_unmerged
        and not baseline.closed
        and not snap.merged
        and "closed" in want
    ):
        events.append({"event": "closed"})

    return events


# ---------------------------------------------------------------------------
# Pure verdict + consent classification (pr-consent's core)
# ---------------------------------------------------------------------------

def effective_verdict(
    reviews: Iterable[Review], head_sha: str, author: str
) -> str:
    """Reduce a PR's reviews to one effective verdict at ``head_sha``.

    Considers only *submitted*, non-comment, non-dismissed reviews that are not
    the PR author's own.  The latest such review (by id) wins.  An ``APPROVED``
    review only counts if it was submitted against the current head -- a stale
    approval on a superseded head is treated as no-verdict so the PR is left for
    re-review.

    Returns ``"APPROVED"``, ``"CHANGES_REQUESTED"``, or ``""`` (no verdict).
    """
    latest_id = -1
    verdict = ""
    latest_commit = ""
    for r in reviews:
        state = r.state.upper()
        if state == "REQUEST_CHANGES":
            state = "CHANGES_REQUESTED"
        if state not in VERDICT_STATES:
            continue
        if r.dismissed:
            continue
        if author and r.user and r.user == author:
            continue  # a PR author's own review is never a gate
        if r.id > latest_id:
            latest_id = r.id
            verdict = state
            latest_commit = r.commit_id or ""
    if verdict == "APPROVED" and head_sha and latest_commit and latest_commit != head_sha:
        return ""  # stale approval on an old head -> not actionable
    return verdict


def title_is_wip(title: str, wip_title_prefixes: Iterable[str]) -> bool:
    """True when ``title`` starts with any configured WIP prefix (case-insensitive).

    With no prefixes configured this is always False (binding-absent = no-op).
    """
    t = (title or "").strip().lower()
    return any(t.startswith(p.strip().lower()) for p in wip_title_prefixes if p.strip())


def merge_state(snap: PRSnapshot) -> str:
    """One-word merge disposition for a glance: merged/closed/conflict/clean/unknown."""
    if snap.merged:
        return "merged"
    if snap.closed_unmerged:
        return "closed"
    if snap.mergeable is False:
        return "conflict"
    if snap.mergeable is True:
        return "clean"
    return "unknown"


@dataclass(frozen=True)
class PRState:
    """The unified verdict/conflict/merge classification of a PR.

    One value both ``pr-watch`` (event context) and ``pr-status`` (glance) read,
    and the decision ``pr-merge`` acts on (``consent_action``).
    """

    verdict: str          # "APPROVED" | "CHANGES_REQUESTED" | ""
    merge_state: str      # merged | closed | conflict | clean | unknown
    conflict: bool        # mergeable is False
    consent_present: bool  # the automerge_label is already on the PR
    held: tuple[str, ...]  # hold labels present on the PR
    wip: bool             # draft or a WIP title prefix
    consent_action: str   # "apply" | "already" | "skip" -- what pr-merge should do
    reason: str           # human-readable justification for consent_action

    @property
    def eligible(self) -> bool:
        """True when the PR is eligible to have merge consent applied now."""
        return self.consent_action == "apply"


def classify_state(
    snap: PRSnapshot,
    *,
    automerge_label: str = "",
    hold_labels: Iterable[str] = (),
    wip_title_prefixes: Iterable[str] = (),
) -> PRState:
    """Map a provider snapshot onto the unified :class:`PRState`.

    The one classifier the family shares.  The facility binding
    (``automerge_label`` / ``hold_labels`` / ``wip_title_prefixes``) is passed
    in; with everything empty it degrades cleanly -- no holds, no WIP, and
    ``consent_action`` still reflects the verdict + mergeability (it just reports
    that no auto-merge label is configured rather than proposing to apply one).

    "Consent" is the *concept* (has the author authorized the merge?);
    ``automerge_label`` is the concrete label that expresses it (facility value:
    ``auto-merge``; think ADO's "auto-complete").

    ``consent_action`` mirrors the facility ``pr-consent`` eligibility rules:

    - ``already`` -- the auto-merge label is already present (nothing to do).
    - ``apply``   -- open, not draft/WIP, no hold, mergeable, approved at head,
                     and an auto-merge label is configured but not yet present.
    - ``skip``    -- any blocking condition, with ``reason`` naming it.
    """
    label_set = {lbl.lower() for lbl in snap.labels}
    hold_set = {h.strip().lower() for h in hold_labels if h.strip()}
    held = tuple(sorted(label_set & hold_set))
    wip = snap.draft or title_is_wip(snap.title, wip_title_prefixes)
    verdict = effective_verdict(snap.reviews, snap.head_sha, snap.author)
    ms = merge_state(snap)
    consent_present = bool(automerge_label) and automerge_label.lower() in label_set

    action, reason = _consent_decision(
        snap, verdict=verdict, merge_state=ms, held=held, wip=wip,
        automerge_label=automerge_label, consent_present=consent_present,
    )
    return PRState(
        verdict=verdict,
        merge_state=ms,
        conflict=snap.mergeable is False,
        consent_present=consent_present,
        held=held,
        wip=wip,
        consent_action=action,
        reason=reason,
    )


def _consent_decision(
    snap: PRSnapshot,
    *,
    verdict: str,
    merge_state: str,
    held: tuple[str, ...],
    wip: bool,
    automerge_label: str,
    consent_present: bool,
) -> tuple[str, str]:
    """Decide what ``pr-merge`` should do with this PR (pure; see classify_state)."""
    if consent_present:
        return "already", f"{automerge_label} already present"
    if merge_state == "merged":
        return "skip", "already merged"
    if merge_state == "closed":
        return "skip", "closed without merging"
    if snap.draft:
        return "skip", "draft"
    if wip:
        return "skip", "WIP title prefix"
    if held:
        return "skip", f"hold label present: {', '.join(held)}"
    if merge_state == "conflict":
        return "skip", "not mergeable (conflict -> needs rebase)"
    if verdict == "CHANGES_REQUESTED":
        return "skip", "changes requested"
    if verdict != "APPROVED":
        return "skip", "not yet approved"
    if not automerge_label:
        # Approved + eligible, but the repo configured no auto-merge mechanism.
        # Not an error -- just nothing this command can apply.
        return "skip", "no auto-merge label configured (binding absent)"
    return "apply", "approved at current head"


__all__ = [
    "ALL_TRANSITIONS",
    "DEFAULT_UNTIL",
    "VERDICT_STATES",
    "Baseline",
    "PRSnapshot",
    "PRState",
    "Review",
    "classify_state",
    "compute_events",
    "effective_verdict",
    "merge_state",
    "title_is_wip",
]
