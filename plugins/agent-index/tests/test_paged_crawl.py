"""Acquisition units share pending-crawl recovery, scope and retirement guards."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.paged_crawl import acquire_pages
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls, recover_source
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import AcquisitionPage, SourceDelta


def entry(number: int, source: str = "forge:repo:issues") -> FileEntry:
    return FileEntry(str(number), f"body {number}", "text", source)


def acquire(journal, discover, **kwargs):
    return acquire_pages(
        "alias", family="forge:repo", stored_family="forge:repo", scope="account-a",
        last_commit="base", journal=journal, discover=discover, **kwargs,
    )


def test_restart_resumes_last_durable_unit_before_any_indexing(tmp_path):
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    calls = []

    def discover(base, cursor, **kwargs):
        assert base == "base"
        calls.append(cursor)
        if cursor == "next":
            raise RuntimeError("interrupted remote page")
        return AcquisitionPage(SourceDelta(upserts=[entry(1)], commit="target"), "next")

    with pytest.raises(RuntimeError, match="interrupted"):
        acquire(journal, discover)
    assert calls == [None, "next"]
    assert journal.load("alias", "account-a") is None
    with pytest.raises(PendingCrawlConflict, match="garbage collection"):
        journal.assert_gc_available()
    with pytest.raises(PendingCrawlConflict, match="another alias"):
        journal.assert_family_available("other", "forge:repo:issues")
    journal.assert_family_available("independent", "forge:other")
    before = journal.read_payload("alias", "account-a")
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    state = IndexState(sources={"alias": SourceState(
        last_commit="base", acquisition_scope="account-a",
    )})
    state_file = tmp_path / "state.json"
    applied = []

    def resumed(base, cursor, **kwargs):
        assert cursor == "next"
        assert journal.read_payload("alias", "account-a") == before
        assert state.sources["alias"].last_commit == "base"
        return AcquisitionPage(SourceDelta(upserts=[entry(2)], commit="target"))

    def apply(*, manifest, full):
        applied.append(manifest)
        assert len(manifest.upserts) == 2 and not full
        assert journal.load("alias", "account-a") == (manifest, False)
        state.mark_source_indexed("alias", manifest.commit, 2)
        return 2, 0, 2

    assert recover_source(
        "alias", scope="account-a", state=state, state_file=state_file, paths=paths, full=False,
        acquire=lambda: acquire(journal, resumed), apply=apply, family="forge:repo",
    ) == (2, 0, 2)
    assert IndexState.load(state_file).sources["alias"].last_commit == "target"
    assert len(applied) == 1 and journal.sources() == []


def test_page_budget_is_visible_and_retry_does_not_repeat_saved_units(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    calls = []

    def discover(base, cursor, **kwargs):
        number = int(cursor or "0") + 1
        calls.append(number)
        return AcquisitionPage(
            SourceDelta(upserts=[entry(number)], commit="target"),
            str(number) if number < 3 else None,
        )

    with pytest.raises(RuntimeError, match="page budget"):
        acquire(journal, discover, page_budget=2)
    manifest = acquire(journal, discover, page_budget=2)
    assert calls == [1, 2, 3]
    assert [item.path for item in manifest.upserts] == ["1", "2", "3"]
    assert journal.load("alias", "account-a") == (manifest, False)


@pytest.mark.parametrize("bad", [
    AcquisitionPage(SourceDelta(commit="changed")),
    AcquisitionPage(SourceDelta(upserts=[entry(1)], commit="target")),
    AcquisitionPage(SourceDelta(upserts=[entry(2, "foreign")], commit="target")),
    AcquisitionPage(SourceDelta(commit="target", complete=False)),
    AcquisitionPage(SourceDelta(commit="target"), "next"),
    AcquisitionPage(SourceDelta(commit="target"), version=9),
    AcquisitionPage(SourceDelta(commit="target"), cursor=""),
    SourceDelta(commit="target"),
])
def test_invalid_page_never_replaces_valid_partial_progress(tmp_path, bad):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: AcquisitionPage(
            SourceDelta(upserts=[entry(1)], commit="target"), "next",
        ), page_budget=1)
    before = journal.read_payload("alias", "account-a")
    with pytest.raises(ValueError):
        acquire(journal, lambda *a, **k: bad)
    assert journal.read_payload("alias", "account-a") == before


def test_progress_publication_compare_and_swap_preserves_newer_owner(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))

    def discover(*args, **kwargs):
        journal.save(CrawlManifest(source="alias"), scope="account-a", full=False)
        return AcquisitionPage(SourceDelta(commit="target"), "next")

    with pytest.raises(PendingCrawlConflict, match="changed"):
        acquire(journal, discover)
    assert journal.load("alias", "account-a") == (CrawlManifest(source="alias"), False)


@pytest.mark.parametrize("mutation", ["scope", "base", "family", "full", "hints", "legacy", "bad"])
def test_pending_page_scope_mode_and_corruption_refuse_before_provider(tmp_path, mutation):
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    connector = SimpleNamespace(
        source_name="forge:repo", recovery_identity=("target", "account-a"),
    )
    from agent_index.indexing.pending_crawl import recovery_scope

    scope = recovery_scope("config", connector)
    manifest = CrawlManifest(
        source="alias", accounting_family="forge:repo", stored_family="forge:repo", commit="target",
    )
    payload = json.dumps({
        "version": 2, "full": False, "manifest": asdict(manifest),
        "base_commit": "base", "cursor": "next", "generation": "1" * 32,
    })
    journal.compare_and_save(
        "alias", scope=scope, payload=payload, expected=None, family="forge:repo",
    )
    state = IndexState(sources={"alias": SourceState(last_commit="base", acquisition_scope=scope)})
    state.stored_families["alias"] = "forge:repo"

    def discover(*args, **kwargs):
        pytest.fail("incompatible pending acquisition reached provider")

    setattr(discover, "acquisition_page_versions", (1,))
    connector.discover_delta_page = discover
    kwargs = {"full": False}
    if mutation == "scope":
        connector.recovery_identity = ("target", "account-b")
    elif mutation == "base":
        state.sources["alias"].last_commit = "other"
    elif mutation == "family":
        state.stored_families["alias"] = "forge:changed"
    elif mutation == "full":
        kwargs["full"] = True
    elif mutation == "hints":
        kwargs["change_hints"] = object()
    elif mutation == "legacy":
        connector.discover_delta_page = None
    else:
        with sqlite3.connect(tmp_path / "paths.db") as conn:
            conn.execute("UPDATE pending_crawls SET payload = '{}'")
    with pytest.raises(ValueError):
        collect_source_changes(
            "alias", state=state, path_index=paths, source_aware=True, source_scope="config",
            connector=connector, **kwargs,
        )
    assert journal.sources() == ["alias"]


def test_in_progress_phase_is_structurally_distinct_from_complete_v1(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: AcquisitionPage(
            SourceDelta(commit="target"), "next",
        ), page_budget=1)
    payload = json.loads(journal.read_payload("alias", "account-a"))
    # The retained v1 envelope schema rejects the new phase before publication.
    assert set(payload) != {"version", "full", "manifest"} and payload["version"] != 1


def test_cancellation_after_acquisition_does_not_publish_unacknowledged_unit(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    requested = False

    def discover(*args, **kwargs):
        nonlocal requested
        requested = True
        return AcquisitionPage(SourceDelta(upserts=[entry(1)], commit="target"))

    def cancel():
        if requested:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        acquire(journal, discover, cancel_check=cancel)
    assert journal.sources() == []


def test_empty_paged_acquisition_survives_failed_state_publication(tmp_path, monkeypatch):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    manifest = acquire(journal, lambda *a, **k: AcquisitionPage(SourceDelta(commit="target")))
    state = IndexState()

    def apply(*, manifest, full):
        state.mark_source_indexed("alias", manifest.commit, 0)
        return 0, 0, 0

    monkeypatch.setattr(state, "save", lambda _: (_ for _ in ()).throw(OSError("state refused")))
    with pytest.raises(OSError, match="state refused"):
        recover_source(
            "alias", scope="account-a", state=state, state_file=tmp_path / "state.json",
            paths=journal._paths, full=False, acquire=lambda: pytest.fail("reacquisition"),
            apply=apply,
        )
    assert journal.load("alias", "account-a") == (manifest, False)
    assert state.sources == {}
