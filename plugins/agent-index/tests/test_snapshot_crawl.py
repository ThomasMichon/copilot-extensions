"""Full snapshots cannot authorize deletion before complete, pinned acquisition."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.manifest import CrawlManifest, PollRefresh
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import (
    PendingCrawlConflict, PendingCrawls, recover_source, recovery_scope,
)
from agent_index.indexing.snapshot_crawl import acquire_snapshot, decode_snapshot
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SnapshotPage, SourceDelta

FAMILY = "forge:repo"


def entry(path="keep", source=FAMILY):
    return FileEntry(path, "body", "text", source)


def acquire(journal, discover, **kwargs):
    return acquire_snapshot(
        "alias", family=FAMILY, stored_family=FAMILY, scope="account-a",
        last_commit="base", journal=journal, discover=discover, **kwargs,
    )


def test_full_budget_survives_restart_and_comparison_never_reacquires(tmp_path, monkeypatch):
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch([(FAMILY, f"{i:04}", None, 1) for i in range(513)])
    journal = PendingCrawls(paths)
    calls = []

    def discover(snapshot, cursor, **kwargs):
        calls.append((snapshot, cursor))
        if snapshot is None:
            return SnapshotPage("immutable", [entry("0000")], "target", "next")
        assert (snapshot, cursor) == ("immutable", "next")
        return SnapshotPage(snapshot, [entry("0001")], "target")

    monkeypatch.setattr(paths, "get_paths_by_family", lambda *a: pytest.fail("unbounded listing"))
    with pytest.raises(RuntimeError, match="unit budget"):
        acquire(journal, discover, unit_budget=1)
    partial = decode_snapshot(journal.read_payload("alias", "account-a"), "alias")
    assert partial.manifest.deletions == [] and partial.cursor == "next"
    assert journal.load("alias", "account-a") is None
    with pytest.raises(PendingCrawlConflict, match="garbage collection"):
        journal.assert_gc_available()
    with pytest.raises(PendingCrawlConflict, match="another alias"):
        journal.assert_family_available("other", FAMILY + ":issues")
    journal.assert_family_available("other", "forge:independent")
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    for expected_deleted in (0, 254, 510):
        with pytest.raises(RuntimeError, match="unit budget"):
            acquire(journal, discover, unit_budget=1)
        current = decode_snapshot(journal.read_payload("alias", "account-a"), "alias")
        assert current.generation == partial.generation and current.cursor is None
        assert len(current.manifest.deletions) == expected_deleted
        assert paths.count(FAMILY) == 513
    manifest = acquire(journal, discover, unit_budget=1)
    assert calls == [(None, None), ("immutable", "next")]
    assert [item.path for item in manifest.upserts] == ["0000", "0001"]
    assert len(manifest.deletions) == 511 and manifest.commit == "target"
    assert manifest.stats.files_scanned == 2 and manifest.stats.files_deleted == 511
    assert journal.load("alias", "account-a") == (manifest, True)


@pytest.mark.parametrize("bad", [
    SnapshotPage("other", commit="target"),
    SnapshotPage("immutable", commit="changed"),
    SnapshotPage("immutable", [entry("keep")], "target"),
    SnapshotPage("immutable", [entry(source="foreign")], "target"),
    SnapshotPage("immutable", commit="target", cursor="next"),
    SnapshotPage("", commit="target"),
    SnapshotPage("immutable", commit="target", cursor=""),
    SnapshotPage("immutable", commit="target", version=True),
    SnapshotPage("immutable", commit="target", version=9),
    SnapshotPage("immutable", [entry(str(i)) for i in range(257)], "target"),
    SourceDelta(commit="target"),
])
def test_bad_provider_units_preserve_the_last_acknowledged_snapshot(tmp_path, bad):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: SnapshotPage(
            "immutable", [entry()], "target", "next",
        ), unit_budget=1)
    before = journal.read_payload("alias", "account-a")
    with pytest.raises(ValueError):
        acquire(journal, lambda *a, **k: bad)
    assert journal.read_payload("alias", "account-a") == before


@pytest.mark.parametrize("phase", ["acquiring", "comparing"])
def test_failed_unit_and_cancellation_preserve_progress_without_effects(tmp_path, phase):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: SnapshotPage(
            "immutable", commit="target", cursor="next" if phase == "acquiring" else None,
        ), unit_budget=1)
    before = journal.read_payload("alias", "account-a")

    def failure(*args, **kwargs):
        raise OSError("provider unavailable")

    if phase == "acquiring":
        with pytest.raises(OSError, match="provider unavailable"):
            acquire(journal, failure)
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        acquire(
            journal, lambda *a, **k: SnapshotPage("immutable", commit="target"),
            cancel_check=cancel,
        )
    assert journal.read_payload("alias", "account-a") == before
    assert journal._paths.count() == 0


@pytest.mark.parametrize("binding", ["scope", "base", "family", "full", "capability"])
def test_recovery_scope_mode_and_capability_refuse_before_provider(tmp_path, binding):
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    connector = SimpleNamespace(source_name=FAMILY, recovery_identity=("target", "account-a"))
    scope = recovery_scope("config", connector)
    with pytest.raises(RuntimeError, match="budget"):
        acquire_snapshot(
            "alias", family=FAMILY, stored_family=FAMILY, scope=scope, last_commit="base",
            journal=journal, discover=lambda *a, **k: SnapshotPage("immutable", cursor="next"),
            unit_budget=1,
        )

    def discover(*args, **kwargs):
        pytest.fail("incompatible full acquisition reached provider")

    discover.snapshot_page_versions = (1,)
    connector.discover_snapshot_page = discover
    state = IndexState(sources={"alias": SourceState(last_commit="base", acquisition_scope=scope)})
    state.stored_families["alias"] = FAMILY
    full = True
    if binding == "scope":
        connector.recovery_identity = ("target", "account-b")
    elif binding == "base":
        state.sources["alias"].last_commit = "changed"
    elif binding == "family":
        state.stored_families["alias"] = "forge:previous"
    elif binding == "full":
        full = False
    else:
        discover.snapshot_page_versions = (2,)
    with pytest.raises(ValueError):
        collect_source_changes(
            "alias", state=state, path_index=paths, full=full, source_aware=True,
            source_scope="config", connector=connector,
        )


def test_snapshot_connector_requires_effective_account_identity(tmp_path):
    connector = SimpleNamespace(source_name=FAMILY, discover_snapshot_page=lambda *a, **k: None)
    with pytest.raises(ValueError, match="recovery_identity"):
        recovery_scope("config", connector)


def test_stale_snapshot_publication_cannot_replace_newer_journal(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))

    def discover(*args, **kwargs):
        journal.save(CrawlManifest(source="alias"), scope="account-a", full=False)
        return SnapshotPage("immutable")

    with pytest.raises(PendingCrawlConflict, match="changed"):
        acquire(journal, discover)
    assert journal.load("alias", "account-a") == (CrawlManifest(source="alias"), False)


@pytest.mark.parametrize("field,value", [
    ("full", False), ("version", 1), ("generation", "bad"), ("cursor", ""),
    ("snapshot", ""), ("after", ["foreign", "path"]), ("after", [FAMILY]),
    ("after", [FAMILY, "path"]),
])
def test_corrupt_envelope_cannot_be_resumed_or_discarded(tmp_path, field, value):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: SnapshotPage("immutable", cursor="next"), unit_budget=1)
    data = json.loads(journal.read_payload("alias", "account-a"))
    data[field] = value
    payload = json.dumps(data)
    with journal._paths._connect() as conn:
        conn.execute("UPDATE pending_crawls SET payload = ?", (payload,))
    with pytest.raises(ValueError):
        journal.load("alias", "account-a")
    with pytest.raises(ValueError):
        journal.discard_acquisition("alias", scope="account-a", family=FAMILY, expected_payload=payload)
    assert journal.read_payload("alias", "account-a") == payload


def test_explicit_discard_only_retires_the_exact_unapplied_full_snapshot(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: SnapshotPage("immutable"), unit_budget=1)
    before = journal.read_payload("alias", "account-a")
    with pytest.raises(PendingCrawlConflict, match="changed"):
        journal.discard_acquisition("alias", scope="account-a", family=FAMILY, expected_payload="stale")
    journal.discard_acquisition("alias", scope="account-a", family=FAMILY, expected_payload=before)
    assert journal.sources() == []
    manifest = acquire(journal, lambda *a, **k: SnapshotPage("immutable"))
    complete = journal.read_payload("alias", "account-a")
    with pytest.raises(ValueError):
        journal.discard_acquisition("alias", scope="account-a", family=FAMILY, expected_payload=complete)
    assert journal.load("alias", "account-a") == (manifest, True)


def test_full_snapshot_covers_retained_empty_history_only_after_state_publication(tmp_path, monkeypatch):
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    connector = SimpleNamespace(source_name=FAMILY, recovery_identity=("target", "account-a"))
    scope = recovery_scope("config", connector)
    token = uuid.uuid4().hex
    hint = CrawlManifest(
        source="alias", accounting_family=FAMILY, deletions=[DeletedFile(FAMILY + ":commits", "empty")],
        poll_refresh=PollRefresh(True, {}, {"empty": token}),
    )
    journal.save(hint, scope=scope, full=False)
    journal.clear("alias", manifest=hint, scope=scope)
    refresh = journal.refresh_candidates("alias", scope, FAMILY)
    calls = []

    def discover(snapshot, cursor, **kwargs):
        calls.append((snapshot, cursor))
        return SnapshotPage("immutable", [entry()], "target")

    discover.snapshot_page_versions = (1,)
    connector.discover_snapshot_page = discover
    state = IndexState(sources={"alias": SourceState(last_commit="base", acquisition_scope=scope)})
    state.stored_families["alias"] = FAMILY
    original_save = state.save
    monkeypatch.setattr(state, "save", lambda _: (_ for _ in ()).throw(OSError("state refused")))

    def apply(*, manifest, full):
        assert full and manifest.poll_refresh == refresh
        assert manifest.deletions == [DeletedFile(FAMILY + ":commits", "empty")]
        state.mark_source_indexed("alias", manifest.commit, 1)
        return 1, 0, 1

    with pytest.raises(OSError, match="state refused"):
        recover_source(
            "alias", scope=scope, state=state, state_file=tmp_path / "state.json",
            paths=paths, full=True, family=FAMILY,
            acquire=lambda: collect_source_changes(
                "alias", state=state, path_index=paths, full=True, source_aware=True,
                source_scope="config", connector=connector,
            ), apply=apply,
        )
    manifest, full = journal.load("alias", scope)
    assert full and manifest.commit == "target"
    assert state.sources["alias"].last_commit == "base"
    assert journal.refresh_candidates("alias", scope, FAMILY) == refresh
    assert json.loads(journal.read_payload("alias", scope))["version"] == 3
    monkeypatch.setattr(state, "save", original_save)
    recover_source(
        "alias", scope=scope, state=state, state_file=tmp_path / "state.json", paths=paths,
        full=True, family=FAMILY, apply=apply,
        acquire=lambda: acquire_snapshot(
            "alias", family=FAMILY, stored_family=FAMILY, scope=scope, last_commit="target",
            journal=journal, discover=discover, refresh=refresh,
        ),
    )
    assert len(calls) == 2
    assert IndexState.load(tmp_path / "state.json").sources["alias"].last_commit == "target"
    assert journal.sources() == []


def test_full_capability_never_changes_ordinary_delta_acquisition(tmp_path):
    def snapshot(*args, **kwargs):
        pytest.fail("ordinary polling selected a full snapshot")

    snapshot.snapshot_page_versions = (1,)
    connector = SimpleNamespace(
        source_name=FAMILY, recovery_identity=("target", "account-a"),
        discover_snapshot_page=snapshot,
        discover_delta=lambda *a, **k: SourceDelta(commit="target"),
    )
    manifest = collect_source_changes(
        "alias", state=IndexState(), path_index=PathIndex(tmp_path / "paths.db"),
        connector=connector, source_aware=True, full=False,
    )
    assert manifest.is_empty and manifest.commit == "target"


@pytest.mark.parametrize("versions", [(), (2,), (True,)])
def test_unadvertised_full_contract_preserves_legacy_reconciliation(tmp_path, versions):
    def snapshot(*args, **kwargs):
        pytest.fail("unadvertised full contract was selected")

    snapshot.snapshot_page_versions = versions
    connector = SimpleNamespace(
        source_name=FAMILY, recovery_identity=("target", "account-a"),
        discover_snapshot_page=snapshot, current_commit=lambda: "target",
        discover=lambda **kwargs: [entry()],
    )
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed(FAMILY, "gone")
    manifest = collect_source_changes(
        "alias", state=IndexState(), path_index=paths, connector=connector,
        source_aware=True, full=True,
    )
    assert manifest.upserts == [entry()]
    assert manifest.deletions == [DeletedFile(FAMILY, "gone")]
    assert manifest.commit == "target"


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_invalid_full_budget_refuses_before_provider_or_intent(tmp_path, budget):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(ValueError, match="budget"):
        acquire(journal, lambda *a, **k: pytest.fail("invalid budget reached provider"), unit_budget=budget)
    assert journal.sources() == []


def test_full_snapshot_phase_refuses_delta_only_reader(tmp_path):
    from agent_index.indexing.paged_crawl import decode_acquisition

    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: SnapshotPage("immutable"), unit_budget=1)
    with pytest.raises(ValueError, match="envelope"):
        decode_acquisition(journal.read_payload("alias", "account-a"), "alias")


def test_identical_replacement_snapshot_has_a_new_generation(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    page = SnapshotPage("immutable", cursor="next")
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: page, unit_budget=1)
    old = journal.read_payload("alias", "account-a")
    journal.discard_acquisition("alias", scope="account-a", family=FAMILY, expected_payload=old)
    with pytest.raises(RuntimeError, match="budget"):
        acquire(journal, lambda *a, **k: page, unit_budget=1)
    replacement = journal.read_payload("alias", "account-a")
    assert decode_snapshot(old, "alias").generation != decode_snapshot(replacement, "alias").generation
    with pytest.raises(PendingCrawlConflict, match="changed"):
        journal.compare_and_save(
            "alias", scope="account-a", family=FAMILY, expected=old, payload=old,
        )
    with pytest.raises(PendingCrawlConflict, match="changed"):
        journal.discard_acquisition("alias", scope="account-a", family=FAMILY, expected_payload=old)
    assert journal.read_payload("alias", "account-a") == replacement
