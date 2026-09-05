"""Provider-neutral PR event/verdict contract for the ``pr-*`` command family.

This is the single **pure** seam that ``pr-watch`` (transition events) and
``pr-status`` (glance verdict / conflict / merge state) both build on.  It
unifies the pure cores of the two multi-machine system tools -- ``tools/pr-watch`` (the
transition diff + cursor) and ``tools/pr-consent`` (the head-aware verdict
reduction + consent eligibility) -- into one place so the family speaks one
vocabulary regardless of provider.

Design constraints that keep it a *contract*, not an implementation:

- **No network.**  Every function is a pure transform of its inputs; the
  provider fetches a :class:`PRSnapshot` and hands it in.
- **No config import.**  The multi-machine system binding (auto-merge label, hold labels, WIP
  title prefixes) is passed in as explicit arguments, so this module never
  couples to ``config`` or a specific hosting service.  Binding-absent (empty
  arguments) degrades cleanly -- no holds, no WIP, verdict/mergeability still
  classify.
- **Stdlib only.**  No new dependency.

The heavier machinery -- polling a provider, the CLI surface, moving the
multi-machine system tools onto this seam -- lands in later phases of the
``pr-command-family`` effort.  Phase 1 ships only this contract + its tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

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
    "checks_failed",      # a required CI check rolled up to failure (#225)
    "approval_dismissed", # an approving review was dismissed (#225)
    "merged",             # the PR became merged
    "closed",             # the PR closed without merging
)

#: The actionable default: everything that needs the author's attention -- a
#: review by someone else, a merge-state change, a CI/approval regression, or
#: the PR landing/closing. Bare ``commented`` is excluded (noisy) but available
#: via ``any``.
DEFAULT_UNTIL = (
    "changes_requested", "approved", "conflict", "mergeable",
    "checks_failed", "approval_dismissed", "merged", "closed",
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
    updated_at: str = ""
    """Provider timestamp for the PR's latest mutation."""
    reviews: tuple[Review, ...] = ()
    author: str = ""             # the PR creator's login (its own reviews never fire)
    mergeable: bool | None = None
    """Provider mergeability flag: True (ready), False (conflict/blocked), or
    None when the provider hasn't computed it yet (some compute it async, so a
    just-opened PR can briefly report None)."""
    checks_state: str = ""
    """Provider-neutral CI rollup for the head commit (#225): ``"success"`` |
    ``"failure"`` | ``"pending"`` | ``""`` (unknown / no checks configured). A
    provider that doesn't report it leaves ``""`` -- which never fires a
    ``checks_failed`` transition, so the field is additive and safe."""
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
    checks_state: str = ""
    """The arm-time CI rollup a ``checks_failed`` transition diffs against (#225).
    ``""`` means "not yet known"; the wait loop adopts the first concrete value
    without firing (only a later flip *into* failure is a transition). Not
    encoded in the cursor (recomputed each poll)."""
    approved: bool | None = None
    """Whether the PR had an effective approval at arm time (#225). ``None`` means
    "not yet known"; the wait loop adopts the first concrete value without firing.
    A True->dismissed regression fires ``approval_dismissed``. Not encoded in the
    cursor."""

    @classmethod
    def from_snapshot(cls, snap: PRSnapshot) -> Baseline:
        return cls(
            max_review_id=snap.max_review_id,
            merged=snap.merged,
            closed=snap.pr_state == "closed",
            mergeable=snap.mergeable,
            checks_state=snap.checks_state,
            approved=(
                effective_verdict(snap.reviews, snap.head_sha, snap.author)
                == "approved"
            ),
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

        # CI checks regressed to failure (#225): fire only on a transition INTO
        # failure from a KNOWN non-failure state. An unknown baseline (``""`` ==
        # not yet known, e.g. a cursor-only re-arm) is adopted by the caller
        # without firing -- so an already-failed check at arm time does not alert
        # ("changes from here on"), exactly like the ``None`` mergeable baseline.
        if (
            baseline.checks_state not in ("", "failure")
            and snap.checks_state == "failure"
            and "checks_failed" in want
        ):
            events.append({"event": "checks_failed", "checks_state": snap.checks_state})

        # An approving review was DISMISSED (#225): the approval regressed and a
        # dismissed approval is present. Distinct from a fresh changes-requested
        # review (which fires ``changes_requested`` via the review-id loop above,
        # and leaves no dismissed approval), so the two never double-fire.
        if baseline.approved is True and "approval_dismissed" in want:
            snap_approved = (
                effective_verdict(snap.reviews, snap.head_sha, snap.author)
                == "approved"
            )
            dismissed_approval = any(
                r.dismissed and r.state.upper() == "APPROVED" for r in snap.reviews
            )
            if not snap_approved and dismissed_approval:
                events.append({"event": "approval_dismissed"})

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
    reviews: Iterable[Review],
    head_sha: str,
    author: str,
    *,
    allow_stale_approval: bool = False,
    stale_approval_head_sha: str = "",
    stale_approval_head_observed_at: str = "",
) -> str:
    """Reduce a PR's reviews to one effective verdict at ``head_sha``.

    Considers only *submitted*, non-comment, non-dismissed reviews that are not
    the PR author's own.  The latest such review (by id) wins.  An ``APPROVED``
    review normally counts only if it was submitted against the current head.
    When ``allow_stale_approval`` is true, a stale approval remains effective
    only when provider-clock evidence proves the exact live head was observed
    before the approval was submitted. Every plugin-mediated push clears and
    reacquires that evidence, so a later mediated push cannot inherit an older
    approval. Callers still expose staleness through :class:`PRState`.

    Returns ``"APPROVED"``, ``"CHANGES_REQUESTED"``, or ``""`` (no verdict).
    """
    latest = _latest_verdict(reviews, author)
    verdict = latest.state if latest is not None else ""
    latest_commit = latest.commit_id if latest is not None else ""
    if (
        verdict == "APPROVED"
        and head_sha
        and latest_commit
        and latest_commit != head_sha
        and not _stale_approval_is_authoritative(
            latest,
            head_sha=head_sha,
            allow_stale_approval=allow_stale_approval,
            stale_approval_head_sha=stale_approval_head_sha,
            stale_approval_head_observed_at=stale_approval_head_observed_at,
        )
    ):
        return ""
    return verdict


def _latest_verdict(reviews: Iterable[Review], author: str) -> Review | None:
    """Return the latest actionable review."""
    latest_id = -1
    latest: Review | None = None
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
            latest = Review(
                id=r.id,
                state=state,
                user=r.user,
                submitted_at=r.submitted_at,
                commit_id=r.commit_id,
                dismissed=r.dismissed,
            )
    return latest


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _stale_approval_is_authoritative(
    review: Review | None,
    *,
    head_sha: str,
    allow_stale_approval: bool,
    stale_approval_head_sha: str,
    stale_approval_head_observed_at: str,
) -> bool:
    """Prove the observed head stayed current through approval submission."""
    if review is None or not allow_stale_approval:
        return False
    if not head_sha or stale_approval_head_sha != head_sha:
        return False
    submitted = _parse_timestamp(review.submitted_at)
    observed = _parse_timestamp(stale_approval_head_observed_at)
    if submitted is not None:
        submitted = submitted.replace(microsecond=0)
    if observed is not None:
        observed = observed.replace(microsecond=0)
    return bool(
        submitted is not None
        and observed is not None
        and observed < submitted
    )


def title_is_wip(title: str, wip_title_prefixes: Iterable[str]) -> bool:
    """True when ``title`` starts with any configured WIP prefix (case-insensitive).

    With no prefixes configured this is always False (binding-absent = no-op).
    """
    t = (title or "").strip().lower()
    return any(t.startswith(p.strip().lower()) for p in wip_title_prefixes if p.strip())


#: Canonical prefix used when *marking* a PR draft. Gitea (<= 1.26, the multi-machine system
#: server) has no native ``draft`` boolean -- a WIP title prefix IS its native
#: draft mechanism, and the API's ``draft`` field is derived from it. ``WIP:`` is
#: Gitea's default ``WORK_IN_PROGRESS_PREFIXES`` entry, so the server recognises
#: it regardless of the local ``wip_title_prefixes`` binding.
DEFAULT_WIP_PREFIX = "WIP:"

#: WIP prefixes always recognised in addition to the repo binding, so stripping /
#: detection works even against a title marked by a different tool or the server
#: default. Kept lowercase for case-insensitive comparison.
_BUILTIN_WIP_PREFIXES = ("wip:", "wip ", "[wip]", "draft:", "[draft]")

#: The prefixes the Gitea server actually treats as draft (its default
#: ``WORK_IN_PROGRESS_PREFIXES`` = ``WIP:,[WIP]``). ``ensure_wip_title`` must
#: guarantee **one of these** -- not merely any binding/builtin variant like
#: ``Draft:`` -- or the server won't mark the PR draft even though the title
#: "looks" WIP. Kept lowercase for case-insensitive comparison.
_SERVER_NATIVE_WIP_PREFIXES = ("wip:", "[wip]")


def ensure_wip_title(title: str, wip_title_prefixes: Iterable[str] = ()) -> str:
    """Return ``title`` guaranteed to carry a *server-recognised* WIP prefix.

    Used by ``create_pr --draft`` to open a native Gitea draft (a WIP-prefixed
    title). Idempotent only against the prefixes the **server** treats as draft
    (``WIP:`` / ``[WIP]``): a title already carrying a non-native marker (e.g.
    ``Draft:``) is NOT a Gitea draft, so the canonical ``WIP:`` is still
    prepended to guarantee the PR actually opens draft. ``wip_title_prefixes`` is
    accepted for signature symmetry with :func:`strip_wip_title` but is not used
    for the idempotency check -- a binding variant the server ignores must not
    suppress the canonical prefix.
    """
    if title_is_wip(title, _SERVER_NATIVE_WIP_PREFIXES):
        return title or ""
    return f"{DEFAULT_WIP_PREFIX} {(title or '').strip()}".strip()


def strip_wip_title(
    title: str, wip_title_prefixes: Iterable[str] = ()
) -> tuple[str, bool]:
    """Strip **all** leading WIP prefixes from ``title`` (fully un-draft it).

    Returns ``(clean_title, was_wip)``. ``was_wip`` is False when the title
    carried no recognised WIP prefix, so the caller can refuse to "un-draft" a PR
    that is not a draft rather than silently no-op. Strips repeatedly so a
    doubly-marked title (e.g. ``"WIP: [WIP] x"``) is left with **no** recognised
    prefix -- otherwise the server would still see it as draft after a "success".
    """
    prefixes = sorted(
        # Preserve each prefix verbatim (do NOT strip): the binding's bare-word
        # marker is spelled ``"wip "`` *with* a trailing space precisely so it
        # matches a word boundary and not a longer word ("wips", "wiped"). Only
        # drop entries that are empty/whitespace-only and dedupe.
        {p for p in (*tuple(wip_title_prefixes), *_BUILTIN_WIP_PREFIXES) if p.strip()},
        key=len,
        reverse=True,  # longest match first so "[wip]" wins over a bare "wip "
    )
    current = (title or "").lstrip()
    stripped_any = False
    while True:
        low = current.lower()
        for p in prefixes:
            if low.startswith(p.lower()):
                current = current[len(p):].lstrip(" :\t")
                stripped_any = True
                break
        else:
            break
    return current.strip(), stripped_any


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
    approval_stale: bool  # latest approval targets an older head
    approval_stale_authorized: bool  # live head predates stale approval
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
    allow_stale_approval: bool = False,
    stale_approval_head_sha: str = "",
    stale_approval_head_observed_at: str = "",
) -> PRState:
    """Map a provider snapshot onto the unified :class:`PRState`.

    The one classifier the family shares.  The multi-machine system binding
    (``automerge_label`` / ``hold_labels`` / ``wip_title_prefixes``) is passed
    in; with everything empty it degrades cleanly -- no holds, no WIP, and
    ``consent_action`` still reflects the verdict + mergeability (it just reports
    that no auto-merge label is configured rather than proposing to apply one).

    "Consent" is the *concept* (has the author authorized the merge?);
    ``automerge_label`` is the concrete label that expresses it (multi-machine system value:
    ``auto-merge``; think ADO's "auto-complete").

    ``consent_action`` mirrors the multi-machine system ``pr-consent`` eligibility rules:

    - ``already`` -- the auto-merge label is already present (nothing to do).
    - ``apply``   -- open, not draft/WIP, no hold, mergeable, approved at head,
                     and an auto-merge label is configured but not yet present.
    - ``skip``    -- any blocking condition, with ``reason`` naming it.
    """
    label_set = {lbl.lower() for lbl in snap.labels}
    hold_set = {h.strip().lower() for h in hold_labels if h.strip()}
    held = tuple(sorted(label_set & hold_set))
    wip = snap.draft or title_is_wip(snap.title, wip_title_prefixes)
    latest = _latest_verdict(snap.reviews, snap.author)
    latest_verdict = latest.state if latest is not None else ""
    latest_commit = latest.commit_id if latest is not None else ""
    approval_stale = bool(
        latest_verdict == "APPROVED"
        and snap.head_sha
        and latest_commit
        and latest_commit != snap.head_sha
    )
    approval_stale_authorized = bool(
        approval_stale
        and _stale_approval_is_authoritative(
            latest,
            head_sha=snap.head_sha,
            allow_stale_approval=allow_stale_approval,
            stale_approval_head_sha=stale_approval_head_sha,
            stale_approval_head_observed_at=stale_approval_head_observed_at,
        )
    )
    verdict = effective_verdict(
        snap.reviews,
        snap.head_sha,
        snap.author,
        allow_stale_approval=allow_stale_approval,
        stale_approval_head_sha=stale_approval_head_sha,
        stale_approval_head_observed_at=stale_approval_head_observed_at,
    )
    ms = merge_state(snap)
    consent_present = bool(automerge_label) and automerge_label.lower() in label_set

    action, reason = _consent_decision(
        snap, verdict=verdict, merge_state=ms, held=held, wip=wip,
        automerge_label=automerge_label, consent_present=consent_present,
        approval_required=approval_required,
        approval_stale=approval_stale,
        approval_stale_authorized=approval_stale_authorized,
    )
    return PRState(
        verdict=verdict,
        approval_stale=approval_stale,
        approval_stale_authorized=approval_stale_authorized,
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
    approval_stale: bool = False,
    approval_stale_authorized: bool = False,
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
        if approval_stale and approval_stale_authorized:
            return "apply", "stale approval submitted after current head was published"
        return "apply", "approved at current head"
    return "apply", "eligible (no changes requested; approval not required)"


def merge_readiness(
    snap: PRSnapshot,
    *,
    automerge_label: str = "",
    hold_labels: Iterable[str] = (),
    wip_title_prefixes: Iterable[str] = (),
    approval_required: bool = True,
    allow_stale_approval: bool = False,
    stale_approval_head_sha: str = "",
    stale_approval_head_observed_at: str = "",
) -> dict:
    """A caller-facing "what stands between this PR and merge" summary.

    Runs the shared :func:`classify_state` and renders its verdict/consent
    vocabulary into a JSON-able dict -- so ``pr-watch`` can tell a woken caller
    *what to do next* rather than only *that a review landed*. Beyond the raw
    classification it adds two action booleans the caller keys off directly:

    - ``needs_consent`` -- the PR is approved (or approval-optional) and
      otherwise unblocked, but the merge-consent label is not yet applied
      (``consent_action == "apply"``). The caller must **grant consent** (e.g.
      add the auto-merge label) for the PR to merge; it will NOT merge on its
      own. This is the signal a bare ``approved`` transition failed to convey.
    - ``clear_to_merge`` -- nothing but consent stands between the PR and a
      merge: consent is already present, or a single consent action away
      (``consent_action`` in ``{"apply", "already"}``).

    Binding-absent (no ``automerge_label`` configured) degrades cleanly:
    ``needs_consent`` / ``clear_to_merge`` are False and ``reason`` says so --
    a repo whose merges are human-driven simply reports the verdict + merge
    state with no consent action to take.
    """
    st = classify_state(
        snap,
        automerge_label=automerge_label,
        hold_labels=hold_labels,
        wip_title_prefixes=wip_title_prefixes,
        approval_required=approval_required,
        allow_stale_approval=allow_stale_approval,
        stale_approval_head_sha=stale_approval_head_sha,
        stale_approval_head_observed_at=stale_approval_head_observed_at,
    )
    return {
        "verdict": st.verdict,
        "approval_stale": st.approval_stale,
        "approval_stale_authorized": st.approval_stale_authorized,
        "merge_state": st.merge_state,
        "conflict": st.conflict,
        "mergeable": snap.mergeable,
        "consent_present": st.consent_present,
        "consent_action": st.consent_action,
        "consent_label": automerge_label,
        "eligible": st.eligible,
        "needs_consent": st.consent_action == "apply",
        "clear_to_merge": st.consent_action in ("apply", "already"),
        "held": list(st.held),
        "wip": st.wip,
        "reason": st.reason,
    }


__all__ = [
    "ALL_TRANSITIONS",
    "DEFAULT_UNTIL",
    "DEFAULT_WIP_PREFIX",
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
    "ensure_wip_title",
    "merge_readiness",
    "merge_state",
    "strip_wip_title",
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
PROFILE_PR_SELF_MERGE = "pr-self-merge"    # PR-gated, submitter merges directly

#: Every pr-* author verb, for describing applicability.
_ALL_PR_VERBS = ("create-pr", "pr-watch", "pr-status", "pr-merge", "pr-complete")


@dataclass(frozen=True)
class PRFlowProfile:
    """How a repo lands work, derived from its PR config -- the answer to
    "which flow does *this* repo use, and do the pr-* verbs apply here?"

    Not a per-PR classification (that is :class:`PRState`); a per-*repo* one.
    Agents should read this **before** driving a PR so they pick the right flow
    for the target repo instead of assuming the local multi-machine system's shape.

    - ``profile``       -- one of the ``PROFILE_*`` tokens.
    - ``requires_pr``   -- direct-to-default-branch is refused (``pr.required``).
    - ``merge_mode``    -- who lands it: ``"direct"`` | ``"human"`` |
      ``"agent-consent"`` | ``"self-direct"``.
    - ``applicable_verbs`` -- pr-* verbs that apply to this repo.
    - ``summary``       -- one-line human description of the flow.

    Legibility matrix (drives :func:`pr_reminder`):

    - ``reviewer`` / ``review_blocking`` / ``review_latency_hint`` -- who
      reviews, whether it gates the merge, and roughly how long it takes.
    - ``self_approve`` -- legacy provider capability used to select the
      submitter-direct profile; it never tells a GitHub author to cast a review.
    - ``conflict_retriggers_review`` -- a post-approval rebase+push re-reviews.
    - ``rebase_owner`` -- who keeps the PR mergeable (``"submitter"`` / ``""``).
    """

    profile: str
    requires_pr: bool
    merge_mode: str
    provider: str
    automerge_label: str
    applicable_verbs: tuple[str, ...]
    summary: str
    reviewer: str = ""
    review_blocking: bool = False
    review_latency_hint: str = ""
    self_approve: bool = False
    conflict_retriggers_review: bool = True
    rebase_owner: str = "submitter"
    # Merge/update policy (repo-overridable; #225) -- reported by every pr-*
    # surface so an agent knows THIS repo's update/merge defaults at the point
    # of action.
    branch_update_strategy: str = "rebase"   # rebase | merge
    merge_strategy: str = "squash"           # squash | merge | rebase
    prefer_auto_merge: bool = True

    def applies(self, verb: str) -> bool:
        """True when ``verb`` (e.g. ``"pr-merge"``) is part of this repo's flow."""
        return verb in self.applicable_verbs


def classify_pr_flow(
    *,
    enabled: bool,
    required: bool = False,
    provider: str = "",
    automerge_label: str = "",
    reviewer: str = "",
    review_blocking: bool = False,
    review_latency_hint: str = "",
    self_approve: bool = False,
    merge_actor: str = "",
    conflict_retriggers_review: bool = True,
    branch_update_strategy: str = "rebase",
    merge_strategy: str = "squash",
    prefer_auto_merge: bool = True,
) -> PRFlowProfile:
    """Derive a repo's :class:`PRFlowProfile` from its PR config values.

    Three shapes, distinguished only by config (no network, no provider call):

    - **direct** (``pr.enabled`` false): no PR flow. ``finalize`` lands the
      worktree to the default branch; the pr-* verbs do not apply.
    - **pr-agent-merge** (enabled + an ``automerge_label`` is bound): the author
      signals **merge consent** with that label after approval, and the review
      gate merges. The full pr-* family applies -- this is the multi-machine system's own
      auto-review + auto-merge shape.
    - **pr-human-merge** (enabled but **no** ``automerge_label``): PR-gated, but
      the agent has no consent/merge mechanism -- a **human** approves and
      merges. ``create-pr`` / ``pr-watch`` / ``pr-status`` / ``pr-complete``
      apply; **``pr-merge`` does not** (there is no consent label to apply).

    The absence of ``automerge_label`` is the human-merge signal *by design*.
    The one ambiguity a caller must resolve out-of-band: an ``enabled`` repo
    that *should* have an ``automerge_label`` but is missing it because the
    checkout's anchor is stale looks identical to a genuine human-merge repo.
    Callers that expect agent-merge (e.g. the multi-machine system) should confirm the
    anchor is current before treating an empty label as "human-merge".

    A fourth shape, **pr-self-merge**, sits between agent-consent and
    human-merge: PR-required, but the **submitter performs the merge directly**
    (no consent label) -- selected by ``self_approve`` or
    ``merge_actor == "submitter-direct"``. Review approval remains governed by
    the provider/repository policy; in particular, GitHub authors cannot approve
    their own PRs. The full pr-* family applies (``pr-merge --now`` performs the
    mediated direct merge once required checks/reviews permit it).
    """
    _matrix = dict(
        reviewer=reviewer,
        review_blocking=review_blocking,
        review_latency_hint=review_latency_hint,
        self_approve=self_approve,
        conflict_retriggers_review=conflict_retriggers_review,
        rebase_owner="submitter",
        branch_update_strategy=branch_update_strategy,
        merge_strategy=merge_strategy,
        prefer_auto_merge=prefer_auto_merge,
    )
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
            reviewer="",
            review_blocking=False,
            review_latency_hint=review_latency_hint,
            self_approve=False,
            conflict_retriggers_review=False,
            rebase_owner="",
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
            **_matrix,
        )
    if self_approve or merge_actor == "submitter-direct":
        return PRFlowProfile(
            profile=PROFILE_PR_SELF_MERGE,
            requires_pr=required,
            merge_mode="self-direct",
            provider=provider,
            automerge_label="",
            applicable_verbs=_ALL_PR_VERBS,
            summary=(
                f"PR-gated ({provider or 'provider'}); the submitter "
                f"merges directly (`pr-merge <#> --now`) once required "
                f"checks/reviews permit it. Full pr-* family applies."
            ),
            **_matrix,
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
        **_matrix,
    )


# ---------------------------------------------------------------------------
# PR-flow reminders -- state-aware "rules + next step" guidance per repo
# ---------------------------------------------------------------------------
#
# A reminder is a **stay-on-the-rails** aid: every pr-* / push-changes verb, on
# BOTH its success and its error/refusal path, tells the calling agent where it
# is, which states are allowed next, and which *sanctioned agent-worktrees verb*
# to use next. It is deliberately a pure transform of a PRFlowProfile (+ the
# verb, the coarse PR state, and whether the command succeeded).
#
# HARD INVARIANT -- no bypass tactics. A reminder MUST NEVER steer an agent to a
# tool that goes around this flow: no raw provider CLI (`gh pr merge`, `gh api`,
# `az repos`), no force/override flags (`--force`, `--admin`, `--no-verify`), no
# direct REST calls. Even though an agent may have general permission to run
# those, the reminder's whole job is to keep work ON the reviewed rails, so it
# only ever names agent-worktrees verbs and the sanctioned flow. A refusal says
# what to do *within* the rules (wait, watch, let the reviewer merge), never how
# to skip them. This invariant is enforced by ``test_pr_contract`` (a token
# scan over every reminder's rendered text).

#: Coarse PR states a verb may know it is in (from a snapshot, or "" when
#: unknown). The reminder tailors "next" / "waiting on" to these.
PR_STATE_UNKNOWN = ""
PR_STATE_CREATED = "created"
PR_STATE_AWAITING_REVIEW = "awaiting-review"
PR_STATE_APPROVED = "approved"
PR_STATE_CHANGES_REQUESTED = "changes-requested"
PR_STATE_CONFLICT = "conflict"
PR_STATE_MERGED = "merged"


@dataclass(frozen=True)
class PRReminder:
    """A compact, state-aware reminder of a repo's PR rules + next step.

    Pure data derived from a :class:`PRFlowProfile` (+ the verb, the coarse PR
    state, and command outcome). Rendered to prose for stdio and to a dict for
    the ``--json`` ``reminder`` node, so a calling agent never has to remember
    the per-repo flow -- and is never nudged off the sanctioned rails.

    - ``headline``    -- one line: where you are (or what was refused).
    - ``next_step``   -- the recommended next action, a sanctioned verb.
    - ``waiting_on``  -- allowed states you may next be in / waiting for.
    - ``use_instead`` -- on a refusal, the sanctioned verb(s) to use instead.
    - ``cautions``    -- gotchas (a rebase re-triggers review, etc.).
    """

    profile: str
    verb: str
    state: str
    ok: bool
    headline: str
    next_step: str
    waiting_on: tuple[str, ...]
    use_instead: tuple[str, ...]
    cautions: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "verb": self.verb,
            "state": self.state,
            "ok": self.ok,
            "headline": self.headline,
            "next": self.next_step,
            "waiting_on": list(self.waiting_on),
            "use_instead": list(self.use_instead),
            "cautions": list(self.cautions),
        }

    def text(self) -> str:
        """Render a compact multi-line reminder for stdio."""
        tag = "Reminder" if self.ok else "Reminder (blocked)"
        lines = [f"{tag} [{self.profile}] {self.headline}"]
        if self.next_step:
            lines.append(f"  Next: {self.next_step}")
        if self.waiting_on:
            lines.append(f"  Allowed next: {', '.join(self.waiting_on)}")
        if self.use_instead:
            lines.append(f"  Use instead: {', '.join(self.use_instead)}")
        for c in self.cautions:
            lines.append(f"  Note: {c}")
        return "\n".join(lines)


def _review_phrase(flow: PRFlowProfile) -> str:
    """Human phrase for the repo's review, or '' when none is configured."""
    if not flow.reviewer:
        return ""
    latency = f" ({flow.review_latency_hint})" if flow.review_latency_hint else ""
    kind = "blocking" if flow.review_blocking else "non-blocking"
    return f"{flow.reviewer} review{latency}, {kind}"


def _merge_instruction(flow: PRFlowProfile) -> str:
    """The sanctioned way THIS repo merges -- always an agent-worktrees verb."""
    if flow.profile == PROFILE_PR_SELF_MERGE:
        return (
            "merge with `pr-merge <#> --now` once required checks/reviews "
            "allow it"
            + (
                " (GitHub authors cannot approve their own PRs)"
                if flow.provider.lower() == "github"
                else ""
            )
        )
    if flow.profile == PROFILE_PR_AGENT_MERGE:
        label = flow.automerge_label or "the consent label"
        return (f"after an approval, consent with `pr-merge <#>` (applies "
                f"'{label}'); the review gate merges")
    if flow.profile == PROFILE_PR_HUMAN_MERGE:
        who = flow.reviewer or "a reviewer"
        return f"{who} approves and merges -- you do not merge here"
    return "finalize lands the work (no PR)"


def _policy_phrase(flow: PRFlowProfile) -> str:
    """One-line update/merge policy for this repo (#225), or '' for direct."""
    if flow.profile == PROFILE_DIRECT:
        return ""
    merge = f"merge via {flow.merge_strategy}"
    if flow.prefer_auto_merge:
        merge += " (prefer CI-gated auto-merge)"
    return f"policy: update branch via {flow.branch_update_strategy}, {merge}"


def _cautions(flow: PRFlowProfile) -> tuple[str, ...]:
    out: list[str] = []
    if flow.profile != PROFILE_DIRECT and flow.conflict_retriggers_review:
        out.append("a post-approval rebase + `push-changes` re-triggers review")
    if flow.rebase_owner:
        out.append(f"{flow.rebase_owner} owns rebase + keeping the PR mergeable")
    policy = _policy_phrase(flow)
    if policy:
        out.append(policy)
    return tuple(out)


def pr_reminder(
    flow: PRFlowProfile,
    verb: str,
    state: str = "",
    *,
    ok: bool = True,
    reason: str = "",
) -> PRReminder:
    """Build a state-aware reminder for ``verb`` under this repo's ``flow``.

    ``ok=False`` marks an error/refusal path: the reminder then leads with the
    blockage and fills ``use_instead`` with the sanctioned verb(s) to use --
    never a bypass (see the module HARD INVARIANT).
    """
    review = _review_phrase(flow)
    merge = _merge_instruction(flow)
    cautions = _cautions(flow)

    # Direct-push repo: no PR ceremony at all.
    if flow.profile == PROFILE_DIRECT:
        # Any PR-landing verb (including ``create-pr`` / ``pr-create``, which do
        # NOT start with "pr") should point at the sanctioned ``finalize``.
        _pr_verb = verb.startswith("pr") or verb in ("create-pr", "pr-create")
        return PRReminder(
            profile=flow.profile, verb=verb, state=state, ok=ok,
            headline="direct-push repo -- no PR flow",
            next_step="`finalize` lands the worktree to the default branch",
            waiting_on=(), use_instead=(("finalize",) if _pr_verb else ()),
            cautions=(),
        )

    # ---- error / refusal path (both outcomes are reminded) ----------------
    if not ok:
        headline = reason or f"{verb} does not apply to this repo"
        if verb == "pr-merge" and flow.profile == PROFILE_PR_HUMAN_MERGE:
            return PRReminder(
                flow.profile, verb, state, ok,
                headline=(reason or "you cannot merge in this repo"),
                next_step=merge,
                waiting_on=("approved", "merged"),
                use_instead=("pr-watch", "pr-status"),
                cautions=cautions,
            )
        if verb == "pr-merge" and flow.profile == PROFILE_PR_AGENT_MERGE:
            return PRReminder(
                flow.profile, verb, state, ok,
                headline=(reason or "not eligible for consent yet"),
                next_step=merge,
                waiting_on=("approved", "merged"),
                use_instead=("pr-watch", "pr-status"),
                cautions=cautions,
            )
        if verb == "pr-merge" and flow.profile == PROFILE_PR_SELF_MERGE:
            # Submitter-self-merge repo: `pr-merge` (no `--now`) is a no-op --
            # point at the sanctioned direct-merge verb, not a bypass.
            return PRReminder(
                flow.profile, verb, state, ok,
                headline=(reason or "this repo merges directly (self-merge)"),
                next_step=merge,
                waiting_on=(
                    ("approved", "merged")
                    if flow.review_blocking
                    else ("merged",)
                ),
                use_instead=(
                    ("pr-watch", "pr-status")
                    if flow.review_blocking
                    else ("pr-merge --now",)
                ),
                cautions=cautions,
            )
        return PRReminder(
            flow.profile, verb, state, ok,
            headline=headline, next_step=merge,
            waiting_on=(), use_instead=("pr-status", "pr-watch"),
            cautions=cautions,
        )

    # ---- success path -----------------------------------------------------
    if verb in ("create-pr", "pr-create"):
        nxt = merge if not review else f"wait for {review}, then {merge}"
        c = cautions
        if (
            flow.reviewer
            and (
                flow.review_blocking
                or flow.profile != PROFILE_PR_SELF_MERGE
            )
        ):
            # #3581 nudge: review is triggered by the repo's own process
            # (webhook / auto-assign). Actually *requesting* a reviewer is
            # org- and platform-specific, so it is left to manual action --
            # surface that so an agent whose PR sits unreviewed knows the next
            # step is a manual reviewer request, not a tooling gap.
            c = c + (
                f"{flow.reviewer} review is triggered by this repo's own "
                "process; if none appears, requesting a reviewer is a manual, "
                "org/platform-specific step (not automated here) -- follow the "
                "repo's contribution process",
            )
        return PRReminder(
            flow.profile, verb, state or PR_STATE_CREATED, ok,
            headline="PR created",
            next_step=nxt,
            waiting_on=(review,) if review else (),
            use_instead=(),
            cautions=c,
        )

    if verb == "pr-watch":
        wait = []
        if review:
            wait.append(review)
        wait.append("approval" if (flow.review_blocking
                                   or flow.profile == PROFILE_PR_HUMAN_MERGE)
                    else "merge")
        wait.append("conflict")
        # #225: pr-watch also wakes on CI/approval regressions, so name them as
        # states the caller may next be alerted to.
        wait.extend(("checks_failed", "approval_dismissed"))
        return PRReminder(
            flow.profile, verb, state, ok,
            headline="watching the PR",
            next_step=merge,
            waiting_on=tuple(w for w in wait if w),
            use_instead=(),
            cautions=cautions,
        )

    if verb == "pr-merge":
        if state == PR_STATE_MERGED:
            # The direct self-merge just landed -- the only sanctioned next step
            # is cleaning up the worktree.
            return PRReminder(
                flow.profile, verb, state, ok,
                headline="merged",
                next_step="`finalize` cleans up the worktree now that the PR is merged",
                waiting_on=(),
                use_instead=(),
                cautions=cautions,
            )
        return PRReminder(
            flow.profile, verb, state, ok,
            headline="merge step",
            next_step=merge,
            waiting_on=(
                ("approved", "merged")
                if flow.review_blocking
                else ("merged",)
            ),
            use_instead=(),
            cautions=cautions,
        )

    if verb == "push-changes":
        return PRReminder(
            flow.profile, verb, state, ok,
            headline="pushed to the PR branch",
            next_step=(f"wait for {review}; then {merge}" if review else merge),
            waiting_on=(review,) if review else (),
            use_instead=(),
            cautions=cautions,
        )

    # Generic (pr-status / pr-complete / unknown verb).
    return PRReminder(
        flow.profile, verb, state, ok,
        headline=flow.summary,
        next_step=merge,
        waiting_on=(review,) if review else (),
        use_instead=(),
        cautions=cautions,
    )


# ---------------------------------------------------------------------------
# Adopt-time research: read a repo's ACTUAL provider settings, then derive the
# policy matrix to match (#225). The read lives in the provider; the mapping
# from live settings -> config policy is pure and provider-neutral, here.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepoPolicy:
    """A repo's live PR-relevant provider settings (read at adopt time).

    Every field is tri-state (``None`` == the provider couldn't determine it),
    so a partial read still derives what it can. ``supported`` is False when the
    provider can't read settings at all; ``error`` carries the reason.
    """

    supported: bool = True
    error: str = ""
    allow_squash: bool | None = None
    allow_merge_commit: bool | None = None
    allow_rebase: bool | None = None
    allow_auto_merge: bool | None = None
    delete_branch_on_merge: bool | None = None
    required_approving_reviews: int | None = None
    has_required_status_checks: bool | None = None


def derive_policy_matrix(policy: RepoPolicy) -> dict:
    """Map live :class:`RepoPolicy` settings onto the config policy matrix (#225).

    Pure: turns "what the provider actually allows" into the repo-overridable
    ``pr:`` policy keys, so a registered repo's config mirrors reality instead of
    a guess. Only keys the settings *speak to* are emitted; an unknown setting is
    omitted (the config default applies). Returns a plain dict ready to drop into
    the ``pr:`` block.
    """
    out: dict = {}
    if not policy.supported:
        return out

    # merge_strategy: honor the repo's allowed methods, preferring squash.
    if policy.allow_squash is not None or policy.allow_merge_commit is not None \
            or policy.allow_rebase is not None:
        if policy.allow_squash:
            out["merge_strategy"] = "squash"
        elif policy.allow_merge_commit:
            out["merge_strategy"] = "merge"
        elif policy.allow_rebase:
            out["merge_strategy"] = "rebase"
        # (all three false/unknown -> leave the default)

    # branch_update_strategy: rebase unless the repo forbids rebase merges but
    # allows merge commits -- a signal it prefers merge-based history.
    if policy.allow_rebase is False and policy.allow_merge_commit:
        out["branch_update_strategy"] = "merge"

    # prefer_auto_merge: mirror whether the provider offers native auto-merge.
    if policy.allow_auto_merge is not None:
        out["prefer_auto_merge"] = bool(policy.allow_auto_merge)

    # review_blocking: a required approving review OR a required status check
    # means the review/CI genuinely gates the merge.
    blocking_signals = []
    if policy.required_approving_reviews is not None:
        blocking_signals.append(policy.required_approving_reviews > 0)
    if policy.has_required_status_checks is not None:
        blocking_signals.append(bool(policy.has_required_status_checks))
    if blocking_signals:
        out["review_blocking"] = any(blocking_signals)

    return out


__all__ += ["RepoPolicy", "derive_policy_matrix"]
