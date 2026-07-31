from __future__ import annotations

import base64
import json
import time

import httpx

from agent_index.sources import get_connector
from agent_index.sources.azure_devops import AzureDevOpsConnector


def _json_response(
    status_code: int,
    payload,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers=headers or {},
    )


def test_ado_discovers_work_items_and_pull_requests_with_pagination() -> None:
    auth_header = "Basic " + base64.b64encode(b":pat-token").decode("ascii")
    seen_paths: list[str] = []
    seen_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == auth_header
        seen_paths.append(request.url.path)
        path = request.url.path
        if path.endswith("/_apis/wit/wiql"):
            wiql = json.loads(request.content.decode("utf-8"))["query"]
            assert "[System.TeamProject] = 'widgets'" in wiql
            assert "[System.AreaPath] UNDER 'widgets\\service'" in wiql
            assert "[System.IterationPath] UNDER 'widgets\\sprint 1'" in wiql
            return _json_response(200, {"workItems": [{"id": 42}]})
        if path.endswith("/_apis/wit/workitemsbatch"):
            return _json_response(
                200,
                {
                    "value": [
                        {
                            "id": 42,
                            "fields": {
                                "System.WorkItemType": "Bug",
                                "System.Title": "Escapes <b>HTML</b>",
                                "System.State": "Active",
                                "System.AreaPath": "widgets\\service",
                                "System.IterationPath": "widgets\\sprint 1",
                                "System.Tags": "api; regression",
                                "System.AssignedTo": {"displayName": "Ada Lovelace"},
                                "System.ChangedDate": "2026-01-02T03:04:05Z",
                                "System.Description": "<p>Description <b>body</b></p>",
                                "Microsoft.VSTS.TCM.ReproSteps": "<div>Step one<br>Step two</div>",
                                "Microsoft.VSTS.Common.AcceptanceCriteria": (
                                    "<ul><li>Done</li></ul>"
                                ),
                            },
                        }
                    ]
                },
            )
        if path.endswith("/_apis/wit/workItems/42/comments"):
            return _json_response(
                200,
                {
                    "comments": [
                        {
                            "text": "<p>Work item <em>comment</em></p>",
                            "createdBy": {"displayName": "Reviewer"},
                            "createdDate": "2026-01-02T04:00:00Z",
                        }
                    ]
                },
            )
        if path.endswith("/_apis/git/pullrequests"):
            token = request.url.params.get("continuationToken")
            seen_tokens.append(token)
            if token is None:
                return _json_response(
                    200,
                    {
                        "value": [
                            {
                                "pullRequestId": 7,
                                "title": "Add widget",
                                "description": "Pull request body",
                                "status": "active",
                                "repository": {"id": "repo-1", "name": "widgets"},
                                "sourceRefName": "refs/heads/feature",
                                "targetRefName": "refs/heads/main",
                                "createdBy": {"displayName": "Grace Hopper"},
                                "creationDate": "2026-01-03T00:00:00Z",
                            }
                        ]
                    },
                    headers={"x-ms-continuationtoken": "next"},
                )
            return _json_response(
                200,
                {
                    "value": [
                        {
                            "pullRequestId": 8,
                            "title": "Fix widget",
                            "description": "Completed body",
                            "status": "completed",
                            "repository": {"id": "repo-1", "name": "widgets"},
                            "sourceRefName": "refs/heads/fix",
                            "targetRefName": "refs/heads/main",
                            "createdBy": {"displayName": "Alan Turing"},
                            "creationDate": "2026-01-04T00:00:00Z",
                            "closedDate": "2026-01-05T00:00:00Z",
                        }
                    ]
                },
            )
        if "/_apis/git/repositories/repo-1/pullRequests/" in path and path.endswith("/threads"):
            return _json_response(
                200,
                {
                    "value": [
                        {
                            "comments": [
                                {
                                    "content": "Thread comment",
                                    "author": {"displayName": "Maintainer"},
                                    "publishedDate": "2026-01-03T01:00:00Z",
                                }
                            ]
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="pat-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        changed_since="2026-01-01T00:00:00Z",
        area_path="widgets\\service",
        iteration_path="widgets\\sprint 1",
    )

    entries = connector.discover()

    assert {entry.path for entry in entries} == {
        "workitems/42.md",
        "pulls/7.md",
        "pulls/8.md",
    }
    work_item = next(entry for entry in entries if entry.path == "workitems/42.md")
    assert work_item.language == "work_item"
    assert work_item.source == "ado:acme/widgets:workitems"
    assert work_item.metadata == {
        "id": 42,
        "work_item_type": "Bug",
        "state": "Active",
        "area_path": "widgets\\service",
        "iteration_path": "widgets\\sprint 1",
        "tags": ["api", "regression"],
        "assigned_to": "Ada Lovelace",
        "changed_date": "2026-01-02T03:04:05Z",
    }
    assert "<b>" not in work_item.content
    assert "Description body" in work_item.content
    assert "Work item comment" in work_item.content

    pull = next(entry for entry in entries if entry.path == "pulls/7.md")
    assert pull.language == "pull_request"
    assert pull.source == "ado:acme/widgets:pulls"
    assert pull.metadata == {
        "id": 7,
        "status": "active",
        "repository": "widgets",
        "source_ref": "refs/heads/feature",
        "target_ref": "refs/heads/main",
        "created_by": "Grace Hopper",
        "closed_date": None,
    }
    assert "Thread comment" in pull.content
    assert seen_tokens == [None, "next"]
    assert any(path.endswith("/_apis/wit/workitemsbatch") for path in seen_paths)


def test_ado_429_retry_after_sleeps_then_proceeds(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/_apis/wit/wiql") and calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        if request.url.path.endswith("/_apis/wit/wiql"):
            return _json_response(200, {"workItems": []})
        if request.url.path.endswith("/_apis/git/pullrequests"):
            return _json_response(200, {"value": []})
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="pat-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        changed_since="2026-01-01T00:00:00Z",
    )

    assert connector.list_paths() == {
        "ado:acme/widgets:workitems": set(),
        "ado:acme/widgets:pulls": set(),
    }
    assert calls == 3
    assert 2 in sleeps


def test_ado_list_paths_returns_ids_only() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/_apis/wit/wiql"):
            return _json_response(200, {"workItems": [{"id": 1}, {"id": 2}]})
        if request.url.path.endswith("/_apis/git/pullrequests"):
            return _json_response(
                200,
                {"value": [{"pullRequestId": 10}, {"pullRequestId": 11}]},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "azure-devops:acme/widgets",
        token="pat-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        changed_since="2026-01-01T00:00:00Z",
    )

    assert connector.list_paths() == {
        "ado:acme/widgets:workitems": {"workitems/1.md", "workitems/2.md"},
        "ado:acme/widgets:pulls": {"pulls/10.md", "pulls/11.md"},
    }
    assert not any("workitemsbatch" in path or path.endswith("/comments") for path in seen_paths)


def test_ado_registry_aliases(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_ADO_TOKEN", "pat-token")

    assert isinstance(get_connector("ado:acme/widgets"), AzureDevOpsConnector)
    assert isinstance(get_connector("azure-devops:acme/widgets"), AzureDevOpsConnector)
