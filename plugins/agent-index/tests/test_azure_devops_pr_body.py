"""List previews cannot certify complete pull-request descriptions."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.azure_devops import AzureDevOpsConnector


@pytest.mark.parametrize(
    "outcome",
    [
        "short",
        "complete",
        "unicode",
        "cleared",
        "wrong-id",
        "wrong-repo",
        "unavailable",
        "missing-body",
    ],
)
def test_selected_pull_description_is_complete_or_refused(outcome: str) -> None:
    preview = "x" * (399 if outcome == "short" else 400)
    if outcome == "unicode":
        preview = "\U0001f600" * 200
    complete = preview + " complete description tail"
    listed: dict[str, Any] = {
        "pullRequestId": 7,
        "title": "Selected change",
        "description": preview,
        "creationDate": "2026-01-03T00:00:00Z",
        "repository": {"id": "repo-1", "name": "selected"},
    }
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path == "/acme/widgets/_apis/git/pullrequests":
            assert request.url.params["searchCriteria.repositoryId"] == "repo-1"
            return httpx.Response(200, json={"value": [listed]})
        if path == "/acme/widgets/_apis/git/pullrequests/7":
            assert outcome != "short"
            if outcome == "unavailable":
                return httpx.Response(404, json={"message": "not available"})
            detail = listed | {"description": None if outcome == "cleared" else complete}
            if outcome == "wrong-id":
                detail["pullRequestId"] = 8
            elif outcome == "wrong-repo":
                detail["repository"] = {"id": "repo-2"}
            elif outcome == "missing-body":
                del detail["description"]
            return httpx.Response(200, json=detail)
        assert path == "/acme/widgets/_apis/git/repositories/repo-1/pullRequests/7/threads"
        return httpx.Response(200, json={"value": [{"comments": [{"content": "Review text"}]}]})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository_id": "repo-1"}],
    )

    @measure_reindex
    def run() -> dict[str, object]:
        if outcome in {"wrong-id", "wrong-repo", "missing-body", "unavailable"}:
            error = httpx.HTTPStatusError if outcome == "unavailable" else ValueError
            with pytest.raises(error):
                connector.discover_changed("2026-01-02T00:00:00Z")
        else:
            entries = connector.discover_changed("2026-01-02T00:00:00Z")
            assert len(entries) == 1
            assert entries[0].source == "ado:acme/widgets:pulls"
            assert entries[0].path == "pulls/7.md"
            assert "Review text" in entries[0].content
            if outcome == "cleared":
                assert preview not in entries[0].content
            else:
                assert (preview if outcome == "short" else complete) in entries[0].content
        return {}

    try:
        counts = run()["work_counts"]
    finally:
        connector._http.client.close()
    assert isinstance(counts, dict)
    expected_bodies = 1 if outcome in {"short", "unavailable", "missing-body"} else 2
    assert counts["acquisition_by_source"] == {
        "ado:acme/widgets:pulls": {
            "identity_records": 1,
            "item_body_records": expected_bodies,
            "comment_body_records": int(outcome in {"short", "complete", "unicode", "cleared"}),
        },
    }
    assert requests == [
        "/acme/widgets/_apis/git/pullrequests",
        *([] if outcome == "short" else ["/acme/widgets/_apis/git/pullrequests/7"]),
        *(
            ["/acme/widgets/_apis/git/repositories/repo-1/pullRequests/7/threads"]
            if outcome in {"short", "complete", "unicode", "cleared"}
            else []
        ),
    ]
    purposes = counts["http_by_purpose"]
    assert purposes["identity_page"]["attempts"] == 1
    assert purposes.get("item_body", {}).get("attempts", 0) == int(outcome != "short")


@pytest.mark.parametrize("body", ["missing", "number", "null"])
def test_invalid_list_description_is_refused_before_comments(body: str) -> None:
    listed: dict[str, Any] = {
        "pullRequestId": 7,
        "repository": {"id": "repo-1"},
    }
    if body != "missing":
        listed["description"] = 9 if body == "number" else None
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/pullrequests"):
            return httpx.Response(200, json={"value": [listed]})
        assert body == "null"
        assert request.url.path.endswith("/pullRequests/7/threads")
        return httpx.Response(200, json={"value": []})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository_id": "repo-1"}],
    )
    try:
        if body == "null":
            entries = connector.discover()
            assert len(entries) == 1
        else:
            with pytest.raises(ValueError, match="description"):
                connector.discover()
    finally:
        connector._http.client.close()
    assert len(requests) == (2 if body == "null" else 1)


def test_pull_counts_retain_parsed_work_on_invalid_body_and_id_only_paths() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": "account"}})
        if path.endswith("/repositories"):
            return httpx.Response(200, json={"value": [{"id": "repo-1", "name": "widgets"}]})
        if path.endswith("/pullrequests"):
            item = {"pullRequestId": 1, "description": None, "repository": {"id": "repo-1"}}
            return httpx.Response(
                200,
                json={
                    "value": [
                        item,
                        item,
                        None,
                        {"pullRequestId": 2, "description": 9},
                        {"pullRequestId": 3},
                    ]
                },
            )
        assert path.endswith("/pullRequests/1/threads")
        return httpx.Response(
            200,
            json={
                "value": [
                    {"comments": [{"content": ""}, {"content": None}, {"content": []}, {}, None]},
                    {"comments": [{"content": "another"}]},
                ]
            },
        )

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository": "widgets", "creator": "me"}],
    )

    @measure_reindex
    def run(paths_only: bool = False) -> dict[str, object]:
        if paths_only:
            assert connector.list_paths() == {
                "ado:acme/widgets:workitems": set(),
                "ado:acme/widgets:pulls": {"pulls/1.md", "pulls/2.md", "pulls/3.md"},
            }
        else:
            with pytest.raises(ValueError, match="description"):
                connector.discover()
        return {}

    try:
        counts = run()["work_counts"]
        assert isinstance(counts, dict)
        assert counts["acquisition_by_source"] == {
            "ado:acme/widgets:pulls": {
                "identity_records": 4,
                "item_body_records": 2,
                "comment_body_records": 3,
            },
        }
        assert counts["http_by_purpose"]["metadata"]["attempts"] == 2
        assert counts["http_by_purpose"]["identity_page"]["attempts"] == 1
        assert counts["http_by_purpose"]["comment_page"]["attempts"] == 1
        assert "unclassified" not in counts["http_by_purpose"]
        path_counts = run(True)["work_counts"]
        assert isinstance(path_counts, dict)
        assert path_counts["acquisition_by_source"] == {
            "ado:acme/widgets:pulls": {
                "identity_records": 4,
                "item_body_records": 2,
                "comment_body_records": 0,
            },
        }
    finally:
        connector._http.client.close()


def test_cancellation_after_description_read_prevents_comment_request() -> None:
    cancelled = False
    requests: list[str] = []
    listed = {
        "pullRequestId": 7,
        "description": "x" * 400,
        "repository": {"id": "repo-1"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cancelled
        requests.append(request.url.path)
        if request.url.path.endswith("/pullrequests"):
            return httpx.Response(200, json={"value": [listed]})
        if request.url.path.endswith("/pullrequests/7"):
            cancelled = True
            return httpx.Response(200, json=listed)
        return httpx.Response(200, json={"value": []})

    def cancel_check() -> None:
        if cancelled:
            raise RuntimeError("cancelled after description")

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository_id": "repo-1"}],
    )
    try:
        with pytest.raises(RuntimeError, match="cancelled after description"):
            connector.discover(cancel_check=cancel_check)
    finally:
        connector._http.client.close()
    assert requests == [
        "/acme/widgets/_apis/git/pullrequests",
        "/acme/widgets/_apis/git/pullrequests/7",
    ]


def test_repeated_description_reads_do_not_request_bodyless_304() -> None:
    listed: dict[str, Any] = {
        "pullRequestId": 7,
        "description": "x" * 400,
        "repository": {"id": "repo-1"},
    }
    detail_requests: list[httpx.Request] = []
    complete = listed["description"] + " complete description tail"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pullrequests"):
            return httpx.Response(200, json={"value": [listed]})
        if request.url.path.endswith("/pullrequests/7"):
            detail_requests.append(request)
            if request.headers.get("If-None-Match") == '"revision-1"':
                return httpx.Response(304)
            return httpx.Response(
                200,
                json=listed | {"description": complete},
                headers={"ETag": '"revision-1"'},
            )
        assert request.url.path.endswith("/pullRequests/7/threads")
        return httpx.Response(200, json={"value": []})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository_id": "repo-1"}],
    )

    @measure_reindex
    def run() -> dict[str, object]:
        entries = connector.discover()
        assert len(entries) == 1
        assert complete in entries[0].content
        return {}

    try:
        for _ in range(2):
            counts = run()["work_counts"]
            assert isinstance(counts, dict)
            assert counts["http"]["attempts"] == 3
            assert counts["http"]["not_modified"] == 0
            assert counts["http_by_purpose"]["item_body"]["attempts"] == 1
    finally:
        connector._http.client.close()
    assert len(detail_requests) == 2
    assert all("If-None-Match" not in request.headers for request in detail_requests)


def test_description_reads_follow_window_and_duplicate_filtering() -> None:
    selected = {
        "pullRequestId": 7,
        "title": "Listed title",
        "status": "active",
        "sourceRefName": "refs/heads/listed",
        "description": "x" * 400,
        "creationDate": "2026-01-03T00:00:00Z",
        "repository": {"id": "repo-1", "name": "listed-repo"},
    }
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path.endswith("/pullrequests"):
            stale = selected | {"pullRequestId": 8, "creationDate": "2026-01-01T00:00:00Z"}
            return httpx.Response(200, json={"value": [selected, selected, stale]})
        if path.endswith("/pullrequests/7"):
            return httpx.Response(
                200,
                json=selected
                | {
                    "description": "Complete description tail",
                    "title": "Conflicting detail title",
                    "status": "completed",
                    "sourceRefName": "refs/heads/conflicting",
                    "repository": {"id": "repo-1", "name": "conflicting-repo"},
                },
            )
        assert path.endswith("/pullRequests/7/threads")
        return httpx.Response(200, json={"value": []})

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository_id": "repo-1"}],
    )
    try:
        entries = connector.discover_changed("2026-01-02T00:00:00Z")
        assert [entry.path for entry in entries] == ["pulls/7.md"]
        assert "Complete description tail" in entries[0].content
        assert entries[0].content.startswith("# Listed title\n")
        assert "Status: active\n" in entries[0].content
        assert "Source: refs/heads/listed\n" in entries[0].content
        assert "Repository: listed-repo\n" in entries[0].content
        assert "conflicting" not in entries[0].content.casefold()
    finally:
        connector._http.client.close()
    assert requests == [
        "/acme/widgets/_apis/git/pullrequests",
        "/acme/widgets/_apis/git/pullrequests/7",
        "/acme/widgets/_apis/git/repositories/repo-1/pullRequests/7/threads",
    ]
