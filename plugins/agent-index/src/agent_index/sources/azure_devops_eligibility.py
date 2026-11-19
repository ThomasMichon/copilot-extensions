"""Read-only, singleton work-item eligibility under a finite query union."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_index.sources.azure_devops_query_scope import restrict_wiql_to_candidates
from agent_index.sources.good_citizen_http import ApiResult, GoodCitizenSession

_API_VERSION = "7.1"
_MAX_QUERIES = 16


@dataclass(frozen=True)
class WorkItemEligibility:
    """Query membership evidence, not deletion or reconciliation authority."""

    item_id: int
    eligible: bool
    source_scope: tuple[str, ...]
    query_binding: str


def _fresh_object(result: ApiResult) -> dict[str, Any]:
    if result.not_modified or not isinstance(result.data, dict):
        raise ValueError("Azure DevOps eligibility requires a fresh object response")
    if result.headers.get("x-ms-continuationtoken") or result.data.get("continuationToken"):
        raise ValueError("Azure DevOps eligibility refuses continued results")
    return result.data


def _definition(result: ApiResult, saved_id: str) -> dict[str, Any]:
    data = _fresh_object(result)
    if (
        not isinstance(data.get("id"), str)
        or data["id"].casefold() != saved_id.casefold()
        or data.get("queryType") != "flat"
    ):
        raise ValueError("Azure DevOps eligibility requires the requested flat saved definition")
    for flag in ("isFolder", "isDeleted", "isInvalidSyntax"):
        if flag in data and (type(data[flag]) is not bool or data[flag]):
            raise ValueError("Azure DevOps eligibility refuses unavailable saved definitions")
    modified = data.get("lastModifiedDate")
    if modified is not None and (not isinstance(modified, str) or len(modified) > 256):
        raise ValueError("Azure DevOps eligibility received invalid saved-definition metadata")
    return {
        key: data[key]
        for key in (
            "id",
            "queryType",
            "wiql",
            "isFolder",
            "isDeleted",
            "isInvalidSyntax",
            "lastModifiedDate",
        )
        if key in data
    }


def _selected(result: ApiResult, item_id: int, source: str) -> bool:
    from agent_index.indexing.work_counts import observe_acquisition

    data = _fresh_object(result)
    records = data.get("workItems")
    if records is None and data.get("queryType") == "flat":
        records = []
    if isinstance(records, list) and (counts := observe_acquisition(source)) is not None:
        counts.identity_records += sum(isinstance(record, dict) for record in records)
    if (
        data.get("queryType") != "flat"
        or data.get("workItemRelations") not in (None, [])
        or not isinstance(records, list)
        or len(records) > 1
        or any(
            not isinstance(record, dict)
            or type(record.get("id")) is not int
            or record["id"] != item_id
            for record in records
        )
        or ("count" in data and (type(data["count"]) is not int or data["count"] != len(records)))
        or any(flag in data and data[flag] is not False for flag in ("isTruncated", "hasMore"))
    ):
        raise ValueError("Azure DevOps eligibility received invalid singleton selection")
    return bool(records)


def qualify_work_item(
    http: GoodCitizenSession,
    *,
    item_id: int,
    queries: list[dict[str, Any]],
    project_path: Callable[..., str],
    org_path: Callable[..., str],
    source: str,
    scope: tuple[str, ...],
    cancel_check: Callable[[], None] | None = None,
) -> WorkItemEligibility | None:
    """Evaluate every configured query without widening discovery or persisting progress.

    Empty configuration returns None without I/O. At most sixteen queries permit
    one account read, sixteen definition reads and sixteen singleton selections.
    The shared HTTP client's bounded retries and throttling remain in force.
    """
    if type(item_id) is not int or not 1 <= item_id <= 2_147_483_647:
        raise ValueError("Azure DevOps eligibility requires one positive int32 candidate")
    if len(queries) > _MAX_QUERIES:
        raise ValueError("Azure DevOps eligibility exceeds the 16-query budget")

    def check() -> None:
        if cancel_check:
            cancel_check()

    check()
    if not queries:
        return None
    # Validate inline members before any I/O, even when a saved member precedes them.
    for spec in queries:
        if "wiql" in spec:
            restrict_wiql_to_candidates(spec["wiql"], [item_id])
        elif (
            not isinstance(spec.get("saved_query_id"), str)
            or not 1 <= len(spec["saved_query_id"]) <= 256
        ):
            raise ValueError("Azure DevOps eligibility requires a bounded saved-query ID")
    check()
    account_data = _fresh_object(
        http.get_json(
            org_path("_apis", "connectionData"),
            params={"api-version": _API_VERSION},
            etag=False,
            purpose="metadata",
        )
    )
    check()
    user = account_data.get("authenticatedUser")
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 256:
        raise ValueError("Azure DevOps eligibility requires an authenticated account ID")

    prepared: list[str] = []
    bindings: list[dict[str, Any]] = []
    for spec in queries:
        check()
        definition: dict[str, Any] | None = None
        if "wiql" in spec:
            wiql = spec["wiql"]
        else:
            saved_id = spec["saved_query_id"]
            result = http.get_json(
                project_path("_apis", "wit", "queries", saved_id),
                params={"api-version": _API_VERSION, "$expand": "clauses"},
                etag=False,
                purpose="metadata",
            )
            check()
            definition = _definition(result, saved_id)
            wiql = definition.get("wiql")
        prepared.append(restrict_wiql_to_candidates(wiql, [item_id]))
        bindings.append({"spec": dict(spec), "definition": definition})

    eligible = False
    for wiql in prepared:
        check()
        result = http.post_json(
            project_path("_apis", "wit", "wiql"),
            params={"api-version": _API_VERSION},
            json={"query": wiql},
            purpose="identity_page",
        )
        selected = _selected(result, item_id, source)
        check()
        eligible = eligible or selected
    binding = hashlib.sha256(
        json.dumps(bindings, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
    ).hexdigest()
    return WorkItemEligibility(item_id, eligible, (*scope, user_id), binding)
