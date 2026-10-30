"""Pure read generations; acquisition, source custody and effects have other owners."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlsplit


EVIDENCE_SCHEMA = "agent-pull-requests.review-evidence"
GENERATION_SCHEMA = "agent-pull-requests.review-generation"
SECTIONS = ("pull_request", "iterations", "changes", "discussions", "reviewers", "policies")
MAX_BYTES = 8 * 1024 * 1024
MAX_GENERATION_BYTES = 2 * MAX_BYTES
MAX_RECORDS = 20_000


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str) or not value.strip() or value != value.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"{field} must be a nonempty string without surrounding whitespace")
    return value


def _scope(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("scope must be an object")
    names = ("provider", "endpoint", "project_id", "repository_id", "principal_id", "number")
    if set(value) != set(names):
        raise ValueError("scope requires exactly provider/endpoint/project/repository/principal/number")
    result = {key: _text(value.get(key), key) for key in names if key != "number"}
    if result["provider"] != "azure-devops":
        raise ValueError("unsupported provider; this adapter supports azure-devops")
    try:
        endpoint = urlsplit(result["endpoint"])
        invalid_endpoint = (
            endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment
        )
        _ = endpoint.port
    except ValueError:
        invalid_endpoint = True
    if invalid_endpoint:
        raise ValueError("endpoint must be an explicit HTTPS endpoint without credentials or query")
    result["endpoint"] = result["endpoint"].rstrip("/")
    if type(value.get("number")) is not int or value["number"] <= 0:
        raise ValueError("number must be a positive integer")
    result["number"] = value["number"]
    return result


def _select(value: dict, names: tuple[str, ...]) -> dict:
    return {key: copy.deepcopy(value[key]) for key in names if key in value}


def _actor(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("actor must be an object or null")
    return _select(value, ("id", "displayName", "uniqueName", "isContainer"))


def _commit(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("commit must be an object or null")
    result = _select(value, ("commitId",))
    if "commitId" in result and (
        not isinstance(result["commitId"], str)
        or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", result["commitId"])
    ):
        raise ValueError("commitId must be a full lowercase Git object id")
    return result


def _pull(value: dict, scope: dict, complete: bool) -> dict:
    repository = value.get("repository")
    if repository is not None and not isinstance(repository, dict):
        raise ValueError("pull request repository must be an object")
    project = (repository or {}).get("project")
    if project is not None and not isinstance(project, dict):
        raise ValueError("pull request project must be an object")
    for actual, expected, label in (
        (value.get("pullRequestId"), scope["number"], "PR"),
        ((repository or {}).get("id"), scope["repository_id"], "repository"),
        ((project or {}).get("id"), scope["project_id"], "project"),
    ):
        if actual is not None and (type(actual) is not type(expected) or actual != expected):
            raise ValueError(f"pull request {label} does not match scope")
        if complete and actual is None:
            raise ValueError(f"complete pull request lacks {label} identity")
    result = _select(value, (
        "pullRequestId", "title", "description", "status", "isDraft",
        "sourceRefName", "targetRefName", "creationDate", "closedDate",
        "mergeStatus", "completionQueueTime", "completionOptions",
        "supportsIterations", "hasMultipleMergeBases",
    ))
    for field in ("createdBy", "closedBy", "autoCompleteSetBy"):
        if field in value:
            result[field] = _actor(value[field])
    for field in ("lastMergeSourceCommit", "lastMergeTargetCommit", "lastMergeCommit"):
        if field in value:
            result[field] = _commit(value[field])
    if repository is not None:
        result["repository"] = _select(repository, ("id", "name"))
        if project is not None:
            result["repository"]["project"] = _select(project, ("id", "name"))
    return result


def _record(name: str, value: dict) -> dict:
    if name == "iterations":
        result = _select(value, ("id", "description", "createdDate", "updatedDate", "reason"))
        if type(result.get("id")) is not int or result["id"] <= 0:
            raise ValueError("iteration requires a positive integer id")
        for field in ("sourceRefCommit", "targetRefCommit", "commonRefCommit"):
            if field in value:
                result[field] = _commit(value[field])
        return result
    if name == "changes":
        result = _select(value, ("changeTrackingId", "changeId", "changeType", "originalPath"))
        item = value.get("item")
        if not isinstance(item, dict):
            raise ValueError("change item must be an object with a usable repository path")
        path = item.get("path")
        if (
            not isinstance(path, str) or not path.startswith("/") or path == "/"
            or "\0" in path or any(part in (".", "..") for part in path.split("/"))
        ):
            raise ValueError("change item must have a usable repository path")
        result["item"] = _select(item, (
            "path", "objectId", "originalObjectId", "gitObjectType", "isFolder",
        ))
        return result
    if name == "discussions":
        result = _select(value, (
            "id", "status", "publishedDate", "lastUpdatedDate", "isDeleted",
            "threadContext", "pullRequestThreadContext",
        ))
        if "properties" in value:
            if not isinstance(value["properties"], dict):
                raise ValueError("discussion properties must be an object")
            result["properties"] = _select(value["properties"], (
                "CodeReviewThreadType", "CodeReviewVoteResult", "CodeReviewVotedByTfId",
            ))
        if "comments" in value:
            if not isinstance(value["comments"], list):
                raise ValueError("discussion comments must be a list")
            result["comments"] = []
            for comment in value["comments"]:
                if not isinstance(comment, dict):
                    raise ValueError("discussion comment must be an object")
                item = _select(comment, (
                    "id", "parentCommentId", "content", "commentType", "publishedDate",
                    "lastUpdatedDate", "lastContentUpdatedDate", "isDeleted",
                ))
                if "author" in comment:
                    item["author"] = _actor(comment["author"])
                result["comments"].append(item)
        return result
    if name == "reviewers":
        return _select(value, (
            "id", "displayName", "uniqueName", "vote", "isRequired",
            "isContainer", "votedFor", "hasDeclined", "isFlagged",
        ))
    return _select(value, (
        "evaluationId", "status", "startedDate", "completedDate",
        "configuration", "context",
    ))


def _instant(value: object) -> datetime:
    text = _text(value, "observation time")
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("observation time must be an ISO-8601 instant") from None
    if result.utcoffset() is None:
        raise ValueError("observation time requires a timezone")
    return result


def _section(name: str, value: object, scope: dict) -> dict:
    if value is None:
        return {
            "data": None, "complete": False, "errors": ["not_supplied"],
            "observed_from": None, "observed_to": None,
        }
    if not isinstance(value, dict) or type(value.get("complete")) is not bool:
        raise ValueError(f"{name} requires explicit completeness")
    errors = value.get("errors")
    if not isinstance(errors, list) or any(not isinstance(error, str) or not error for error in errors):
        raise ValueError(f"{name} requires explicit error codes")
    if value["complete"] and errors:
        raise ValueError(f"{name} cannot be complete with errors")
    start, end = value.get("observed_from"), value.get("observed_to")
    if _instant(end) < _instant(start):
        raise ValueError(f"{name} observation interval is reversed")
    data = value.get("data")
    if name == "pull_request":
        if not isinstance(data, dict):
            raise ValueError("pull_request data must be an object")
        data = _pull(data, scope, value["complete"])
    else:
        if (
            not isinstance(data, list) or len(data) > MAX_RECORDS
            or any(not isinstance(row, dict) for row in data)
        ):
            raise ValueError(f"{name} data must be a bounded list of objects")
        data = [_record(name, row) for row in data]
        if name == "iterations" and len({row["id"] for row in data}) != len(data):
            raise ValueError("iteration ids must be unique")
        # Provider order is not material, but comment order within a thread is.
        data.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
    result = {
        "data": data, "complete": value["complete"], "errors": list(errors),
        "observed_from": start, "observed_to": end,
    }
    if name == "changes":
        binding = value.get("revision")
        if not isinstance(binding, dict) or set(binding) != {"head_sha", "iteration", "common_sha"}:
            raise ValueError("changes requires a head/iteration/common revision binding")
        if type(binding["iteration"]) is not int or binding["iteration"] <= 0:
            raise ValueError("changes iteration must be a positive integer")
        result["revision"] = {
            "head_sha": _text(binding["head_sha"], "changes head_sha"),
            "iteration": binding["iteration"],
            "common_sha": _text(binding["common_sha"], "changes common_sha"),
        }
    return result


def _revision(sections: dict) -> dict:
    pull = sections["pull_request"]["data"] or {}
    head = (pull.get("lastMergeSourceCommit") or {}).get("commitId")
    iterations = sections["iterations"]["data"] or []
    latest = (
        max(iterations, key=lambda row: row["id"])
        if iterations and sections["iterations"]["complete"] and sections["pull_request"]["complete"]
        else {}
    )
    iteration_head = (latest.get("sourceRefCommit") or {}).get("commitId")
    current = latest if head and iteration_head == head else {}
    return {
        "head_sha": head, "iteration": current.get("id"),
        "iteration_head_sha": iteration_head,
        "iteration_target_sha": (current.get("targetRefCommit") or {}).get("commitId"),
        "common_sha": (current.get("commonRefCommit") or {}).get("commitId"),
        "target_sha": (pull.get("lastMergeTargetCommit") or {}).get("commitId"),
    }


def _bounded(value: object, budget: int) -> None:
    try:
        encoded = json.dumps(value, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("workspace input must be finite JSON") from None
    if len(encoded) > budget:
        raise ValueError("workspace input exceeds its byte budget")


def _generation(value: object, scope: dict) -> dict:
    _bounded(value, MAX_GENERATION_BYTES)
    if not isinstance(value, dict):
        raise ValueError("previous generation must be an object")
    body = {key: item for key, item in value.items() if key != "id"}
    if (
        value.get("schema") != GENERATION_SCHEMA or type(value.get("version")) is not int
        or value["version"] != 1 or value.get("id") != _digest(body)
        or value.get("scope") != scope or not isinstance(value.get("sections"), dict)
        or set(value["sections"]) != set(SECTIONS)
        or any(not isinstance(section, dict) for section in value["sections"].values())
        or not isinstance(value.get("revision"), dict)
    ):
        raise ValueError("previous generation is incompatible, corrupt or outside this scope")
    return value


def build_generation(evidence: object, previous: object = None) -> dict:
    """Normalize supplied evidence; no IO, eligibility decision or upstream effects."""
    _bounded(evidence, MAX_BYTES)
    if (
        not isinstance(evidence, dict) or evidence.get("schema") != EVIDENCE_SCHEMA
        or type(evidence.get("version")) is not int or evidence["version"] != 1
    ):
        raise ValueError("unsupported review-evidence contract; expected version 1")
    scope = _scope(evidence.get("scope"))
    raw = evidence.get("sections")
    if not isinstance(raw, dict) or set(raw) - set(SECTIONS):
        raise ValueError("sections must be an object with supported section names")
    sections = {name: _section(name, raw.get(name), scope) for name in SECTIONS}
    revision = _revision(sections)
    changes = sections["changes"]
    if changes["data"] is not None and any(
        not revision[key] or changes["revision"][key] != revision[key]
        for key in ("head_sha", "iteration", "common_sha")
    ):
        changes["complete"] = False
        changes["errors"] = [*changes["errors"], "revision_mismatch"]
    body = {
        "schema": GENERATION_SCHEMA, "version": 1, "scope": scope,
        "revision": revision, "sections": sections,
        "complete": (
            all(section["complete"] for section in sections.values())
            and all(revision[key] for key in ("head_sha", "iteration", "common_sha"))
        ),
        "source_attachment": {"state": "unsupported", "head_sha": revision["head_sha"]},
    }
    generation = {**body, "id": _digest(body)}
    _bounded(generation, MAX_GENERATION_BYTES)
    before = _generation(previous, scope) if previous is not None else None
    changed = [
        name for name in SECTIONS if before is None
        or {k: v for k, v in sections[name].items() if not k.startswith("observed_")}
        != {k: v for k, v in before["sections"][name].items() if not k.startswith("observed_")}
    ]
    invalidations = set()
    if before is None or "changes" in changed or any(
        not revision[key] or revision[key] != before["revision"].get(key)
        for key in ("head_sha", "iteration", "common_sha")
    ):
        invalidations.update(("source_analysis", "inline_anchors", "review_decision"))
    if "discussions" in changed:
        invalidations.update(("discussion_decisions", "review_decision"))
    if changed:
        invalidations.add("review_decision")
    return {
        "schema": "agent-pull-requests.review-sync", "version": 1,
        "previous_id": before["id"] if before else None,
        "generation": generation, "changed_sections": changed,
        "invalidations": sorted(invalidations),
        "acquisition": "caller_supplied", "effects": "none",
    }
