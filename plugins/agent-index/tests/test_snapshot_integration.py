"""Pinned full acquisition reaches the real shared engine in both store layouts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_consumer_runtime import Client
from test_git_delta import commit, connector as git_connector, repo as _repo

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.indexing.state import IndexState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import SnapshotPage, SourceDelta
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store

repo = _repo


@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
@pytest.mark.parametrize("models", [1, 2])
def test_native_full_retry_preserves_data_until_complete_and_then_reconciles(
    tmp_path, monkeypatch, selected, models,
):
    if selected:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    family = "sample:repo"
    profiles = {
        name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
        for name in ("a", "b")[:models]
    }
    config = IndexConfig(data_dir=tmp_path / "data", model_profiles=profiles, cluster_enabled=False)
    config.ensure_dirs()
    store = initialize_source_store(config) if selected else MultiModelStore(config.lance_dir)
    for profile in profiles.values():
        store.register_model(profile)
    clients = {name: Client(name, []) for name in profiles}
    old = [
        FileEntry("keep.md", "# Kept\n\nOriginal.", "markdown", family),
        FileEntry("gone.md", "# Removed\n\nOld content.", "markdown", family),
        FileEntry("history", "Old history.", "text", family + ":commits"),
    ]
    connector = SimpleNamespace(
        source_name=family, recovery_identity=("fixture-target", "account-a"),
        discover_delta=lambda *a, **k: SourceDelta(upserts=old, commit="base"),
    )
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState()

    def run(full):
        return engine._index_source(
            "alias", config=config, state=state, path_index=paths, multi_store=store,
            multi_clients=clients, full=full, connector=connector,
        )

    run(False)
    scope = recovery_scope("alias", connector)
    before = store.projection_snapshot()
    previous_requests = [list(client.texts) for client in clients.values()]
    calls = []
    failed = True
    kept = FileEntry("keep.md", "# Kept\n\nUpdated.", "markdown", family)
    added = FileEntry("new.md", "# New\n\nNew content.", "markdown", family)

    def snapshot(token, cursor, **kwargs):
        calls.append((token, cursor))
        if token is None:
            return SnapshotPage("pinned", [kept], "target", "next")
        assert (token, cursor) == ("pinned", "next")
        if failed:
            raise RuntimeError("interrupted snapshot")
        return SnapshotPage(token, [added], "target")

    snapshot.snapshot_page_versions = (1,)
    connector.discover_snapshot_page = snapshot
    with pytest.raises(RuntimeError, match="interrupted snapshot"):
        run(True)
    assert store.projection_snapshot() == before
    assert [client.texts for client in clients.values()] == previous_requests
    assert paths.count() == 3
    assert state.sources["alias"].last_commit == "base"
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "base"
    assert PendingCrawls(paths).load("alias", scope) is None
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState.load(config.state_file)
    failed = False
    run(True)
    assert calls == [(None, None), ("pinned", "next"), ("pinned", "next")]
    assert store.get_document(family, "keep.md").content == kept.content
    assert store.get_document(family, "new.md").content == added.content
    assert store.get_document(family, "gone.md") is None
    assert store.get_document(family + ":commits", "history") is None
    assert paths.count() == 2
    assert state.sources["alias"].last_commit == "target"
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "target"
    assert PendingCrawls(paths).sources() == []


@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
def test_external_snapshot_resumes_through_native_two_model_engine(
    tmp_path, monkeypatch, selected,
):
    from test_provider_snapshot import snapshot_receiver
    from test_provider_delta import ENTRY, FAMILY
    from agent_index.sources.providers import ProviderError

    if selected:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    receiver = snapshot_receiver(tmp_path)
    receiver.config["delta"] = {
        "version": 1, "commit": "base", "complete": True, "deletions": [],
        "upserts": [
            {**ENTRY, "path": "gone.md"},
            {**ENTRY, "source": FAMILY + ":commits", "path": "old-history"},
        ],
    }
    profiles = {
        name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
        for name in ("a", "b")
    }
    config = IndexConfig(data_dir=tmp_path / "data", model_profiles=profiles, cluster_enabled=False)
    config.ensure_dirs()
    store = initialize_source_store(config) if selected else MultiModelStore(config.lance_dir)
    for profile in profiles.values():
        store.register_model(profile)
    clients = {name: Client(name, []) for name in profiles}
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState()

    def run(full):
        return engine._index_source(
            "alias", config=config, state=state, path_index=paths, multi_store=store,
            multi_clients=clients, full=full, connector=receiver.connector(),
        )

    run(False)
    before = store.projection_snapshot()
    requests = [list(client.texts) for client in clients.values()]
    receiver.config["fail_snapshot"] = True
    with pytest.raises(ProviderError, match="temporarily unavailable"):
        run(True)
    assert store.projection_snapshot() == before
    assert [client.texts for client in clients.values()] == requests
    assert paths.count() == 2
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "base"
    receiver.config["fail_snapshot"] = False
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState.load(config.state_file)
    run(True)
    assert store.get_document(FAMILY, "new.md").content == ENTRY["content"]
    assert store.get_document(FAMILY + ":commits", "history") is not None
    assert store.get_document(FAMILY, "gone.md") is None
    assert store.get_document(FAMILY + ":commits", "old-history") is None
    assert paths.count() == 2
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "snapshot-tip"
    assert PendingCrawls(paths).sources() == []
    assert sum(not any(arg.startswith("--snapshot=") for arg in call)
               and "--snapshot-version" in call
               for call in receiver.calls()) == 1


@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
def test_git_full_snapshot_restart_pins_both_streams_through_native_engine(
    repo, tmp_path, monkeypatch, selected,
):
    from agent_index.sources import git_snapshot

    if selected:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    source = git_connector(repo)
    family = source.source_name
    target = source.current_commit()
    profiles = {
        name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
        for name in ("a", "b")
    }
    config = IndexConfig(data_dir=tmp_path / "data", model_profiles=profiles, cluster_enabled=False)
    config.ensure_dirs()
    store = initialize_source_store(config) if selected else MultiModelStore(config.lance_dir)
    for profile in profiles.values():
        store.register_model(profile)
    clients = {name: Client(name, []) for name in profiles}
    old = [
        FileEntry("gone.md", "Old file.", "markdown", family),
        FileEntry("old-history", "Old history.", "text", family + ":commits"),
    ]
    seed = SimpleNamespace(
        source_name=family, recovery_identity=source.recovery_identity,
        discover_delta=lambda *a, **k: SourceDelta(upserts=old, commit="base"),
    )
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState()

    def run(connector, full):
        return engine._index_source(
            "alias", config=config, state=state, path_index=paths, multi_store=store,
            multi_clients=clients, full=full, connector=connector,
        )

    run(seed, False)
    before = store.projection_snapshot()
    requests = [list(client.texts) for client in clients.values()]
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 2)
    read = git_snapshot._tree
    calls = []

    def interrupted(connector, pinned, directory):
        calls.append((pinned, directory))
        if len(calls) == 2:
            raise RuntimeError("interrupted Git tree acquisition")
        return read(connector, pinned, directory)

    monkeypatch.setattr(git_snapshot, "_tree", interrupted)
    with pytest.raises(RuntimeError, match="interrupted Git tree"):
        run(source, True)
    assert store.projection_snapshot() == before
    assert [client.texts for client in clients.values()] == requests
    assert paths.count() == 2
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "base"
    (repo / "keep.md").write_text("Later revision.\n", encoding="utf-8")
    later = commit(repo)
    assert later != target
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState.load(config.state_file)
    run(git_connector(repo), True)
    assert store.get_document(family, "keep.md").content == "keep.md\n"
    assert store.get_document(family + ":commits", f"commits/{target}.txt") is not None
    assert store.get_document(family + ":commits", f"commits/{later}.txt") is None
    assert store.get_document(family, "gone.md") is None
    assert store.get_document(family + ":commits", "old-history") is None
    assert all(pinned == target for pinned, _ in calls)
    assert IndexState.load(config.state_file).sources["alias"].last_commit == target
    assert PendingCrawls(paths).sources() == []
