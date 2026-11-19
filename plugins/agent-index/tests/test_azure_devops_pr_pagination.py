"""PR inventories follow the documented offset API without certifying partial coverage."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState
from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.azure_devops import AzureDevOpsConnector


def _connector(handler: httpx.MockTransport, *, page_size: Any = 2) -> AzureDevOpsConnector:
    return AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        transport=handler,
        min_interval_s=0,
        work_item_queries=[],
        pull_request_queries=[
            {"repository_id": "repo-1", "status": "active", "target_ref": "refs/heads/main"},
            {"repository_id": "repo-1", "status": "completed"},
        ],
        page_size=page_size,
    )


@pytest.mark.parametrize("paths_only", [False, True])
@pytest.mark.parametrize("total", [0, 3, 4])
def test_headerless_pages_preserve_query_union_and_counting(paths_only: bool, total: int) -> None:
    offsets: dict[str, list[int]] = {"active": [], "completed": []}
    details: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pullrequests"):
            query = request.url.params
            status = query["searchCriteria.status"]
            assert query["searchCriteria.repositoryId"] == "repo-1"
            assert query["api-version"] == "7.1"
            assert query["$top"] == "2"
            assert "continuationToken" not in query
            assert request.headers.get("If-None-Match") is None
            if status == "active":
                assert query["searchCriteria.targetRefName"] == "refs/heads/main"
            offset = int(query["$skip"])
            offsets[status].append(offset)
            assert len(offsets[status]) <= 3
            ids = list(range(1, total + 1)) if status == "active" else ([1] if total else [])
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "pullRequestId": number,
                            "repository": {"id": "repo-1"},
                            "description": "Preview",
                        }
                        for number in ids[offset : offset + 2]
                    ]
                },
                headers={"ETag": '"inventory"'},
            )
        if path.endswith("/threads"):
            return httpx.Response(200, json={"value": []})
        number = int(path.rsplit("/", 1)[1])
        details.append(number)
        return httpx.Response(
            200,
            json={
                "pullRequestId": number,
                "repository": {"id": "repo-1"},
                "description": "Complete",
            },
        )

    connector = _connector(httpx.MockTransport(handler), page_size=2)

    @measure_reindex
    def run() -> dict[str, object]:
        if paths_only:
            assert connector.list_paths()["ado:acme/widgets:pulls"] == {
                f"pulls/{number}.md" for number in range(1, total + 1)
            }
        else:
            entries = connector.discover()
            assert [entry.path for entry in entries] == [
                f"pulls/{number}.md" for number in range(1, total + 1)
            ]
            assert all("Complete" in entry.content for entry in entries)
        return {}

    try:
        result = run()
    finally:
        connector._http.client.close()
    active_offsets = list(range(0, total, 2)) + [total]
    assert offsets == {
        "active": active_offsets,
        "completed": [0, 1] if total else [0],
    }
    assert details == ([] if paths_only else list(range(1, total + 1)))
    counts = result["work_counts"]
    assert isinstance(counts, dict)
    assert counts["acquisition_by_source"]["ado:acme/widgets:pulls"]["identity_records"] == (
        total + bool(total)
    )


def test_server_short_pages_do_not_certify_exhaustion() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["$skip"])
        offsets.append(offset)
        assert len(offsets) <= 4
        assert request.url.params["$top"] == "2"
        return httpx.Response(
            200,
            json={"value": [{"pullRequestId": offset + 1}] if offset < 3 else []},
        )

    connector = _connector(httpx.MockTransport(handler))
    connector._pull_request_queries = [{"repository_id": "repo-1"}]
    try:
        assert connector.list_paths()["ado:acme/widgets:pulls"] == {
            "pulls/1.md",
            "pulls/2.md",
            "pulls/3.md",
        }
    finally:
        connector._http.client.close()
    assert offsets == [0, 1, 2, 3]


@pytest.mark.parametrize("fault", ["304", "http", "json", "repeat", "cycle", "oversized"])
def test_later_page_failure_preserves_collector_paths_and_checkpoint(
    fault: str,
    tmp_path: Path,
) -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": "test-account"}})
        assert request.url.path.endswith("/pullrequests")
        offset = int(request.url.params["$skip"])
        offsets.append(offset)
        assert len(offsets) <= 3, "Traversal failed to reject repeated inventory"
        if offset:
            if fault == "304":
                return httpx.Response(304)
            if fault == "http":
                return httpx.Response(404)
            if fault == "json":
                return httpx.Response(200, content=b"invalid json")
        ids = [3, 4] if fault == "cycle" and offset == 2 else [1, 2]
        if fault == "oversized" and offset:
            ids = [3, 4, 5]
        return httpx.Response(200, json={"value": [{"pullRequestId": i} for i in ids]})

    connector = _connector(httpx.MockTransport(handler), page_size=2)
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch(
        [
            ("ado:acme/widgets:pulls", "pulls/9.md", "old", 1),
            ("ado:other/project:pulls", "pulls/9.md", "foreign", 1),
        ]
    )
    before_paths = paths.get_paths_by_family("ado")
    state = IndexState()
    before_state = asdict(state)
    try:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            collect_source_changes(
                "selected",
                state=state,
                path_index=paths,
                connector=connector,
                full=True,
                source_aware=True,
            )
    finally:
        connector._http.client.close()
    assert offsets == ([0, 2, 4] if fault == "cycle" else [0, 2])
    assert paths.get_paths_by_family("ado") == before_paths
    assert asdict(state) == before_state


@pytest.mark.parametrize("page_size", [0, -1, True, 1.5, "2", None])
def test_invalid_page_size_refuses_before_network(page_size: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Invalid page size reached network: {request.url}")

    with pytest.raises(ValueError, match="page_size"):
        _connector(httpx.MockTransport(handler), page_size=page_size)


def test_cancellation_between_pages_prevents_next_request() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offsets.append(int(request.url.params["$skip"]))
        return httpx.Response(200, json={"value": [{"pullRequestId": 1}]})

    def cancel() -> None:
        if offsets:
            raise RuntimeError("cancelled")

    connector = _connector(httpx.MockTransport(handler), page_size=1)
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            connector.list_paths(cancel_check=cancel)
    finally:
        connector._http.client.close()
    assert offsets == [0]
