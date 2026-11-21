"""Native point application remains separate from polling and claim completion."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
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
from agent_index.indexing.component_application import (
    PreparedComponentApplication, qualify_applied_component,
)
from agent_index.indexing.component_manifest import ComponentIdentity, ComponentVersion
from agent_index.indexing.component_read import ComponentRead, read_component
from agent_index.indexing.point_hints import PointBinding, PointChangeHints, WorkItemRevisionTarget
from agent_index.indexing.component_publication import reconcile_component_publication
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
    LAYOUT, MARKER, SourceDomain, SourceLayoutError, initialize_source_store, validate_source_store,
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
        identity: ComponentIdentity | None = None,
    ) -> CrawlManifest:
        manifest = CrawlManifest(
            "selected", accounting_family=FAMILY, commit="unchanged-poll",
            upserts=[] if deleted else [FileEntry(PATH, content, "text", FAMILY)],
            deletions=[DeletedFile(FAMILY, PATH)] if deleted else [],
            component_versions=[ComponentVersion(FAMILY, PATH, version, "fields-v1", identity)],
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


@pytest.mark.parametrize("identity", [None, ComponentIdentity("work_item_revision", "one")])
def test_native_apply_reopen_duplicate_and_older_retain_parent_and_poll(
    application: Fixture, identity: ComponentIdentity | None,
) -> None:
    f = application
    manifest = f.retain(3, identity=identity)
    old_state = f.config.state_file.read_bytes()
    prepared = f.prepare(manifest)
    assert len(f.calls) == 2
    assert not f.paths.get_entries([(FAMILY, PATH)])
    assert prepared.publish() == (1, 0)
    expected = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert expected is not None
    assert expected.content.component.identity == identity
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
    assert f.prepare(f.retain(1, "An older edit.", identity=identity)).publish() == (0, 1)
    assert ComponentLedger(f.config, f.store).get(FAMILY, PATH) == expected
    assert f.calls == []
    assert f.config.state_file.read_bytes() == old_state


@pytest.mark.parametrize("identity", [None, ComponentIdentity("comment_version", "one", "two")])
def test_replacement_and_tombstone_prevent_old_edit_resurrection(
    application: Fixture, identity: ComponentIdentity | None,
) -> None:
    f = application
    assert f.prepare(f.retain(1, identity=identity)).publish() == (1, 0)
    original = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert original is not None
    f.retire_fixture_parent()
    assert f.prepare(f.retain(2, "New fields.", identity=identity)).publish() == (1, 0)
    updated = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert updated is not None and updated.content.component.version == 2
    assert set(original.projection.vector_chunk_ids) < set(updated.projection.vector_chunk_ids)
    f.retire_fixture_parent()
    assert f.prepare(f.retain(3, deleted=True, identity=identity)).publish() == (1, 0)
    deleted = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert deleted is not None and deleted.content.deleted
    assert deleted.content.component.identity == identity
    f.reopen()
    snapshot = qualify_applied_component(
        ComponentLedger(f.config, f.store), f.paths, deleted, scope=SCOPE, max_rows=8,
    )
    assert snapshot.evidence == deleted and snapshot.chunks == ()
    assert deleted.projection.content_rows == b"[]"
    assert all(model.rows == b"[]" for model in deleted.projection.models)
    assert f.paths.get_entry(FAMILY, PATH) is None
    f.retire_fixture_parent()
    f.calls.clear()
    assert f.prepare(f.retain(2, "New fields.", identity=identity)).publish() == (0, 1)
    assert f.calls == []
    assert ComponentLedger(f.config, f.store).get(FAMILY, PATH) == deleted


@pytest.mark.parametrize(("prior", "identity"), [
    (None, ComponentIdentity("work_item_revision", "one")),
    (ComponentIdentity("work_item_revision", "one"), None),
    (ComponentIdentity("work_item_revision", "one"), ComponentIdentity("work_item_revision", "two")),
    (ComponentIdentity("comment_version", "one", "two"), ComponentIdentity("comment_version", "other", "two")),
])
def test_identity_rebinding_refuses_before_embedding_or_native_effects(
    application: Fixture, prior: ComponentIdentity | None, identity: ComponentIdentity | None,
) -> None:
    f = application
    f.prepare(f.retain(2, identity=prior)).publish()
    previous = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    f.retire_fixture_parent()
    manifest = f.retain(3, identity=identity)
    before = f.domain.receipt()
    checkpoint = f.paths.get_entry(FAMILY, PATH)
    f.calls.clear()
    with pytest.raises(ValueError, match="identity"):
        f.prepare(manifest)
    assert f.calls == [] and f.domain.receipt() == before
    assert f.paths.get_entry(FAMILY, PATH) == checkpoint
    assert ComponentLedger(f.config, f.store).get(FAMILY, PATH) == previous


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


def reconcile(f: Fixture, manifest: CrawlManifest) -> tuple[int, int]:
    return reconcile_component_publication(
        manifest, scope=SCOPE, family=FAMILY, max_rows=8,
        config=f.config, paths=f.paths, store=f.store,
    )


@pytest.mark.parametrize("stage", ["ordering", "checkpoint", "clean"])
@pytest.mark.parametrize("deleted", [False, True])
def test_sealed_native_publication_reconciles_suffix_without_embedding_or_claim_completion(
    application: Fixture, monkeypatch: pytest.MonkeyPatch, stage: str, deleted: bool,
) -> None:
    f = application
    f.prepare(f.retain(1)).publish()
    f.retire_fixture_parent()
    manifest = f.retain(2, "Replacement fields.", deleted=deleted)
    prepared = f.prepare(manifest)
    parent = PendingCrawls(f.paths).read_payload("selected", SCOPE)
    poll = f.config.state_file.read_bytes()

    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture sealed interruption")

    with monkeypatch.context() as patch:
        if stage == "ordering":
            patch.setattr(prepared.ledger, "put", interrupt)
        elif stage == "checkpoint":
            patch.setattr(f.paths, "remove" if deleted else "mark_indexed_batch", interrupt)
        else:
            save = f.domain.save

            def refuse_clean(value: dict[str, Any]) -> None:
                if value["state"] == "clean":
                    interrupt()
                save(value)

            patch.setattr(f.domain, "save", refuse_clean)
        with pytest.raises(RuntimeError, match="sealed interruption"):
            prepared.publish()
    receipt = f.domain.receipt()
    assert receipt["state"] == "dirty" and receipt["reconciliation"]
    with pytest.raises(SourceLayoutError, match="owning reconciliation"):
        validate_source_store(f.store)
    f.reopen()
    f.calls.clear()
    assert reconcile(f, manifest) == (1, 0)
    assert f.calls == []
    assert f.domain.receipt()["state"] == "clean"
    assert "reconciliation" not in f.domain.receipt()
    assert f.domain.receipt()["logical_projection"]["epoch"] != (
        receipt["logical_projection"]["epoch"]
    )
    evidence = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert evidence is not None and evidence.content.component.version == 2
    assert evidence.content.deleted == deleted
    assert (f.paths.get_entry(FAMILY, PATH) is None) == deleted
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    assert f.config.state_file.read_bytes() == poll
    assert f.prepare(manifest).publish() == (0, 1)
    assert f.calls == []
    with pytest.raises(ValueError, match="completion is not supported"):
        PendingCrawls(f.paths).clear("selected")


@pytest.mark.parametrize(
    "drift", ["native", "coverage", "checkpoint", "count", "parent", "intent", "policy", "table-identity"],
)
def test_sealed_reconciliation_refuses_changed_native_and_pending_owners(
    application: Fixture, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    f = application
    manifest = f.retain(1)
    prepared = f.prepare(manifest)

    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture checkpoint interruption")

    with monkeypatch.context() as patch:
        patch.setattr(f.paths, "mark_indexed_batch", interrupt)
        with pytest.raises(RuntimeError, match="checkpoint interruption"):
            prepared.publish()
    if drift == "native":
        table = f.store.content_store.db.open_table(f.config.content_table)
        table.add(table.search().limit(1).to_list())
    elif drift == "coverage":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM embedding_coverage")
    elif drift == "checkpoint":
        f.paths.mark_indexed(FAMILY, PATH, content_hash="foreign", chunk_count=7)
    elif drift == "count":
        f.paths.mark_indexed(
            FAMILY, PATH, content_hash=prepared.contents[(FAMILY, PATH)].fingerprint, chunk_count=7,
        )
    elif drift == "parent":
        f.retire_fixture_parent()
        f.retain(1)
    elif drift == "intent":
        prepared.prepared.journal.put("component-publication-v1", [("publication", b"{}")])
    elif drift == "policy":
        f.config.model_profiles["first"] = replace(f.config.model_profiles["first"], revision="next")
    else:
        original = f.config.lance_dir / "canonical.lance"
        copied = f.config.lance_dir / "copied-canonical.lance"
        shutil.copytree(original, copied)
        original.rename(f.config.lance_dir / "retained-canonical.lance")
        copied.rename(original)
    before = f.domain.receipt()
    checkpoints = f.paths.get_all_entries()
    with pytest.raises((SourceLayoutError, PendingCrawlConflict, ValueError)):
        reconcile(f, manifest)
    assert f.domain.receipt() == before
    assert f.paths.get_all_entries() == checkpoints


def test_mid_native_interruption_has_no_reconciliation_authority(
    application: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    manifest = f.retain(1)
    prepared = f.prepare(manifest)

    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture vector interruption")

    monkeypatch.setattr(f.store, "upsert_vectors", interrupt)
    with pytest.raises(RuntimeError, match="vector interruption"):
        prepared.publish()
    before = f.domain.receipt()
    assert before["state"] == "dirty" and "reconciliation" not in before
    with pytest.raises(SourceLayoutError, match="intent is missing"):
        reconcile(f, manifest)
    assert f.domain.receipt() == before


def test_interrupted_ordering_effect_revokes_earlier_native_seal(
    application: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    manifest = f.retain(1)
    prepared = f.prepare(manifest)

    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture interrupted native ledger binding")

    monkeypatch.setattr(prepared.ledger, "_bind", interrupt)
    with pytest.raises(RuntimeError, match="native ledger binding"):
        prepared.publish()
    before = f.domain.receipt()
    assert before["state"] == "dirty" and "reconciliation" not in before
    with pytest.raises(SourceLayoutError, match="seal is missing or changed"):
        reconcile(f, manifest)
    assert f.domain.receipt() == before


def test_empty_native_phase_seal_retains_legacy_uncertainty_refusal(
    application: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    manifest = f.retain(1, deleted=True)
    prepared = f.prepare(manifest)

    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture before first ordering effect")

    monkeypatch.setattr(prepared.ledger, "put", interrupt)
    with pytest.raises(RuntimeError, match="first ordering effect"):
        prepared.publish()
    assert f.domain.receipt()["uncertain_effect"] is True
    assert reconcile(f, manifest) == (1, 0)
    evidence = ComponentLedger(f.config, f.store).get(FAMILY, PATH)
    assert evidence is not None and evidence.content.deleted


def test_reconciliation_finishes_mixed_completed_and_pending_point_suffixes(
    application: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    names = ["a.txt", "b.txt", "c.txt"]
    initial = CrawlManifest(
        "selected", accounting_family=FAMILY,
        upserts=[FileEntry(path, f"Original {path}.", "text", FAMILY) for path in names],
        component_versions=[ComponentVersion(FAMILY, path, 1, "fields-v1") for path in names],
    )
    PendingCrawls(f.paths).save(initial, scope=SCOPE, full=False)
    assert f.prepare(initial).publish() == (3, 0)
    original = ComponentLedger(f.config, f.store).get(FAMILY, "c.txt")
    unchanged_checkpoint = f.paths.get_checkpoint_rows([(FAMILY, "c.txt")])
    f.retire_fixture_parent()
    manifest = CrawlManifest(
        "selected", accounting_family=FAMILY,
        upserts=[
            FileEntry("a.txt", "Replacement a.txt.", "text", FAMILY),
            FileEntry("c.txt", "Original c.txt.", "text", FAMILY),
        ],
        deletions=[DeletedFile(FAMILY, "b.txt")],
        component_versions=[
            ComponentVersion(FAMILY, path, 1 if path == "c.txt" else 2, "fields-v1")
            for path in names
        ],
    )
    PendingCrawls(f.paths).save(manifest, scope=SCOPE, full=False)
    prepared = f.prepare(manifest)
    mark = f.paths.mark_indexed_batch

    def interrupt_after_checkpoint(entries: list[tuple[str, str, str | None, int]]) -> None:
        mark(entries)
        raise RuntimeError("fixture after first checkpoint")

    with monkeypatch.context() as patch:
        patch.setattr(f.paths, "mark_indexed_batch", interrupt_after_checkpoint)
        with pytest.raises(RuntimeError, match="after first checkpoint"):
            prepared.publish()
    completed = f.paths.get_checkpoint_rows([(FAMILY, "a.txt")])
    f.reopen()
    f.calls.clear()
    with monkeypatch.context() as patch:
        patch.setattr(
            f.paths, "remove", Mock(side_effect=RuntimeError("fixture second checkpoint")),
        )
        with pytest.raises(RuntimeError, match="second checkpoint"):
            reconcile(f, manifest)
    assert reconcile(f, manifest) == (2, 1)
    assert f.calls == []
    assert f.paths.get_checkpoint_rows([(FAMILY, "a.txt")]) == completed
    assert f.paths.get_entry(FAMILY, "b.txt") is None
    assert ComponentLedger(f.config, f.store).get(FAMILY, "c.txt") == original
    assert f.paths.get_checkpoint_rows([(FAMILY, "c.txt")]) == unchanged_checkpoint
    assert f.prepare(manifest).publish() == (0, 3)


def test_process_exit_after_native_seal_releases_exclusion_and_reconciles(
    application: Fixture,
) -> None:
    f = application
    child = subprocess.run(
        [sys.executable, "-c", """
import os
import runpy
import sys
from pathlib import Path
import pytest

module = runpy.run_path(sys.argv[1])
fixture = module["application"].__wrapped__(Path(sys.argv[2]), pytest.MonkeyPatch())
prepared = fixture.prepare(fixture.retain(1))
fixture.paths.mark_indexed_batch = lambda *_args, **_kwargs: os._exit(47)
prepared.publish()
""", __file__, str(f.config.data_dir.parent)],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert child.returncode == 47, child.stdout + child.stderr
    f.reopen()
    retained = PendingCrawls(f.paths).load("selected", SCOPE)
    assert retained is not None
    manifest, full = retained
    assert not full and f.domain.receipt()["state"] == "dirty"
    assert reconcile(f, manifest) == (1, 0)
    assert f.calls == []
    assert f.prepare(manifest).publish() == (0, 1)


@pytest.mark.parametrize("missing", ["coverage", "checkpoint", "checkpoint-count"])
def test_missing_coverage_or_path_evidence_cannot_be_blessed_by_cached_vectors(
    application: Fixture, missing: str,
) -> None:
    f = application
    manifest = f.retain(1)
    f.prepare(manifest).publish()
    if missing == "coverage":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM embedding_coverage")
    elif missing == "checkpoint-count":
        checkpoint = f.paths.get_entry(FAMILY, PATH)
        assert checkpoint is not None
        f.paths.mark_indexed(FAMILY, PATH, content_hash=checkpoint[0], chunk_count=7)
    else:
        f.paths.invalidate_batch([(FAMILY, PATH)])
    f.reopen()
    before = f.domain.receipt()
    with pytest.raises(SourceLayoutError, match="coverage is incomplete|checkpoint is incomplete"):
        f.prepare(manifest)
    assert f.domain.receipt() == before


@pytest.mark.parametrize("deleted", [False, True])
def test_applied_snapshot_reopens_without_changing_parent_poll_or_native_state(
    application: Fixture, deleted: bool,
) -> None:
    f = application
    f.prepare(f.retain(3, deleted=deleted)).publish()
    f.reopen()
    ledger = ComponentLedger(f.config, f.store)
    evidence = ledger.get(FAMILY, PATH)
    assert evidence is not None
    before = f.domain.receipt(), f.paths.get_checkpoint_rows([(FAMILY, PATH)])
    parent = PendingCrawls(f.paths).read_payload("selected", SCOPE)
    poll = f.config.state_file.read_bytes()
    f.calls.clear()
    snapshot = qualify_applied_component(ledger, f.paths, evidence, scope=SCOPE, max_rows=8)
    assert snapshot.evidence == evidence and snapshot.coverage == "named-target-only"
    assert snapshot.evidence.content.component.version == 3
    assert snapshot.evidence.content.deleted == deleted
    assert [chunk.content for chunk in snapshot.chunks] == ([] if deleted else ["Selected fields."])
    assert (f.domain.receipt(), f.paths.get_checkpoint_rows([(FAMILY, PATH)])) == before
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    assert f.config.state_file.read_bytes() == poll and f.calls == []


@pytest.mark.parametrize(
    "drift", ["scope", "ordering", "native", "coverage", "checkpoint-count", "path-owner"],
)
def test_applied_snapshot_refuses_unqualified_evidence_without_repair(
    application: Fixture, drift: str,
) -> None:
    f = application
    f.prepare(f.retain(3)).publish()
    ledger = ComponentLedger(f.config, f.store)
    evidence = ledger.get(FAMILY, PATH)
    assert evidence is not None
    paths = f.paths
    if drift == "ordering":
        evidence = replace(
            evidence, content=replace(
                evidence.content, component=replace(evidence.content.component, version=4),
            ),
        )
    elif drift == "native":
        f.store.delete_by_file(FAMILY, PATH)
        prepare_ingestion_indices(f.store, max_rows=0)
    elif drift == "coverage":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM embedding_coverage")
    elif drift == "checkpoint-count":
        f.paths.mark_indexed(
            FAMILY, PATH, content_hash=evidence.content.fingerprint, chunk_count=7,
        )
    elif drift == "path-owner":
        paths = PathIndex(f.config.data_dir / "wrong.db")
    before = f.domain.receipt(), f.paths.get_checkpoint_rows([(FAMILY, PATH)])
    parent = PendingCrawls(f.paths).read_payload("selected", SCOPE)
    poll = f.config.state_file.read_bytes()
    with pytest.raises((SourceLayoutError, ValueError)):
        qualify_applied_component(
            ledger, paths, evidence, scope="other" if drift == "scope" else SCOPE, max_rows=8,
        )
    assert (f.domain.receipt(), f.paths.get_checkpoint_rows([(FAMILY, PATH)])) == before
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    assert f.config.state_file.read_bytes() == poll


def select_point_fixture(f: Fixture) -> PointChangeHints:
    """Author synthetic retained state only; no production writer or migration is enabled."""
    hints = PointChangeHints(
        source="selected", canonical_family=FAMILY, account_id="fixture",
        binding=PointBinding("https://example.test/project", "query", "context"),
        targets=(WorkItemRevisionTarget("opaque-item", 3),),
    )
    receipt = f.domain.receipt()
    receipt.update(protocol=2, point_models={
        "protocol": 1, "content_table": f.config.content_table,
        "models": {
            key: {"table_name": profile.table_name, "dim": profile.dim}
            for key, profile in f.config.model_profiles.items()
        },
    }, point_scope={
        "source": hints.source, "canonical_family": hints.canonical_family,
        "account_id": hints.account_id, "binding": hints.binding.to_dict(),
    })
    (f.config.lance_dir / MARKER).write_text(json.dumps(receipt))
    f.store = MultiModelStore(
        f.config.lance_dir, content_table=f.config.content_table, point_scope=hints.scope,
    )
    for profile in f.config.model_profiles.values():
        f.store.register_model(profile)
    return hints


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize("identity", [
    ComponentIdentity("work_item_revision", "opaque-item"),
    ComponentIdentity("comment_version", "other-parent", "opaque-comment"),
])
def test_selected_component_read_retains_identity_and_default_refusal(
    application: Fixture, deleted: bool, identity: ComponentIdentity,
) -> None:
    f = application
    f.prepare(f.retain(3, deleted=deleted, identity=identity)).publish()
    hints = select_point_fixture(f)
    ledger = ComponentLedger(f.config, f.store)
    before = {str(p): p.read_bytes() for p in f.config.data_dir.rglob("*") if p.is_file()}
    f.calls.clear()
    result = read_component(
        ledger, f.paths, PATH, point_scope=hints.scope, application_scope=SCOPE, max_rows=8,
    )
    assert type(result) is ComponentRead
    assert result.point_scope == hints.scope and result.identity == identity
    assert (result.source, result.path, result.applied_version, result.semantics) == (
        FAMILY, PATH, 3, "fields-v1",
    )
    assert result.coverage == "named-target-only" and result.deleted == deleted
    assert [c.content for c in result.chunks] == ([] if deleted else ["Selected fields."])
    with f.domain.operation(f.store.content_store.db, point_scope=hints.scope):
        for read in (
            f.store.stats, f.store.content_store.stats,
            lambda: f.store.get_document(FAMILY, PATH),
            lambda: f.store.content_store.fts_search("Selected"),
            lambda: ledger.get(FAMILY, PATH),
            lambda: f.store.get_vector_table("first").get_vectors([]),
            lambda: f.store.upsert_content([]),
        ):
            with pytest.raises(SourceLayoutError, match="not admitted"):
                read()
    for constructor in (MultiModelStore,):
        with pytest.raises(SourceLayoutError):
            constructor(f.config.lance_dir)
    assert f.calls == []
    assert {str(p): p.read_bytes() for p in f.config.data_dir.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "drift", ["scope", "family", "binding", "untyped", "missing", "coverage",
              "checkpoint", "missing-path-db", "empty-path-db", "empty-deleted-path-db",
              "corrupt-path-db", "policy", "native", "receipt", "budget"],
)
def test_selected_component_read_refuses_unqualified_points_without_repair(
    application: Fixture, drift: str,
) -> None:
    f = application
    identity = None if drift == "untyped" else ComponentIdentity("work_item_revision", "opaque")
    f.prepare(f.retain(3, identity=identity, deleted=drift == "empty-deleted-path-db")).publish()
    if drift == "coverage":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM embedding_coverage")
    elif drift == "checkpoint":
        f.paths.invalidate_batch([(FAMILY, PATH)])
    elif drift == "native":
        f.store.delete_by_file(FAMILY, PATH)
        prepare_ingestion_indices(f.store, max_rows=0)
    hints = select_point_fixture(f)
    selected = hints.scope
    if drift == "family":
        selected = replace(hints, canonical_family="other").scope
    elif drift == "binding":
        selected = replace(hints, binding=replace(hints.binding, query_context="other")).scope
    elif drift == "policy":
        f.config.model_profiles["first"] = replace(
            f.config.model_profiles["first"], revision="other",
        )
    elif drift == "receipt":
        marker = f.config.lance_dir / MARKER
        receipt = json.loads(marker.read_text())
        receipt["point_scope"]["account_id"] = "other"
        marker.write_text(json.dumps(receipt))
    elif drift == "missing-path-db":
        Path(f.paths._db_path).unlink()
    elif drift in ("empty-path-db", "empty-deleted-path-db", "corrupt-path-db"):
        Path(f.paths._db_path).write_bytes(b"not SQLite" if drift == "corrupt-path-db" else b"")
    before = {str(p): p.read_bytes() for p in f.config.data_dir.rglob("*") if p.is_file()}
    with pytest.raises((SourceLayoutError, ValueError)):
        read_component(
            ComponentLedger(f.config, f.store), f.paths,
            "unknown" if drift == "missing" else PATH, point_scope=selected,
            application_scope="other" if drift == "scope" else SCOPE,
            max_rows=0 if drift == "budget" else 8,
        )
    assert {str(p): p.read_bytes() for p in f.config.data_dir.rglob("*") if p.is_file()} == before


def test_selected_read_never_ignores_or_changes_uncheckpointed_path_evidence(
    application: Fixture,
) -> None:
    f = application
    f.prepare(f.retain(3, identity=ComponentIdentity("work_item_revision", "opaque"))).publish()
    hints = select_point_fixture(f)
    with closing(f.paths._connect()) as conn:
        conn.execute("UPDATE indexed_files SET indexed_at = indexed_at + 1")
        conn.commit()
        assert Path(f.paths._db_path + "-wal").stat().st_size > 0
        before = {str(p): p.read_bytes() for p in f.config.data_dir.rglob("*") if p.is_file()}
        with pytest.raises(SourceLayoutError, match="path evidence"):
            read_component(
                ComponentLedger(f.config, f.store), f.paths, PATH,
                point_scope=hints.scope, application_scope=SCOPE, max_rows=8,
            )
        assert {str(p): p.read_bytes() for p in f.config.data_dir.rglob("*") if p.is_file()} == before


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


@pytest.mark.parametrize("arrival", ["selected-union", "before-preparation", "during-preparation"])
def test_projected_same_path_capacity_refuses_all_mutations_before_writer(
    application: Fixture, arrival: str,
) -> None:
    f = application

    def add_unrelated() -> None:
        f.hook = None
        legacy = Chunk("Retained document.", PATH, "text", "text", 1, 1, "sample:legacy")
        f.store.upsert("first", [legacy], np.ones((1, 2), dtype=np.float32))
        f.paths.mark_indexed("sample:legacy", PATH, content_hash="retained", chunk_count=1)
        prepare_ingestion_indices(f.store, max_rows=1)

    if arrival == "before-preparation":
        add_unrelated()
    elif arrival == "during-preparation":
        f.hook = add_unrelated
    sources = [FAMILY + ":a", FAMILY + ":b"] if arrival == "selected-union" else [FAMILY + ":new"]
    manifest = CrawlManifest(
        "selected", accounting_family=FAMILY,
        upserts=[FileEntry(PATH, f"Fields for {source}.", "text", source) for source in sources],
        component_versions=[ComponentVersion(source, PATH, 1, "fields-v1") for source in sources],
    )
    PendingCrawls(f.paths).save(manifest, scope=SCOPE, full=False)
    prepared = f.prepare(manifest, max_rows=1)
    before = f.domain.receipt()
    checkpoints = f.paths.get_all_entries()
    parent = PendingCrawls(f.paths).read_payload("selected", SCOPE)
    with pytest.raises(ScalarIndexPreparationRequired, match="Projected file path"):
        prepared.publish()
    assert f.domain.receipt() == before
    assert f.paths.get_all_entries() == checkpoints
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    for source in sources:
        assert ComponentLedger(f.config, f.store).get(source, PATH) is None


def test_projected_same_path_capacity_accounts_for_selected_deletion(application: Fixture) -> None:
    f = application
    first, second = FAMILY + ":z", FAMILY + ":a"
    initial = CrawlManifest(
        "selected", accounting_family=FAMILY,
        upserts=[FileEntry(PATH, "Original fields.", "text", first)],
        component_versions=[ComponentVersion(first, PATH, 1, "fields-v1")],
    )
    PendingCrawls(f.paths).save(initial, scope=SCOPE, full=False)
    f.prepare(initial, max_rows=1).publish()
    f.retire_fixture_parent()
    manifest = CrawlManifest(
        "selected", accounting_family=FAMILY,
        upserts=[FileEntry(PATH, "Replacement fields.", "text", second)],
        deletions=[DeletedFile(first, PATH)],
        component_versions=[
            ComponentVersion(first, PATH, 2, "fields-v1"),
            ComponentVersion(second, PATH, 1, "fields-v1"),
        ],
    )
    PendingCrawls(f.paths).save(manifest, scope=SCOPE, full=False)
    assert f.prepare(manifest, max_rows=1).publish() == (2, 0)
    assert f.paths.get_entry(first, PATH) is None
    assert f.paths.get_entry(second, PATH) is not None
    assert f.domain.receipt()["state"] == "clean"


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
