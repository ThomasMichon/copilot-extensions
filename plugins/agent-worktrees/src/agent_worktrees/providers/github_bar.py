"""GitHub's merge-bar read for ``pr bar`` (``GitHubProvider.get_bar_snapshot``).

One full read of a pull request over GraphQL through ``gh``: the PR core (with every
label), its head's checks, every review and every review thread -- each list paged
to its last page -- and the core again at the end. Never raises: each part that
can't be read, or comes back truncated or malformed, is named in
``Snapshot.errors`` so :func:`agent_worktrees.pr_bar.evaluate` reports it
``unknown``, never met.
"""

from __future__ import annotations

import json

from ..pr_bar import Snapshot

_PR_QUERY = """query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
pullRequest(number:$number){state mergeable reviewDecision headRefOid isDraft title author{login}
labels(first:100){pageInfo{hasNextPage endCursor} nodes{name}}}}}"""
_LABELS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){labels(first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{name}}}}}"""
_CHECKS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){commits(last:1){nodes{commit{oid
statusCheckRollup{contexts(first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{__typename ... on CheckRun{name status conclusion} ... on StatusContext{context state}}}}}}}}}}"""
_REVIEWS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviews(first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{databaseId author{__typename login} state submittedAt body commit{oid}}}}}}"""
_THREADS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{isResolved isOutdated path comments(first:1){nodes{author{login}}}}}}}}"""
#: A runaway cursor stops here rather than looping forever (100 pages = 10,000 items).
MAX_PAGES = 100


class ReadError(RuntimeError):
    pass


#: A response shaped other than expected (a list where an object belongs, ...):
#: read as that part being unreadable, never raised out of :func:`read_bar`.
_SHAPE_ERRORS = (ReadError, AttributeError, TypeError, KeyError, IndexError, ValueError)


def _unreadable(exc: Exception) -> str:
    return str(exc) if isinstance(exc, ReadError) else f"malformed response ({type(exc).__name__}: {exc})"


def _s(value) -> str:
    return value if isinstance(value, str) else ""


_STATES, _MERGEABLE = ("OPEN", "CLOSED", "MERGED"), ("MERGEABLE", "CONFLICTING", "UNKNOWN")
_DECISIONS = (None, "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED")


_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING")


def _review(node: dict) -> None:
    """Fail closed on the review fields the verdicts turn on: an unreadable state, or
    a submitted review without a readable time (it orders the verdicts)."""
    state, at = node.get("state"), node.get("submittedAt")
    if state not in _REVIEW_STATES:
        raise ReadError(f"reviews: an unreadable state {state!r}"[:200])
    if state != "PENDING" and not (isinstance(at, str) and at):
        raise ReadError(f"reviews: a {state} review without a readable time")
    if node.get("body") is not None and not isinstance(node.get("body"), str):
        raise ReadError("reviews: an unreadable body")
    commit = node.get("commit")
    if state in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED") and not (
            isinstance(commit, dict) and isinstance(commit.get("oid"), str) and commit["oid"]):
        raise ReadError(f"reviews: a {state} review without a readable commit (it decides 'on the head')")


def _field(pr: dict, key: str, valid) -> object:
    """A core field the bar decides on, or :class:`ReadError`: a malformed lifecycle
    state, head, draft flag or title must never read as a clean PR."""
    value = pr.get(key)
    if not valid(value):
        raise ReadError(f"an unreadable {key}: {value!r}"[:200])
    return value


def _login(node) -> str:
    """The author's login; ``""`` for a deleted user (``null``). Any other shape is
    unreadable: the author decides who counts as a human reviewer."""
    author = node.get("author")
    if author is None:
        return ""
    if not isinstance(author, dict) or not isinstance(author.get("login", ""), str):
        raise ReadError("an author that isn't readable")
    return author.get("login") or ""


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
    if not isinstance(data, dict):
        raise ReadError(f"unexpected GraphQL response shape: {type(data).__name__}")
    if data.get("errors"):
        raise ReadError(f"GraphQL errors: {json.dumps(data.get('errors'))[:300]}")
    repo = data.get("data")
    repo = repo.get("repository") if isinstance(repo, dict) else None
    pr = repo.get("pullRequest") if isinstance(repo, dict) else None
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
        if not isinstance(conn, dict):
            raise ReadError(f"{what}: the response had no readable list")
        page, info = conn.get("nodes"), conn.get("pageInfo")
        if not isinstance(page, list) or not isinstance(info, dict) \
                or not isinstance(info.get("hasNextPage"), bool):
            raise ReadError(f"{what}: a page without readable nodes or pagination info")
        if not all(isinstance(n, dict) for n in page):
            raise ReadError(f"{what}: a page held an unreadable (null) entry")
        nodes += page
        if not info["hasNextPage"]:
            return nodes
        cursor = info.get("endCursor") or ""
        if not cursor or cursor == after:
            raise ReadError(f"{what}: the page cursor didn't advance")
        after = cursor
    raise ReadError(f"{what}: more than {MAX_PAGES} pages")


def _core(run, **kw) -> dict:
    """The PR's core fields with **every** label (a hold label can sit past the first
    page): ``labels`` becomes the full, sorted list of names. :class:`ReadError` on any
    unreadable or truncated label page."""
    pr = _graphql(run, _PR_QUERY, **kw)
    first = pr.get("labels")
    page = first.get("nodes") if isinstance(first, dict) else None
    info = first.get("pageInfo") if isinstance(first, dict) else None
    if not isinstance(page, list) or not isinstance(info, dict) \
            or not isinstance(info.get("hasNextPage"), bool) or not all(isinstance(n, dict) for n in page):
        raise ReadError("labels: a page without readable nodes or pagination info")
    if info["hasNextPage"]:
        page = _pages(run, _LABELS_QUERY, lambda pr: pr.get("labels"), what="labels", **kw)
    names = [n.get("name") for n in page]
    if not all(isinstance(name, str) and name for name in names):
        raise ReadError("labels: a label without a readable name")
    return {**pr, "labels": sorted(names)}


def _checks_conn(pr: dict, head: str):
    """The head commit's check contexts; an empty list when the commit has no
    rollup (nothing reported), ``None`` (unreadable) when the commit is missing.
    Every page must be for *head*: a head that moved and came back (A -> B -> A)
    between pages would otherwise pass B's checks off as A's."""
    nodes = ((pr.get("commits") or {}).get("nodes") or [])
    commit = (nodes[-1] or {}).get("commit") if nodes else None
    if not isinstance(commit, dict):
        return None
    if commit.get("oid") != head:
        raise ReadError(f"checks: a page was for {str(commit.get('oid'))[:9]}, not the head {head[:9]}")
    rollup = commit.get("statusCheckRollup")
    return rollup.get("contexts") if isinstance(rollup, dict) else {"nodes": [], "pageInfo": {"hasNextPage": False}}


def read_bar(repo: str, number: int, *, host: str, token: str | None = None, run=None) -> Snapshot:
    """One full read of *repo*#*number* (every page), never raising: each part
    that can't be read is named in ``Snapshot.errors``."""
    if run is None:
        from .base import run_cli as run
    owner, _, name = repo.partition("/")
    env = {"GH_TOKEN": token} if token else {}
    kw = {"host": host, "env": env, "owner": owner, "name": name, "number": int(number)}
    snap = Snapshot(repo=repo, number=int(number))
    try:
        pr = _core(run, **kw)
        snap.state = _field(pr, "state", lambda v: v in _STATES)
        snap.mergeable = _field(pr, "mergeable", lambda v: v in _MERGEABLE)
        snap.head = _field(pr, "headRefOid", lambda v: isinstance(v, str) and bool(v))
        snap.draft = _field(pr, "isDraft", lambda v: isinstance(v, bool))
        snap.title = _field(pr, "title", lambda v: isinstance(v, str))
        snap.review_decision = _field(pr, "reviewDecision", lambda v: v in _DECISIONS) or ""
        snap.author, snap.labels = _login(pr), list(pr["labels"])
    except _SHAPE_ERRORS as exc:
        snap.errors["pr"] = _unreadable(exc)
        return snap
    parts = (
        ("checks", _CHECKS_QUERY, lambda pr: _checks_conn(pr, snap.head), lambda n: {
            "name": _s(n.get("name")) or _s(n.get("context")) or "?",
            "status": _s(n.get("status")) or _s(n.get("state")), "conclusion": _s(n.get("conclusion"))}),
        ("reviews", _REVIEWS_QUERY, lambda pr: pr.get("reviews"), lambda n: _review(n) or {
            "id": n.get("databaseId") if type(n.get("databaseId")) is int else None,
            "author": _login(n), "bot": (n.get("author") or {}).get("__typename") == "Bot",
            "state": _s(n.get("state")), "commit": _s((n.get("commit") or {}).get("oid")),
            "body": _s(n.get("body")), "at": _s(n.get("submittedAt"))}),
        ("threads", _THREADS_QUERY, lambda pr: pr.get("reviewThreads"), lambda n: {
            "resolved": n.get("isResolved") is True, "outdated": n.get("isOutdated") is True,
            "path": _s(n.get("path")), "author": _login(((n.get("comments") or {}).get("nodes") or [{}])[0])}),
    )
    for part, query, path, shape in parts:
        try:
            setattr(snap, part, [shape(n) for n in _pages(run, query, path, what=part, **kw)])
        except _SHAPE_ERRORS as exc:
            snap.errors[part] = _unreadable(exc)
    try:  # the same core again: any change since the first read means a mixed view
        after = _core(run, **kw)
        snap.head_after = _field(after, "headRefOid", lambda v: isinstance(v, str) and bool(v))
        changed = [f for f in ("state", "isDraft", "title", "labels", "reviewDecision")
                   if after.get(f) != pr.get(f)]
        # GitHub computes mergeability lazily: UNKNOWN first, then the answer. Only a
        # flip between two answers is a change; otherwise the latest read stands.
        known = {pr.get("mergeable"), after.get("mergeable")} <= {"MERGEABLE", "CONFLICTING"}
        if known and after.get("mergeable") != pr.get("mergeable"):
            changed.append("mergeable")
        snap.mergeable = _field(after, "mergeable", lambda v: v in _MERGEABLE)
        snap.changed = ", ".join(changed)
    except _SHAPE_ERRORS as exc:
        snap.errors["pr"] = f"re-reading the PR: {_unreadable(exc)}"
    return snap
