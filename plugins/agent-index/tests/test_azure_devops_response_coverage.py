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
            if int(request.url.params["$skip"]):
                return httpx.Response(200, json={"value": []})
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
        *[
            ("pulls", {"value": [item]})
            for item in [
                {},
                {"pullRequestId": None},
                {"pullRequestId": True},
                {"pullRequestId": "7"},
                {"pullRequestId": []},
                {"pullRequestId": {}},
                {"pullRequestId": 0},
                {"pullRequestId": -7},
                {"pullRequestId": 7, "repository": []},
                {"pullRequestId": 7, "repository": {"id": 9}},
            ]
        ],
        ("threads", {}),
        ("threads", {"value": None}),
        ("threads", {"value": [None]}),
        ("threads", {"value": [{"comments": {}}]}),
        ("threads", {"value": [{"comments": "not a collection"}]}),
        ("threads", {"value": [{"comments": [None]}]}),
        ("threads", {"value": [{"comments": ["not a record"]}]}),
        ("threads", {"value": [{"comments": [{"content": []}]}]}),
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


@pytest.mark.parametrize("item", [{}, {"pullRequestId": True}, {"pullRequestId": []}])
def test_unusable_selected_identity_cannot_certify_path_inventory(
    item: dict[str, Any],
) -> None:
    connector = _connector({"value": [item]}, "pulls")
    try:
        with pytest.raises(ValueError, match="Azure DevOps.*identity"):
            connector.list_paths()
    finally:
        connector._http.client.close()


@pytest.mark.parametrize(
    "thread",
    [{}, {"comments": None}, {"comments": []}, {"comments": [{}, {"content": None}]}],
)
def test_thread_serialized_defaults_remain_valid(thread: dict[str, Any]) -> None:
    connector = _connector({"value": [thread]}, "threads")
    try:
        assert [entry.path for entry in connector.discover()] == ["pulls/7.md"]
    finally:
        connector._http.client.close()


def _body_connector(
    owner: str,
    transport: httpx.MockTransport,
) -> AzureDevOpsConnector:
    return AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        transport=transport,
        min_interval_s=0,
        work_item_queries=[{"saved_query_id": "shared/team-query"}] if owner == "query" else [],
        pull_request_queries=[{"repository_id": "repo-1"}] if owner == "threads" else [],
    )


def _body_response(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/connectionData"):
        return httpx.Response(200, json={"authenticatedUser": {"id": "test-account"}})
    if "/wiql/" in path:
        return httpx.Response(200, json={"workItems": [{"id": 7}]})
    if path.endswith("/workitemsbatch"):
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 7,
                        "fields": {"System.Title": "Selected item", "System.Description": "Body"},
                    }
                ]
            },
        )
    if path.endswith("/workItems/7/comments"):
        return httpx.Response(200, json={"comments": [{"text": "Complete comment"}]})
    if path.endswith("/pullrequests"):
        return httpx.Response(
            200,
            json={
                "value": []
                if int(request.url.params["$skip"])
                else [
                    {
                        "pullRequestId": 7,
                        "repository": {"id": "repo-1"},
                        "description": "",
                    }
                ]
            },
        )
    assert path.endswith("/repositories/repo-1/pullRequests/7/threads")
    return httpx.Response(200, json={"value": [{"comments": [{"content": "Complete comment"}]}]})


@pytest.mark.parametrize("owner", ["query", "threads"])
def test_repeated_complete_body_reads_do_not_send_validators(owner: str) -> None:
    validators: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        selected = (
            "/wiql/" in request.url.path
            if owner == "query"
            else (request.url.path.endswith("/threads"))
        )
        if selected:
            validators.append(request.headers.get("If-None-Match"))
            assert request.url.params["api-version"] == "7.1"
            if owner == "query":
                assert request.url.path.endswith("/wiql/shared/team-query")
            if validators[-1] is not None:
                return httpx.Response(304)
        result = _body_response(request)
        if selected:
            result.headers["ETag"] = '"selected-revision"'
        return result

    connector = _body_connector(owner, httpx.MockTransport(handler))

    @measure_reindex
    def run() -> dict[str, object]:
        entries = connector.discover()
        assert [entry.path for entry in entries] == [
            "workitems/7.md" if owner == "query" else "pulls/7.md",
        ]
        assert "Complete comment" in entries[0].content
        return {}

    try:
        for _ in range(2):
            counts = run()["work_counts"]
            assert isinstance(counts, dict)
            assert counts["acquisition_by_source"] == {
                f"ado:acme/widgets:{'workitems' if owner == 'query' else 'pulls'}": {
                    "identity_records": 1,
                    "item_body_records": 1,
                    "comment_body_records": 1,
                },
            }
            assert counts["http"]["not_modified"] == 0
    finally:
        connector._http.client.close()
    assert validators == [None, None]


@pytest.mark.parametrize("owner", ["query", "threads"])
def test_unexpected_bodyless_response_preserves_collector_state(
    owner: str, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (owner == "query" and "/wiql/" in request.url.path) or (
            owner == "threads" and request.url.path.endswith("/threads")
        ):
            return httpx.Response(304)
        return _body_response(request)

    connector = _body_connector(owner, httpx.MockTransport(handler))
    family = f"ado:acme/widgets:{'workitems' if owner == 'query' else 'pulls'}"
    path = "workitems/7.md" if owner == "query" else "pulls/7.md"
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch(
        [
            (family, path, "old", 1),
            ("ado:other/project:pulls", "pulls/7.md", "foreign", 1),
        ]
    )
    before_paths = paths.get_paths_by_family("ado")
    state = IndexState()
    before_state = asdict(state)
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


def test_nested_record_observations_survive_refusal() -> None:
    connector = _connector(
        {"value": [{"comments": [{"content": "parsed"}, None, {"content": None}]}]},
        "threads",
    )

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
        "ado:acme/widgets:pulls": {
            "identity_records": 1,
            "item_body_records": 0,
            "comment_body_records": 2,
        },
    }
