"""Continuation traversal cannot return partial inventories as complete coverage."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState
from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.azure_devops import AzureDevOpsConnector
from agent_index.sources.good_citizen_http import ApiResult, GoodCitizenSession


def test_repeated_traversal_acquires_complete_bodies_without_conditional_validators() -> None:
    validators: list[str | None] = []
    observed: list[ApiResult] = []

    def handler(request: httpx.Request) -> httpx.Response:
        validators.append(request.headers.get("If-None-Match"))
        if validators[-1] is not None:
            return httpx.Response(304)
        return httpx.Response(200, json={"value": [7]}, headers={"ETag": '"selected"'})

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )
    try:
        for _ in range(2):
            pages = session.paginate_continuation("/items", on_page=observed.append)
            assert [page.data for page in pages] == [{"value": [7]}]
    finally:
        session.client.close()
    assert validators == [None, None]
    assert [page.data for page in observed] == [{"value": [7]}, {"value": [7]}]


@pytest.mark.parametrize("later_page", [False, True])
def test_not_modified_page_refuses_instead_of_returning_a_prefix(later_page: bool) -> None:
    requests: list[httpx.Request] = []
    observed: list[ApiResult] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if later_page and len(requests) == 1:
            return httpx.Response(
                200,
                json={"value": [7]},
                headers={"x-ms-continuationtoken": "next"},
            )
        return httpx.Response(304)

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )
    try:
        with pytest.raises(ValueError, match="continuation"):
            session.paginate_continuation("/items", on_page=observed.append)
    finally:
        session.client.close()
    assert len(requests) == 1 + later_page
    assert len(observed) == int(later_page)


@pytest.mark.parametrize("tokens", [("same", "same"), ("first", "second", "first")])
def test_repeated_token_refuses_before_refetching_a_page(tokens: tuple[str, ...]) -> None:
    requested_tokens: list[str | None] = []
    observed: list[ApiResult] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_tokens.append(request.url.params.get("continuationToken"))
        assert len(requested_tokens) <= len(tokens), "Traversal refetched a repeated token"
        return httpx.Response(
            200,
            json={"value": [7]},
            headers={"x-ms-continuationtoken": tokens[len(requested_tokens) - 1]},
        )

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )
    try:
        with pytest.raises(ValueError, match="continuation"):
            session.paginate_continuation("/items", on_page=observed.append)
    finally:
        session.client.close()
    assert requested_tokens == [None, *tokens[:-1]]
    assert len(observed) == len(tokens)


def test_initial_continuation_token_cannot_be_requested_again() -> None:
    requested_tokens: list[str] = []
    token = "already-requested"

    def handler(request: httpx.Request) -> httpx.Response:
        requested_tokens.append(request.url.params["continuationToken"])
        assert len(requested_tokens) == 1, "Traversal refetched its initial token"
        return httpx.Response(
            200,
            json={"value": [7]},
            headers={"x-ms-continuationtoken": token},
        )

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )
    try:
        with pytest.raises(ValueError, match="continuation"):
            session.paginate_continuation("/items", params={"continuationToken": token})
    finally:
        session.client.close()
    assert requested_tokens == [token]


def test_opaque_custom_tokens_empty_terminal_and_http_accounting_are_preserved() -> None:
    token = "opaque +/&=token"
    requested_tokens: list[str | None] = []
    observed: list[ApiResult] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api-version"] == "7.1"
        assert request.headers.get("If-None-Match") is None
        requested_tokens.append(request.url.params.get("cursor"))
        return httpx.Response(
            200,
            json={"value": [7] if len(requested_tokens) == 1 else []},
            headers={"X-Next": token} if len(requested_tokens) == 1 else {},
        )

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )

    @measure_reindex
    def run() -> dict[str, object]:
        return {
            "pages": session.paginate_continuation(
                "/items",
                params={"api-version": "7.1"},
                continuation_param="cursor",
                continuation_header="X-Next",
                purpose="identity_page",
                on_page=observed.append,
            )
        }

    try:
        result = run()
    finally:
        session.client.close()
    pages = result["pages"]
    assert isinstance(pages, list)
    assert [page.data for page in pages] == [{"value": [7]}, {"value": []}]
    assert requested_tokens == [None, token]
    assert len(observed) == 2
    counts = result["work_counts"]
    assert isinstance(counts, dict)
    http = counts["http_by_purpose"]["identity_page"]
    assert http["attempts"] == http["responses"] == http["json_results"] == 2


@pytest.mark.parametrize("fault", ["initial-304", "later-304", "repeat", "cycle"])
def test_incomplete_pr_traversal_cannot_publish_deletion_coverage(
    fault: str,
    tmp_path: Path,
) -> None:
    listing_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal listing_requests
        if request.url.path.endswith("/connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": "test-account"}})
        assert request.url.path == "/acme/widgets/_apis/git/pullrequests"
        listing_requests += 1
        assert listing_requests <= 3, "Traversal refetched a repeated token"
        if fault == "initial-304" or (fault == "later-304" and listing_requests == 2):
            return httpx.Response(304)
        token = "second" if fault == "cycle" and listing_requests == 2 else "first"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "pullRequestId": 7,
                        "description": "",
                        "repository": {"id": "repo-1"},
                    }
                ]
            },
            headers={"x-ms-continuationtoken": token},
        )

    connector = AzureDevOpsConnector(
        "ado:acme/widgets",
        token="fixture-token",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        work_item_queries=[],
        pull_request_queries=[{"repository_id": "repo-1"}],
    )
    paths = PathIndex(tmp_path / "paths.db")
    family = "ado:acme/widgets:pulls"
    paths.mark_indexed_batch(
        [
            (family, "pulls/7.md", "old-seven", 1),
            (family, "pulls/8.md", "old-eight", 1),
            ("ado:other/project:pulls", "pulls/8.md", "foreign", 1),
        ]
    )
    before_paths = paths.get_paths_by_family("ado")
    state = IndexState()
    before_state = asdict(state)
    try:
        with pytest.raises(ValueError, match="continuation"):
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
    assert listing_requests == {"initial-304": 1, "later-304": 2, "repeat": 2, "cycle": 3}[fault]
