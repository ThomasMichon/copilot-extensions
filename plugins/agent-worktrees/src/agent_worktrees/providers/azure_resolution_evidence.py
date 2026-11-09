"""Full native candidate and discussion evidence for selected ADO resolution."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from ..pr_ado_remediation import RemediationError, digest
from .azure_observation import property_value, validate_target
from .azure_remediation import complete, full_sha, positive, thread_fingerprint
from .azure_observation_reads import AdoReader
from .base import ProviderError


def native_id(value: Any) -> str:
    if not isinstance(value, str):
        raise RemediationError("Canonical native identity is unavailable")
    try:
        result = str(UUID(value))
    except ValueError:
        raise RemediationError("Canonical native identity is malformed") from None
    if result == str(UUID(int=0)):
        raise RemediationError("Canonical native identity is unavailable")
    return result


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise RemediationError("Canonical HTTPS organization endpoint is required")
    _ = parsed.port
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def _ref(value: Any) -> str:
    if (not isinstance(value, str) or not value.startswith("refs/heads/")
            or len(value) > 1024 or len(value) <= len("refs/heads/")
            or re.search(r"[\x00-\x20\x7f~^:?*\\\[]|\.\.|@\{", value)):
        raise RemediationError("Native branch identity is unavailable")
    return value


def candidate(reader: AdoReader, pr: dict, provider: Any, repo: str,
              number: int) -> tuple[dict, set[int]]:
    """Version 1 hashes the full endpoint/target/source/refs/commits/iteration tuple."""
    validate_target(pr, provider, repo)
    if type(pr.get("pullRequestId")) is not int or pr["pullRequestId"] != number:
        raise RemediationError("Native PR identity differs from the selected target")
    repository = pr["repository"]
    project = repository["project"]
    fork = pr.get("forkSource")
    if fork is not None and not isinstance(fork, dict):
        raise RemediationError("Native source repository is ambiguous")
    source = fork.get("repository") if fork is not None else repository
    if not isinstance(source, dict) or not isinstance(source.get("project"), dict):
        raise RemediationError("Native source repository identity is unavailable")
    iterations = complete(reader.iterations(require_count=True))
    ids = [positive(row.get("id"), "Iteration identity") for row in iterations]
    if not ids or len(ids) != len(set(ids)):
        raise RemediationError("Native source iteration is unavailable or ambiguous")
    latest = max(iterations, key=lambda row: row["id"])
    head = full_sha((pr.get("lastMergeSourceCommit") or {}).get("commitId"))
    base = full_sha((pr.get("lastMergeTargetCommit") or {}).get("commitId"))
    if full_sha((latest.get("sourceRefCommit") or {}).get("commitId")) != head:
        raise RemediationError("Native source and iteration evidence differ")
    value = {
        "version": 1, "endpoint": endpoint(reader.api_base),
        "project_id": native_id(project.get("id")),
        "repository_id": native_id(repository.get("id")), "number": number,
        "source_project_id": native_id(source["project"].get("id")),
        "source_repository_id": native_id(source.get("id")),
        "source_ref": _ref(pr.get("sourceRefName")), "target_ref": _ref(pr.get("targetRefName")),
        "head_sha": head, "base_sha": base,
        "common_sha": full_sha((latest.get("commonRefCommit") or {}).get("commitId")),
        "iteration": latest["id"],
    }
    return {**value, "fingerprint": "sha256:" + digest(value)}, set(ids)


def discussions(reader: AdoReader, iteration_ids: set[int]) -> tuple[dict, ...]:
    """Require the endpoint's complete inline comments and unique native identities."""
    rows = complete(reader.threads(require_count=True))
    validate_discussions(rows, iteration_ids)
    return rows


def validate_discussions(rows: tuple[dict, ...], iteration_ids: set[int]) -> None:
    ids = [positive(row.get("id"), "Thread identity") for row in rows]
    if len(ids) != len(set(ids)):
        raise RemediationError("Native thread identities are ambiguous")
    for row in rows:
        comments = row.get("comments")
        if not isinstance(comments, list) or any(not isinstance(c, dict) for c in comments):
            raise RemediationError("Native nested discussion evidence is unavailable")
        if any(not isinstance(c.get("author"), dict) for c in comments):
            raise RemediationError("Native discussion author evidence is unavailable")
        if (not isinstance(row.get("properties") or {}, dict)
                or not isinstance(row.get("pullRequestThreadContext") or {}, dict)):
            raise RemediationError("Native discussion context is malformed")
        if (row.get("isDeleted") is True
                or property_value(row.get("properties") or {}, "CodeReviewThreadType") == "VoteUpdate"
                or not any(c.get("commentType") in ("text", 1) and not c.get("isDeleted") for c in comments)):
            continue
        thread_fingerprint(row)
        comment_ids = {comment["id"] for comment in comments}
        if any(c["parentCommentId"] and c["parentCommentId"] not in comment_ids for c in comments):
            raise RemediationError("Native parent comment evidence is incomplete")
        if type(row.get("status")) not in (int, str):
            raise RemediationError("Native thread status is malformed")
        context = (row.get("pullRequestThreadContext") or {}).get("iterationContext")
        if context is not None and any(context.get(key) not in iteration_ids for key in (
            "firstComparingIteration", "secondComparingIteration",
        )):
            raise RemediationError("Native discussion iteration is unavailable")


def report_candidate(reader: AdoReader, pr: dict, provider: Any, repo: str,
                     number: int) -> tuple[tuple[dict, set[int]] | None, str]:
    """Missing additive evidence must not strengthen or invalidate a legacy report."""
    try:
        return candidate(reader, pr, provider, repo, number), ""
    except (ValueError, TypeError, KeyError, AttributeError, ProviderError) as exc:
        return None, str(exc)


def report_evidence(before: tuple[dict, set[int]] | None, after: tuple[dict, set[int]] | None, *,
                    complete_report: bool, rows: tuple[dict, ...], error: str = "") -> dict:
    """Expose a usable expectation only for one complete, stable candidate."""
    changed = before[0] != after[0] if before is not None and after is not None else None
    if before is not None and after is not None and complete_report and not changed:
        try:
            validate_discussions(rows, after[1])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            error = str(exc)
        else:
            return {"available": True, "changed": False, "candidate": after[0], "error": ""}
    return {
        "available": False, "changed": changed, "candidate": None,
        "observed_candidate": after[0] if after is not None else None,
        "error": error or "Complete stable native resolution evidence is unavailable",
    }
