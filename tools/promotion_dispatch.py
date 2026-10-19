"""Recover deferred Dev advances from existing immutable GitHub scheduling data."""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from verify_promotion_workflows import _api

_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-f]{40}")
_PROMOTION = re.compile(r"release/promote-([1-9][0-9]{0,19})")
_STATE = ".github/release-pipeline-state.json"


class PromotionDispatchError(RuntimeError):
    """Scheduling evidence is unavailable; a new generation must not be guessed."""


@dataclass(frozen=True)
class DispatchDecision:
    source_sha: str | None
    reason: str


def _pages(request: Callable[[str], object], path: str, key: str | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: set[int] = set()
    for page in range(1, 11):
        data = request(f"{path}&per_page=100&page={page}")
        batch = data.get(key) if key is not None and isinstance(data, dict) else data
        if not isinstance(batch, list) or len(batch) > 100:
            raise PromotionDispatchError("promotion history page is invalid")
        for row in batch:
            identifier = row.get("id" if key else "number") if isinstance(row, dict) else None
            if type(identifier) is not int or identifier <= 0 or identifier in seen:
                raise PromotionDispatchError("promotion history contains ambiguous identities")
            seen.add(identifier)
            rows.append(row)
        if len(batch) < 100:
            if key is not None and (
                type(data.get("total_count")) is not int or data["total_count"] != len(rows)
            ):
                raise PromotionDispatchError("promotion history inventory is incomplete")
            return rows
    raise PromotionDispatchError("promotion history exceeds the bounded pagination limit")


def _admitted(request: Callable[[str], object], run: dict) -> bool:
    """Only a successful Gate plus non-skipped source work owns a generation."""
    jobs = _pages(request, f"actions/runs/{run['id']}/jobs?filter=all", "jobs")
    admitted_attempts: set[int] = set()
    source_attempts: set[int] = set()
    for job in jobs:
        attempt, name = job.get("run_attempt"), job.get("name")
        if type(attempt) is not int or attempt <= 0 or not isinstance(name, str):
            raise PromotionDispatchError("promotion admission job identity is invalid")
        status, conclusion = job.get("status"), job.get("conclusion")
        if status not in ("queued", "in_progress", "completed", "waiting", "pending", "requested"):
            raise PromotionDispatchError("promotion admission job status is invalid")
        if status == "completed" and conclusion not in (
            "success", "failure", "cancelled", "timed_out", "action_required",
            "neutral", "skipped", "stale", "startup_failure",
        ):
            raise PromotionDispatchError("promotion admission job conclusion is invalid")
        if name == "gate (confirm this is a dev commit)" and (
            status == "completed" and conclusion == "success"
        ):
            admitted_attempts.add(attempt)
        started = job.get("started_at")
        if started is not None:
            _creation_time(started)
        if name.startswith("full - ") and conclusion != "skipped" and started is not None:
            source_attempts.add(attempt)
    return bool(admitted_attempts & source_attempts)


def _creation_time(value: object) -> str:
    if not isinstance(value, str):
        raise PromotionDispatchError("promotion generation creation time is unavailable")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PromotionDispatchError("promotion generation creation time is invalid") from error
    return value


def _canonical_run(run: object, repository: str) -> bool:
    if (
        not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0
        or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0
    ):
        raise PromotionDispatchError("promotion generation identity is invalid")
    return (
        run.get("head_branch") == "main"
        and run.get("path") == ".github/workflows/validate-and-promote.yml"
        and run.get("event") in ("repository_dispatch", "workflow_dispatch")
        and isinstance(run.get("repository"), dict)
        and run["repository"].get("full_name") == repository
        and isinstance(run.get("head_repository"), dict)
        and run["head_repository"].get("full_name") == repository
    )


def _newer_admitted(request: Callable[[str], object], repository: str, run: dict) -> bool:
    if not _canonical_run(run, repository):
        raise PromotionDispatchError("terminal promotion is not the canonical trusted Main promoter")
    created = _creation_time(run.get("created_at"))
    generations = _pages(
        request, "actions/workflows/validate-and-promote.yml/runs?created="
        + quote(">=" + created, safe=""), "workflow_runs",
    )
    for generation in generations:
        canonical = _canonical_run(generation, repository)
        ordering = (_creation_time(generation.get("created_at")), generation["id"])
        if canonical and ordering > (created, run["id"]) and _admitted(request, generation):
            return True
    return False


def _retirements(request: Callable[..., object], repository: str) -> list[dict]:
    """CLOSED excludes MERGED here; REST's state=closed includes both."""
    owner, name = repository.split("/")
    cursor = None
    rows: list[dict] = []
    seen: set[int] = set()
    for _ in range(10):
        query = (
            "query($owner:String!,$name:String!,$cursor:String){"
            "repository(owner:$owner,name:$name){pullRequests("
            "states:CLOSED,baseRefName:\"main\",first:100,after:$cursor,"
            "orderBy:{field:CREATED_AT,direction:DESC}){totalCount "
            "pageInfo{hasNextPage endCursor} nodes{number state merged "
            "headRefName headRefOid headRepository{nameWithOwner} author{login}}}}}"
        )
        data = request("graphql", body={
            "query": query, "variables": {"owner": owner, "name": name, "cursor": cursor},
        })
        if not isinstance(data, dict) or data.get("errors"):
            raise PromotionDispatchError("closed-unmerged promotion history is unavailable")
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
            raise PromotionDispatchError("closed-unmerged promotion history is incomplete")
        for row in batch:
            if (
                not isinstance(row, dict) or type(row.get("number")) is not int
                or row["number"] <= 0 or row["number"] in seen
                or row.get("state") != "CLOSED" or row.get("merged") is not False
            ):
                raise PromotionDispatchError("closed-unmerged promotion identity is invalid")
            seen.add(row["number"])
            rows.append(row)
        if not info["hasNextPage"]:
            if connection["totalCount"] != len(rows):
                raise PromotionDispatchError("closed-unmerged promotion history changed")
            return rows
        next_cursor = info.get("endCursor")
        if (
            not batch or not isinstance(next_cursor, str) or not 0 < len(next_cursor) <= 1024
            or next_cursor == cursor
        ):
            raise PromotionDispatchError("closed-unmerged promotion cursor is invalid")
        cursor = next_cursor
    raise PromotionDispatchError("closed-unmerged promotion history exceeds the pagination limit")


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
) -> None:
    """Recheck queued automatic admissions against retirements recorded meanwhile."""
    if (
        not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or not isinstance(source, str) or not _SHA.fullmatch(source)
        or type(wait_seconds) not in (int, float) or not 0 < wait_seconds <= 120
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
        if _newer_admitted(request, repository, run):
            return DispatchDecision(None, "newer-generation-owns-progress")
    elif closed_main_pr is not None:
        terminal = closed_pr(closed_main_pr, merged_required=False)
        if terminal is None:
            return DispatchDecision(None, "main-pr-reopened")
        if not terminal["merged"] and not _PROMOTION.fullmatch(terminal["head"]["ref"]):
            return DispatchDecision(None, "unmerged-control-pr")
        own = _PROMOTION.fullmatch(terminal["head"]["ref"])
        if own:
            # The same ordering rule must govern delayed closure and completion.
            run = request(f"actions/runs/{int(own[1])}")
            if (
                not isinstance(run, dict) or type(run.get("id")) is not int
                or run["id"] != int(own[1])
            ):
                raise PromotionDispatchError("closed promotion generation is unavailable")
            if _newer_admitted(request, repository, run):
                return DispatchDecision(None, "newer-generation-owns-progress")
        else:
            generations = _pages(
                request, "actions/workflows/validate-and-promote.yml/runs?status=in_progress",
                "workflow_runs",
            )
            if any(_canonical_run(generation, repository) and _admitted(request, generation)
                   for generation in generations):
                return DispatchDecision(None, "newer-generation-owns-progress")
    else:
        terminal = None

    paused, released = state("main")
    if paused:
        return DispatchDecision(None, "main-paused")
    ref = request("git/ref/heads/dev")
    target = ref.get("object") if isinstance(ref, dict) else None
    source = target.get("sha") if isinstance(target, dict) else None
    if (
        not isinstance(target, dict) or target.get("type") != "commit"
        or not isinstance(source, str) or not _SHA.fullmatch(source)
    ):
        raise PromotionDispatchError("current Dev identity is unavailable")
    if source == released:
        return DispatchDecision(None, "source-already-current")
    if retired_source(request, repository, source, state):
        return DispatchDecision(None, "retired-source-unchanged")

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
        if ci_succeeded(run):
            return DispatchDecision(source, "current-source-ci-succeeded")
    data = request(f"actions/workflows/ci.yml/runs?event=push&head_sha={source}&per_page=100")
    rows = data.get("workflow_runs") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict) or not isinstance(rows, list) or type(data.get("total_count")) is not int
        or data["total_count"] != len(rows) or len(rows) > 1
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise PromotionDispatchError("current Dev CI inventory is incomplete or ambiguous")
    if len(rows) == 1 and ci_succeeded(rows[0]):
        return DispatchDecision(source, "deferred-source-ci-succeeded")
    return DispatchDecision(None, "current-source-ci-not-ready")
