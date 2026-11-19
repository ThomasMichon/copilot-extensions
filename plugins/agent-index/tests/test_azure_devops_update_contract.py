"""PR creation timestamps cannot certify later description or comment changes."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.azure_devops import AzureDevOpsConnector


@pytest.mark.parametrize(
    "marker",
    [
        "not-a-timestamp",
        " ",
        "NaN",
        "inf",
        "-inf",
        "1e9999",
        "1e30",
        "0001-01-01T00:00:00Z",
        "9999-12-31T23:59:59-01:00",
    ],
)
def test_invalid_work_item_marker_refuses_before_any_acquisition(marker: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"workItems": []})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{"wiql": "Operator query"}],
        pull_request_queries=[],
    )
    try:
        with pytest.raises(ValueError, match="Azure DevOps change marker"):
            connector.discover_changed(marker)
    finally:
        connector._http.client.close()
    assert requests == []


@pytest.mark.parametrize(
    "marker",
    ["2026-01-03T00:01:00Z", "2026-01-03T00:01:00", "2026-01-02T19:01:00-05:00", "1767398460"],
)
@pytest.mark.parametrize("query_key", ["wiql", "saved_query_id"])
def test_valid_work_item_marker_preserves_overlap_and_operator_query(
    marker: str,
    query_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_ADO_OVERLAP_SECONDS", "300")
    bodies: list[list[int]] = []
    comments: list[int] = []
    query = "SELECT [System.Id] FROM WorkItemLinks WHERE Arbitrary Operator Predicate"
    stamps = {
        1: "2026-01-02T23:55:59Z",
        2: "2026-01-02T23:56:00Z",
        3: "2026-01-03T00:00:00Z",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/wiql"):
            assert query_key == "wiql"
            assert json.loads(request.content) == {"query": query}
            return httpx.Response(200, json={"workItems": [{"id": n} for n in stamps]})
        if path.endswith("/wiql/saved-query"):
            assert query_key == "saved_query_id"
            assert request.method == "GET"
            return httpx.Response(200, json={"workItems": [{"id": n} for n in stamps]})
        if path.endswith("/workitemsbatch"):
            payload = json.loads(request.content)
            if "fields" in payload:
                assert payload == {
                    "ids": [1, 2, 3],
                    "fields": ["System.Id", "System.ChangedDate"],
                    "errorPolicy": "fail",
                }
            else:
                assert payload == {"ids": [2, 3], "$expand": "all", "errorPolicy": "fail"}
                bodies.append(payload["ids"])
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": n,
                            "fields": {
                                "System.Title": f"Item {n}",
                                "System.ChangedDate": stamps[n],
                            },
                        }
                        for n in payload["ids"]
                    ]
                },
            )
        assert path.endswith("/comments")
        comments.append(int(path.split("/")[-2]))
        return httpx.Response(200, json={"comments": []})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{query_key: query if query_key == "wiql" else "saved-query"}],
        pull_request_queries=[],
    )
    try:
        entries = connector.discover_changed(marker)
    finally:
        connector._http.client.close()
    assert [entry.path for entry in entries] == ["workitems/2.md", "workitems/3.md"]
    assert bodies == [[2, 3]]
    assert comments == [2, 3]


def test_invalid_marker_cannot_publish_reconciliation_or_replace_checkpoint(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": "test-account"}})
        assert request.url.path.endswith("/wiql")
        return httpx.Response(200, json={"workItems": []})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{"wiql": "Operator query"}],
        pull_request_queries=[],
    )
    paths = PathIndex(tmp_path / "paths.db")
    family = "ado:acme/widgets:workitems"
    paths.mark_indexed_batch([(family, "workitems/1.md", "old-body", 1)])
    state = IndexState(
        sources={
            "selected": SourceState(
                last_commit="not-a-timestamp",
                chunk_count=1,
                acquisition_scope=recovery_scope("selected", connector),
            )
        },
        stored_families={"selected": connector.source_name},
        total_chunks=1,
    )
    before_state = asdict(state)
    before_paths = paths.get_paths_by_family("ado")
    try:
        with pytest.raises(ValueError, match="Azure DevOps change marker"):
            collect_source_changes(
                "selected",
                state=state,
                path_index=paths,
                connector=connector,
                full=False,
                source_aware=True,
            )
    finally:
        connector._http.client.close()
    assert asdict(state) == before_state
    assert paths.get_paths_by_family("ado") == before_paths
    assert all(request.url.path.endswith(("/connectionData", "/wiql")) for request in requests)


def _updated_pr_provider(request: httpx.Request) -> httpx.Response:
    pull: dict[str, Any] = {
        "pullRequestId": 7,
        "title": "Older change",
        "description": "Updated description",
        "creationDate": "2025-01-01T00:00:00Z",
        "status": "active",
        "repository": {"id": "repo-1", "name": "selected"},
        "sourceRefName": "refs/heads/feature",
        "targetRefName": "refs/heads/main",
    }
    path = request.url.path
    if path == "/acme/widgets/_apis/wit/wiql":
        return httpx.Response(200, json={"workItems": []})
    if path == "/acme/widgets/_apis/git/pullrequests":
        assert request.url.params["searchCriteria.repositoryId"] == "repo-1"
        assert request.url.params["searchCriteria.status"] == "active"
        minimum = request.url.params.get("searchCriteria.minTime")
        selected = (
            not int(request.url.params["$skip"])
            and (minimum is None or pull["creationDate"] >= minimum)
        )
        return httpx.Response(200, json={"value": [pull] if selected else []})
    if path == "/acme/widgets/_apis/git/pullrequests/7":
        return httpx.Response(200, json=pull | {"description": "Updated complete description"})
    assert path == "/acme/widgets/_apis/git/repositories/repo-1/pullRequests/7/threads"
    return httpx.Response(
        200,
        json={
            "value": [
                {
                    "comments": [
                        {
                            "content": "New review comment",
                            "publishedDate": "2026-01-03T00:00:00Z",
                        }
                    ]
                }
            ]
        },
    )


@pytest.mark.parametrize("with_work_items", [False, True])
def test_timestamp_pr_discovery_refuses_before_any_acquisition(with_work_items: bool) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _updated_pr_provider(request)

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{"wiql": "Operator query"}] if with_work_items else [],
        pull_request_queries=[{"repository_id": "repo-1", "status": "active"}],
    )
    try:
        with pytest.raises(ValueError, match="PR timestamp discovery is unsupported"):
            connector.discover_changed("2026-01-02T00:00:00Z")
    finally:
        connector._http.client.close()
    assert requests == []


@pytest.mark.parametrize("marker", [None, ""])
@pytest.mark.parametrize("explicit_full", [False, True])
def test_full_discovery_retains_old_pr_edits_and_id_only_scope(
    explicit_full: bool,
    marker: str | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _updated_pr_provider(request)

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository_id": "repo-1", "status": "active"}],
    )
    try:
        entries = connector.discover() if explicit_full else connector.discover_changed(marker)
        assert [(entry.source, entry.path) for entry in entries] == [
            ("ado:acme/widgets:pulls", "pulls/7.md"),
        ]
        assert "Updated complete description" in entries[0].content
        assert "New review comment" in entries[0].content
        assert connector.list_paths() == {
            "ado:acme/widgets:workitems": set(),
            "ado:acme/widgets:pulls": {"pulls/7.md"},
        }
    finally:
        connector._http.client.close()
    assert [request.url.path for request in requests] == [
        "/acme/widgets/_apis/git/pullrequests",
        "/acme/widgets/_apis/git/pullrequests",
        "/acme/widgets/_apis/git/pullrequests/7",
        "/acme/widgets/_apis/git/repositories/repo-1/pullRequests/7/threads",
        "/acme/widgets/_apis/git/pullrequests",
        "/acme/widgets/_apis/git/pullrequests",
    ]
    assert all("searchCriteria.minTime" not in request.url.params for request in requests)
