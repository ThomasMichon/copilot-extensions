"""PR creation timestamps cannot certify later description or comment changes."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent_index.sources.azure_devops import AzureDevOpsConnector


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
        selected = minimum is None or pull["creationDate"] >= minimum
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


@pytest.mark.parametrize("explicit_full", [False, True])
def test_full_discovery_retains_old_pr_edits_and_id_only_scope(explicit_full: bool) -> None:
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
        entries = connector.discover() if explicit_full else connector.discover_changed(None)
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
        "/acme/widgets/_apis/git/pullrequests/7",
        "/acme/widgets/_apis/git/repositories/repo-1/pullRequests/7/threads",
        "/acme/widgets/_apis/git/pullrequests",
    ]
    assert all("searchCriteria.minTime" not in request.url.params for request in requests)
