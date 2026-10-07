"""``pr bar`` -- a PR's merge bar as typed clauses, each with its own evidence.

A PR clears the bar when, **on one head**, every clause is ``met``:

- ``ci_green`` -- every check reported on the head passed (or was skipped/neutral);
- ``review_on_head`` -- the reviewer's (default: Copilot's) latest review is on the head;
- ``review_findings_zero`` -- that review reports no open and no previously missed
  findings, and doesn't disagree with the review threads;
- ``threads_unresolved_zero`` -- no review thread is unresolved;
- ``human_reviews_answered`` -- no human reviewer's latest verdict requests changes;
- ``mergeable`` -- the provider sees no merge conflict;
- ``merge_policy`` -- the repo's own merge policy, through the shared
  ``pr_contract.classify_state`` classifier: no hold label, not a draft or WIP
  title, no provider-level change request, and an approval on the head where the
  policy requires one. Without a supplied policy it is ``unknown``.

Each clause is ``met`` | ``pending`` (waiting on someone else) | ``failed`` (the
author has something to do) | ``unknown`` (it couldn't be read). The rules that
make it trustworthy rather than merely convenient:

- **Fail closed.** Every list is read to its last page; a page that can't be
  read makes the clauses built on it ``unknown`` -- never ``met`` -- and an empty
  check list is ``unknown``, not passing.
- **Same head.** The head is read before and after everything else; if it moved,
  every clause is ``unknown``: a verdict assembled across two heads describes
  neither.

The read is the provider's own (``PRProvider.get_bar_snapshot``; GitHub's lives in
``providers/github_bar.py``), so this command stays provider-agnostic, and a
provider without one yields :func:`unsupported` -- every clause ``unknown``. The
evaluation is a pure function of the snapshot (:func:`evaluate`), so the same
verdict can be replayed from recorded snapshots.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

#: The login GitHub gives Copilot's pull-request reviewer.
COPILOT_REVIEWER = "copilot-pull-request-reviewer"
#: Each clause's evidence is a short tail, never a dump.
EVIDENCE_CAP = 600
CLAUSES = ("ci_green", "review_on_head", "review_findings_zero",
           "threads_unresolved_zero", "human_reviews_answered", "mergeable", "merge_policy")
#: Exit codes of ``pr bar``.
EXIT = {"met": 0, "merged": 0, "pending": 10, "failed": 11, "unknown": 12}

_FAILED_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED",
                       "STARTUP_FAILURE", "STALE"}
_PASSED_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


@dataclass
class Clause:
    id: str
    status: str
    evidence: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        self.evidence = self.evidence[-EVIDENCE_CAP:]
        self.error = self.error[-EVIDENCE_CAP:]


@dataclass
class Snapshot:
    """What one read of a PR saw. ``errors`` names each part that couldn't be read
    (``pr``, ``checks``, ``reviews``, ``threads``) and why."""

    repo: str
    number: int
    state: str = ""            # OPEN | CLOSED | MERGED
    head: str = ""
    head_after: str = ""       # the head re-read after everything else
    changed: str = ""          # what else about the PR changed between the first and last read
    author: str = ""
    mergeable: str = ""        # MERGEABLE | CONFLICTING | UNKNOWN
    review_decision: str = ""  # the provider's aggregate (APPROVED | CHANGES_REQUESTED | ...), "" if none
    draft: bool = False
    title: str = ""
    labels: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)    # {name, status, conclusion}
    reviews: list[dict] = field(default_factory=list)   # {author, bot, state, commit, body, at}
    threads: list[dict] = field(default_factory=list)   # {resolved, outdated, path, author}
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Bar:
    repo: str
    number: int
    head: str
    state: str
    verdict: str
    clauses: list[Clause]
    observed_at: str

    def to_dict(self) -> dict:
        return {**asdict(self), "clauses": [asdict(c) for c in self.clauses]}


def _short(sha: str) -> str:
    return (sha or "?")[:9]


def _ci(snap: Snapshot) -> Clause:
    if "checks" in snap.errors:
        return Clause("ci_green", "unknown", error=snap.errors["checks"])
    if not snap.checks:
        return Clause("ci_green", "unknown", f"no checks reported on {_short(snap.head)}")
    failed, pending = [], []
    for c in snap.checks:
        conclusion, status = (c.get("conclusion") or "").upper(), (c.get("status") or "").upper()
        if conclusion in _FAILED_CONCLUSIONS or status in ("FAILURE", "ERROR"):
            failed.append(c.get("name") or "?")
        elif conclusion not in _PASSED_CONCLUSIONS and status != "SUCCESS":
            pending.append(c.get("name") or "?")
    if failed:
        return Clause("ci_green", "failed", f"failing on {_short(snap.head)}: {', '.join(failed)}")
    if pending:
        return Clause("ci_green", "pending", f"pending on {_short(snap.head)}: {', '.join(pending)}")
    return Clause("ci_green", "met", f"{len(snap.checks)} checks passed on {_short(snap.head)}")


def _latest_by(snap: Snapshot, login: str) -> dict | None:
    mine = [r for r in snap.reviews if (r.get("author") or "").lower() == login.lower()]
    return max(mine, key=lambda r: r.get("at") or "") if mine else None


def _review_on_head(snap: Snapshot, reviewer: str) -> Clause:
    if "reviews" in snap.errors:
        return Clause("review_on_head", "unknown", error=snap.errors["reviews"])
    latest = _latest_by(snap, reviewer)
    if latest is None:
        return Clause("review_on_head", "pending", f"no review by {reviewer} yet")
    if latest.get("commit") != snap.head:
        return Clause("review_on_head", "pending",
                      f"{reviewer}'s latest review ({latest.get('at')}) is on "
                      f"{_short(latest.get('commit', ''))}; the head is {_short(snap.head)}")
    return Clause("review_on_head", "met", f"{reviewer} reviewed {_short(snap.head)} at {latest.get('at')}")


def finding_counts(body: str) -> tuple[int, int] | None:
    """``(open, previously_missed)`` from a Copilot review summary, or ``None``
    when the body doesn't state its open findings."""
    text = re.sub(r"<[^>]+>", " ", body or "")
    found = re.search(r"(\d+)\s+open\s+findings?", text, re.IGNORECASE)
    if not found:
        return None
    missed = re.search(r"previously\s+missed\s*\((\d+)\)", text, re.IGNORECASE)
    return int(found.group(1)), int(missed.group(1)) if missed else 0


def _findings(snap: Snapshot, reviewer: str) -> Clause:
    if "reviews" in snap.errors or "threads" in snap.errors:
        return Clause("review_findings_zero", "unknown",
                      error=snap.errors.get("reviews") or snap.errors.get("threads", ""))
    latest = _latest_by(snap, reviewer)
    if latest is None or latest.get("commit") != snap.head:
        return Clause("review_findings_zero", "pending", f"waiting for {reviewer}'s review of {_short(snap.head)}")
    counts = finding_counts(latest.get("body", ""))
    if counts is None:
        return Clause("review_findings_zero", "unknown",
                      f"{reviewer}'s review of {_short(snap.head)} states no open-finding count")
    open_count, missed = counts
    reviewer_open = sum(1 for t in snap.threads
                        if _open(t) and (t.get("author") or "").lower() == reviewer.lower())
    if open_count != reviewer_open:
        return Clause("review_findings_zero", "unknown",
                      f"the summary reports {open_count} open findings but {reviewer_open} "
                      f"thread(s) by {reviewer} are unresolved")
    if open_count or missed:
        return Clause("review_findings_zero", "failed",
                      f"{open_count} open, {missed} previously missed on {_short(snap.head)}")
    return Clause("review_findings_zero", "met", f"0 open, 0 previously missed on {_short(snap.head)}")


def _threads(snap: Snapshot) -> Clause:
    if "threads" in snap.errors:
        return Clause("threads_unresolved_zero", "unknown", error=snap.errors["threads"])
    unresolved = [t for t in snap.threads if _open(t)]
    if unresolved:
        where = ", ".join(sorted({t.get("path") or "(PR)" for t in unresolved}))
        return Clause("threads_unresolved_zero", "failed", f"{len(unresolved)} unresolved: {where}")
    return Clause("threads_unresolved_zero", "met", f"0 unresolved of {len(snap.threads)}")


def _is_human(review: dict, snap: Snapshot, reviewer: str) -> bool:
    """A reviewer who is a person: not an app (GraphQL's ``Bot``; a REST login's
    ``[bot]`` suffix), not the PR's reviewer of record, not its author."""
    low = (review.get("author") or "").lower()
    return bool(low) and not review.get("bot") and not low.endswith("[bot]") \
        and low not in (reviewer.lower(), COPILOT_REVIEWER, (snap.author or "").lower())


def _humans(snap: Snapshot, reviewer: str) -> Clause:
    if "reviews" in snap.errors:
        return Clause("human_reviews_answered", "unknown", error=snap.errors["reviews"])
    verdicts: dict[str, dict] = {}
    for r in sorted(snap.reviews, key=lambda r: r.get("at") or ""):
        if _is_human(r, snap, reviewer) and r.get("state") in (
                "APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            verdicts[r["author"]] = r
    asking = sorted(a for a, r in verdicts.items() if r.get("state") == "CHANGES_REQUESTED")
    if asking:
        return Clause("human_reviews_answered", "failed", f"changes requested by {', '.join(asking)}")
    humans = {r.get("author") for r in snap.reviews if _is_human(r, snap, reviewer)}
    return Clause("human_reviews_answered", "met",
                  f"no outstanding change request ({len(humans)} human reviewer(s))")


def _mergeable(snap: Snapshot) -> Clause:
    if snap.state == "CLOSED":
        return Clause("mergeable", "failed", "the PR is closed without being merged")
    value = (snap.mergeable or "").upper()
    if value == "MERGEABLE":
        return Clause("mergeable", "met", "no merge conflict")
    if value == "CONFLICTING":
        return Clause("mergeable", "failed", "merge conflict with the base")
    return Clause("mergeable", "pending", f"the provider hasn't computed mergeability ({value or 'unset'})")


def _open(thread: dict) -> bool:
    """Still open: neither resolved nor outdated (outdated is terminal in the shared
    thread contract, ``pr_contract.CommentThread``)."""
    return not thread.get("resolved") and not thread.get("outdated")


def _outstanding_change_requests(snap: Snapshot) -> list[str]:
    """Reviewers whose latest verdict (approve, request changes, dismissed) requests
    changes: a later review by someone else doesn't answer it."""
    latest: dict[str, str] = {}
    for r in sorted(snap.reviews, key=lambda r: r.get("at") or ""):
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED") and r.get("author"):
            latest[r["author"]] = r["state"]
    me = (snap.author or "").lower()
    return sorted(a for a, s in latest.items() if s == "CHANGES_REQUESTED" and a.lower() != me)


def _policy(snap: Snapshot, policy: dict | None) -> Clause:
    """The repo's merge policy (``approval_required``, ``hold_labels``,
    ``wip_title_prefixes``, ``review_blocking``) through the shared classifier."""
    if policy is None:
        return Clause("merge_policy", "unknown", "no merge policy was supplied")
    if "reviews" in snap.errors:
        return Clause("merge_policy", "unknown", error=snap.errors["reviews"])
    from .pr_contract import PRSnapshot, Review, classify_state
    reviews = tuple(
        Review(id=int(r.get("id") or i + 1), state=r.get("state") or "", user=r.get("author") or "",
               submitted_at=r.get("at") or "", commit_id=r.get("commit") or "",
               dismissed=r.get("state") == "DISMISSED")
        for i, r in enumerate(snap.reviews))
    state = classify_state(
        PRSnapshot(pr_state="closed" if snap.state in ("CLOSED", "MERGED") else "open",
                   merged=snap.state == "MERGED", head_sha=snap.head, reviews=reviews,
                   author=snap.author, labels=tuple(snap.labels), title=snap.title, draft=snap.draft,
                   mergeable={"MERGEABLE": True, "CONFLICTING": False}.get((snap.mergeable or "").upper())),
        hold_labels=policy.get("hold_labels") or (),
        wip_title_prefixes=policy.get("wip_title_prefixes") or (),
        approval_required=bool(policy.get("approval_required", True)),
        review_blocking=bool(policy.get("review_blocking", True)),
    )
    if state.held:
        return Clause("merge_policy", "failed", f"hold label: {', '.join(state.held)}")
    if state.wip:
        return Clause("merge_policy", "failed", "a draft, or a WIP title")
    asking = _outstanding_change_requests(snap)
    if asking:  # any reviewer's latest verdict -- an app's too: the provider blocks on it
        return Clause("merge_policy", "failed", f"changes requested by {', '.join(asking)} (their latest verdict)")
    if snap.review_decision == "CHANGES_REQUESTED":
        return Clause("merge_policy", "failed", "the provider reports an outstanding change request")
    if state.verdict == "CHANGES_REQUESTED":
        return Clause("merge_policy", "failed", "the provider's review verdict is changes requested")
    if policy.get("human_approval_required"):
        humans = {}
        for r in sorted(snap.reviews, key=lambda r: r.get("at") or ""):
            if _is_human(r, snap, "") and r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                humans[r["author"]] = r
        if not any(r.get("state") == "APPROVED" and r.get("commit") == snap.head for r in humans.values()):
            return Clause("merge_policy", "pending", "this actor needs a person's approval on "
                          f"{_short(snap.head)} (a reviewer app's doesn't count)")
    if policy.get("approval_required", True) and state.verdict != "APPROVED":
        return Clause("merge_policy", "pending", f"the repo's policy requires an approval on "
                      f"{_short(snap.head)} (verdict: {state.verdict or 'none'})")
    required = "approved" if policy.get("approval_required", True) else "no approval required"
    return Clause("merge_policy", "met", f"no hold, not a draft or WIP, {required}")


def evaluate(snap: Snapshot, *, reviewer: str = COPILOT_REVIEWER, now: str = "",
             policy: dict | None = None) -> Bar:
    """The bar for one snapshot -- pure, so recorded snapshots replay exactly. *policy*
    is the repo's merge policy (see :func:`_policy`); without it ``merge_policy``
    is ``unknown``, so the bar can't be met by default."""
    observed = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "pr" in snap.errors or not snap.head:
        clauses = [Clause(c, "unknown", error=snap.errors.get("pr", "no head read")) for c in CLAUSES]
        return Bar(snap.repo, snap.number, snap.head, snap.state, "unknown", clauses, observed)
    if snap.head_after and snap.head_after != snap.head:
        moved = f"the head moved from {_short(snap.head)} to {_short(snap.head_after)} during the read"
        clauses = [Clause(c, "unknown", moved) for c in CLAUSES]
        return Bar(snap.repo, snap.number, snap.head_after, snap.state, "unknown", clauses, observed)
    if snap.changed:
        clauses = [Clause(c, "unknown", f"the PR changed during the read: {snap.changed}") for c in CLAUSES]
        return Bar(snap.repo, snap.number, snap.head, snap.state, "unknown", clauses, observed)
    clauses = [_ci(snap), _review_on_head(snap, reviewer), _findings(snap, reviewer),
               _threads(snap), _humans(snap, reviewer), _mergeable(snap), _policy(snap, policy)]
    if snap.state == "MERGED":
        verdict = "merged"
    else:
        statuses = {c.status for c in clauses}
        verdict = next((s for s in ("failed", "unknown", "pending") if s in statuses), "met")
    return Bar(snap.repo, snap.number, snap.head, snap.state, verdict, clauses, observed)


def unsupported(repo: str, number: int, provider: str) -> Snapshot:
    """What a provider without a merge-bar read returns: every clause reads ``unknown``."""
    return Snapshot(repo=repo, number=int(number),
                    errors={"pr": f"the {provider} provider has no merge-bar read"})
