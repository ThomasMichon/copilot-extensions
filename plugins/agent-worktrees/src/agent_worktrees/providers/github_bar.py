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
pullRequest(number:$number){state mergeable headRefOid isDraft title author{login}
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
pageInfo{hasNextPage endCursor} nodes{databaseId author{login} state submittedAt body commit{oid}}}}}}"""
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
    if not isinstance(data, dict):
        raise ReadError(f"unexpected GraphQL response shape: {type(data).__name__}")
    if data.get("errors"):
        raise ReadError(f"GraphQL errors: {json.dumps(data.get('errors'))[:300]}")
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
    return {**pr, "labels": sorted(n.get("name") or "" for n in page)}


def _checks_conn(pr: dict):
    """The head commit's check contexts; an empty list when the commit has no
    rollup (nothing reported), ``None`` (unreadable) when the commit is missing."""
    nodes = ((pr.get("commits") or {}).get("nodes") or [])
    commit = (nodes[-1] or {}).get("commit") if nodes else None
    if not isinstance(commit, dict):
        return None
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
        snap.state, snap.mergeable = pr.get("state") or "", pr.get("mergeable") or ""
        snap.head, snap.author = pr.get("headRefOid") or "", (pr.get("author") or {}).get("login", "")
        snap.draft, snap.title = bool(pr.get("isDraft")), pr.get("title") or ""
        snap.labels = list(pr["labels"])
    except ReadError as exc:
        snap.errors["pr"] = str(exc)
        return snap
    parts = (
        ("checks", _CHECKS_QUERY, _checks_conn, lambda n: {
            "name": n.get("name") or n.get("context") or "?",
            "status": n.get("status") or n.get("state") or "",
            "conclusion": n.get("conclusion") or ""}),
        ("reviews", _REVIEWS_QUERY, lambda pr: pr.get("reviews"), lambda n: {
            "id": n.get("databaseId"),
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
    try:  # the same core again: any change since the first read means a mixed view
        after = _core(run, **kw)
        snap.head_after = after.get("headRefOid") or ""
        snap.changed = ", ".join(f for f in ("state", "mergeable", "isDraft", "title", "labels")
                                 if after.get(f) != pr.get(f))
    except ReadError as exc:
        snap.errors["pr"] = f"re-reading the PR: {exc}"
    return snap
