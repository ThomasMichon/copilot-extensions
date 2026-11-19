"""Exact named-version evidence never becomes combined-discussion coverage."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.prepared_points import PointAcquisition, PreparedPoints
from agent_index.indexing.point_hints import (
    CommentVersionTarget, PointBinding, PointChangeHints, PointTarget, WorkItemRevisionTarget,
)
from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.azure_devops import AzureDevOpsConnector
from agent_index.sources.azure_devops_eligibility import WorkItemEligibility
from agent_index.sources.azure_devops_points import acquire_named_point
from agent_index.sources.base import nonsecret_target

_QUERY = "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
_ITEM = WorkItemRevisionTarget("7", 3, ("event-a",))
_COMMENT = CommentVersionTarget("7", "5", 2, ("event-b",))


class Provider:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.item: dict[str, Any] = {
            "id": 7, "rev": 3,
            "fields": {"System.Title": "Selected", "System.Description": "<p>Body</p>"},
            "commentVersionRef": {"commentId": 99, "version": 8},
        }
        self.comment: dict[str, Any] = {
            "id": 5, "version": 2, "text": "<p>Named discussion</p>", "isDeleted": False,
        }
        self.user = "fixture-user"
        self.selected = True
        self.status = 200
        self.headers: dict[str, str] = {}
        self.redirect = False
        self.on_body: Callable[[], None] = lambda: None
        self.body_reads = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert "if-none-match" not in request.headers
        if request.url.path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": self.user}})
        if "/queries/" in request.url.path:
            return httpx.Response(200, json={"id": "saved", "queryType": "flat", "wiql": _QUERY})
        if request.method == "POST":
            assert request.url.path.endswith("/wiql")
            return httpx.Response(200, json={
                "queryType": "flat", "workItems": [{"id": 7}] if self.selected else [],
            })
        if "/comments/5/versions/2" in request.url.path:
            if self.redirect and "/workitems/7/" in request.url.path:
                return httpx.Response(302, headers={
                    "location": "/acme/widgets/_apis/wit/workitems/8/comments/5/versions/2"
                    "?api-version=7.1-preview.1",
                })
            assert request.url.params["api-version"] == "7.1-preview.1"
            payload = self.comment
        else:
            assert request.url.path.endswith("/workitems/7/revisions/3")
            assert request.url.params["api-version"] == "7.1"
            payload = self.item
        self.body_reads += 1
        self.on_body()
        return httpx.Response(self.status, json=payload, headers=self.headers)


@pytest.fixture
def subject() -> Iterator[tuple[Provider, AzureDevOpsConnector, WorkItemEligibility]]:
    provider = Provider()
    connector = AzureDevOpsConnector(
        "ado:acme/widgets", token="fixture-token", min_interval_s=0,
        work_item_queries=[{"wiql": _QUERY}], pull_request_queries=[],
        transport=httpx.MockTransport(provider.handle),
    )
    connector._http._max_retries = 0
    expected = connector.work_item_eligibility(7)
    assert expected is not None
    provider.requests.clear()
    try:
        yield provider, connector, expected
    finally:
        connector._http.client.close()


def _claim(
    connector: AzureDevOpsConnector, expected: WorkItemEligibility,
) -> PendingPointClaim:
    return PendingPointClaim("retained-task", "a" * 32, PointChangeHints(
        source="selected", canonical_family=connector.source_name + ":workitem-components",
        account_id=expected.source_scope[-1],
        binding=PointBinding(
            nonsecret_target(str(connector._http.client.base_url).rstrip("/")),
            json.dumps(connector._work_item_queries, sort_keys=True), expected.query_binding,
        ),
        targets=(_ITEM, _COMMENT),
    ))


def test_retained_named_reads_resume_without_repeating_provider_body_acquisition(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], tmp_path: Path,
) -> None:
    provider, connector, expected = subject
    claim = _claim(connector, expected)
    first = acquire_named_point(connector, claim=claim, target=_ITEM, expected=expected)
    assert first.entry is not None
    paths = PathIndex(tmp_path / "paths.db")
    manifest = CrawlManifest(
        source=claim.hints.source, accounting_family=claim.hints.canonical_family,
        commit="unchanged-poll", upserts=[first.entry], component_versions=[first.component],
    )
    pending = PendingCrawls(paths)
    pending.save(manifest, scope="account/endpoint/query", full=False, point_claim=claim)
    payload = pending.read_payload(manifest.source, "account/endpoint/query")
    assert payload is not None

    def reopen() -> PreparedPoints:
        return PreparedPoints(PreparedJournal(
            PathIndex(tmp_path / "paths.db"), manifest.source, "account/endpoint/query", payload,
            point_claim=claim,
        ))

    points = reopen()
    points.retain(PointAcquisition(
        first.target, first.component, first.entry, first.deletion,
    ), claim=first.claim)
    with pytest.raises(ValueError, match="incomplete"):
        points.compose()
    for target in claim.hints.targets:
        points = reopen()
        if points.read(target) is None:
            observed = acquire_named_point(connector, claim=claim, target=target, expected=expected)
            points.retain(PointAcquisition(
                observed.target, observed.component, observed.entry, observed.deletion,
            ), claim=observed.claim)
    candidate = reopen().compose()
    assert provider.body_reads == 2
    assert len(candidate.upserts) == 2 and candidate.commit == "unchanged-poll"
    assert candidate.poll_refresh is None
    assert pending.read_payload(manifest.source, "account/endpoint/query") == payload


@pytest.mark.parametrize("target", [_ITEM, _COMMENT])
def test_exact_point_retains_claim_and_never_fetches_discussion_or_legacy_current_body(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], target: PointTarget,
) -> None:
    provider, connector, expected = subject
    claim = _claim(connector, expected)
    result = acquire_named_point(connector, claim=claim, target=target, expected=expected)
    assert result.claim == claim and result.target == target and result.selection == expected
    assert result.entry is not None and result.deletion is None
    assert result.entry.source == claim.hints.canonical_family
    assert result.entry.metadata["coverage"] == "named-target-only"
    assert result.component.source == result.entry.source
    assert result.component.path == result.entry.path
    assert provider.body_reads == 1 and len(provider.requests) == 5
    assert not hasattr(connector, "discover_hints_page") and connector.current_commit() is None
    if isinstance(target, WorkItemRevisionTarget):
        assert result.entry.path == "workitems/7/body.md" and result.component.version == 3
        assert result.entry.content == connector._work_item_entry(provider.item, []).content
        assert connector._work_item_entry(provider.item, []).path == "workitems/7.md"
        assert "Comments" not in result.entry.content
    else:
        assert result.entry.path == "workitems/7/comments/5.md" and result.component.version == 2
        assert "Named discussion" in result.entry.content


def test_named_deleted_comment_is_only_its_versioned_component_deletion(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
) -> None:
    provider, connector, expected = subject
    provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    result = acquire_named_point(
        connector, claim=_claim(connector, expected), target=_COMMENT, expected=expected,
    )
    assert result.entry is None and result.deletion is not None
    assert result.deletion.path == "workitems/7/comments/5.md"
    assert result.deletion.source == result.component.source
    assert result.component.version == 2 and provider.body_reads == 1


@pytest.mark.parametrize("mismatch", ["account", "query", "family", "endpoint", "target", "item"])
def test_binding_and_unclaimed_target_refuse_before_io(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], mismatch: str,
) -> None:
    provider, connector, expected = subject
    claim, target = _claim(connector, expected), _ITEM
    if mismatch == "account":
        claim = replace(claim, hints=replace(claim.hints, account_id="other"))
    elif mismatch == "query":
        claim = replace(claim, hints=replace(
            claim.hints, binding=replace(claim.hints.binding, query_context="other"),
        ))
    elif mismatch == "family":
        claim = replace(claim, hints=replace(
            claim.hints, canonical_family=connector.source_name + ":workitems",
        ))
    elif mismatch == "endpoint":
        claim = replace(claim, hints=replace(
            claim.hints, binding=replace(claim.hints.binding, endpoint="https://other.example"),
        ))
    elif mismatch == "target":
        target = WorkItemRevisionTarget("7", 4)
    else:
        expected = replace(expected, item_id=8)
    with pytest.raises(ValueError):
        acquire_named_point(connector, claim=claim, target=target, expected=expected)
    assert provider.requests == []


@pytest.mark.parametrize("field,value", [
    ("id", 8), ("rev", 4), ("rev", True), ("fields", {}),
])
def test_wrong_item_revision_never_becomes_coverage(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], field: str, value: Any,
) -> None:
    provider, connector, expected = subject
    provider.item[field] = value
    with pytest.raises(ValueError):
        acquire_named_point(connector, claim=_claim(connector, expected), target=_ITEM,
                            expected=expected)


@pytest.mark.parametrize("field,value", [
    ("id", 8), ("id", True), ("version", 3), ("version", True),
    ("isDeleted", None), ("isDeleted", 1), ("workItemId", 8), ("text", None),
    ("hasMore", True), ("continuationToken", "more"), ("nextPage", "next"),
])
def test_comment_response_requires_exact_version_and_complete_unit(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], field: str, value: Any,
) -> None:
    provider, connector, expected = subject
    provider.comment[field] = value
    with pytest.raises(ValueError):
        acquire_named_point(connector, claim=_claim(connector, expected), target=_COMMENT,
                            expected=expected)


def test_actual_comment_request_parent_is_required_even_without_a_body_parent_field(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
) -> None:
    provider, connector, expected = subject
    provider.redirect = True
    connector._http.client.follow_redirects = True
    with pytest.raises(ValueError, match="different request"):
        acquire_named_point(connector, claim=_claim(connector, expected), target=_COMMENT,
                            expected=expected)
    assert any("/workitems/8/" in r.url.path for r in provider.requests)


@pytest.mark.parametrize("change", ["account", "membership", "query"])
def test_context_change_after_exact_read_preserves_an_unacknowledged_claim(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], change: str,
) -> None:
    provider, connector, expected = subject

    def changed() -> None:
        if change == "account":
            provider.user = "other"
        elif change == "membership":
            provider.selected = False
        else:
            connector._work_item_queries = [{"wiql": _QUERY.replace("Active", "Done")}]

    provider.on_body = changed
    with pytest.raises(ValueError, match="eligibility changed"):
        acquire_named_point(connector, claim=_claim(connector, expected), target=_ITEM,
                            expected=expected)
    assert provider.body_reads == 1


@pytest.mark.parametrize("status", [403, 404])
def test_missing_or_denied_is_not_deletion(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], status: int,
) -> None:
    provider, connector, expected = subject
    provider.status = status
    with pytest.raises(httpx.HTTPStatusError):
        acquire_named_point(connector, claim=_claim(connector, expected), target=_COMMENT,
                            expected=expected)
    assert provider.body_reads == 1


def test_cancel_after_point_read_and_decoded_budget_refuse(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, connector, expected = subject

    def cancelled() -> None:
        if provider.body_reads:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        acquire_named_point(connector, claim=_claim(connector, expected), target=_ITEM,
                            expected=expected, cancel_check=cancelled)
    monkeypatch.setattr("agent_index.sources.azure_devops_points._DECODED_BYTES", 128)
    with pytest.raises(ValueError, match="decoded budget"):
        acquire_named_point(connector, claim=_claim(connector, expected), target=_ITEM,
                            expected=expected)


def test_maximum_query_union_stays_at_67_logical_requests(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
) -> None:
    provider, connector, _expected = subject
    connector._work_item_queries = [{"saved_query_id": "saved"}] * 16
    expected = connector.work_item_eligibility(7)
    assert expected is not None
    provider.requests.clear()
    acquire_named_point(connector, claim=_claim(connector, expected), target=_ITEM, expected=expected)
    assert len(provider.requests) == 67 and provider.body_reads == 1


@pytest.mark.parametrize("target", [
    WorkItemRevisionTarget("07", 3), WorkItemRevisionTarget("../7", 3),
    WorkItemRevisionTarget("2147483648", 3), WorkItemRevisionTarget("7", 2147483648),
    CommentVersionTarget("7", "05", 2),
])
def test_noncanonical_or_oversized_ado_targets_refuse_before_io(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility], target: PointTarget,
) -> None:
    provider, connector, expected = subject
    claim = _claim(connector, expected)
    claim = replace(claim, hints=replace(claim.hints, targets=(target,)))
    with pytest.raises(ValueError, match="int32"):
        acquire_named_point(connector, claim=claim, target=target, expected=expected)
    assert provider.requests == []


@pytest.mark.parametrize("oversized", [False, True])
def test_actual_body_counts_are_component_scoped_even_when_decoded_budget_refuses(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    monkeypatch: pytest.MonkeyPatch, oversized: bool,
) -> None:
    _provider, connector, expected = subject
    if oversized:
        monkeypatch.setattr("agent_index.sources.azure_devops_points._DECODED_BYTES", 128)

    @measure_reindex
    def run() -> dict[str, Any]:
        if oversized:
            with pytest.raises(ValueError, match="decoded budget"):
                acquire_named_point(connector, claim=_claim(connector, expected), target=_ITEM,
                                    expected=expected)
        else:
            acquire_named_point(connector, claim=_claim(connector, expected), target=_ITEM,
                                expected=expected)
        return {}

    counts = cast(dict[str, Any], run()["work_counts"])
    acquired = counts["acquisition_by_source"][connector.source_name + ":workitem-components"]
    assert acquired["item_body_records"] == 1 and acquired["comment_body_records"] == 0
    assert counts["http_by_purpose"]["item_body"]["attempts"] == 1
