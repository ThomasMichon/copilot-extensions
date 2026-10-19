"""Immutable, source/base-bound admission evidence from the trusted Main Gate."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

_SHA = re.compile(r"[0-9a-f]{40}")
UPLOAD_STEP = "Upload immutable admitted source"
GATE_JOB = "gate (confirm this is a dev commit)"


class PromotionDispatchError(RuntimeError):
    """Scheduling evidence is unavailable; a new generation must not be guessed."""


@dataclass(frozen=True)
class SourceAdmission:
    run_id: int
    attempt: int
    artifact_id: int


def pages(request: Callable[..., object], path: str, key: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[int] = set()
    for page in range(1, 11):
        data = request(f"{path}&per_page=100&page={page}")
        batch = data.get(key) if isinstance(data, dict) else None
        if not isinstance(batch, list) or len(batch) > 100:
            raise PromotionDispatchError("promotion history page is invalid")
        for row in batch:
            identifier = row.get("id") if isinstance(row, dict) else None
            if type(identifier) is not int or identifier <= 0 or identifier in seen:
                raise PromotionDispatchError("promotion history contains ambiguous identities")
            seen.add(identifier)
            rows.append(row)
        if len(batch) < 100:
            if type(data.get("total_count")) is not int or data["total_count"] != len(rows):
                raise PromotionDispatchError("promotion history inventory is incomplete")
            return rows
    raise PromotionDispatchError("promotion history exceeds the bounded pagination limit")


def creation_time(value: object) -> str:
    if not isinstance(value, str):
        raise PromotionDispatchError("promotion generation creation time is unavailable")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PromotionDispatchError("promotion generation creation time is invalid") from error
    return value


def canonical_run(run: object, repository: str) -> bool:
    if (
        not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0
        or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0
        or not isinstance(run.get("head_sha"), str) or not _SHA.fullmatch(run["head_sha"])
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


def _succeeded(value: dict) -> bool:
    status, conclusion = value.get("status"), value.get("conclusion")
    if status not in ("queued", "in_progress", "completed", "waiting", "pending", "requested"):
        raise PromotionDispatchError("source admission execution status is invalid")
    if status == "completed" and conclusion not in (
        "success", "failure", "cancelled", "timed_out", "action_required",
        "neutral", "skipped", "stale", "startup_failure",
    ):
        raise PromotionDispatchError("source admission execution conclusion is invalid")
    return status == "completed" and conclusion == "success"


def source_admissions(
    request: Callable[..., object], repository: str, source: str, main_base: str,
) -> tuple[SourceAdmission, ...]:
    """Artifact names bind inputs; successful trusted Gate steps bind authority."""
    if not _SHA.fullmatch(source) or not _SHA.fullmatch(main_base):
        raise PromotionDispatchError("invalid source admission identity")
    name = f"promotion-admission-{source}-{main_base}"
    artifacts = pages(request, "actions/artifacts?name=" + quote(name, safe=""), "artifacts")
    owners: list[SourceAdmission] = []
    seen: set[int] = set()
    for artifact in artifacts:
        workflow = artifact.get("workflow_run")
        if (
            artifact.get("name") != name or type(artifact.get("size_in_bytes")) is not int
            or artifact["size_in_bytes"] <= 0 or type(artifact.get("expired")) is not bool
            or not isinstance(workflow, dict) or type(workflow.get("id")) is not int
            or workflow["id"] <= 0 or not isinstance(workflow.get("head_sha"), str)
            or not _SHA.fullmatch(workflow["head_sha"])
        ):
            raise PromotionDispatchError("source admission artifact identity is invalid")
        created = creation_time(artifact.get("created_at"))
        if workflow.get("head_branch") != "main":
            continue
        run = request(f"actions/runs/{workflow['id']}")
        if not canonical_run(run, repository):
            continue
        if run["id"] != workflow["id"] or run["head_sha"] != workflow["head_sha"]:
            raise PromotionDispatchError("source admission artifact belongs to another execution")
        jobs = pages(request, f"actions/runs/{run['id']}/jobs?filter=all", "jobs")
        matches: list[int] = []
        declared = False
        successful_upload = False
        for job in jobs:
            if job.get("name") != GATE_JOB:
                continue
            attempt, steps = job.get("run_attempt"), job.get("steps")
            if (
                type(attempt) is not int or not 0 < attempt <= run["run_attempt"]
                or not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps)
            ):
                raise PromotionDispatchError("source admission Gate attempt is invalid")
            gate_succeeded = _succeeded(job)
            uploads = [step for step in steps if step.get("name") == UPLOAD_STEP]
            if len(uploads) > 1:
                raise PromotionDispatchError("source admission upload step is ambiguous")
            if not uploads:
                continue
            declared = True
            upload = uploads[0]
            if not gate_succeeded or not _succeeded(upload):
                continue
            successful_upload = True
            started, completed = (
                creation_time(upload.get("started_at")), creation_time(upload.get("completed_at")),
            )
            if started <= created <= completed:
                matches.append(attempt)
        if not declared:
            # Older controllers did not issue admission records. A source job
            # cannot manufacture trusted Gate authority by borrowing the name.
            continue
        if not successful_upload:
            continue
        if not matches:
            raise PromotionDispatchError("source admission artifact has no successful Gate binding")
        if len(matches) != 1 or run["id"] in seen:
            raise PromotionDispatchError("source admission ownership is ambiguous")
        seen.add(run["id"])
        owners.append(SourceAdmission(run["id"], matches[0], artifact["id"]))
    return tuple(owners)
