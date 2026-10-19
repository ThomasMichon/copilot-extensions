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

    def request(path: str) -> object:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PromotionDispatchError("deferred-promotion evidence deadline expired")
        return caller(f"repos/{repository}/{path}", timeout=min(30, remaining))

    def state(ref: str) -> tuple[bool, str | None]:
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
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            return DispatchDecision(None, "promotion-not-complete")
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
        created = run.get("created_at")
        if not isinstance(created, str):
            raise PromotionDispatchError("completed promotion creation time is unavailable")
        try:
            datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise PromotionDispatchError("completed promotion creation time is invalid") from error
        newer = request(
            "actions/workflows/validate-and-promote.yml/runs?created="
            + quote(">=" + created, safe="") + "&per_page=100",
        )
        generations = newer.get("workflow_runs") if isinstance(newer, dict) else None
        if not isinstance(newer, dict) or not isinstance(generations, list) or len(generations) > 100:
            raise PromotionDispatchError("promotion generation inventory is unavailable")
        for generation in generations:
            if (
                not isinstance(generation, dict) or type(generation.get("id")) is not int
                or generation["id"] <= 0 or not isinstance(generation.get("created_at"), str)
            ):
                raise PromotionDispatchError("promotion generation identity is invalid")
            try:
                datetime.strptime(generation["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            except ValueError as error:
                raise PromotionDispatchError("promotion generation creation time is invalid") from error
            if (
                generation.get("head_branch") == "main"
                and generation.get("path") == ".github/workflows/validate-and-promote.yml"
                and generation.get("event") in ("repository_dispatch", "workflow_dispatch")
                and (
                    generation["created_at"] > created
                    or (generation["created_at"] == created and generation["id"] > completed_promotion_run)
                )
            ):
                return DispatchDecision(None, "newer-generation-owns-progress")
        if type(newer.get("total_count")) is not int or newer["total_count"] != len(generations):
            raise PromotionDispatchError("promotion generation inventory is incomplete")
    elif closed_main_pr is not None:
        terminal = closed_pr(closed_main_pr, merged_required=False)
        if terminal is None:
            return DispatchDecision(None, "main-pr-reopened")
        if not terminal["merged"] and not _PROMOTION.fullmatch(terminal["head"]["ref"]):
            return DispatchDecision(None, "unmerged-control-pr")
        running = request("actions/workflows/validate-and-promote.yml/runs?status=in_progress&per_page=100")
        generations = running.get("workflow_runs") if isinstance(running, dict) else None
        if (
            not isinstance(running, dict) or not isinstance(generations, list)
            or type(running.get("total_count")) is not int
            or running["total_count"] != len(generations) or len(generations) > 100
        ):
            raise PromotionDispatchError("running promotion inventory is incomplete")
        own = _PROMOTION.fullmatch(terminal["head"]["ref"])
        for generation in generations:
            if (
                not isinstance(generation, dict) or type(generation.get("id")) is not int
                or generation["id"] <= 0
            ):
                raise PromotionDispatchError("running promotion identity is invalid")
            if (
                generation.get("path") == ".github/workflows/validate-and-promote.yml"
                and generation.get("head_branch") == "main"
                and (own is None or generation["id"] != int(own[1]))
            ):
                return DispatchDecision(None, "another-generation-running")
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
    if terminal is not None and not terminal["merged"]:
        _, retired_source = state(terminal["head"]["sha"])
        if retired_source is None:
            raise PromotionDispatchError("retired promotion has no recorded source identity")
        if retired_source == source:
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
