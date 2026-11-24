"""Strict query evidence, with independent per-member collection progress."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any

from ..pr_checks import ChecksReport
from ..pr_review_policy import KNOWN_REVIEW_STATES, normalized_review_time
from ..pr_snapshot import PRSnapshot, Review
from .github_batch_query import COLLECTIONS, MAX_RECORDS, PAGE_SIZE, PageRequest
from .github_checks import checks_from_data

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def timestamp(value: object) -> float:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z", value,
    ) is None:
        raise ValueError("invalid batch timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").timestamp()
    except (ValueError, OverflowError):
        raise ValueError("invalid batch timestamp") from None


def _text(value: object, *, maximum: int = 1024, empty: bool = False) -> str:
    if (
        not isinstance(value, str) or len(value) > maximum or (not value and not empty)
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid batch text")
    return value


def _oid(value: object) -> str:
    if not isinstance(value, str) or _OID.fullmatch(value) is None:
        raise ValueError("invalid batch commit identity")
    return value


@dataclass(frozen=True)
class Quota:
    cost: int
    remaining: int
    reset_at: str

    @classmethod
    def decode(cls, value: object) -> Quota:
        if (
            not isinstance(value, dict) or type(value.get("cost")) is not int
            or value["cost"] != 1 or type(value.get("remaining")) is not int
            or not 0 <= value["remaining"] <= 1_000_000_000
        ):
            raise ValueError("missing or unexpected batch quota")
        timestamp(value.get("resetAt"))
        return cls(value["cost"], value["remaining"], value["resetAt"])


@dataclass
class Collection:
    total: int | None = None
    cursor: str | None | bool = None
    nodes: list[dict[str, Any]] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)
    cursors: set[str] = field(default_factory=set)

    def append(self, value: object) -> None:
        if self.cursor is False:
            raise ValueError("unexpected completed batch collection")
        if not isinstance(value, dict):
            raise ValueError("missing batch collection")
        total, nodes, page = value.get("totalCount"), value.get("nodes"), value.get("pageInfo")
        if (
            type(total) is not int or not 0 <= total <= MAX_RECORDS
            or not isinstance(nodes, list) or len(nodes) > PAGE_SIZE
            or not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool
            or self.total is not None and total != self.total
        ):
            raise ValueError("invalid or changing batch collection")
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError("redacted batch collection member")
            identity = _text(node.get("id"))
            if identity in self.ids:
                raise ValueError("duplicate batch collection member")
            self.ids.add(identity)
        count = len(self.nodes) + len(nodes)
        if count > total or page["hasNextPage"] != (count < total):
            raise ValueError("incomplete batch collection")
        cursor = page.get("endCursor")
        if cursor is not None:
            cursor = _text(cursor)
            if not cursor.isascii() or cursor in self.cursors:
                raise ValueError("repeated batch collection cursor")
            self.cursors.add(cursor)
        if page["hasNextPage"]:
            if cursor is None or not nodes:
                raise ValueError("repeated batch collection cursor")
            self.cursor = cursor
        else:
            self.cursor = False
        self.total = total
        self.nodes.extend(nodes)


def _candidate(value: dict[str, Any], number: int) -> dict[str, Any]:
    if (
        type(value.get("number")) is not int or value["number"] != number
        or value.get("state") not in ("OPEN", "CLOSED", "MERGED")
        or type(value.get("merged")) is not bool or type(value.get("isDraft")) is not bool
        or value["merged"] != (value["state"] == "MERGED")
        or value.get("mergeable") not in ("MERGEABLE", "CONFLICTING", "UNKNOWN")
        or not isinstance(value.get("author"), dict)
    ):
        raise ValueError("missing or foreign batch candidate")
    timestamp(value.get("updatedAt"))
    return {
        "state": value["state"], "merged": value["merged"],
        "head": _oid(value.get("headRefOid")), "base": _oid(value.get("baseRefOid")),
        "base_ref": _text(value.get("baseRefName")),
        "updated_at": value["updatedAt"],
        "title": _text(value.get("title"), maximum=65536, empty=True),
        "draft": value["isDraft"], "author": _text(value["author"].get("login")),
        "mergeable": value["mergeable"],
    }


def _reviews(nodes: list[dict[str, Any]]) -> tuple[Review, ...]:
    result = []
    seen: set[int] = set()
    for node in nodes:
        raw = node.get("fullDatabaseId")
        if type(raw) is int:
            identity = raw
        elif isinstance(raw, str) and re.fullmatch(r"[1-9][0-9]{0,18}", raw):
            identity = int(raw)
        else:
            raise ValueError("missing numeric batch review identity")
        state, author, commit = node.get("state"), node.get("author"), node.get("commit")
        submitted_at = node.get("submittedAt")
        if (
            not 1 <= identity <= 9_223_372_036_854_775_807 or identity in seen
            or state not in KNOWN_REVIEW_STATES or not isinstance(author, dict)
            or submitted_at is not None and not isinstance(submitted_at, str)
            or state != "PENDING" and (
                not isinstance(submitted_at, str) or normalized_review_time(submitted_at) is None
            )
            or commit is not None and not isinstance(commit, dict)
        ):
            raise ValueError("invalid batch review provenance")
        seen.add(identity)
        result.append(Review(
            id=identity, state=state, user=_text(author.get("login")),
            submitted_at=str(submitted_at or ""),
            commit_id=_oid(commit.get("oid")) if commit is not None else "",
            dismissed=state == "DISMISSED",
        ))
    return tuple(result)


def _latest_runs(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], tuple[float | None, dict[str, Any]]] = {}
    for node in nodes:
        suite = node.get("checkSuite")
        app = suite.get("app") if isinstance(suite, dict) else None
        if not isinstance(app, dict):
            raise ValueError("missing batch check application")
        key = _text(app.get("id")), _text(node.get("name"))
        started = timestamp(node["startedAt"]) if node.get("startedAt") is not None else None
        previous = selected.get(key)
        if previous is not None:
            if started is None or previous[0] is None or started == previous[0]:
                raise ValueError("ambiguous batch check rerun ordering")
            if started < previous[0]:
                continue
        selected[key] = started, node
    return [node for _, node in selected.values()]


def _checks(nodes: list[dict[str, Any]]) -> ChecksReport:
    statuses, runs = [], []
    check_nodes = []
    for node in nodes:
        if node.get("__typename") == "StatusContext":
            timestamp(node.get("createdAt"))
            statuses.append({
                "context": _text(node.get("context")), "state": str(node.get("state")).lower(),
                "description": node.get("description"), "target_url": node.get("targetUrl"),
                "created_at": node["createdAt"],
            })
        elif node.get("__typename") == "CheckRun":
            check_nodes.append(node)
        else:
            raise ValueError("unknown batch check context")
    statuses.sort(key=lambda row: row["created_at"], reverse=True)
    for node in _latest_runs(check_nodes):
        runs.append({
            "name": node["name"], "status": str(node.get("status")).lower(),
            "conclusion": str(node.get("conclusion")).lower(),
            "details_url": node.get("detailsUrl"), "html_url": node.get("url"),
            "output": {"summary": node.get("summary"), "title": node.get("title")},
        })
    latest: dict[str, dict[str, Any]] = {}
    for row in statuses:
        latest.setdefault(row["context"].casefold(), row)
    states = [row["state"] for row in latest.values()]
    state = (
        "failure" if any(item in ("failure", "error") for item in states)
        else "pending" if "pending" in states else "success"
    )
    return checks_from_data(
        {"state": state, "total_count": len(statuses), "statuses": statuses},
        runs, runs_complete=True,
    )


@dataclass
class Member:
    number: int
    candidate: dict[str, Any] | None = None
    collections: dict[str, Collection] = field(default_factory=lambda: {
        name: Collection() for name in COLLECTIONS
    })
    reason: str = ""

    @property
    def complete(self) -> bool:
        return not self.reason and all(item.cursor is False for item in self.collections.values())

    def request(self) -> PageRequest:
        return PageRequest(self.number, **{
            name: item.cursor for name, item in self.collections.items()
        })

    def append(self, value: object) -> None:
        if not isinstance(value, dict):
            raise ValueError("missing batch candidate")
        candidate = _candidate(value, self.number)
        if self.candidate is not None and candidate != self.candidate:
            raise ValueError("batch candidate changed")
        self.candidate = candidate
        commits = value.get("commits")
        nodes = commits.get("nodes") if isinstance(commits, dict) else None
        if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
            raise ValueError("missing batch head commit")
        commit = nodes[0].get("commit")
        if not isinstance(commit, dict) or commit.get("oid") != candidate["head"]:
            raise ValueError("batch head commit changed")
        for name in COLLECTIONS:
            collection = self.collections[name]
            if collection.cursor is False:
                continue
            evidence: object
            if name == "checks":
                rollup = commit.get("statusCheckRollup", ...)
                if rollup is None:
                    evidence = {"totalCount": 0, "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None}}
                elif isinstance(rollup, dict):
                    evidence = rollup.get("contexts")
                else:
                    raise ValueError("missing batch check rollup")
            else:
                evidence = value.get(name)
            collection.append(evidence)

    def snapshot(self) -> PRSnapshot:
        if not self.complete or self.candidate is None:
            raise ValueError("incomplete batch candidate")
        value = self.candidate
        checks = _checks(self.collections["checks"].nodes)
        if not checks.complete:
            raise ValueError("incomplete batch checks")
        return PRSnapshot(
            pr_state="open" if value["state"] == "OPEN" else "closed",
            merged=value["merged"], head_sha=value["head"], base_sha=value["base"],
            base_ref=value["base_ref"], updated_at=value["updated_at"], endpoint="github.com",
            title=value["title"], draft=value["draft"], author=value["author"],
            mergeable={"MERGEABLE": True, "CONFLICTING": False, "UNKNOWN": None}[value["mergeable"]],
            reviews=_reviews(self.collections["reviews"].nodes), reviews_complete=True,
            labels=tuple(_text(node.get("name")) for node in self.collections["labels"].nodes),
            checks_state=checks.state, checks_complete=checks.complete,
            check_failures=checks.failures, check_failures_omitted=checks.omitted,
        )


def response(value: object, repository: str, count: int) -> tuple[dict[str, Any], set[int], Quota]:
    """Unscoped errors invalidate the envelope; scoped failures isolate one alias."""
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise ValueError("missing batch data")
    data = value["data"]
    quota = Quota.decode(data.get("rateLimit"))
    selected = data.get("repository")
    if (
        not isinstance(selected, dict) or selected.get("nameWithOwner", "").casefold() != repository.casefold()
        or set(selected) != {"nameWithOwner", *(f"p{index}" for index in range(count))}
    ):
        raise ValueError("missing or foreign batch repository")
    errors = value.get("errors", [])
    if not isinstance(errors, list):
        raise ValueError("invalid batch errors")
    failed: set[int] = set()
    for error in errors:
        path = error.get("path") if isinstance(error, dict) else None
        if (
            not isinstance(path, list) or len(path) < 2 or path[0] != "repository"
            or path[1] not in {f"p{index}" for index in range(count)}
        ):
            raise ValueError("unscoped batch error")
        failed.add(int(path[1][1:]))
    return selected, failed, quota
