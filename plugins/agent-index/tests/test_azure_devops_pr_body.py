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
