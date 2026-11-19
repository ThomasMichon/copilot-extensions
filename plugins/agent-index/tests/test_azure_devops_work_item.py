"""Finite subject acquisition refuses partial or unbound work without legacy fallback."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import httpx
import pytest

from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.azure_devops import AzureDevOpsConnector
from agent_index.sources.azure_devops_eligibility import WorkItemEligibility
from agent_index.sources.azure_devops_work_item import acquire_work_item

_QUERY = "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
_SOURCE = "ado:acme/widgets:workitems"


def _comment(number: int, *, deleted: bool = False) -> dict[str, Any]:
    return {
        "id": number,
        "workItemId": 7,
        "version": 2,
        "text": f"Discussion {number}",
        "isDeleted": deleted,
        "createdBy": {"displayName": "Writer"},
        "modifiedDate": "2026-01-03T00:00:00Z",
    }


class Provider:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.item: dict[str, Any] = {
            "id": 7,
            "rev": 3,
            "fields": {"System.Title": "Selected", "System.Description": "<p>Body</p>"},
        }
        self.pages: list[dict[str, Any]] = [{"comments": [], "count": 0, "totalCount": 0}]
        self.user = "fixture-user"
        self.query = _QUERY
        self.selected = True
        self.body_reads = 0
        self.status = 200
        self.response_headers: dict[str, str] = {}
        self.on_request: Callable[[httpx.Request], None] = lambda _: None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert "if-none-match" not in request.headers
        self.on_request(request)
        if request.url.path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": self.user}})
        if "/queries/" in request.url.path:
            return httpx.Response(
                200,
                json={"id": "saved", "queryType": "flat", "wiql": self.query},
            )
        if request.method == "POST":
            assert request.url.path.endswith("/wiql")
            return httpx.Response(
                200,
                json={"queryType": "flat", "workItems": [{"id": 7}] if self.selected else []},
            )
        assert "/workitems/7" in request.url.path
        if request.url.path.endswith("/comments"):
            assert request.url.params["$top"] == "100"
            assert request.url.params["includeDeleted"] == "true"
            assert request.url.params["order"] == "asc"
            token = request.url.params.get("continuationToken")
            payload = self.pages[int(token) if token else 0]
        else:
            self.body_reads += 1
            payload = self.item
        return httpx.Response(self.status, json=payload, headers=self.response_headers)


@pytest.fixture
def subject() -> Iterator[tuple[Provider, AzureDevOpsConnector, WorkItemEligibility]]:
    provider = Provider()
    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        work_item_queries=[{"wiql": _QUERY}],
        pull_request_queries=[],
        transport=httpx.MockTransport(provider.handle),
    )
    expected = connector.work_item_eligibility(7)
    assert expected is not None
    provider.requests.clear()
    try:
        yield provider, connector, expected
    finally:
        connector._http.client.close()


def test_complete_observation_reuses_renderer_and_preserves_counts(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
) -> None:
    provider, connector, expected = subject
    provider.pages = [
        {"comments": [_comment(1)], "count": 1, "totalCount": 3, "continuationToken": "1"},
        {"comments": [_comment(2, deleted=True), _comment(3)], "count": 2, "totalCount": 3},
    ]

    @measure_reindex
    def run() -> dict[str, object]:
        observation = acquire_work_item(connector, expected=expected, revision=3)
        assert observation is not None
        assert observation.selection == expected and observation.revision == 3
        assert observation.comment_versions == ((1, 2, False), (2, 2, True), (3, 2, False))
        assert observation.entry == connector._work_item_entry(
            provider.item,
            [_comment(1), _comment(3)],
        )
        assert "Discussion 2" not in observation.entry.content
        assert connector.current_commit() is None
        assert not hasattr(connector, "discover_hints_page")
        return {}

    counts = cast(dict[str, Any], run()["work_counts"])
    assert len(provider.requests) == 8
    assert counts["http_by_purpose"]["item_body"]["attempts"] == 2
    assert counts["http_by_purpose"]["comment_page"]["attempts"] == 2
    assert counts["acquisition_by_source"][_SOURCE] == {
        "identity_records": 2,
        "item_body_records": 2,
        "comment_body_records": 3,
    }


def test_maximum_admitted_unit_uses_72_logical_requests(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
) -> None:
    provider, connector, _ = subject
    connector._work_item_queries = [{"saved_query_id": "saved"}] * 16
    expected = connector.work_item_eligibility(7)
    assert expected is not None
    provider.requests.clear()
    provider.pages = [
        {
            "comments": [_comment(number) for number in range(page * 100 + 1, page * 100 + 101)],
            "count": 100,
            "totalCount": 400,
            **({"continuationToken": str(page + 1)} if page < 3 else {}),
        }
        for page in range(4)
    ]
    result = acquire_work_item(connector, expected=expected, revision=3)
    assert result is not None and len(result.comment_versions) == 400
    assert len(provider.requests) == 72


@pytest.mark.parametrize("negative", [False, True])
def test_empty_or_negative_selection_never_fetches_bodies_or_implies_deletion(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    negative: bool,
) -> None:
    provider, connector, expected = subject
    if negative:
        provider.selected = False
        expected = replace(expected, eligible=False)
    else:
        connector._work_item_queries = []
    assert acquire_work_item(connector, expected=expected, revision=3) is None
    assert provider.body_reads == 0
    assert len(provider.requests) == (2 if negative else 0)


@pytest.mark.parametrize(
    "change",
    ["account", "query", "endpoint", "project", "eligible", "item"],
)
def test_stale_selection_refuses_before_body(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    change: str,
) -> None:
    provider, connector, expected = subject
    if change == "account":
        provider.user = "other-user"
    elif change == "query":
        connector._work_item_queries = [{"wiql": _QUERY.replace("Active", "Done")}]
    elif change in {"endpoint", "project"}:
        scope = list(expected.source_scope)
        scope[0 if change == "endpoint" else 2] += "-other"
        expected = replace(expected, source_scope=tuple(scope))
    elif change == "eligible":
        provider.selected = False
    else:
        expected = replace(expected, item_id=8)
    with pytest.raises(ValueError):
        acquire_work_item(connector, expected=expected, revision=3)
    assert provider.body_reads == 0


@pytest.mark.parametrize("change", ["account", "saved-query", "body", "revision"])
def test_mid_unit_mutation_refuses_without_returning_partial_entry(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    change: str,
) -> None:
    provider, connector, _ = subject
    connector._work_item_queries = [{"saved_query_id": "saved"}]
    expected = connector.work_item_eligibility(7)
    assert expected is not None

    def mutate(request: httpx.Request) -> None:
        if request.url.path.endswith("/comments"):
            if change == "account":
                provider.user = "changed"
            elif change == "saved-query":
                provider.query = _QUERY.replace("Active", "Done")
            elif change == "body":
                provider.item["fields"]["System.Description"] = "Edited"
            else:
                provider.item["rev"] = 4

    provider.on_request = mutate
    with pytest.raises(ValueError):
        acquire_work_item(connector, expected=expected, revision=3)


@pytest.mark.parametrize(
    "patch",
    [
        {"id": 8},
        {"id": True},
        {"rev": 4},
        {"rev": True},
        {"fields": {}},
        {"fields": {"System.Title": "x", "System.Description": {}}},
        {"continuationToken": "more"},
        {"isTruncated": True},
        {"fields": {"System.Title": "x", "System.Description": "x" * (2 * 1024 * 1024)}},
    ],
)
def test_invalid_or_oversized_body_refuses_before_comments(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    patch: dict[str, Any],
) -> None:
    provider, connector, expected = subject
    provider.item.update(patch)
    with pytest.raises(ValueError):
        acquire_work_item(connector, expected=expected, revision=3)
    assert not any(request.url.path.endswith("/comments") for request in provider.requests)


@pytest.mark.parametrize(
    "page",
    [
        {},
        {"comments": [], "count": 0, "totalCount": 401},
        {"comments": [], "count": True, "totalCount": 0},
        {"comments": [], "count": 0, "totalCount": 1},
        {"comments": [], "count": 0, "totalCount": 0, "nextPage": "https://other.invalid"},
        {"comments": [_comment(1)] * 101, "count": 101, "totalCount": 101},
        {"comments": [_comment(1)] * 2, "count": 2, "totalCount": 2},
        {"comments": [_comment(1) | {"text": None}], "count": 1, "totalCount": 1},
        {"comments": [_comment(1) | {"workItemId": 8}], "count": 1, "totalCount": 1},
        {"comments": [_comment(1) | {"version": True}], "count": 1, "totalCount": 1},
        {"comments": [_comment(1) | {"isDeleted": "false"}], "count": 1, "totalCount": 1},
        {"comments": [], "count": 0, "totalCount": 0, "continuationToken": ["x"]},
        {"comments": [], "count": 0, "totalCount": 0, "continuationToken": "x" * 4097},
        {
            "comments": [_comment(1) | {"text": "x" * (2 * 1024 * 1024)}],
            "count": 1,
            "totalCount": 1,
        },
    ],
)
def test_partial_or_malformed_comments_refuse(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    page: dict[str, Any],
) -> None:
    provider, connector, expected = subject
    provider.pages = [page]
    with pytest.raises(ValueError):
        acquire_work_item(connector, expected=expected, revision=3)
    assert provider.body_reads == 1


@pytest.mark.parametrize("failure", ["cycle", "budget", "changed-total", "duplicate"])
def test_continuation_cannot_hide_partial_history(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    failure: str,
) -> None:
    provider, connector, expected = subject
    provider.pages = [
        {
            "comments": [_comment(page + 1)],
            "count": 1,
            "totalCount": 5,
            "continuationToken": str(page + 1),
        }
        for page in range(4)
    ]
    if failure == "cycle":
        provider.pages[1]["continuationToken"] = "1"
    elif failure == "changed-total":
        provider.pages[1]["totalCount"] = 6
    elif failure == "duplicate":
        provider.pages[1]["comments"] = [_comment(1)]
    with pytest.raises(ValueError):
        acquire_work_item(connector, expected=expected, revision=3)
    assert sum(request.url.path.endswith("/comments") for request in provider.requests) <= 4


@pytest.mark.parametrize("status", [304, 403, 404])
def test_unavailable_body_is_not_empty_success_or_tombstone(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    provider, connector, expected = subject
    provider.status = status
    monkeypatch.setattr(connector._http, "_sleep_for_retry", lambda *_: None)
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        acquire_work_item(connector, expected=expected, revision=3)
    assert provider.body_reads == (5 if status == 403 else 1)


def test_cancellation_keeps_already_parsed_observations(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
) -> None:
    provider, connector, expected = subject
    provider.pages = [{"comments": [_comment(1)], "count": 1, "totalCount": 1}]
    cancelled = False

    def stop_after_page(request: httpx.Request) -> None:
        nonlocal cancelled
        cancelled = request.url.path.endswith("/comments")

    def check() -> None:
        if cancelled:
            raise InterruptedError("cancelled")

    provider.on_request = stop_after_page

    @measure_reindex
    def run() -> dict[str, object]:
        with pytest.raises(InterruptedError):
            acquire_work_item(connector, expected=expected, revision=3, cancel_check=check)
        return {}

    counts = cast(dict[str, Any], run()["work_counts"])
    assert counts["acquisition_by_source"][_SOURCE]["comment_body_records"] == 1
    assert provider.body_reads == 1


def test_excessive_retry_configuration_refuses_without_changing_shared_client(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
) -> None:
    provider, connector, expected = subject
    original = deepcopy(connector._work_item_queries)
    connector._http._max_retries = 5
    with pytest.raises(ValueError, match="retry budget"):
        acquire_work_item(connector, expected=expected, revision=3)
    assert provider.requests == [] and connector._http._max_retries == 5
    assert connector._work_item_queries == original


@pytest.mark.parametrize("status", [304, 403, 404])
def test_unavailable_comment_endpoint_is_not_empty_discussion(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    provider, connector, expected = subject

    def deny(request: httpx.Request) -> None:
        if request.url.path.endswith("/comments"):
            provider.status = status

    provider.on_request = deny
    monkeypatch.setattr(connector._http, "_sleep_for_retry", lambda *_: None)
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        acquire_work_item(connector, expected=expected, revision=3)
    assert provider.body_reads == 1
    assert sum(request.url.path.endswith("/comments") for request in provider.requests) == (
        5 if status == 403 else 1
    )


@pytest.mark.parametrize("header", ["different", "1"])
def test_comment_token_header_agreement_and_legacy_comment_id(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    header: str,
) -> None:
    provider, connector, expected = subject
    first = _comment(1)
    first["commentId"] = first.pop("id")
    provider.pages = [
        {"comments": [first], "count": 1, "totalCount": 2, "continuationToken": "1"},
        {"comments": [_comment(2)], "count": 1, "totalCount": 2},
    ]

    def headers(request: httpx.Request) -> None:
        provider.response_headers = (
            {"x-ms-continuationtoken": header}
            if request.url.path.endswith("/comments")
            and "continuationToken" not in request.url.params
            else {}
        )

    provider.on_request = headers
    if header == "different":
        with pytest.raises(ValueError, match="inconsistent continuation"):
            acquire_work_item(connector, expected=expected, revision=3)
    else:
        result = acquire_work_item(connector, expected=expected, revision=3)
        assert result is not None and result.comment_versions == ((1, 2, False), (2, 2, False))


def test_shared_retries_enforce_360_attempt_ceiling_without_an_outer_retry_loop(
    subject: tuple[Provider, AzureDevOpsConnector, WorkItemEligibility],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, connector, _ = subject
    connector._work_item_queries = [{"saved_query_id": "saved"}] * 16
    expected = connector.work_item_eligibility(7)
    assert expected is not None
    provider.requests.clear()
    provider.pages = [
        {
            "comments": [_comment(page + 1)],
            "count": 1,
            "totalCount": 4,
            **({"continuationToken": str(page + 1)} if page < 3 else {}),
        }
        for page in range(4)
    ]
    attempts = 0

    def transient(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts % 5:
            return httpx.Response(503)
        return provider.handle(request)

    monkeypatch.setattr(connector._http, "_sleep_backoff", lambda _: None)
    with httpx.Client(
        base_url="https://dev.azure.com",
        transport=httpx.MockTransport(transient),
    ) as client:
        monkeypatch.setattr(connector._http, "client", client)

        @measure_reindex
        def run() -> dict[str, object]:
            assert acquire_work_item(connector, expected=expected, revision=3) is not None
            return {}

        counts = cast(dict[str, Any], run()["work_counts"])
    assert attempts == 360 and len(provider.requests) == 72
    assert counts["http"]["attempts"] == 360 and counts["http"]["retries"] == 288
