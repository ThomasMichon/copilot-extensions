"""Unknown acquisition responses must not certify an empty source."""

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


def _connector(payload: object, phase: str) -> AzureDevOpsConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/connectionData"):
            result: object = {"authenticatedUser": {"id": "test-account"}}
        elif path.endswith("/wiql"):
            result = payload
        elif path.endswith("/pullrequests"):
            result = (
                payload
                if phase == "pulls"
                else {"value": [{"pullRequestId": 7, "repository": {"id": "repo-1"}}]}
            )
        else:
            assert phase == "threads" and path.endswith("/pullRequests/7/threads")
            result = payload
        return httpx.Response(200, json=result)

    return AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        work_item_queries=[{"wiql": "Operator query"}] if phase == "query" else [],
        pull_request_queries=[] if phase == "query" else [{"repository_id": "repo-1"}],
    )


@pytest.mark.parametrize(
    ("phase", "payload"),
    [
        ("query", {}),
        ("query", []),
        ("query", {"workItems": None}),
        ("query", {"queryType": "unknown"}),
        ("query", {"queryType": "flat", "workItems": {}}),
        ("query", {"workItems": [{"id": 1}, {}]}),
        ("query", {"workItemRelations": [{"target": {"id": True}}]}),
        ("query", {"workItemRelations": [None]}),
        ("pulls", {}),
        ("pulls", {"value": None}),
        ("pulls", {"count": 0}),
        ("pulls", {"value": [None]}),
        ("threads", {}),
        ("threads", {"value": None}),
        ("threads", {"value": [None]}),
    ],
)
def test_invalid_response_cannot_publish_full_deletion_manifest(
    tmp_path: Path,
    phase: str,
    payload: object,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    family = f"ado:acme/widgets:{'workitems' if phase == 'query' else 'pulls'}"
    paths.mark_indexed_batch(
        [
            (family, "workitems/1.md" if phase == "query" else "pulls/7.md", "old", 1),
            ("ado:other/project:pulls", "pulls/7.md", "foreign", 1),
        ]
    )
    before_paths = paths.get_paths_by_family("ado")
    state = IndexState()
    before_state = asdict(state)
    connector = _connector(payload, phase)
    try:
        with pytest.raises(ValueError, match="Azure DevOps"):
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


@pytest.mark.parametrize(
    "payload",
    [
        {"workItems": []},
        {"workItemRelations": []},
        {"queryType": "flat"},
        {"queryType": "oneHop", "workItemRelations": None},
        {"queryType": "tree"},
    ],
)
def test_query_empty_collections_and_serialized_defaults_remain_valid(
    payload: dict[str, Any],
) -> None:
    connector = _connector(payload, "query")
    try:
        assert connector.discover() == []
    finally:
        connector._http.client.close()


@pytest.mark.parametrize("phase", ["pulls", "threads"])
@pytest.mark.parametrize("payload", [[], {"count": 0, "value": []}])
def test_explicit_empty_collection_and_legacy_array_remain_valid(
    phase: str,
    payload: object,
) -> None:
    connector = _connector(payload, phase)
    try:
        entries = connector.discover()
    finally:
        connector._http.client.close()
    assert [entry.path for entry in entries] == ([] if phase == "pulls" else ["pulls/7.md"])


def test_query_record_observations_survive_refusal() -> None:
    connector = _connector({"workItems": [{"id": 1}, {"id": 1}, None]}, "query")

    @measure_reindex
    def run() -> dict[str, object]:
        with pytest.raises(ValueError, match="Azure DevOps"):
            connector.discover()
        return {}

    try:
        counts = run()["work_counts"]
    finally:
        connector._http.client.close()
    assert isinstance(counts, dict)
    assert counts["acquisition_by_source"] == {
        "ado:acme/widgets:workitems": {
            "identity_records": 2,
            "item_body_records": 0,
            "comment_body_records": 0,
        },
    }
