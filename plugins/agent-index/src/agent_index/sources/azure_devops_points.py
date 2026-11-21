"""Explicit named-version reads, disconnected from discovery and execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from agent_index.indexing.component_manifest import ComponentIdentity, ComponentVersion
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import (
    CommentVersionTarget, PointBinding, PointTarget, WorkItemRevisionTarget,
)
from agent_index.indexing.work_counts import observe_acquisition
from agent_index.sources.azure_devops_eligibility import WorkItemEligibility
from agent_index.sources.azure_devops_work_item import _item, _object, _token
from agent_index.sources.base import FileEntry, nonsecret_target
from agent_index.sources.delta import DeletedFile

if TYPE_CHECKING:
    from agent_index.sources.azure_devops import AzureDevOpsConnector

_DECODED_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class NamedPointObservation:
    """One exact acquisition, not durable application or whole-claim coverage."""

    claim: PendingPointClaim
    target: PointTarget
    selection: WorkItemEligibility
    component: ComponentVersion
    entry: FileEntry | None
    deletion: DeletedFile | None


def _number(value: str) -> int:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 10
        or any(char not in "0123456789" for char in value)
        or value.startswith("0") or int(value) > 2_147_483_647
    ):
        raise ValueError("Azure DevOps point target requires a canonical positive int32 identity")
    return int(value)


def _require_selection(
    connector: AzureDevOpsConnector, claim: PendingPointClaim,
    item_id: int, expected: WorkItemEligibility,
) -> None:
    family = connector.source_name + ":workitem-components"
    endpoint = nonsecret_target(str(connector._http.client.base_url).rstrip("/"))
    binding = PointBinding(
        endpoint, json.dumps(connector._work_item_queries, sort_keys=True), expected.query_binding,
    )
    if (
        not expected.eligible or expected.item_id != item_id
        or expected.source_scope != (endpoint, connector.org, connector.project, claim.hints.account_id)
        or claim.hints.canonical_family != family or claim.hints.binding != binding
    ):
        raise ValueError("Azure DevOps point target account/query/representation binding changed")


def _point_component(connector: AzureDevOpsConnector, target: PointTarget) -> ComponentVersion:
    item_id = _number(target.item_id)
    if isinstance(target, WorkItemRevisionTarget):
        path, version = f"workitems/{item_id}/body.md", target.revision
        semantics = "ado-point-components/v1:work-item-body"
    else:
        comment_id = _number(target.comment_id)
        path, version = f"workitems/{item_id}/comments/{comment_id}.md", target.version
        semantics = "ado-point-components/v1:comment"
    return ComponentVersion(
        connector.source_name + ":workitem-components", path, version, semantics,
        ComponentIdentity(
            target.kind, target.item_id,
            target.comment_id if isinstance(target, CommentVersionTarget) else None,
        ),
    )


def acquire_named_point(
    connector: AzureDevOpsConnector, *, claim: PendingPointClaim, target: PointTarget,
    expected: WorkItemEligibility, cancel_check: Callable[[], None] | None = None,
) -> NamedPointObservation:
    """Read one qualified named version without admitting a source or executor.

    At most two 33-request eligibility checks and one exact version read permit
    67 logical requests / 335 attempts with the shared retry cap. Cancellation
    is checked between requests, not inside HTTP retries. Accepted decoded body
    and rendered content/metadata each fit 2 MiB; HTTP buffering/JSON decoding precede
    this check, so it is not a transport, peak-memory or elapsed-time budget.
    """
    from agent_index.sources.azure_devops import _identity_name, _plain

    if type(claim) is not PendingPointClaim or type(target) not in (
        WorkItemRevisionTarget, CommentVersionTarget,
    ) or target not in claim.hints.targets:
        raise ValueError("Azure DevOps point target must belong to the whole retained claim")
    item_id = _number(target.item_id)
    version = target.revision if isinstance(target, WorkItemRevisionTarget) else target.version
    if version > 2_147_483_647:
        raise ValueError("Azure DevOps point target version exceeds int32")
    comment_id = _number(target.comment_id) if isinstance(target, CommentVersionTarget) else None
    family = connector.source_name + ":workitem-components"
    _require_selection(connector, claim, item_id, expected)
    http = connector._http
    if type(http._max_retries) is not int or not 0 <= http._max_retries <= 4:
        raise ValueError("Azure DevOps point target exceeds the shared retry budget")

    def check() -> None:
        if cancel_check:
            cancel_check()

    def qualify() -> None:
        check()
        if connector.work_item_eligibility(item_id, cancel_check=cancel_check) != expected:
            raise ValueError("Azure DevOps point target eligibility changed")
        check()

    qualify()
    path = connector._project_path("_apis", "wit", "workitems", str(item_id))
    if comment_id is None:
        path += f"/revisions/{version}"
        api_version, purpose = "7.1", "item_body"
    else:
        path += f"/comments/{comment_id}/versions/{version}"
        api_version, purpose = "7.1-preview.1", "comment_page"
    component = _point_component(connector, target)
    component_path = component.path
    params = {"api-version": api_version}
    request = http.client.build_request("GET", path, params=params)
    check()
    result = http.get_json(path, params=params, etag=False, purpose=purpose)
    counts = observe_acquisition(family)
    if counts is not None and not result.not_modified and isinstance(result.data, dict):
        if comment_id is None:
            counts.item_body_records += 1
        else:
            counts.comment_body_records += 1
    check()
    # CommentVersion has no parent field; the actual request binds its item.
    if result.request is None or (result.request.method, result.request.url) != (
        request.method, request.url,
    ):
        raise ValueError("Azure DevOps point response belongs to a different request")
    data = _object(result)
    if (
        _token(result, data)
        or data.get("nextPage")
        or len(json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode()) > _DECODED_BYTES
    ):
        raise ValueError("Azure DevOps point response is continued or exceeds the decoded budget")
    metadata = {
        "representation": "point-components", "representation_version": 1,
        "coverage": "named-target-only", "component_kind": target.kind,
        "item_id": item_id, "provider_version": version,
    }
    entry: FileEntry | None
    deletion: DeletedFile | None = None
    if comment_id is None:
        _item(data, expected, version)
        original = connector._work_item_entry(data, [])
        entry = replace(
            original, path=component_path, source=family,
            metadata={**original.metadata, **metadata},
        )
    else:
        if (
            type(data.get("id")) is not int or data["id"] != comment_id
            or type(data.get("version")) is not int or data["version"] != version
            or type(data.get("isDeleted")) is not bool
            or "workItemId" in data and (
                type(data["workItemId"]) is not int or data["workItemId"] != item_id
            )
        ):
            raise ValueError("Azure DevOps comment has mismatched identity/version/deletion state")
        text = data.get("text")
        if not data["isDeleted"] and not isinstance(text, str):
            raise ValueError("Azure DevOps named comment requires complete text")
        if data["isDeleted"]:
            entry, deletion = None, DeletedFile(family, component_path)
        else:
            entry = FileEntry(
                component_path,
                f"# Work item {item_id}, comment {comment_id}\n\n{_plain(text)}\n",
                "work_item", family, metadata={
                    **metadata, "comment_id": comment_id,
                    "created_by": _identity_name(data.get("createdBy")),
                    "modified_by": _identity_name(data.get("modifiedBy")),
                    "modified_date": data.get("modifiedDate"),
                },
            )
    if entry is not None and len(json.dumps(
        {"content": entry.content, "metadata": entry.metadata}, ensure_ascii=True,
    ).encode()) > _DECODED_BYTES:
        raise ValueError("Azure DevOps point rendering exceeds the decoded-content budget")
    qualify()
    return NamedPointObservation(
        claim, target, expected, component, entry, deletion,
    )
