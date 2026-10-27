"""Explicit repair releases only unapplied acquisition, never mandatory replay."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.paged_crawl import acquire_pages, discard_acquisition
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls, recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.delta import AcquisitionPage, SourceDelta
from test_github_poll import BASE, SOURCE, connector, item


def test_deleted_planned_item_can_be_explicitly_repaired_without_tombstone_or_cursor_loss(tmp_path):
    listings = 0
    reconciling = False

    def handler(request):
        nonlocal listings
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path == "/repos/acme/widgets/issues":
            listings += 1
            return httpx.Response(200, json=[item(1)] if reconciling else [item(1), item(2)])
        if request.url.path == "/repos/acme/widgets/issues/2":
            return httpx.Response(404)
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=item(1))

    provider = connector(handler)
    scope = recovery_scope("config", provider)
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    state = IndexState(sources={"alias": SourceState(last_commit=BASE, acquisition_scope=scope)})
    state.stored_families["alias"] = SOURCE
    state_file = tmp_path / "state.json"
    state.save(state_file)
    before = state_file.read_bytes()

    def collect(*, full=False):
        return collect_source_changes(
            "alias", state=state, path_index=paths, full=full, source_aware=True,
            connector=provider, source_scope="config",
        )

    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            collect()
    assert listings == 1
    raw = journal.read_payload("alias", scope)
    with pytest.raises(ValueError, match="pending"):
        collect(full=True)
    with pytest.raises(PendingCrawlConflict):
        journal.assert_gc_available()
    discard_acquisition(
        "alias", paths=paths, connector=provider, source_scope="config", expected_payload=raw,
    )
    assert journal.sources() == [] and paths.get_paths_by_family(SOURCE) == {}
    assert state_file.read_bytes() == before and state.sources["alias"].last_commit == BASE
    journal.assert_gc_available()
    reconciling = True
    manifest = collect(full=True)
    assert listings == 2 and [entry.path for entry in manifest.upserts] == ["issues/1.md"]
    assert manifest.deletions == []  # The earlier 404 granted no tombstone.
    assert state_file.read_bytes() == before  # Acquiring repair does not apply or commit it.


def fixture(tmp_path):
    paths = PathIndex(tmp_path / "paths.db")
    provider = SimpleNamespace(source_name="forge:repo", recovery_identity=("target", "account"))
    scope = recovery_scope("config", provider)
    journal = PendingCrawls(paths)

    def acquire(discover):
        return acquire_pages(
            "alias", family=provider.source_name, stored_family=provider.source_name,
            scope=scope, last_commit="base", journal=journal, discover=discover, page_budget=1,
        )

    with pytest.raises(RuntimeError, match="budget"):
        acquire(lambda *a, **k: AcquisitionPage(SourceDelta(commit="target"), "next"))
    return paths, provider, scope, journal, acquire


@pytest.mark.parametrize("change", ["account", "target", "family", "config", "missing-identity"])
def test_discard_requires_original_actual_source_account_and_configuration(tmp_path, change):
    paths, provider, scope, journal, _ = fixture(tmp_path)
    raw = journal.read_payload("alias", scope)
    config = "config"
    if change == "config":
        config = "other"
    elif change == "family":
        provider.source_name = "forge:other"
    elif change == "missing-identity":
        del provider.recovery_identity
    else:
        provider.recovery_identity = (
            ("other-target", "account") if change == "target" else ("target", "other-account")
        )
    with pytest.raises((ValueError, PendingCrawlConflict)):
        discard_acquisition(
            "alias", paths=paths, connector=provider, source_scope=config, expected_payload=raw,
        )
    assert journal.read_payload("alias", scope) == raw


def test_discard_cannot_remove_newer_progress_or_completed_manifest(tmp_path):
    paths, provider, scope, journal, acquire = fixture(tmp_path)
    raw = journal.read_payload("alias", scope)
    with pytest.raises(RuntimeError, match="budget"):
        acquire(lambda *a, **k: AcquisitionPage(SourceDelta(commit="target"), "later"))
    with pytest.raises(PendingCrawlConflict, match="changed"):
        discard_acquisition(
            "alias", paths=paths, connector=provider, source_scope="config", expected_payload=raw,
        )
    manifest = acquire(lambda *a, **k: AcquisitionPage(SourceDelta(commit="target")))
    complete = journal.read_payload("alias", scope)
    with pytest.raises(ValueError, match="envelope"):
        discard_acquisition(
            "alias", paths=paths, connector=provider, source_scope="config", expected_payload=complete,
        )
    assert journal.load("alias", scope) == (manifest, False)


def test_explicit_discard_fences_inflight_unit_before_complete_manifest_promotion(tmp_path):
    paths, provider, scope, journal, acquire = fixture(tmp_path)
    raw = journal.read_payload("alias", scope)

    def discover(*args, **kwargs):
        discard_acquisition(
            "alias", paths=paths, connector=provider, source_scope="config", expected_payload=raw,
        )
        return AcquisitionPage(SourceDelta(commit="target"))

    with pytest.raises(PendingCrawlConflict, match="changed"):
        acquire(discover)
    assert journal.sources() == []
    assert journal.load("alias", scope) is None


@pytest.mark.parametrize("stale_completes", [False, True])
def test_identical_recreated_plan_rejects_stale_publication_and_discard(tmp_path, stale_completes):
    paths, provider, scope, journal, acquire = fixture(tmp_path)
    original = journal.read_payload("alias", scope)
    recreated = None

    def stale_unit(*args, **kwargs):
        nonlocal recreated
        discard_acquisition(
            "alias", paths=paths, connector=provider, source_scope="config",
            expected_payload=original,
        )
        with pytest.raises(RuntimeError, match="budget"):
            acquire(lambda *a, **k: AcquisitionPage(SourceDelta(commit="target"), "next"))
        recreated = journal.read_payload("alias", scope)
        old, new = json.loads(original), json.loads(recreated)
        assert old.pop("generation") != new.pop("generation")
        assert old == new  # Provider plan, content, cursor and scope are identical.
        return AcquisitionPage(
            SourceDelta(commit="target"), None if stale_completes else "later",
        )

    with pytest.raises(PendingCrawlConflict, match="changed"):
        acquire(stale_unit)
    assert journal.read_payload("alias", scope) == recreated
    with pytest.raises(PendingCrawlConflict, match="changed"):
        discard_acquisition(
            "alias", paths=paths, connector=provider, source_scope="config",
            expected_payload=original,
        )
    with pytest.raises(RuntimeError, match="budget"):
        acquire(lambda *a, **k: AcquisitionPage(SourceDelta(commit="target"), "later"))
    assert json.loads(journal.read_payload("alias", scope))["generation"] == (
        json.loads(recreated)["generation"]
    )
    completed = acquire(lambda *a, **k: AcquisitionPage(SourceDelta(commit="target")))
    assert journal.load("alias", scope) == (completed, False)
