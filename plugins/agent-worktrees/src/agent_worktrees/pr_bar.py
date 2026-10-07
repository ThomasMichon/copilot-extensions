"""``pr bar`` -- a PR's merge bar as typed clauses, each with its own evidence.

A PR clears the bar when, **on one head**, every clause is ``met``:

- ``ci_green`` -- every check reported on the head passed (or was skipped/neutral);
- ``review_on_head`` -- the reviewer's (default: Copilot's) latest review is on the head;
- ``review_findings_zero`` -- that review reports no open and no previously missed
  findings, and doesn't disagree with the review threads;
- ``threads_unresolved_zero`` -- no review thread is unresolved;
- ``human_reviews_answered`` -- no human reviewer's latest verdict requests changes;
- ``mergeable`` -- the provider sees no merge conflict.

Each clause is ``met`` | ``pending`` (waiting on someone else) | ``failed`` (the
author has something to do) | ``unknown`` (it couldn't be read). The rules that
make it trustworthy rather than merely convenient:

- **Fail closed.** Every list is read to its last page; a page that can't be
  read makes the clauses built on it ``unknown`` -- never ``met`` -- and an empty
  check list is ``unknown``, not passing.
- **Same head.** The head is read before and after everything else; if it moved,
  every clause is ``unknown``: a verdict assembled across two heads describes
  neither.

The provider read is GitHub's GraphQL API through ``gh``; the evaluation is a
pure function of the snapshot (:func:`evaluate`), so the same verdict can be
replayed from recorded snapshots.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

#: The login GitHub gives Copilot's pull-request reviewer.
COPILOT_REVIEWER = "copilot-pull-request-reviewer"
#: Each clause's evidence is a short tail, never a dump.
EVIDENCE_CAP = 600
CLAUSES = ("ci_green", "review_on_head", "review_findings_zero",
           "threads_unresolved_zero", "human_reviews_answered", "mergeable")
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
    author: str = ""
    mergeable: str = ""        # MERGEABLE | CONFLICTING | UNKNOWN
    checks: list[dict] = field(default_factory=list)    # {name, status, conclusion}
    reviews: list[dict] = field(default_factory=list)   # {author, state, commit, body, at}
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
                        if not t.get("resolved") and (t.get("author") or "").lower() == reviewer.lower())
    if open_count and not reviewer_open:
        return Clause("review_findings_zero", "unknown",
                      f"the summary reports {open_count} open findings but no thread by {reviewer} is unresolved")
    if open_count or missed:
        return Clause("review_findings_zero", "failed",
                      f"{open_count} open, {missed} previously missed on {_short(snap.head)}")
    return Clause("review_findings_zero", "met", f"0 open, 0 previously missed on {_short(snap.head)}")


def _threads(snap: Snapshot) -> Clause:
    if "threads" in snap.errors:
        return Clause("threads_unresolved_zero", "unknown", error=snap.errors["threads"])
    unresolved = [t for t in snap.threads if not t.get("resolved")]
    if unresolved:
        where = ", ".join(sorted({t.get("path") or "(PR)" for t in unresolved}))
        return Clause("threads_unresolved_zero", "failed", f"{len(unresolved)} unresolved: {where}")
    return Clause("threads_unresolved_zero", "met", f"0 unresolved of {len(snap.threads)}")


def _is_human(login: str, snap: Snapshot, reviewer: str) -> bool:
    low = (login or "").lower()
    return bool(low) and low not in (reviewer.lower(), COPILOT_REVIEWER, (snap.author or "").lower()) \
        and not low.endswith("[bot]")


def _humans(snap: Snapshot, reviewer: str) -> Clause:
    if "reviews" in snap.errors:
        return Clause("human_reviews_answered", "unknown", error=snap.errors["reviews"])
    verdicts: dict[str, dict] = {}
    for r in sorted(snap.reviews, key=lambda r: r.get("at") or ""):
        if _is_human(r.get("author", ""), snap, reviewer) and r.get("state") in (
                "APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            verdicts[r["author"]] = r
    asking = sorted(a for a, r in verdicts.items() if r.get("state") == "CHANGES_REQUESTED")
    if asking:
        return Clause("human_reviews_answered", "failed", f"changes requested by {', '.join(asking)}")
    humans = {r.get("author") for r in snap.reviews if _is_human(r.get("author", ""), snap, reviewer)}
    return Clause("human_reviews_answered", "met",
                  f"no outstanding change request ({len(humans)} human reviewer(s))")


def _mergeable(snap: Snapshot) -> Clause:
    value = (snap.mergeable or "").upper()
    if value == "MERGEABLE":
        return Clause("mergeable", "met", "no merge conflict")
    if value == "CONFLICTING":
        return Clause("mergeable", "failed", "merge conflict with the base")
    return Clause("mergeable", "pending", f"the provider hasn't computed mergeability ({value or 'unset'})")


def evaluate(snap: Snapshot, *, reviewer: str = COPILOT_REVIEWER, now: str = "") -> Bar:
    """The bar for one snapshot -- pure, so recorded snapshots replay exactly."""
    observed = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "pr" in snap.errors or not snap.head:
        clauses = [Clause(c, "unknown", error=snap.errors.get("pr", "no head read")) for c in CLAUSES]
        return Bar(snap.repo, snap.number, snap.head, snap.state, "unknown", clauses, observed)
    if snap.head_after and snap.head_after != snap.head:
        moved = f"the head moved from {_short(snap.head)} to {_short(snap.head_after)} during the read"
        clauses = [Clause(c, "unknown", moved) for c in CLAUSES]
        return Bar(snap.repo, snap.number, snap.head_after, snap.state, "unknown", clauses, observed)
    clauses = [_ci(snap), _review_on_head(snap, reviewer), _findings(snap, reviewer),
               _threads(snap), _humans(snap, reviewer), _mergeable(snap)]
    if snap.state == "MERGED":
        verdict = "merged"
    else:
        statuses = {c.status for c in clauses}
        verdict = next((s for s in ("failed", "unknown", "pending") if s in statuses), "met")
    return Bar(snap.repo, snap.number, snap.head, snap.state, verdict, clauses, observed)


# -- the GitHub read ---------------------------------------------------------------------

_PR_QUERY = """query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
pullRequest(number:$number){state mergeable headRefOid author{login}}}}"""
_CHECKS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){commits(last:1){nodes{commit{oid
statusCheckRollup{contexts(first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{__typename ... on CheckRun{name status conclusion} ... on StatusContext{context state}}}}}}}}}}"""
_REVIEWS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviews(first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{author{login} state submittedAt body commit{oid}}}}}}"""
_THREADS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{isResolved isOutdated path comments(first:1){nodes{author{login}}}}}}}}"""
#: A runaway cursor stops here rather than looping forever (100 pages = 10,000 items).
MAX_PAGES = 100


class ReadError(RuntimeError):
    pass


def _graphql(run, query: str, *, host: str, env: dict, owner: str, name: str,
             number: int, after: str = "") -> dict:
    args = ["gh", "api", "graphql", "--hostname", host, "-f", f"query={query}",
            "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}"]
    if after:
        args += ["-f", f"after={after}"]
    proc = run(args, env=env)
    if proc.returncode != 0:
        raise ReadError((proc.stderr or proc.stdout or "gh api graphql failed").strip())
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise ReadError(f"non-JSON GraphQL response: {exc}") from exc
    if not isinstance(data, dict) or data.get("errors"):
        raise ReadError(f"GraphQL errors: {json.dumps((data or {}).get('errors'))[:300]}")
    pr = ((data.get("data") or {}).get("repository") or {}).get("pullRequest")
    if not isinstance(pr, dict):
        raise ReadError("the pull request wasn't in the response")
    return pr


def _pages(run, query: str, path, *, what: str, **kw) -> list[dict]:
    """Every node of one connection, page by page; :class:`ReadError` on any
    unreadable page, a cursor that doesn't advance, or more than :data:`MAX_PAGES`."""
    nodes: list[dict] = []
    after = ""
    for _ in range(MAX_PAGES):
        conn = path(_graphql(run, query, after=after, **kw))
        if conn is None:
            return nodes
        nodes += [n for n in conn.get("nodes") or [] if isinstance(n, dict)]
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return nodes
        cursor = info.get("endCursor") or ""
        if not cursor or cursor == after:
            raise ReadError(f"{what}: the page cursor didn't advance")
        after = cursor
    raise ReadError(f"{what}: more than {MAX_PAGES} pages")


def _checks_conn(pr: dict):
    nodes = ((pr.get("commits") or {}).get("nodes") or [])
    commit = (nodes[-1] or {}).get("commit") if nodes else None
    rollup = (commit or {}).get("statusCheckRollup")
    return (rollup or {}).get("contexts") if rollup else None


def read_github(repo: str, number: int, *, host: str, token: str | None = None, run=None) -> Snapshot:
    """One full read of *repo*#*number* (every page), never raising: each part
    that can't be read is named in ``Snapshot.errors``."""
    if run is None:
        from .providers.base import run_cli as run
    owner, _, name = repo.partition("/")
    env = {"GH_TOKEN": token} if token else {}
    kw = {"host": host, "env": env, "owner": owner, "name": name, "number": int(number)}
    snap = Snapshot(repo=repo, number=int(number))
    try:
        pr = _graphql(run, _PR_QUERY, **kw)
        snap.state, snap.mergeable = pr.get("state") or "", pr.get("mergeable") or ""
        snap.head, snap.author = pr.get("headRefOid") or "", (pr.get("author") or {}).get("login", "")
    except ReadError as exc:
        snap.errors["pr"] = str(exc)
        return snap
    parts = (
        ("checks", _CHECKS_QUERY, _checks_conn, lambda n: {
            "name": n.get("name") or n.get("context") or "?",
            "status": n.get("status") or n.get("state") or "",
            "conclusion": n.get("conclusion") or ""}),
        ("reviews", _REVIEWS_QUERY, lambda pr: pr.get("reviews"), lambda n: {
            "author": (n.get("author") or {}).get("login", ""), "state": n.get("state") or "",
            "commit": (n.get("commit") or {}).get("oid", ""), "body": n.get("body") or "",
            "at": n.get("submittedAt") or ""}),
        ("threads", _THREADS_QUERY, lambda pr: pr.get("reviewThreads"), lambda n: {
            "resolved": bool(n.get("isResolved")), "outdated": bool(n.get("isOutdated")),
            "path": n.get("path") or "",
            "author": (((n.get("comments") or {}).get("nodes") or [{}])[0].get("author") or {}).get("login", "")}),
    )
    for part, query, path, shape in parts:
        try:
            setattr(snap, part, [shape(n) for n in _pages(run, query, path, what=part, **kw)])
        except ReadError as exc:
            snap.errors[part] = str(exc)
    try:
        snap.head_after = _graphql(run, _PR_QUERY, **kw).get("headRefOid") or ""
    except ReadError as exc:
        snap.errors["pr"] = f"re-reading the head: {exc}"
    return snap
