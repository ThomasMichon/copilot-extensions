from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import pytest

from agent_index.sources import get_connector
from agent_index.sources.azure_devops import AzureDevOpsConnector
from agent_index.indexing.work_counts import measure_reindex


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


def _work_item_payload(work_item_id: int, *, changed: str = "2026-01-02T03:04:05Z") -> dict:
    return {
        "id": work_item_id,
        "fields": {
            "System.WorkItemType": "Bug",
            "System.Title": "Escapes <b>HTML</b>",
            "System.State": "Active",
            "System.AreaPath": "widgets\\service",
            "System.IterationPath": "widgets\\sprint 1",
            "System.Tags": "api; regression",
            "System.AssignedTo": {"displayName": "Ada Lovelace"},
            "System.ChangedDate": changed,
            "System.Description": "<p>Description <b>body</b></p>",
            "Microsoft.VSTS.TCM.ReproSteps": "<div>Step one<br>Step two</div>",
            "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Done</li></ul>",
        },
    }


def test_ado_discovers_work_items_and_pull_requests_with_pagination() -> None:
    auth_header = "Basic " + base64.b64encode(b":pat-token").decode("ascii")
    seen_paths: list[str] = []
    seen_tokens: list[str | None] = []
    operator_wiql = (
        "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = 'widgets' "
        "AND [System.AreaPath] UNDER 'widgets\\service'"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == auth_header
        seen_paths.append(request.url.path)
        path = request.url.path
        if path.endswith("/_apis/wit/wiql"):
            wiql = json.loads(request.content.decode("utf-8"))["query"]
            assert wiql == operator_wiql
            return _json_response(200, {"workItems": [{"id": 42}]})
        if path.endswith("/_apis/wit/workitemsbatch"):
            assert json.loads(request.content.decode("utf-8"))["ids"] == [42]
            return _json_response(200, {"value": [_work_item_payload(42)]})
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
            assert request.url.params["searchCriteria.repositoryId"] == "repo-1"
            assert request.url.params["searchCriteria.status"] == "all"
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
        work_item_queries=[{"name": "team-backlog", "wiql": operator_wiql}],
        pull_request_queries=[{"name": "repo-prs", "status": "all", "repository_id": "repo-1"}],
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


def test_ado_multiple_work_item_queries_union_and_dedupe_ids() -> None:
    batch_ids: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/_apis/wit/wiql"):
            wiql = json.loads(request.content.decode("utf-8"))["query"]
            if "Query A" in wiql:
                return _json_response(200, {"workItems": [{"id": 1}, {"id": 2}]})
            if "Query B" in wiql:
                return _json_response(200, {"workItems": [{"id": 2}, {"id": 3}]})
        if path.endswith("/_apis/wit/workitemsbatch"):
            batch_ids.extend(json.loads(request.content.decode("utf-8"))["ids"])
            return _json_response(
                200,
                {
                    "value": [
                        _work_item_payload(1),
                        _work_item_payload(2),
                        _work_item_payload(3),
                    ]
                },
            )
        if path.endswith("/comments"):
            return _json_response(200, {"comments": []})
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="pat-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        work_item_queries=[
            {"name": "a", "wiql": "SELECT [System.Id] FROM WorkItems WHERE Query A"},
            {"name": "b", "wiql": "SELECT [System.Id] FROM WorkItems WHERE Query B"},
        ],
        pull_request_queries=[],
    )

    entries = connector.discover()

    assert batch_ids == [1, 2, 3]
    assert {entry.path for entry in entries} == {
        "workitems/1.md",
        "workitems/2.md",
        "workitems/3.md",
    }


def test_ado_saved_query_id_path_resolves_and_runs() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_paths.append(url)
        if "/_apis/wit/wiql/shared%2Fmy%20query" in url:
            return _json_response(200, {"workItemRelations": [{"target": {"id": 99}}]})
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="pat-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        work_item_queries=[{"name": "saved", "saved_query_id": "shared/my query"}],
        pull_request_queries=[],
    )

    assert connector.list_paths()["ado:acme/widgets:workitems"] == {"workitems/99.md"}
    assert any("shared%2Fmy%20query" in path for path in seen_paths)


def test_ado_pr_reviewer_me_resolves_connection_data_once() -> None:
    connection_data_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal connection_data_calls
        path = request.url.path
        if path.endswith("/_apis/connectionData"):
            connection_data_calls += 1
            return _json_response(200, {"authenticatedUser": {"id": "user-id-1"}})
        if path.endswith("/_apis/git/pullrequests"):
            assert request.url.params["searchCriteria.reviewerId"] == "user-id-1"
            assert request.url.params["searchCriteria.status"] == "active"
            return _json_response(200, {"value": []})
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="pat-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        work_item_queries=[],
        pull_request_queries=[
            {"name": "mine", "reviewer": "me", "status": "active"},
            {"name": "mine-again", "reviewer": "me", "status": "active"},
        ],
    )

    assert connector.list_paths()["ado:acme/widgets:pulls"] == set()
    assert connection_data_calls == 1


def test_ado_config_file_loading_from_env(monkeypatch) -> None:
    config_path = Path.cwd() / ".agent-index-ado-test-config.json"
    try:
        config_path.write_text(
            json.dumps(
                {
                    "work_item_queries": [
                        {
                            "name": "configured-wiql",
                            "wiql": "SELECT [System.Id] FROM WorkItems WHERE Configured WIQL",
                        }
                    ],
                    "pull_request_queries": [
                        {"name": "configured-pr", "status": "active", "creator": "creator-id"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_INDEX_ADO_CONFIG", str(config_path))

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/_apis/wit/wiql"):
                assert "Configured WIQL" in json.loads(request.content.decode("utf-8"))["query"]
                return _json_response(200, {"workItems": [{"id": 5}]})
            if path.endswith("/_apis/git/pullrequests"):
                assert request.url.params["searchCriteria.status"] == "active"
                assert request.url.params["searchCriteria.creatorId"] == "creator-id"
                return _json_response(200, {"value": [{"pullRequestId": 12}]})
            raise AssertionError(f"unexpected request: {request.url}")

        connector = AzureDevOpsConnector(
            "ado:acme/widgets",
            token="pat-token",
            transport=httpx.MockTransport(handler),
            min_interval_s=0,
        )

        assert connector.list_paths() == {
            "ado:acme/widgets:workitems": {"workitems/5.md"},
            "ado:acme/widgets:pulls": {"pulls/12.md"},
        }
    finally:
        config_path.unlink(missing_ok=True)


def test_ado_no_firehose_without_queries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="pat-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        work_item_queries=[],
        pull_request_queries=[],
    )

    assert connector.discover() == []
    assert requests == []


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
        work_item_queries=[
            {"name": "empty", "wiql": "SELECT [System.Id] FROM WorkItems WHERE Empty"}
        ],
        pull_request_queries=[{"name": "active", "status": "active"}],
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
        work_item_queries=[{"name": "ids", "wiql": "SELECT [System.Id] FROM WorkItems WHERE Ids"}],
        pull_request_queries=[{"name": "all", "status": "all"}],
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


@pytest.mark.parametrize("changed_ids", [[], [2, 201]])
def test_ado_incremental_filters_metadata_before_hydrating_bodies(
    changed_ids: list[int],
) -> None:
    wiql = "SELECT [System.Id] FROM WorkItemLinks WHERE Arbitrary Operator Predicate"
    ids = list(range(1, 202))
    metadata_batches: list[list[int]] = []
    body_ids: list[int] = []
    comment_ids: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/_apis/wit/wiql"):
            assert json.loads(request.content)["query"] == wiql
            return _json_response(200, {"workItems": [{"id": number} for number in ids]})
        if path.endswith("/_apis/wit/wiql/saved-query"):
            return _json_response(200, {"workItems": [{"id": 2}, {"id": 201}]})
        if path.endswith("/workitemsbatch"):
            payload = json.loads(request.content)
            batch = payload["ids"]
            if "fields" in payload:
                assert payload == {
                    "ids": batch,
                    "fields": ["System.Id", "System.ChangedDate"],
                    "errorPolicy": "fail",
                }
                metadata_batches.append(batch)
                return _json_response(200, {"value": [
                    {"id": number, "fields": {
                        "System.ChangedDate": (
                            "2026-01-03T00:00:00Z" if number in changed_ids
                            else "2026-01-01T00:00:00Z"
                        ),
                    }}
                    for number in reversed(batch)
                ]})
            assert payload == {"ids": batch, "$expand": "all"}
            body_ids.extend(batch)
            return _json_response(200, {"value": [
                _work_item_payload(number, changed="2026-01-03T00:00:00Z")
                for number in batch
            ]})
        if path.endswith("/comments"):
            number = int(path.split("/")[-2])
            assert number in changed_ids
            comment_ids.append(number)
            return _json_response(200, {"comments": [{"text": "Updated comment"}]})
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets", token="pat-token", min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[
            {"wiql": wiql}, {"saved_query_id": "saved-query"},
        ],
        pull_request_queries=[],
    )

    entries = connector.discover_changed("2026-01-03T00:01:00Z")

    assert metadata_batches == [ids[:200], ids[200:]]
    assert body_ids == changed_ids
    assert comment_ids == changed_ids
    assert [entry.path for entry in entries] == [
        f"workitems/{number}.md" for number in changed_ids
    ]
    assert all("Updated comment" in entry.content for entry in entries)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unexpected", "invalid-date"])
def test_ado_incremental_refuses_incomplete_metadata_before_body_fetch(fault: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/wiql"):
            return _json_response(200, {"workItems": [{"id": 1}, {"id": 2}]})
        if request.url.path.endswith("/workitemsbatch"):
            payload = json.loads(request.content)
            assert payload.get("fields") == ["System.Id", "System.ChangedDate"]
            items = [
                {"id": 1, "fields": {"System.ChangedDate": "2026-01-03T00:00:00Z"}},
                {"id": 2, "fields": {"System.ChangedDate": "2026-01-03T00:00:00Z"}},
            ]
            if fault == "missing":
                items.pop()
            elif fault == "duplicate":
                items[1] = items[0]
            elif fault == "unexpected":
                items[1]["id"] = 3
            else:
                items[1]["fields"]["System.ChangedDate"] = "not-a-timestamp"
            return _json_response(200, {"value": items})
        raise AssertionError(f"unexpected request: {request.url}")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets", token="pat-token", min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{"wiql": "SELECT [System.Id] FROM WorkItems"}],
        pull_request_queries=[],
    )

    with pytest.raises(ValueError, match="work-item metadata"):
        connector.discover_changed("2026-01-03T00:01:00Z")


@pytest.mark.parametrize("cancel_after_metadata", [False, True])
def test_ado_incremental_checks_cancellation_and_body_timestamp(
    cancel_after_metadata: bool,
) -> None:
    metadata_done = False
    body_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_done, body_requests
        if request.url.path.endswith("/wiql"):
            return _json_response(200, {"workItems": [{"id": 1}]})
        if request.url.path.endswith("/workitemsbatch"):
            payload = json.loads(request.content)
            if "fields" in payload:
                metadata_done = True
                return _json_response(200, {"value": [
                    {"id": 1, "fields": {"System.ChangedDate": "2026-01-03T00:00:00Z"}},
                ]})
            body_requests += 1
            return _json_response(200, {"value": [
                _work_item_payload(1, changed="2026-01-01T00:00:00Z"),
            ]})
        raise AssertionError(f"unexpected request: {request.url}")

    def cancel_check() -> None:
        if metadata_done and cancel_after_metadata:
            raise RuntimeError("cancelled after metadata")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets", token="pat-token", min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{"wiql": "SELECT [System.Id] FROM WorkItems"}],
        pull_request_queries=[],
    )
    if cancel_after_metadata:
        with pytest.raises(RuntimeError, match="cancelled after metadata"):
            connector.discover_changed("2026-01-03T00:01:00Z", cancel_check=cancel_check)
    else:
        assert connector.discover_changed(
            "2026-01-03T00:01:00Z", cancel_check=cancel_check,
        ) == []
    assert body_requests == (0 if cancel_after_metadata else 1)


@pytest.mark.parametrize(
    "outcome", ["complete", "later-404", "cycle", "missing-token", "bad-page", "cancel"],
)
def test_ado_work_item_comment_pages_are_complete_or_refused(outcome: str) -> None:
    tokens: list[str | None] = []
    token = "opaque +/&token"
    first_page_seen = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_page_seen
        path = request.url.path
        if path.endswith("/wiql"):
            return _json_response(200, {"workItems": [{"id": 42}]})
        if path.endswith("/workitemsbatch"):
            return _json_response(200, {"value": [_work_item_payload(42)]})
        assert path.endswith("/workItems/42/comments")
        current = request.url.params.get("continuationToken")
        tokens.append(current)
        if current is None:
            first_page_seen = True
            page = {
                "comments": [{"id": 1, "text": "First page"}],
                "continuationToken": token,
                "nextPage": "https://foreign.example/never-follow-this-url",
            }
            if outcome == "missing-token":
                del page["continuationToken"]
            return _json_response(200, page)
        assert current == token
        if outcome == "later-404":
            return _json_response(404, {"message": "interrupted continuation"})
        if outcome == "bad-page":
            return _json_response(200, {"comments": {"invalid": "not a list"}})
        return _json_response(200, {
            "comments": [{"id": 2, "text": "Last page"}],
            "continuationToken": token if outcome == "cycle" else None,
        })

    def cancel_check() -> None:
        if outcome == "cancel" and first_page_seen:
            raise RuntimeError("cancelled between comment pages")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets", token="pat-token", min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{"wiql": "SELECT [System.Id] FROM WorkItems"}],
        pull_request_queries=[],
    )

    @measure_reindex
    def run() -> dict[str, object]:
        return {"entries": connector.discover(cancel_check=cancel_check)}

    if outcome == "complete":
        result = run()
        assert len(result["entries"]) == 1
        assert "First page" in result["entries"][0].content
        assert "Last page" in result["entries"][0].content
        assert result["work_counts"]["http_by_purpose"]["comment_page"]["attempts"] == 2
    else:
        error = {
            "later-404": httpx.HTTPStatusError,
            "cancel": RuntimeError,
        }.get(outcome, ValueError)
        with pytest.raises(error):
            run()
    assert tokens == (
        [None] if outcome in {"cancel", "missing-token"} else [None, token]
    )


@pytest.mark.parametrize("status", [200, 404])
def test_ado_work_item_comments_preserve_legacy_and_initial_not_found(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/workItems/42/comments")
        assert request.url.params.get("continuationToken") is None
        return _json_response(status, {"value": [{"text": "Legacy comment"}]})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets", token="pat-token", min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[], pull_request_queries=[],
    )
    assert connector._work_item_comments(42) == (
        [{"text": "Legacy comment"}] if status == 200 else []
    )
