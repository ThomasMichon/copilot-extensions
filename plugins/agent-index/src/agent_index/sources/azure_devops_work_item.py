"""Finite current work-item observations; not snapshot or deletion coverage."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_index.sources.azure_devops_eligibility import WorkItemEligibility
from agent_index.sources.base import FileEntry
from agent_index.sources.good_citizen_http import ApiResult

if TYPE_CHECKING:
    from agent_index.sources.azure_devops import AzureDevOpsConnector

_COMMENT_PAGES = 4
_PAGE_SIZE = 100
_DECODED_BYTES = 2 * 1024 * 1024
_TOKEN_BYTES = 4096


@dataclass(frozen=True)
class WorkItemObservation:
    """One rendered subject and its observed versions, without atomic coverage.

    The comments API has no documented snapshot fence. Neither stable item
    reads nor terminal pagination proves an immutable discussion or query exit.
    This value deliberately is not a SourceDelta or AcquisitionPage.
    """

    selection: WorkItemEligibility
    revision: int
    entry: FileEntry
    comment_versions: tuple[tuple[int, int, bool], ...]


def _object(result: ApiResult) -> dict[str, Any]:
    if result.not_modified or not isinstance(result.data, dict):
        raise ValueError("Azure DevOps work-item unit requires a fresh object")
    data = result.data
    if any(flag in data and data[flag] is not False for flag in ("isTruncated", "hasMore")):
        raise ValueError("Azure DevOps work-item unit refuses truncated responses")
    return data


def _token(result: ApiResult, data: dict[str, Any]) -> str | None:
    body = data.get("continuationToken")
    header = result.headers.get("x-ms-continuationtoken")
    for token in (body, header):
        if token not in (None, "") and (
            not isinstance(token, str)
            or not token.strip()
            or len(token.encode("utf-8")) > _TOKEN_BYTES
        ):
            raise ValueError("Azure DevOps work-item unit has invalid continuation")
    if body and header and body != header:
        raise ValueError("Azure DevOps work-item unit has inconsistent continuation")
    token = body or header or None
    if data.get("nextPage") and not token:
        raise ValueError("Azure DevOps work-item unit is missing continuation")
    return token


def _item(data: dict[str, Any], selection: WorkItemEligibility, revision: int) -> None:
    fields = data.get("fields")
    if (
        type(data.get("id")) is not int
        or data["id"] != selection.item_id
        or type(data.get("rev")) is not int
        or data["rev"] != revision
        or not isinstance(fields, dict)
        or not isinstance(fields.get("System.Title"), str)
    ):
        raise ValueError("Azure DevOps work-item unit has mismatched identity, revision or fields")
    for field in (
        "System.Title",
        "System.WorkItemType",
        "System.State",
        "System.AreaPath",
        "System.IterationPath",
        "System.Tags",
        "System.ChangedDate",
        "System.Description",
        "Microsoft.VSTS.TCM.ReproSteps",
        "Microsoft.VSTS.Common.AcceptanceCriteria",
    ):
        if fields.get(field) is not None and not isinstance(fields[field], str):
            raise ValueError("Azure DevOps work-item unit has invalid rendered fields")


def acquire_work_item(
    connector: AzureDevOpsConnector,
    *,
    expected: WorkItemEligibility,
    revision: int,
    cancel_check: Callable[[], None] | None = None,
) -> WorkItemObservation | None:
    """Acquire one query-qualified item, with no persistent state or legacy fallback.

    At most two 33-request eligibility checks, two body reads and four comment
    pages give 72 logical requests / 360 attempts with the shared retry cap.
    Cancellation is checked between logical requests, not inside HTTP retries.
    Four hundred comment records and 2 MiB of serialized decoded body/comment
    responses are accepted at most. HTTP buffering/JSON decoding precede that
    check: this is not a transport-byte, peak-memory or elapsed-time limit.
    Empty queries and negative membership return None, never a tombstone.
    """
    if type(revision) is not int or not 1 <= revision <= 2_147_483_647:
        raise ValueError("Azure DevOps work-item unit requires a positive int32 revision")
    http = connector._http
    if type(http._max_retries) is not int or not 0 <= http._max_retries <= 4:
        raise ValueError("Azure DevOps work-item unit exceeds the shared retry budget")

    def check() -> None:
        if cancel_check:
            cancel_check()

    check()
    selection = connector.work_item_eligibility(expected.item_id, cancel_check=cancel_check)
    if selection is None:
        return None
    if selection != expected:
        raise ValueError("Azure DevOps work-item unit selection binding changed")
    if not selection.eligible:
        return None

    remaining = _DECODED_BYTES

    def decode(result: ApiResult) -> dict[str, Any]:
        nonlocal remaining
        check()
        data = _object(result)
        remaining -= len(json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode())
        if remaining < 0:
            raise ValueError("Azure DevOps work-item unit exceeds the decoded-content budget")
        return data

    path = connector._project_path("_apis", "wit", "workitems", str(selection.item_id))

    def body() -> dict[str, Any]:
        check()
        result = http.get_json(
            path,
            params={"api-version": "7.1"},
            etag=False,
            purpose="item_body",
        )
        connector._observe_records([result.data], kind="workitems")
        data = decode(result)
        if _token(result, data):
            raise ValueError("Azure DevOps work-item unit refuses continued bodies")
        _item(data, selection, revision)
        return data

    item = body()
    comments: list[dict[str, Any]] = []
    versions: dict[int, tuple[int, int, bool]] = {}
    seen_tokens: set[str] = set()
    token: str | None = None
    total: int | None = None
    for _ in range(_COMMENT_PAGES):
        check()
        result = http.get_json(
            f"{path}/comments",
            params={
                "api-version": "7.1-preview.4",
                "$top": _PAGE_SIZE,
                "order": "asc",
                "includeDeleted": "true",
                "continuationToken": token,
            },
            etag=False,
            purpose="comment_page",
        )
        raw = result.data
        connector._observe_records(
            raw.get("comments") if isinstance(raw, dict) else None,
            kind="workitem_comments",
        )
        data = decode(result)
        rows = data.get("comments")
        count, page_total = data.get("count"), data.get("totalCount")
        if (
            not isinstance(rows, list)
            or len(rows) > _PAGE_SIZE
            or type(count) is not int
            or count != len(rows)
            or type(page_total) is not int
            or not 0 <= page_total <= _COMMENT_PAGES * _PAGE_SIZE
            or (total is not None and total != page_total)
        ):
            raise ValueError("Azure DevOps work-item unit has invalid or over-budget comments")
        total = page_total
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Azure DevOps work-item unit has an invalid comment")
            comment_id = row.get("id", row.get("commentId"))
            version, deleted = row.get("version"), row.get("isDeleted", False)
            if (
                type(comment_id) is not int
                or comment_id <= 0
                or comment_id in versions
                or (
                    "commentId" in row
                    and (type(row["commentId"]) is not int or row["commentId"] != comment_id)
                )
                or type(version) is not int
                or version <= 0
                or type(row.get("workItemId")) is not int
                or row["workItemId"] != selection.item_id
                or type(deleted) is not bool
                or (not deleted and not isinstance(row.get("text"), str))
            ):
                raise ValueError(
                    "Azure DevOps work-item unit has invalid comment identity or body"
                )
            versions[comment_id] = (comment_id, version, deleted)
            if not deleted:
                comments.append(row)
        token = _token(result, data)
        if not token:
            if len(versions) != total:
                raise ValueError("Azure DevOps work-item unit has incomplete comment coverage")
            break
        if not rows or token in seen_tokens:
            raise ValueError("Azure DevOps work-item unit continuation made no progress")
        seen_tokens.add(token)
    else:
        raise ValueError("Azure DevOps work-item unit exceeds the comment-page budget")

    if body() != item:
        raise ValueError("Azure DevOps work-item unit body changed during acquisition")
    if connector.work_item_eligibility(selection.item_id, cancel_check=cancel_check) != selection:
        raise ValueError("Azure DevOps work-item unit selection changed during acquisition")
    check()
    entry = connector._work_item_entry(item, comments)
    if len(entry.content.encode("utf-8")) > _DECODED_BYTES:
        raise ValueError("Azure DevOps work-item unit exceeds the rendered-content budget")
    return WorkItemObservation(selection, revision, entry, tuple(versions.values()))
