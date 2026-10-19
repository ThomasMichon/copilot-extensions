"""Recover deferred Dev advances from existing immutable GitHub scheduling data."""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from verify_promotion_workflows import _api
from promotion_admission_evidence import (
    PromotionDispatchError, canonical_run as _canonical_run, creation_time as _creation_time,
    pages as _pages, source_admissions,
)

_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-f]{40}")
_PROMOTION = re.compile(r"release/promote-([1-9][0-9]{0,19})")
_STATE = ".github/release-pipeline-state.json"


@dataclass(frozen=True)
class DispatchDecision:
    source_sha: str | None
    reason: str


def main_pull_requests(
    request: Callable[..., object], repository: str, state: str,
) -> list[dict]:
    """Stable cursors retain surviving entries when earlier PRs leave the set."""
    if state not in ("OPEN", "CLOSED"):
        raise PromotionDispatchError("invalid Main PR inventory state")
    owner, name = repository.split("/")
    direction = "ASC" if state == "OPEN" else "DESC"
    inventory = "open Main PR" if state == "OPEN" else "closed-unmerged promotion"
    cursor = None
    rows: list[dict] = []
    seen: set[int] = set()
    for _ in range(10):
        query = (
            "query($owner:String!,$name:String!,$cursor:String){"
            "repository(owner:$owner,name:$name){pullRequests("
            f"states:{state},baseRefName:\"main\",first:100,after:$cursor,"
            f"orderBy:{{field:CREATED_AT,direction:{direction}}}){{totalCount "
            "pageInfo{hasNextPage endCursor} nodes{number state merged "
            "baseRefName headRefName headRefOid headRepository{nameWithOwner} author{login}}}}}"
        )
        data = request("graphql", body={
            "query": query, "variables": {"owner": owner, "name": name, "cursor": cursor},
        })
        if not isinstance(data, dict) or data.get("errors"):
            raise PromotionDispatchError(f"{inventory} history is unavailable")
        payload = data.get("data")
        repo = payload.get("repository") if isinstance(payload, dict) else None
        connection = repo.get("pullRequests") if isinstance(repo, dict) else None
        batch = connection.get("nodes") if isinstance(connection, dict) else None
        info = connection.get("pageInfo") if isinstance(connection, dict) else None
        if (
            not isinstance(batch, list) or len(batch) > 100 or not isinstance(info, dict)
            or type(info.get("hasNextPage")) is not bool
            or type(connection.get("totalCount")) is not int
        ):
            raise PromotionDispatchError(f"{inventory} history is incomplete")
        for row in batch:
            if (
                not isinstance(row, dict) or type(row.get("number")) is not int
                or row["number"] <= 0 or row["number"] in seen
                or row.get("state") != state or row.get("merged") is not False
                or row.get("baseRefName") != "main"
            ):
                raise PromotionDispatchError(f"{inventory} identity is invalid")
            seen.add(row["number"])
            rows.append(row)
        if not info["hasNextPage"]:
            if connection["totalCount"] != len(rows):
                raise PromotionDispatchError(f"{inventory} history changed")
            return rows
        next_cursor = info.get("endCursor")
        if (
            not batch or not isinstance(next_cursor, str) or not 0 < len(next_cursor) <= 1024
            or next_cursor == cursor
        ):
            raise PromotionDispatchError(f"{inventory} cursor is invalid")
        cursor = next_cursor
    raise PromotionDispatchError(f"{inventory} history exceeds the pagination limit")


def _retirements(request: Callable[..., object], repository: str) -> list[dict]:
    # GraphQL CLOSED excludes MERGED, unlike REST's state=closed.
    return main_pull_requests(request, repository, "CLOSED")


def retired_source(
    request: Callable[..., object], repository: str, source: str,
    read_state: Callable[[str], tuple[bool, str | None]],
) -> bool:
    """Retirement applies to every automatic wake, not just its close event."""
    owner = repository.split("/", 1)[0].casefold()
    rows = _retirements(request, repository)
    states: dict[str, str | None] = {}
    for row in rows:
        branch, repo, user = row.get("headRefName"), row.get("headRepository"), row.get("author")
        if not isinstance(branch, str):
            raise PromotionDispatchError("closed Main PR branch is unavailable")
        if not _PROMOTION.fullmatch(branch):
            continue
        if not isinstance(user, dict) or not isinstance(user.get("login"), str):
            raise PromotionDispatchError("closed promotion publisher is unavailable")
        if user["login"].casefold() != owner:
            continue
        if not isinstance(repo, dict) or not isinstance(repo.get("nameWithOwner"), str):
            raise PromotionDispatchError("closed promotion provenance is unavailable")
        if repo["nameWithOwner"].casefold() != repository.casefold():
            continue
        detail = request(f"pulls/{row['number']}")
        head = detail.get("head") if isinstance(detail, dict) else None
        author = detail.get("user") if isinstance(detail, dict) else None
        if (
            not isinstance(detail, dict) or detail.get("number") != row["number"]
            or detail.get("state") != "closed" or type(detail.get("merged")) is not bool
            or not isinstance(head, dict) or head.get("ref") != branch
            or head.get("sha") != row.get("headRefOid")
            or not isinstance(head.get("repo"), dict)
            or head["repo"].get("full_name") != repo["nameWithOwner"]
            or not isinstance(author, dict) or author.get("login") != user["login"]
            or not isinstance(detail.get("base"), dict) or detail["base"].get("ref") != "main"
        ):
            raise PromotionDispatchError("retired promotion history changed or is incomplete")
        if detail["merged"]:
            continue
        sha = head.get("sha")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise PromotionDispatchError("retired promotion candidate identity is invalid")
        if sha not in states:
            states[sha] = read_state(sha)[1]
        if states[sha] is None:
            raise PromotionDispatchError("retired promotion has no recorded source identity")
        if states[sha] == source:
            return True
    return False


def pipeline_state(request: Callable[[str], object], ref: str) -> tuple[bool, str | None]:
    data = request(f"contents/{_STATE}?ref={quote(ref, safe='')}")
    if (
        not isinstance(data, dict) or data.get("type") != "file"
        or data.get("encoding") != "base64" or type(data.get("size")) is not int
        or not 0 < data["size"] <= 131072 or not isinstance(data.get("content"), str)
        or len(data["content"]) > 180000
    ):
        raise PromotionDispatchError("trusted pipeline state is unavailable or invalid")
    raw = base64.b64decode("".join(data["content"].split()), validate=True)
    value = json.loads(raw)
    if (
        len(raw) != data["size"] or not isinstance(value, dict)
        or value.get("schema") != "copilot-extensions.release-pipeline-state"
        or type(value.get("version")) is not int or value["version"] != 1
        or type(value.get("paused")) is not bool
    ):
        raise PromotionDispatchError("trusted pipeline state has an invalid schema")
    promotion = value.get("last_promotion")
    released = promotion.get("dev_head") if isinstance(promotion, dict) else None
    if promotion is not None and (
        not isinstance(released, str) or not _SHA.fullmatch(released)
    ):
        raise PromotionDispatchError("trusted Main source identity is invalid")
    return value["paused"], released


def require_automatic_source(
    repository: str, source: str, *, api: Callable[..., object], wait_seconds: float = 120,
    main_base: str | None = None,
) -> None:
    """Recheck queued automatic admissions against retirements recorded meanwhile."""
    if (
        not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or not isinstance(source, str) or not _SHA.fullmatch(source)
        or type(wait_seconds) not in (int, float) or not 0 < wait_seconds <= 120
        or (main_base is not None and (not isinstance(main_base, str) or not _SHA.fullmatch(main_base)))
    ):
        raise PromotionDispatchError("invalid automatic promotion source")
    deadline = time.monotonic() + wait_seconds

    def request(path: str, *, body: dict | None = None) -> object:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PromotionDispatchError("retirement evidence deadline expired")
        endpoint = path if path == "graphql" else f"repos/{repository}/{path}"
        return api(endpoint, timeout=min(30, remaining), **({"body": body} if body else {}))

    if retired_source(request, repository, source, lambda ref: pipeline_state(request, ref)):
        raise PromotionDispatchError("retired source is unchanged; automatic promotion is not admitted")
    current_main = _branch_sha(request, "main")
    if main_base is not None and current_main != main_base:
        raise PromotionDispatchError("Main changed after automatic source admission")
    if source_admissions(request, repository, source, current_main):
        raise PromotionDispatchError("an admitted generation already owns this source and Main base")


def _branch_sha(request: Callable[..., object], branch: str) -> str:
    ref = request(f"git/ref/heads/{branch}")
    target = ref.get("object") if isinstance(ref, dict) else None
    sha = target.get("sha") if isinstance(target, dict) else None
    if (
        not isinstance(target, dict) or target.get("type") != "commit"
        or not isinstance(sha, str) or not _SHA.fullmatch(sha)
    ):
        raise PromotionDispatchError(f"current {branch} identity is unavailable")
    return sha


def pending_source(
    repository: str, *, source_ci_run: int | None = None,
    completed_promotion_run: int | None = None, closed_main_pr: int | None = None,
    api: Callable[..., object] | None = None, wait_seconds: float = 120,
) -> DispatchDecision:
    modes = [value for value in (source_ci_run, completed_promotion_run, closed_main_pr)
             if value is not None]
    if (
        not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or len(modes) != 1
        or type(modes[0]) is not int or not 0 < modes[0] < 10 ** 20
        or type(wait_seconds) not in (int, float) or not 0 < wait_seconds <= 120
    ):
        raise PromotionDispatchError("invalid deferred-promotion request")
    caller = _api if api is None else api
    deadline = time.monotonic() + wait_seconds
    owner = repository.split("/", 1)[0].casefold()

    def request(path: str, *, body: dict | None = None) -> object:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PromotionDispatchError("deferred-promotion evidence deadline expired")
        endpoint = path if path == "graphql" else f"repos/{repository}/{path}"
        return caller(endpoint, timeout=min(30, remaining), **({"body": body} if body else {}))

    def state(ref: str) -> tuple[bool, str | None]:
        return pipeline_state(request, ref)

    def closed_pr(number: int, *, merged_required: bool) -> dict | None:
        data = request(f"pulls/{number}")
        if not isinstance(data, dict) or type(data.get("number")) is not int or data["number"] != number:
            raise PromotionDispatchError("closed Main PR identity is unavailable")
        if data.get("state") == "open":
            return None
        head, base, user = data.get("head"), data.get("base"), data.get("user")
        if (
            data.get("state") != "closed" or type(data.get("merged")) is not bool
            or not isinstance(head, dict) or not isinstance(head.get("ref"), str)
            or not isinstance(head.get("sha"), str) or not _SHA.fullmatch(head["sha"])
            or not isinstance(head.get("repo"), dict)
            or not isinstance(head["repo"].get("full_name"), str)
            or head["repo"]["full_name"].casefold() != repository.casefold()
            or not isinstance(base, dict) or base.get("ref") != "main"
            or not isinstance(user, dict) or not isinstance(user.get("login"), str)
            or user["login"].casefold() != owner
        ):
            raise PromotionDispatchError("closed Main PR does not bind the trusted repository owner")
        if merged_required and data["merged"] is not True:
            return None
        if data["merged"] and (
            not isinstance(data.get("merge_commit_sha"), str)
            or not _SHA.fullmatch(data["merge_commit_sha"])
        ):
            raise PromotionDispatchError("closed Main PR has no verified merge identity")
        return data

    if completed_promotion_run is not None:
        run = request(f"actions/runs/{completed_promotion_run}")
        if (
            not isinstance(run, dict) or type(run.get("id")) is not int
            or run["id"] != completed_promotion_run
            or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0
            or not isinstance(run.get("head_sha"), str) or not _SHA.fullmatch(run["head_sha"])
            or run.get("path") != ".github/workflows/validate-and-promote.yml"
            or run.get("head_branch") != "main"
            or run.get("event") not in ("repository_dispatch", "workflow_dispatch")
            or not isinstance(run.get("repository"), dict)
            or run["repository"].get("full_name") != repository
            or not isinstance(run.get("head_repository"), dict)
            or run["head_repository"].get("full_name") != repository
        ):
            raise PromotionDispatchError("completion is not the canonical trusted Main promoter")
        if run.get("status") != "completed":
            return DispatchDecision(None, "promotion-not-complete")
        if run.get("conclusion") not in (
            "success", "failure", "cancelled", "timed_out", "action_required",
            "neutral", "skipped", "stale", "startup_failure",
        ):
            raise PromotionDispatchError("completed promotion conclusion is unavailable or invalid")
        branch = f"release/promote-{completed_promotion_run}"
        rows = request(
            "pulls?state=closed&base=main&head="
            + quote(f"{repository.split('/')[0]}:{branch}", safe="") + "&per_page=100",
        )
        if not isinstance(rows, list) or len(rows) > 1:
            raise PromotionDispatchError("completed promotion PR inventory is ambiguous")
        if not rows:
            return DispatchDecision(None, "no-merged-promotion")
        row = rows[0]
        if not isinstance(row, dict) or type(row.get("number")) is not int or row["number"] <= 0:
            raise PromotionDispatchError("completed promotion PR identity is invalid")
        terminal = closed_pr(row["number"], merged_required=True)
        if terminal is None:
            return DispatchDecision(None, "no-merged-promotion")
        if terminal["head"]["ref"] != branch:
            raise PromotionDispatchError("completed promotion PR belongs to a different generation")
    elif closed_main_pr is not None:
        terminal = closed_pr(closed_main_pr, merged_required=False)
        if terminal is None:
            return DispatchDecision(None, "main-pr-reopened")
        if not terminal["merged"] and not _PROMOTION.fullmatch(terminal["head"]["ref"]):
            return DispatchDecision(None, "unmerged-control-pr")
        own = _PROMOTION.fullmatch(terminal["head"]["ref"])
        if own:
            run = request(f"actions/runs/{int(own[1])}")
            if (
                not isinstance(run, dict) or type(run.get("id")) is not int
                or run["id"] != int(own[1])
            ):
                raise PromotionDispatchError("closed promotion generation is unavailable")
            if not _canonical_run(run, repository):
                raise PromotionDispatchError("closed promotion is not the canonical Main promoter")
    else:
        terminal = None

    main_base = _branch_sha(request, "main")
    paused, released = state(main_base)
    if paused:
        return DispatchDecision(None, "main-paused")
    source = _branch_sha(request, "dev")
    if source == released:
        return DispatchDecision(None, "source-already-current")
    if retired_source(request, repository, source, state):
        return DispatchDecision(None, "retired-source-unchanged")
    if source_admissions(request, repository, source, main_base):
        return DispatchDecision(None, "newer-generation-owns-progress")

    def ci_succeeded(run: object) -> bool:
        if (
            not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0
            or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0
            or not isinstance(run.get("head_sha"), str) or not _SHA.fullmatch(run["head_sha"])
            or run.get("status") not in (
                "requested", "queued", "pending", "waiting", "in_progress", "completed",
            )
            or (run["status"] == "completed" and run.get("conclusion") not in (
                "success", "failure", "cancelled", "timed_out", "action_required",
                "neutral", "skipped", "stale", "startup_failure",
            ))
            or run.get("path") != ".github/workflows/ci.yml"
            or run.get("event") not in ("push", "workflow_dispatch")
            or not isinstance(run.get("repository"), dict)
            or run["repository"].get("full_name") != repository
            or not isinstance(run.get("head_repository"), dict)
            or run["head_repository"].get("full_name") != repository
        ):
            raise PromotionDispatchError("current CI evidence is incomplete or invalid")
        return run["head_sha"] == source and run["status"] == "completed" and run["conclusion"] == "success"

    event_ci = None
    if source_ci_run is not None:
        run = request(f"actions/runs/{source_ci_run}")
        if not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] != source_ci_run:
            raise PromotionDispatchError("source CI event identity is unavailable")
        if (
            run.get("path") != ".github/workflows/ci.yml"
            or run.get("event") not in ("push", "workflow_dispatch")
            or not isinstance(run.get("repository"), dict)
            or run["repository"].get("full_name") != repository
            or not isinstance(run.get("head_repository"), dict)
            or run["head_repository"].get("full_name") != repository
        ):
            return DispatchDecision(None, "source-ci-ineligible")
        ci_succeeded(run)
        if run["head_sha"] == source:
            event_ci = run
    rows = _pages(request, f"actions/workflows/ci.yml/runs?head_sha={source}", "workflow_runs")
    eligible: dict[int, dict] = {}
    for row in rows:
        if row.get("event") not in ("push", "workflow_dispatch"):
            continue
        ci_succeeded(row)
        if row["head_sha"] != source:
            raise PromotionDispatchError("current Dev CI inventory contains a different source")
        eligible[row["id"]] = row
    if event_ci is not None:
        indexed = eligible.get(event_ci["id"])
        if indexed is None or indexed["run_attempt"] <= event_ci["run_attempt"]:
            eligible[event_ci["id"]] = event_ci
    if eligible:
        latest = max(eligible.values(), key=lambda row: (_creation_time(row.get("created_at")), row["id"]))
        if ci_succeeded(latest):
            reason = "current-source-ci-succeeded" if latest is event_ci else "deferred-source-ci-succeeded"
            return DispatchDecision(source, reason)
    return DispatchDecision(None, "current-source-ci-not-ready")
