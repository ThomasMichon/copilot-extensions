"""Finite hosted polling never enumerates the corpus or trusts mutable offsets."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recover_source, recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.github import GitHubConnector

SOURCE = "github:acme/widgets"
BASE = "2026-01-01T00:00:00+00:00"
STAMP = "2026-01-02T00:00:00Z"
URL = "https://api.github.com/repos/acme/widgets"


def item(number=1, **kwargs):
    return {
        "number": number, "title": f"Issue {number}", "body": "body", "comments": 0,
        "updated_at": STAMP, "repository_url": URL, "url": URL + f"/issues/{number}",
        **kwargs,
    }


def connector(handler):
    return GitHubConnector(
        SOURCE, token="fixture", min_interval_s=0, transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("corpus_size", [100, 10000, 100000])
@pytest.mark.parametrize("changed", [False, True])
def test_normal_round_cost_ignores_unchanged_corpus(tmp_path, corpus_size, changed, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path == "/repos/acme/widgets/issues":
            assert request.url.params["since"] == "2025-12-31T23:59:00Z"
            assert request.url.params["page"] == "1"
            return httpx.Response(200, json=[item()] if changed else [])
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=item())

    provider = connector(handler)
    scope = recovery_scope("config", provider)
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState(sources={"alias": SourceState(last_commit=BASE, acquisition_scope=scope)})
    state.stored_families["alias"] = SOURCE
    # No data/corpus query is necessary, irrespective of how much has been indexed.
    state.sources["alias"].chunk_count = corpus_size
    monkeypatch.setattr(paths, "get_paths_by_family", lambda _: pytest.fail("corpus path scan"))
    applied = []

    def acquire():
        return collect_source_changes(
            "alias", state=state, path_index=paths, source_aware=True, full=False,
            connector=provider, source_scope="config",
        )

    def apply(*, manifest, full):
        applied.append(manifest)
        state.mark_source_indexed("alias", manifest.commit, corpus_size)
        return 0, 0, len(manifest.upserts)

    recover_source(
        "alias", scope=scope, state=state, state_file=tmp_path / "state.json",
        paths=paths, full=False, family=SOURCE, acquire=acquire, apply=apply,
    )
    manifest = applied[0]
    assert manifest.deletions == [] and len(manifest.upserts) == int(changed)
    assert manifest.stats.files_scanned == int(changed)
    assert len(calls) == (4 if changed else 2)  # Account, one list, optional item/comments.
    assert PendingCrawls(paths).sources() == []
    assert state.sources["alias"].last_commit == (
        "2026-01-02T00:00:00+00:00" if changed else BASE
    )


def test_restart_does_not_refetch_plan_or_completed_work_items(tmp_path):
    requests = []
    fail = True

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path == "/repos/acme/widgets/issues":
            assert requests.count(request.url.path) == 1
            return httpx.Response(200, json=[item(1), item(2, pull_request={})])
        if request.url.path.endswith("/comments"):
            if fail and "/2/" in request.url.path:
                raise RuntimeError("comment acquisition interrupted")
            return httpx.Response(200, json=[])
        number = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=item(number, **({"pull_request": {}} if number == 2 else {})))

    initial = connector(handler)
    state = IndexState(sources={"alias": SourceState(
        last_commit=BASE, acquisition_scope=recovery_scope("config", initial),
    )})
    state.stored_families["alias"] = SOURCE

    def collect(provider):
        return collect_source_changes(
            "alias", state=state, path_index=PathIndex(tmp_path / "paths.db"),
            source_aware=True, full=False, connector=provider, source_scope="config",
        )

    with pytest.raises(RuntimeError, match="interrupted"):
        collect(connector(handler))
    assert state.sources["alias"].last_commit == BASE
    fail = False
    manifest = collect(connector(handler))
    assert [entry.path for entry in manifest.upserts] == ["issues/1.md", "pulls/2.md"]
    assert requests.count("/repos/acme/widgets/issues/1") == 1
    assert requests.count("/repos/acme/widgets/issues/1/comments") == 1
    assert requests.count("/repos/acme/widgets/issues/2") == 2
    assert manifest.commit == "2026-01-02T00:00:00+00:00"


@pytest.mark.parametrize("payload,headers,status", [
    ([item()], {"Link": '<https://api.github.com/next>; rel="next"'}, 200),
    ([item()] * 101, {}, 200), ([item(), item()], {}, 200),
    ([item(number=True)], {}, 200), ([item(number=-1)], {}, 200),
    ([item(url="https://other.invalid/issues/1")], {}, 200),
    ([item(repository_url="https://other.invalid")], {}, 200),
    ([item(updated_at="bad")], {}, 200), ([item(comments=None)], {}, 200),
    ({}, {}, 200), ([], {}, 304),
])
def test_truncated_or_malformed_window_never_fetches_items_or_advances(payload, headers, status):
    def handler(request):
        assert request.url.path == "/repos/acme/widgets/issues"
        return httpx.Response(status, json=payload, headers=headers)

    with pytest.raises(ValueError):
        connector(handler).discover_delta(BASE)


@pytest.mark.parametrize("marker", [None, "", "not-a-time", "2026-01-01", "nan", "inf"])
def test_missing_invalid_cursor_requires_visible_repair_before_http(marker):
    with pytest.raises(ValueError):
        connector(lambda _: pytest.fail("invalid cursor performed HTTP")).discover_delta(marker)


def test_polling_overlap_numeric_cursor_and_no_implicit_tombstones():
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        assert request.url.params["since"] == "2025-12-31T23:59:00Z"
        return httpx.Response(200, json=[])

    delta = connector(handler).discover_delta("1767225600")
    assert delta.commit == BASE and delta.upserts == delta.deletions == []
    delta.validate(SOURCE)


@pytest.mark.parametrize("change", [
    {"version": 2}, {"source": "other"}, {"account": "other"}, {"base": "other"},
    {"target": "2025-01-01T00:00:00Z"}, {"index": True}, {"index": 1},
    {"items": []}, {"items": [[1, STAMP], [1, STAMP]]},
    {"items": [[True, STAMP]]}, {"items": [[1, "bad"]]},
])
def test_invalid_saved_plan_refuses_before_item_acquisition(change):
    def handler(request):
        assert request.url.path == "/user"
        return httpx.Response(200, json={"id": 123})

    provider = connector(handler)
    plan = {
        "version": 1, "source": SOURCE, "account": "123", "base": BASE,
        "target": STAMP, "items": [[1, STAMP]], "index": 0, **change,
    }
    with pytest.raises(ValueError):
        provider.discover_delta_page(BASE, json.dumps(plan))


def test_explicit_reconciliation_has_a_start_timestamp():
    provider = connector(lambda _: pytest.fail("timestamp must precede discovery HTTP"))
    from datetime import UTC, datetime

    before = datetime.now(UTC)
    assert before <= datetime.fromisoformat(provider.current_commit()) <= datetime.now(UTC)


def test_shared_engine_defers_all_store_effects_until_acquisition_completes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent_index.indexing import engine

    failed = True
    listed = 0
    stored = []

    def handler(request):
        nonlocal listed
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path == "/repos/acme/widgets/issues":
            listed += 1
            return httpx.Response(200, json=[item(1), item(2)])
        if request.url.path.endswith("/comments"):
            if failed and "/2/" in request.url.path:
                raise RuntimeError("interrupted")
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=item(int(request.url.path.rsplit("/", 1)[-1])))

    initial = connector(handler)
    scope = recovery_scope("config", initial)
    state = IndexState(sources={"alias": SourceState(last_commit=BASE, acquisition_scope=scope)})
    state.stored_families["alias"] = SOURCE
    config = SimpleNamespace(
        state_file=tmp_path / "state.json", model_profiles={}, stream_batch_size=2,
    )
    paths = PathIndex(tmp_path / "paths.db")
    state.save(config.state_file)
    monkeypatch.setattr(engine, "selected_layout", lambda: object())

    def store(entries, **kwargs):
        stored.extend(entry.path for entry in entries)
        assert PendingCrawls(paths).load("alias", scope)[0].upserts == entries
        for entry in entries:
            paths.mark_indexed(entry.source, entry.path, chunk_count=1)
        return len(entries), {(entry.source, entry.path) for entry in entries}

    monkeypatch.setattr(engine, "_embed_and_store_files", store)

    def run():
        return engine._index_source(
            "alias", config=config, state=state, path_index=paths,
            multi_store=SimpleNamespace(), multi_clients={}, full=False,
            source_scope="config", connector=connector(handler),
        )

    with pytest.raises(RuntimeError, match="interrupted"):
        run()
    assert stored == [] and paths.get_paths_by_family(SOURCE) == {}
    assert IndexState.load(config.state_file).sources["alias"].last_commit == BASE
    state = IndexState.load(config.state_file)
    paths = PathIndex(tmp_path / "paths.db")
    failed = False
    assert run() == (2, 0, 2)
    assert stored == ["issues/1.md", "issues/2.md"] and listed == 1
    assert PendingCrawls(paths).sources() == []
    assert IndexState.load(config.state_file).sources["alias"].last_commit == (
        "2026-01-02T00:00:00+00:00"
    )
