"""Incomplete body acquisition must not become full-reconciliation coverage."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState
from agent_index.sources.azure_devops import AzureDevOpsConnector


def _item(number: int) -> dict[str, Any]:
    return {
        "id": number,
        "fields": {
            "System.Title": f"Selected item {number}",
            "System.Description": f"Body {number}",
            "System.ChangedDate": "2026-01-03T00:00:00Z",
        },
    }


def _connector(
    payload: object,
    requests: list[httpx.Request],
) -> AzureDevOpsConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": "test-account"}})
        if request.url.path.endswith("/wiql"):
            return httpx.Response(200, json={"workItems": [{"id": 1}, {"id": 2}]})
        if request.url.path.endswith("/workitemsbatch"):
            if "fields" in json.loads(request.content):
                return httpx.Response(200, json={"value": [_item(1), _item(2)]})
            return httpx.Response(200, json=payload)
        assert request.url.path.endswith("/comments")
        return httpx.Response(200, json={"comments": []})

    return AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[{"wiql": "Operator query"}],
        pull_request_queries=[],
    )


@pytest.mark.parametrize("timestamp", [None, "2026-01-03T00:01:00Z"])
@pytest.mark.parametrize(
    "payload",
    [
        {"value": [_item(1)]},
        {"value": [_item(1), _item(1)]},
        {"value": [_item(1), _item(3)]},
        {"value": [_item(1), _item(2), None]},
        {"value": [_item(1) | {"id": True}, _item(2)]},
        {"value": [_item(1), {"id": 2, "fields": None}]},
        {"value": None},
    ],
)
def test_body_batch_refuses_before_comments(payload: object, timestamp: str | None) -> None:
    requests: list[httpx.Request] = []
    connector = _connector(payload, requests)
    try:
        with pytest.raises(ValueError, match="work-item body batch"):
            if timestamp is None:
                connector.discover()
            else:
                connector.discover_changed(timestamp)
    finally:
        connector._http.client.close()
    assert not any(request.url.path.endswith("/comments") for request in requests)


@pytest.mark.parametrize("timestamp", [None, "2026-01-03T00:01:00Z"])
def test_complete_reordered_body_batch_preserves_provider_order(timestamp: str | None) -> None:
    requests: list[httpx.Request] = []
    connector = _connector({"value": [_item(2), _item(1)]}, requests)
    try:
        entries = (
            connector.discover() if timestamp is None else connector.discover_changed(timestamp)
        )
    finally:
        connector._http.client.close()
    assert [entry.path for entry in entries] == ["workitems/2.md", "workitems/1.md"]
    assert "Body 2" in entries[0].content and "Body 1" in entries[1].content
    body_requests = [
        json.loads(request.content)
        for request in requests
        if request.url.path.endswith("/workitemsbatch")
        and "fields" not in json.loads(request.content)
    ]
    assert body_requests == [{"ids": [1, 2], "$expand": "all", "errorPolicy": "fail"}]


def test_incomplete_full_crawl_cannot_publish_deletion_manifest(tmp_path: Path) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    family = "ado:acme/widgets:workitems"
    paths.mark_indexed_batch(
        [
            (family, "workitems/1.md", "old-one", 1),
            (family, "workitems/2.md", "old-two", 1),
            ("ado:other/project:workitems", "workitems/2.md", "foreign", 1),
        ]
    )
    before_paths = paths.get_paths_by_family("ado")
    assert before_paths[family] == {"workitems/1.md", "workitems/2.md"}
    state = IndexState()
    before_state = asdict(state)
    requests: list[httpx.Request] = []
    connector = _connector({"value": [_item(1)]}, requests)
    try:
        with pytest.raises(ValueError, match="work-item body batch"):
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
    assert paths.get_paths_by_family("ado") == before_paths
    assert asdict(state) == before_state
