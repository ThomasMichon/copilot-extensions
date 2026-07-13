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
class Comment:
    """One comment inside a review thread (system/automation notes filtered out)."""

    author: str = ""
    content: str = ""


@dataclass(frozen=True)
class CommentThread:
    """A review discussion thread on a PR, normalized across providers.

    ``status`` follows a small provider-neutral vocabulary -- ``active`` /
    ``pending`` (or empty) is *unresolved*; anything else (``fixed`` / ``closed``
    / ``wontfix`` / ``bydesign`` / ``resolved`` / ``outdated``) is resolved.
    Comment-threading is first-class in the contract so every provider speaks it
    (Azure DevOps maps it cleanly; GitHub/Gitea carry more-irritating details).
    """

    id: int | None = None
    status: str = ""
    file_path: str = ""
    comments: tuple[Comment, ...] = ()

    @property
    def is_active(self) -> bool:
        """True when the thread is still unresolved (needs the author's attention)."""
        return (self.status or "").strip().lower() in ("", "active", "pending")


@dataclass(frozen=True)
class ThreadsResult:
    """Comment threads on a PR, plus whether the provider could report them.

    ``supported`` is False (with ``error`` explaining) when a provider cannot
    read threads -- callers treat that as "no thread signal", never as "no open
    feedback".
    """

    threads: tuple[CommentThread, ...] = ()
    supported: bool = True
    error: str = ""

    @property
    def active(self) -> tuple[CommentThread, ...]:
        """Unresolved threads (the ones a merge gate / feedback loop cares about)."""
        return tuple(t for t in self.threads if t.is_active)


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
    approval_required: bool = True,
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
        approval_required=approval_required,
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
    approval_required: bool = True,
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
        if approval_required:
            return "skip", "not yet approved"
        # Approval-optional repo (self-complete: we own the merge). No blocking
        # verdict and no changes requested -> eligible without an approval vote.
    if not automerge_label:
        # Eligible, but the repo configured no auto-merge/auto-complete mechanism.
        # Not an error -- just nothing this command can apply.
        return "skip", "no auto-merge label configured (binding absent)"
    if verdict == "APPROVED":
        return "apply", "approved at current head"
    return "apply", "eligible (no changes requested; approval not required)"


__all__ = [
    "ALL_TRANSITIONS",
    "DEFAULT_UNTIL",
    "VERDICT_STATES",
    "Baseline",
    "Comment",
    "CommentThread",
    "PRFlowProfile",
    "PRSnapshot",
    "PRState",
    "Review",
    "ThreadsResult",
    "classify_pr_flow",
    "classify_state",
    "compute_events",
    "effective_verdict",
    "merge_state",
    "title_is_wip",
]


# ---------------------------------------------------------------------------
# PR-flow profile -- which flow a repo's config selects, and which pr-* verbs
# apply to it. Pure: derived from config *values* (never imports config), so it
# stays provider-generic and network-free like the rest of this contract.
# ---------------------------------------------------------------------------

#: Canonical flow-profile tokens (stable; safe to switch on).
PROFILE_DIRECT = "direct"                  # no PR flow: land straight to default branch
PROFILE_PR_HUMAN_MERGE = "pr-human-merge"  # PR-gated, a human approves/merges
PROFILE_PR_AGENT_MERGE = "pr-agent-merge"  # PR-gated, author signals merge consent

#: Every pr-* author verb, for describing applicability.
_ALL_PR_VERBS = ("create-pr", "pr-watch", "pr-status", "pr-merge", "pr-complete")


@dataclass(frozen=True)
class PRFlowProfile:
    """How a repo lands work, derived from its PR config -- the answer to
    "which flow does *this* repo use, and do the pr-* verbs apply here?"

    Not a per-PR classification (that is :class:`PRState`); a per-*repo* one.
    Agents should read this **before** driving a PR so they pick the right flow
    for the target repo instead of assuming the local facility's shape.

    - ``profile``       -- one of the ``PROFILE_*`` tokens.
    - ``requires_pr``   -- direct-to-default-branch is refused (``pr.required``).
    - ``merge_mode``    -- who lands it: ``"direct"`` | ``"human"`` |
      ``"agent-consent"``.
    - ``applicable_verbs`` -- pr-* verbs that apply to this repo.
    - ``summary``       -- one-line human description of the flow.
    """

    profile: str
    requires_pr: bool
    merge_mode: str
    provider: str
    automerge_label: str
    applicable_verbs: tuple[str, ...]
    summary: str

    def applies(self, verb: str) -> bool:
        """True when ``verb`` (e.g. ``"pr-merge"``) is part of this repo's flow."""
        return verb in self.applicable_verbs


def classify_pr_flow(
    *,
    enabled: bool,
    required: bool = False,
    provider: str = "",
    automerge_label: str = "",
) -> PRFlowProfile:
    """Derive a repo's :class:`PRFlowProfile` from its PR config values.

    Three shapes, distinguished only by config (no network, no provider call):

    - **direct** (``pr.enabled`` false): no PR flow. ``finalize`` lands the
      worktree to the default branch; the pr-* verbs do not apply.
    - **pr-agent-merge** (enabled + an ``automerge_label`` is bound): the author
      signals **merge consent** with that label after approval, and the review
      gate merges. The full pr-* family applies -- this is the facility's own
      auto-review + auto-merge shape.
    - **pr-human-merge** (enabled but **no** ``automerge_label``): PR-gated, but
      the agent has no consent/merge mechanism -- a **human** approves and
      merges. ``create-pr`` / ``pr-watch`` / ``pr-status`` / ``pr-complete``
      apply; **``pr-merge`` does not** (there is no consent label to apply).

    The absence of ``automerge_label`` is the human-merge signal *by design*.
    The one ambiguity a caller must resolve out-of-band: an ``enabled`` repo
    that *should* have an ``automerge_label`` but is missing it because the
    checkout's anchor is stale looks identical to a genuine human-merge repo.
    Callers that expect agent-merge (e.g. the facility) should confirm the
    anchor is current before treating an empty label as "human-merge".
    """
    if not enabled:
        return PRFlowProfile(
            profile=PROFILE_DIRECT,
            requires_pr=False,
            merge_mode="direct",
            provider="",
            automerge_label="",
            applicable_verbs=(),
            summary=("Direct-push repo -- no PR flow; finalize lands the "
                     "worktree to the default branch."),
        )
    if automerge_label:
        return PRFlowProfile(
            profile=PROFILE_PR_AGENT_MERGE,
            requires_pr=required,
            merge_mode="agent-consent",
            provider=provider,
            automerge_label=automerge_label,
            applicable_verbs=_ALL_PR_VERBS,
            summary=(
                f"PR-gated ({provider or 'provider'}); the author signals merge "
                f"consent (label '{automerge_label}') after approval and the "
                f"review gate merges. Full pr-* family applies."
            ),
        )
    return PRFlowProfile(
        profile=PROFILE_PR_HUMAN_MERGE,
        requires_pr=required,
        merge_mode="human",
        provider=provider,
        automerge_label="",
        applicable_verbs=tuple(v for v in _ALL_PR_VERBS if v != "pr-merge"),
        summary=(
            f"PR-gated ({provider or 'provider'}); a human approves and merges "
            f"(no auto-merge consent label bound). Use create-pr / pr-watch / "
            f"pr-status / pr-complete; pr-merge does not apply here."
        ),
    )
