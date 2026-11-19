"""Singleton membership must preserve every configured query and refuse uncertainty."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, cast

import httpx
import pytest

from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.azure_devops import AzureDevOpsConnector

_QUERY = "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
_SAVED = {"saved_query_id": "saved-query"}
_DEFINITION = {"id": "saved-query", "queryType": "flat", "wiql": _QUERY}


@contextmanager
def _connector(
    queries: list[dict[str, Any]],
    handler: Callable[[httpx.Request], httpx.Response],
) -> Iterator[AzureDevOpsConnector]:
    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        work_item_queries=queries,
        pull_request_queries=[],
        transport=httpx.MockTransport(handler),
    )
    try:
        yield connector
    finally:
        connector._http.client.close()


def _account() -> httpx.Response:
    return httpx.Response(200, json={"authenticatedUser": {"id": "fixture-user"}})


def test_union_uses_only_singleton_queries_and_retains_observations() -> None:
    specs = [{"wiql": _QUERY}, _SAVED, {"wiql": _QUERY.replace("Active", "Done")}]
    original = deepcopy(specs)
    requests: list[httpx.Request] = []
    selections = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal selections
        requests.append(request)
        assert "if-none-match" not in request.headers
        if request.url.path.endswith("/connectionData"):
            return _account()
        if request.url.path.endswith("/queries/saved-query"):
            assert request.url.params["$expand"] == "clauses"
            return httpx.Response(200, json=_DEFINITION, headers={"ETag": '"definition"'})
        assert request.method == "POST"
        assert request.url.path == "/acme/widgets/_apis/wit/wiql"
        assert "$top" not in request.url.params
        restricted = json.loads(request.content)["query"]
        expected = specs[selections].get("wiql", _QUERY)
        assert (
            restricted
            == expected.replace(
                "WHERE ",
                "WHERE (",
            )
            + ") AND [System.Id] IN (7)"
        )
        selections += 1
        return httpx.Response(
            200,
            json={"queryType": "flat", "workItems": [{"id": 7}] if selections == 2 else []},
        )

    with _connector(specs, handler) as connector:

        @measure_reindex
        def run() -> dict[str, Any]:
            result = connector.work_item_eligibility(7)
            assert result is not None and result.eligible and result.item_id == 7
            assert result.source_scope == (
                "https://dev.azure.com",
                "acme",
                "widgets",
                "fixture-user",
            )
            return {}

        counts = cast(dict[str, Any], run()["work_counts"])
        assert selections == 3 and len(requests) == 5
        assert counts["http_by_purpose"]["metadata"]["attempts"] == 2
        assert counts["http_by_purpose"]["identity_page"]["attempts"] == 3
        assert counts["acquisition_by_source"]["ado:acme/widgets:workitems"] == {
            "identity_records": 1,
            "item_body_records": 0,
            "comment_body_records": 0,
        }
        assert specs == original and connector._work_item_queries[0]["wiql"] == _QUERY
        assert connector.current_commit() is None


def test_saved_binding_reads_fresh_definition_and_account_on_each_operation() -> None:
    definition = dict(_DEFINITION)
    account = "first-user"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "if-none-match" not in request.headers
        if request.url.path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": account}})
        if request.method == "GET":
            return httpx.Response(200, json=definition, headers={"ETag": '"same-validator"'})
        assert definition["wiql"].split("WHERE")[1].strip() in json.loads(request.content)["query"]
        return httpx.Response(200, json={"queryType": "flat"})

    with _connector([_SAVED], handler) as connector:
        first = connector.work_item_eligibility(7)
        assert first is not None and not first.eligible
        assert connector.work_item_eligibility(7) == first
        definition["wiql"] = _QUERY.replace("Active", "Closed")
        changed = connector.work_item_eligibility(7)
        assert changed is not None and changed.query_binding != first.query_binding
        definition["lastModifiedDate"] = "2026-01-02T00:00:00Z"
        metadata_changed = connector.work_item_eligibility(7)
        assert (
            metadata_changed is not None
            and metadata_changed.query_binding != changed.query_binding
        )
        account = "second-user"
        scoped = connector.work_item_eligibility(7)
        assert scoped is not None and scoped.source_scope != first.source_scope
    assert len(requests) == 15


@pytest.mark.parametrize(
    "queries", [[], [{"wiql": _QUERY}] * 17, [{"wiql": _QUERY + " ASOF 'x'"}]]
)
def test_empty_or_over_budget_or_unsupported_configuration_never_acquires(
    queries: list[dict[str, Any]],
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request: {request.method} {request.url.path}")

    with _connector(queries, unexpected) as connector:
        if not queries:
            assert connector.work_item_eligibility(7) is None
        else:
            with pytest.raises(ValueError):
                connector.work_item_eligibility(7)


@pytest.mark.parametrize("item_id", [0, -1, True, 2_147_483_648, "7", [7]])
def test_invalid_candidate_refuses_before_requests(item_id: Any) -> None:
    with _connector([{"wiql": _QUERY}], lambda _: pytest.fail("unexpected I/O")) as connector:
        with pytest.raises(ValueError, match="positive int32"):
            connector.work_item_eligibility(item_id)


def test_sixteen_saved_queries_have_a_finite_33_operation_ceiling() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/connectionData"):
            return _account()
        if request.method == "GET":
            return httpx.Response(200, json=_DEFINITION)
        return httpx.Response(200, json={"queryType": "flat", "workItems": None})

    with _connector([_SAVED] * 16, handler) as connector:
        result = connector.work_item_eligibility(7)
        assert result is not None and not result.eligible
    assert len(requests) == 33


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "other"},
        {"id": 7},
        {"queryType": "tree"},
        {"queryType": None},
        {"isFolder": True},
        {"isDeleted": True},
        {"isInvalidSyntax": True},
        {"isFolder": "false"},
        {"wiql": None},
        {"wiql": _QUERY + " ASOF 'x'"},
        {"lastModifiedDate": []},
    ],
)
def test_unqualified_saved_member_refuses_entire_union_before_selection(
    patch: dict[str, Any],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/connectionData"):
            return _account()
        return httpx.Response(200, json={**_DEFINITION, **patch})

    with _connector([{"wiql": _QUERY}, _SAVED], handler) as connector:
        with pytest.raises(ValueError):
            connector.work_item_eligibility(7)
    assert len(requests) == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"queryType": "tree", "workItems": []},
        {"queryType": "flat", "workItems": {}},
        {"queryType": "flat", "workItems": [{"id": 8}]},
        {"queryType": "flat", "workItems": [{"id": True}]},
        {"queryType": "flat", "workItems": [{"id": 7}, {"id": 7}]},
        {"queryType": "flat", "workItemRelations": [{"target": {"id": 7}}]},
        {"queryType": "flat", "continuationToken": "more"},
        {"queryType": "flat", "count": 1},
        {"queryType": "flat", "hasMore": True},
    ],
)
def test_bad_selection_after_a_matching_union_member_never_returns_eligibility(body: Any) -> None:
    selections = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal selections
        if request.method == "GET":
            return _account()
        selections += 1
        return httpx.Response(
            200,
            json={"queryType": "flat", "workItems": [{"id": 7}]} if selections == 1 else body,
        )

    with _connector([{"wiql": _QUERY}] * 2, handler) as connector:
        with pytest.raises(ValueError):
            connector.work_item_eligibility(7)
    assert selections == 2


@pytest.mark.parametrize("stage", ["account", "definition", "selection"])
@pytest.mark.parametrize("status", [304, 403, 404])
def test_bodyless_denied_or_missing_reads_are_not_ineligible(stage: str, status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        current = (
            "account"
            if request.url.path.endswith("/connectionData")
            else "definition"
            if request.method == "GET"
            else "selection"
        )
        if current == stage:
            return httpx.Response(status)
        return _account() if current == "account" else httpx.Response(200, json=_DEFINITION)

    with _connector([_SAVED], handler) as connector:
        connector._http._max_retries = 0
        with pytest.raises(ValueError if status == 304 else httpx.HTTPStatusError):
            connector.work_item_eligibility(7)


def test_cancellation_after_definition_stops_before_selection() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _account() if len(requests) == 1 else httpx.Response(200, json=_DEFINITION)

    def cancel() -> None:
        if len(requests) == 2:
            raise InterruptedError("cancelled")

    with _connector([_SAVED], handler) as connector:
        with pytest.raises(InterruptedError, match="cancelled"):
            connector.work_item_eligibility(7, cancel_check=cancel)
    assert len(requests) == 2


def test_retry_budget_refuses_instead_of_returning_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/connectionData")
        return httpx.Response(503)

    with _connector([_SAVED], handler) as connector:
        monkeypatch.setattr(connector._http, "_sleep_backoff", lambda _: None)

        @measure_reindex
        def run() -> dict[str, Any]:
            with pytest.raises(httpx.HTTPStatusError):
                connector.work_item_eligibility(7)
            return {}

        counts = cast(dict[str, Any], run()["work_counts"])
    assert len(requests) == 5
    assert counts["http_by_purpose"]["metadata"]["attempts"] == 5
    assert counts["http_by_purpose"]["metadata"]["retries"] == 4
    assert "identity_page" not in counts["http_by_purpose"]


@pytest.mark.parametrize("token_form", ["header", "body"])
def test_continued_selection_counts_parsed_records_before_refusal(token_form: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _account()
        body: dict[str, Any] = {"queryType": "flat", "workItems": [{"id": 7}]}
        headers = {}
        if token_form == "header":
            headers["x-ms-continuationtoken"] = "more"
        else:
            body["continuationToken"] = "more"
        return httpx.Response(200, json=body, headers=headers)

    with _connector([{"wiql": _QUERY}], handler) as connector:

        @measure_reindex
        def run() -> dict[str, Any]:
            with pytest.raises(ValueError, match="continued results"):
                connector.work_item_eligibility(7)
            return {}

        counts = cast(dict[str, Any], run()["work_counts"])
    assert counts["acquisition_by_source"]["ado:acme/widgets:workitems"] == {
        "identity_records": 1,
        "item_body_records": 0,
        "comment_body_records": 0,
    }
