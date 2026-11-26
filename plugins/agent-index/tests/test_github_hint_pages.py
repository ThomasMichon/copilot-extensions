"""Opt-in GitHub hinted units use the existing durable acquisition journal."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_index.indexing.change_hints import ChangeHints, MetadataRevision
from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.hinted_crawl import hint_page_discovery
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recover_source, recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.github import GitHubConnector

SOURCE = "github:acme/widgets"
URL = "https://api.github.com/repos/acme/widgets"
STAMP = "2026-01-02T00:00:00Z"
BASE = "prior-poll"
HINTS = ChangeHints(
    source=SOURCE, canonical_family=SOURCE, account_id="123", item_ids=("2", "1", "1"),
    event_ids=("delivery",), metadata_revisions=(MetadataRevision("2", STAMP),),
)


def item(number: int, *, comments: int = 0) -> dict[str, Any]:
    return {
        "number": number, "title": f"Item {number}", "body": "body", "comments": comments,
        "updated_at": STAMP, "repository_url": URL, "url": URL + f"/issues/{number}",
        **({"pull_request": {}} if number == 2 else {}),
    }


def connector(
    handler: Callable[[httpx.Request], httpx.Response], *, paged_hints: bool = True,
) -> GitHubConnector:
    return GitHubConnector(
        SOURCE, token="fixture", transport=httpx.MockTransport(handler),
        min_interval_s=0, paged_hints=paged_hints,
    )


def test_opt_in_retains_legacy_acquisition_and_bounds_each_page() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert "if-none-match" not in request.headers
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        number = int(request.url.path.split("/")[5])
        if request.url.path.endswith("/comments"):
            assert dict(request.url.params) == {"page": "1", "per_page": "100"}
            return httpx.Response(200, json=(
                [{"id": i, "body": f"comment {i}"} for i in range(1, 101)]
                if number == 1 else []
            ))
        return httpx.Response(200, json=item(number, comments=100 if number == 1 else 0))

    for enabled in (False, True):
        provider = connector(handler, paged_hints=enabled)
        try:
            discover = hint_page_discovery(provider)
            if not enabled:
                assert discover is None
                assert len(provider.discover_hints(HINTS, BASE).upserts) == 2
                continue
            assert discover is not None
            before = HINTS.to_json()
            first = discover(HINTS, BASE, None)
            assert first.cursor is not None
            second = discover(HINTS, BASE, first.cursor)
            assert second.cursor is None
            assert first.delta.commit == second.delta.commit == BASE
            assert first.delta.deletions == second.delta.deletions == []
            assert [entry.path for entry in first.delta.upserts] == ["issues/1.md"]
            assert [entry.path for entry in second.delta.upserts] == ["pulls/2.md"]
            assert "comment 100" in first.delta.upserts[0].content
            assert HINTS.to_json() == before
        finally:
            provider._client.close()
    assert len(calls) == 10  # Two accounts and four complete item/comment pairs.
    assert "/repos/acme/widgets/issues" not in calls


@pytest.mark.parametrize(
    "change", ["items", "events", "revision", "account", "base", "recovery", "index"],
)
def test_changed_continuation_refuses_before_item_work(change: str) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=item(1))

    provider = connector(handler)
    try:
        discover = hint_page_discovery(provider)
        assert discover is not None
        cursor = discover(HINTS, BASE, None).cursor
        assert cursor is not None
        calls.clear()
        hints, base = HINTS, BASE
        if change == "items":
            hints = replace(hints, item_ids=("1", "3"))
        elif change == "events":
            hints = replace(hints, event_ids=("different",))
        elif change == "revision":
            hints = replace(
                hints, metadata_revisions=(MetadataRevision("2", "2026-01-03T00:00:00Z"),),
            )
        elif change == "account":
            hints = replace(hints, account_id="other")
        elif change == "base":
            base = "different"
        elif change == "recovery":
            provider._default_since = STAMP
        else:
            data = json.loads(cursor)
            data["index"] = True
            cursor = json.dumps(data)
        with pytest.raises(ValueError):
            discover(hints, base, cursor)
        assert calls == []
    finally:
        provider._client.close()


@pytest.mark.parametrize("failure", ["oversized", "next-page", "incomplete"])
def test_nested_work_refuses_instead_of_exceeding_one_comment_page(failure: str) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path.endswith("/comments"):
            assert failure != "oversized"
            assert request.url.params["page"] == "1"
            return httpx.Response(
                200, json=[{"id": 1}],
                headers={"Link": '<https://api.github.com/ignored>; rel="next"'}
                if failure == "next-page" else {},
            )
        return httpx.Response(200, json=item(1, comments=101 if failure == "oversized" else 2))

    provider = connector(handler)
    try:
        discover = hint_page_discovery(provider)
        assert discover is not None
        with pytest.raises(ValueError, match="100 comments|single complete|incomplete"):
            discover(HINTS, BASE, None)
        assert len(calls) == (2 if failure == "oversized" else 3)
    finally:
        provider._client.close()


def test_empty_hints_and_cancellation_do_not_fetch_items() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/user"
        return httpx.Response(200, json={"id": 123})

    def cancelled() -> None:
        raise RuntimeError("cancelled")

    provider = connector(handler)
    try:
        discover = hint_page_discovery(provider)
        assert discover is not None
        with pytest.raises(RuntimeError, match="cancelled"):
            discover(HINTS, BASE, None, cancel_check=cancelled)
        assert calls == []
        empty = replace(HINTS, item_ids=(), metadata_revisions=(), event_ids=())
        page = discover(empty, BASE, None)
        assert page.cursor is None and page.delta.commit == BASE
        assert page.delta.upserts == [] and page.delta.deletions == []
        assert calls == ["/user"]
    finally:
        provider._client.close()


def test_reconstructed_collector_does_not_repeat_acknowledged_items(tmp_path: Path) -> None:
    calls: list[str] = []
    fail = True

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        assert request.url.path != "/repos/acme/widgets/issues"
        number = int(request.url.path.split("/")[5])
        if request.url.path.endswith("/comments"):
            if number == 2 and fail:
                raise RuntimeError("interrupted comment acquisition")
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=item(number))

    paths_file, state_file = tmp_path / "paths.db", tmp_path / "state.json"
    initial = connector(handler)
    try:
        scope = recovery_scope("config", initial)
        state = IndexState(
            sources={SOURCE: SourceState(last_commit=BASE, acquisition_scope=scope)},
            stored_families={SOURCE: SOURCE},
        )
        state.save(state_file)
        with pytest.raises(RuntimeError, match="interrupted"):
            collect_source_changes(
                SOURCE, state=state, path_index=PathIndex(paths_file), full=False,
                source_aware=True, connector=initial, source_scope="config", change_hints=HINTS,
                require_paged_acquisition=True,
            )
    finally:
        initial._client.close()
    journal = PendingCrawls(PathIndex(paths_file))
    payload = journal.read_payload(SOURCE, scope)
    assert payload is not None and json.loads(payload)["version"] == 5
    assert journal.load(SOURCE, scope) is None
    assert IndexState.load(state_file).sources[SOURCE].last_commit == BASE
    fail = False
    resumed = connector(handler)
    try:
        state = IndexState.load(state_file)
        manifest = collect_source_changes(
            SOURCE, state=state, path_index=PathIndex(paths_file), full=False,
            source_aware=True, connector=resumed, source_scope="config", change_hints=HINTS,
            require_paged_acquisition=True,
        )
    finally:
        resumed._client.close()
    assert [entry.path for entry in manifest.upserts] == ["issues/1.md", "pulls/2.md"]
    assert manifest.deletions == [] and manifest.commit == BASE
    assert calls.count("/repos/acme/widgets/issues/1") == 1
    assert calls.count("/repos/acme/widgets/issues/1/comments") == 1
    assert calls.count("/repos/acme/widgets/issues/2") == 2
    assert calls.count("/repos/acme/widgets/issues/2/comments") == 2

    def apply(*, manifest: CrawlManifest, full: bool) -> tuple[int, int, int]:
        assert not full and len(manifest.upserts) == 2
        state.mark_source_indexed(SOURCE, manifest.commit, 2)
        return 2, 0, 2

    def forbidden() -> CrawlManifest:
        pytest.fail("completed hinted work must replay without provider acquisition")

    before = list(calls)
    assert recover_source(
        SOURCE, scope=scope, state=state, state_file=state_file, paths=PathIndex(paths_file),
        full=False, family=SOURCE, change_hints=HINTS, acquire=forbidden, apply=apply,
    ) == (2, 0, 2)
    assert calls == before
    assert PendingCrawls(PathIndex(paths_file)).read_payload(SOURCE, scope) is None
    assert IndexState.load(state_file).sources[SOURCE].last_commit == BASE
