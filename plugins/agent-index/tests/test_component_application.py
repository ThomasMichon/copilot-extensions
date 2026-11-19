"""Native point application remains separate from polling and claim completion."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, cast
from unittest.mock import Mock

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.engine.client import EngineClient
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.component_application import PreparedComponentApplication
from agent_index.indexing.component_manifest import ComponentVersion
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.runner import IndexingCancelled, ProgressCallback
from agent_index.store.component_ledger import ComponentLedger, initialize_component_ledger
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired
from agent_index.store.source_layout import (
    LAYOUT, SourceDomain, SourceLayoutError, initialize_source_store,
)
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile

FAMILY, PATH, SCOPE = "sample:components", "items/one.txt", "account/endpoint/query"


@dataclass
class Fixture:
    config: IndexConfig
    store: MultiModelStore
    paths: PathIndex
    calls: list[tuple[str, list[str]]] = field(default_factory=list)
    hook: Callable[[], None] | None = None

    @property
    def domain(self) -> SourceDomain:
        value = self.store._source_domain
        assert value is not None
        return value

    def retain(
        self, version: int, content: str = "Selected fields.", *, deleted: bool = False,
    ) -> CrawlManifest:
        manifest = CrawlManifest(
            "selected", accounting_family=FAMILY, commit="unchanged-poll",
            upserts=[] if deleted else [FileEntry(PATH, content, "text", FAMILY)],
            deletions=[DeletedFile(FAMILY, PATH)] if deleted else [],
            component_versions=[ComponentVersion(FAMILY, PATH, version, "fields-v1")],
        )
        PendingCrawls(self.paths).save(manifest, scope=SCOPE, full=False)
        return manifest

    def retire_fixture_parent(self) -> None:
        # A synthetic stand-in for the still-disabled whole-claim completion owner.
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM pending_crawls WHERE source = 'selected'")

    def prepare(
        self, manifest: CrawlManifest, *, max_rows: int = 8, **kwargs: Any,
    ) -> PreparedComponentApplication:
        return PreparedComponentApplication(
            manifest, scope=SCOPE, family=FAMILY, max_rows=max_rows, config=self.config,
            paths=self.paths, store=self.store,
            clients={name: cast(EngineClient, Client(self, name)) for name in self.config.model_profiles},
            **kwargs,
        )

    def reopen(self) -> None:
        self.paths = PathIndex(self.paths._db_path)
        self.store = MultiModelStore(self.config.lance_dir, content_table=self.config.content_table)
        for profile in self.config.model_profiles.values():
            self.store.register_model(profile)


class Client:
    def __init__(self, fixture: Fixture, model: str) -> None:
        self.fixture, self.model = fixture, model

    def embed_texts(self, texts: list[str]) -> np.ndarray[Any, Any]:
        f = self.fixture
        assert not f.domain.writing
        # An embedding call must not retain the publication operation's exclusion.
        assert not getattr(f.domain._local, "operation", False)
        f.calls.append((self.model, texts))
        if f.hook:
            f.hook()
        return np.asarray([[len(text), 1] for text in texts], dtype=np.float32)


@pytest.fixture
def application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "owned", content_table="canonical",
        model_profiles={
            name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
            for name in ("first", "second")
        },
        stream_batch_size=1, cluster_enabled=False,
    )
    store = cast("Callable[[IndexConfig], MultiModelStore]", initialize_source_store)(config)
    initialize_component_ledger(config, store)
    prepare_ingestion_indices(store, max_rows=0)
    config.state_file.write_text('{"poll":"unchanged"}')
    return Fixture(config, store, PathIndex(config.data_dir / "path_index.db"))


def test_native_apply_reopen_duplicate_and_older_retain_parent_and_poll(
    application: Fixture,
) -> None:
    f = application
    manifest = f.retain(3)
    old_state = f.config.state_file.read_bytes()
    prepared = f.prepare(manifest)
    assert len(f.calls) == 2
    assert not f.paths.get_entries([(FAMILY, PATH)])
    assert prepared.publish() == (1, 0)
    expected = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert expected is not None
    checkpoint = f.paths.get_entry(FAMILY, PATH)
    assert checkpoint is not None and checkpoint[0] == expected.content.fingerprint
    parent = PendingCrawls(f.paths).read_payload("selected", SCOPE)
    assert parent is not None
    f.reopen()
    f.calls.clear()
    before = f.domain.receipt()
    assert f.prepare(manifest).publish() == (0, 1)
    assert f.calls == []
    assert f.domain.receipt() == before
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    with pytest.raises(ValueError, match="completion is not supported"):
        PendingCrawls(f.paths).clear("selected")
    f.retire_fixture_parent()
    assert f.prepare(f.retain(1, "An older edit.")).publish() == (0, 1)
    assert ComponentLedger(f.config, f.store).get(FAMILY, PATH) == expected
    assert f.calls == []
    assert f.config.state_file.read_bytes() == old_state


def test_replacement_and_tombstone_prevent_old_edit_resurrection(application: Fixture) -> None:
    f = application
    assert f.prepare(f.retain(1)).publish() == (1, 0)
    original = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert original is not None
    f.retire_fixture_parent()
    assert f.prepare(f.retain(2, "New fields.")).publish() == (1, 0)
    updated = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert updated is not None and updated.content.component.version == 2
    assert set(original.projection.vector_chunk_ids) < set(updated.projection.vector_chunk_ids)
    f.retire_fixture_parent()
    assert f.prepare(f.retain(3, deleted=True)).publish() == (1, 0)
    deleted = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert deleted is not None and deleted.content.deleted
    assert deleted.projection.content_rows == b"[]"
    assert all(model.rows == b"[]" for model in deleted.projection.models)
    assert f.paths.get_entry(FAMILY, PATH) is None
    f.retire_fixture_parent()
    f.calls.clear()
    assert f.prepare(f.retain(2, "New fields.")).publish() == (0, 1)
    assert f.calls == []
    assert ComponentLedger(f.config, f.store).get(FAMILY, PATH) == deleted


def test_equal_version_conflict_refuses_before_embedding_or_mutation(application: Fixture) -> None:
    f = application
    f.prepare(f.retain(2)).publish()
    f.retire_fixture_parent()
    manifest = f.retain(2, "Conflicting fields.")
    before = f.domain.receipt()
    f.calls.clear()
    with pytest.raises(ValueError, match="Equal component versions"):
        f.prepare(manifest)
    assert f.domain.receipt() == before
    assert f.calls == []


@pytest.mark.parametrize("version", [1, 3])
def test_representation_change_requires_explicit_migration(
    application: Fixture, version: int,
) -> None:
    f = application
    f.prepare(f.retain(2)).publish()
    f.retire_fixture_parent()
    manifest = f.retain(version)
    f.retire_fixture_parent()
    manifest.component_versions = [ComponentVersion(FAMILY, PATH, version, "other-rendering")]
    PendingCrawls(f.paths).save(manifest, scope=SCOPE, full=False)
    before = f.domain.receipt()
    f.calls.clear()
    with pytest.raises(SourceLayoutError, match="unqualified"):
        f.prepare(manifest)
    assert f.domain.receipt() == before and not f.calls


def test_preparation_interruption_reuses_completed_pairs_without_publication(
    application: Fixture,
) -> None:
    f = application
    manifest = f.retain(1)

    def interrupt() -> None:
        if f.calls[-1][0] == "second":
            raise IndexingCancelled("fixture cancellation")

    f.hook = interrupt
    before = f.domain.receipt()
    with pytest.raises(IndexingCancelled):
        f.prepare(manifest)
    assert f.domain.receipt() == before
    assert f.paths.get_entry(FAMILY, PATH) is None
    assert ComponentLedger(f.config, f.store).get(FAMILY, PATH) is None
    f.reopen()
    f.hook = None
    f.calls.clear()
    assert f.prepare(manifest).publish() == (1, 0)
    assert [name for name, _ in f.calls] == ["second"]


@pytest.mark.parametrize(
    "drift", ["checkpoint", "native", "policy", "coverage", "parent", "path-owner"],
)
def test_publication_revalidates_preparation_inputs_before_writer(
    application: Fixture, drift: str,
) -> None:
    f = application
    f.prepare(f.retain(1)).publish()
    f.retire_fixture_parent()
    manifest = f.retain(2, "Replacement fields.")
    prepared = f.prepare(manifest)
    if drift == "checkpoint":
        f.paths.invalidate_batch([(FAMILY, PATH)])
    elif drift == "native":
        f.store.delete_by_file(FAMILY, PATH)
        prepare_ingestion_indices(f.store, max_rows=0)
    elif drift == "policy":
        f.config.model_profiles["first"] = replace(f.config.model_profiles["first"], revision="next")
    elif drift == "coverage":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM embedding_coverage")
    elif drift == "path-owner":
        replacement_paths = PathIndex(f.config.data_dir / "other.db")
        f.paths._db_path = replacement_paths._db_path
    else:
        f.retire_fixture_parent()
        f.retain(2, "Replacement fields.")
    before = f.domain.receipt()
    with pytest.raises((PendingCrawlConflict, SourceLayoutError)):
        prepared.publish()
    assert f.domain.receipt() == before


@pytest.mark.parametrize("stage", ["vectors", "ledger", "checkpoint"])
def test_interrupted_writer_preserves_parent_and_refuses_uncertain_reopen(
    application: Fixture, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    f = application
    manifest = f.retain(1)
    prepared = f.prepare(manifest)
    parent = PendingCrawls(f.paths).read_payload("selected", SCOPE)
    state = f.config.state_file.read_bytes()

    def interrupted(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"fixture interrupted {stage}")

    if stage == "vectors":
        monkeypatch.setattr(f.store, "upsert_vectors", interrupted)
    elif stage == "ledger":
        monkeypatch.setattr(prepared.ledger, "put", interrupted)
    else:
        monkeypatch.setattr(f.paths, "mark_indexed_batch", interrupted)
    with pytest.raises(RuntimeError, match="fixture interrupted"):
        prepared.publish()
    assert f.domain.receipt()["state"] == "dirty"
    f.reopen()
    with pytest.raises(SourceLayoutError, match="incomplete or changed"):
        f.prepare(manifest)
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    assert f.config.state_file.read_bytes() == state


@pytest.mark.parametrize("missing", ["coverage", "checkpoint"])
def test_missing_coverage_or_path_evidence_cannot_be_blessed_by_cached_vectors(
    application: Fixture, missing: str,
) -> None:
    f = application
    manifest = f.retain(1)
    f.prepare(manifest).publish()
    if missing == "coverage":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM embedding_coverage")
    else:
        f.paths.invalidate_batch([(FAMILY, PATH)])
    f.reopen()
    before = f.domain.receipt()
    with pytest.raises(SourceLayoutError, match="coverage is incomplete|checkpoint is incomplete"):
        f.prepare(manifest)
    assert f.domain.receipt() == before


def test_retained_id_union_budget_refuses_replacement_before_writer(application: Fixture) -> None:
    f = application
    f.prepare(f.retain(1)).publish()
    f.retire_fixture_parent()
    manifest = f.retain(2, "Replacement fields.")
    before = f.domain.receipt()
    checkpoints = f.paths.get_all_entries()
    with pytest.raises(ScalarIndexPreparationRequired, match="union budget"):
        f.prepare(manifest, max_rows=1)
    assert f.domain.receipt() == before
    assert f.paths.get_all_entries() == checkpoints
    assert PendingCrawls(f.paths).load("selected", SCOPE) == (manifest, False)


@pytest.mark.parametrize("stage", ["before-writer", "after-store"])
def test_cancellation_cannot_publish_ordering_or_path_completion(
    application: Fixture, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    f = application
    cancelled = False

    def check() -> None:
        if cancelled:
            raise IndexingCancelled("fixture cancellation")

    progress = Mock(spec=ProgressCallback)
    progress.check_cancelled.side_effect = check
    manifest = f.retain(1)
    prepared = f.prepare(manifest, progress_cb=progress)
    if stage == "before-writer":
        cancelled = True
    else:
        reconcile = f.store.reconcile_file_chunks

        def cancel_after_store(source: str, path: str, ids: set[str]) -> int:
            nonlocal cancelled
            result = reconcile(source, path, ids)
            cancelled = True
            return result

        monkeypatch.setattr(f.store, "reconcile_file_chunks", cancel_after_store)
    before = f.domain.receipt()
    with pytest.raises(IndexingCancelled):
        prepared.publish()
    assert f.paths.get_entry(FAMILY, PATH) is None
    assert PendingCrawls(f.paths).load("selected", SCOPE) == (manifest, False)
    if stage == "before-writer":
        assert f.domain.receipt() == before
        assert ComponentLedger(f.config, f.store).get(FAMILY, PATH) is None
    else:
        assert f.domain.receipt()["state"] == "dirty"


def test_routed_models_and_unrelated_legacy_rows_are_preserved(application: Fixture) -> None:
    f = application
    f.config.model_profiles["first"] = replace(
        f.config.model_profiles["first"], content_types=frozenset({"text"}),
    )
    f.config.model_profiles["second"] = replace(
        f.config.model_profiles["second"], content_types=frozenset({"function"}),
    )
    legacy = Chunk("Legacy combined document.", "combined.txt", "text", "text", 1, 1, "sample:legacy")
    f.store.upsert("first", [legacy], np.ones((1, 2), dtype=np.float32))
    f.paths.mark_indexed("sample:legacy", "combined.txt", content_hash="legacy", chunk_count=1)
    prepare_ingestion_indices(f.store, max_rows=1)
    before = capture_file_projection(
        f.config, f.store, "sample:legacy", "combined.txt", max_rows=8,
    )
    path_before = f.paths.get_entry("sample:legacy", "combined.txt")
    assert f.prepare(f.retain(1)).publish() == (1, 0)
    assert [name for name, _ in f.calls] == ["first"]
    point = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert point is not None and point.projection.models[1].rows == b"[]"
    assert capture_file_projection(
        f.config, f.store, "sample:legacy", "combined.txt", max_rows=8,
    ) == before
    assert f.paths.get_entry("sample:legacy", "combined.txt") == path_before


@pytest.mark.parametrize("owner", ["path", "clients"])
def test_path_and_model_owner_mismatches_refuse_before_cache_mutation(
    application: Fixture, tmp_path: Path, owner: str,
) -> None:
    f = application
    manifest = f.retain(1)
    before = f.domain.receipt()
    wrong_paths = PathIndex(tmp_path / "wrong.db")
    with pytest.raises(ValueError, match="configured path/model/family"):
        PreparedComponentApplication(
            manifest, scope=SCOPE, family=FAMILY, max_rows=8,
            config=f.config, paths=wrong_paths if owner == "path" else f.paths,
            store=f.store,
            clients={} if owner == "clients" else {
                name: cast(EngineClient, Client(f, name)) for name in f.config.model_profiles
            },
        )
    assert f.domain.receipt() == before
    with closing(wrong_paths._connect()) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'prepared_crawl_%'",
        ).fetchall() == []
    assert capture_file_projection(
        f.config, f.store, FAMILY, PATH, max_rows=8,
    ).content_rows == b"[]"
