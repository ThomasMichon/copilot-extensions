"""Cold native qualification; no component producer, claim or reader is enabled."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray
from test_source_aware_store import cold as cold

from agent_index.engine.client import EngineClient
from agent_index.index_config import IndexConfig
from agent_index.indexing.component_manifest import ComponentVersion
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.state import IndexState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import SourceDomain, SourceLayoutError

SOURCE = "sample:components"
PATH = "items/1/comments/2.md"
SCOPE = "endpoint/account/query/point-components-v1"


@dataclass
class Client:
    calls: int = 0

    def embed_texts(self, texts: list[str]) -> NDArray[np.float32]:
        self.calls += 1
        return np.array([[len(text), 1] for text in texts], dtype=np.float32)


@dataclass
class Receiver:
    config: IndexConfig
    store: MultiModelStore
    journal: PendingCrawls
    client: Client

    @property
    def domain(self) -> SourceDomain:
        assert self.store._source_domain is not None
        return self.store._source_domain

    def pin(
        self, version: int, text: str | None = "Named component.", *, semantics: str = "comment/v1",
    ) -> str:
        manifest = CrawlManifest(
            source="selected", accounting_family=SOURCE, commit="ordinary-poll",
            upserts=[FileEntry(PATH, text, "markdown", SOURCE)] if text is not None else [],
            deletions=[DeletedFile(SOURCE, PATH)] if text is None else [],
            component_versions=[ComponentVersion(SOURCE, PATH, version, semantics)],
        )
        payload = json.dumps({
            "version": 8, "full": False, "manifest": self.journal.manifest_data(manifest),
        })
        self.journal.compare_and_save(
            manifest.source, scope=SCOPE, payload=payload,
            expected=self.journal.read_payload(manifest.source, SCOPE), family=SOURCE,
        )
        return payload

    def apply(self, payload: str, *, scope: str = SCOPE) -> tuple[int, int, int]:
        return self.journal.apply_components(
            "selected", scope=scope, expected_payload=payload, store=self.store,
            config=self.config, clients={"small": cast(EngineClient, self.client)},
        )

    def records(self) -> list[sqlite3.Row]:
        with closing(self.journal._paths._connect()) as conn:
            return conn.execute("SELECT * FROM applied_components ORDER BY source, path").fetchall()


@pytest.fixture
def receiver(cold: tuple[IndexConfig, MultiModelStore]) -> Receiver:
    config, store = cold
    state = IndexState()
    state.mark_source_indexed("selected", "ordinary-poll", 0)
    state.save(config.state_file)
    return Receiver(config, store, PendingCrawls(PathIndex(config.data_dir / "paths.db")), Client())


def test_native_newer_duplicate_equal_conflict_and_reopen(receiver: Receiver) -> None:
    f = receiver
    cursor = f.config.state_file.read_bytes()
    newest = f.pin(7, "Newest named comment.")
    result = f.apply(newest)
    assert result[0] > 0 and result[2] == 1
    assert f.client.calls == 1
    stored = f.store.content_store.get_chunks_for_file(SOURCE, PATH, strict=True)
    assert stored and all("Newest" in row.content for row in stored)
    versions = f.domain.receipt()
    records = [tuple(row) for row in f.records()]
    for version, text in ((7, "Newest named comment."), (6, "Late old comment.")):
        assert f.apply(f.pin(version, text)) == (0, 0, 0)
        assert f.client.calls == 1
        assert f.domain.receipt() == versions
        assert [tuple(row) for row in f.records()] == records
    for text, semantics in (("Conflicting content.", "comment/v1"), ("Newest named comment.", "comment/v2")):
        with pytest.raises(ValueError, match="Equal component versions"):
            f.apply(f.pin(7, text, semantics=semantics))
    f.store = MultiModelStore(f.config.lance_dir)
    for profile in f.config.model_profiles.values():
        f.store.register_model(profile)
    f.journal = PendingCrawls(PathIndex(f.config.data_dir / "paths.db"))
    assert f.apply(f.pin(8, "Newest named comment."))[0] == 0
    assert f.client.calls == 1  # Same bytes at a newer version reuse the existing file checkpoint.
    assert json.loads(f.records()[0]["payload"])["content"]["component"]["version"] == 8
    assert f.config.state_file.read_bytes() == cursor
    assert f.journal.load("selected", SCOPE) is not None
    with pytest.raises(ValueError, match="completion is not supported"):
        f.journal.clear("selected")


def test_native_deletion_retains_order_and_preserves_legacy(receiver: Receiver) -> None:
    f = receiver
    from agent_index.chunking.base import Chunk
    from agent_index.store.ingestion_index import prepare_ingestion_indices

    legacy = Chunk("Unchanged combined item.", "items/1.md", "text", "markdown", 1, 1, "sample:legacy")
    f.store.upsert("small", [legacy], np.array([[1, 0]], dtype=np.float32))
    prepare_ingestion_indices(f.store, max_rows=1)
    f.apply(f.pin(4))
    old_ids = [row.chunk_id for row in f.store.content_store.get_chunks_for_file(SOURCE, PATH)]
    assert f.apply(f.pin(6, None))[1] == 1
    assert f.store.content_store.get_chunks_for_file(SOURCE, PATH, strict=True) == []
    assert f.store.get_vector_table("small").get_vectors(old_ids, strict=True) == {}
    records = [tuple(row) for row in f.records()]
    assert f.apply(f.pin(5, "Out-of-order resurrection.")) == (0, 0, 0)
    assert [tuple(row) for row in f.records()] == records
    with pytest.raises(ValueError, match="Equal component versions"):
        f.apply(f.pin(6, "Same-version resurrection."))
    assert f.apply(f.pin(7, "Positively qualified later content."))[0] > 0
    retained = f.store.get_document("sample:legacy", "items/1.md")
    assert retained is not None and retained.content == legacy.content


def test_exact_parent_scope_and_entire_manifest_validate_before_effects(receiver: Receiver) -> None:
    f = receiver
    original = f.pin(2)
    f.apply(original)
    newer = f.pin(3)
    before = f.domain.receipt()
    with pytest.raises(PendingCrawlConflict, match="pending crawl"):
        f.apply(original)
    with pytest.raises(PendingCrawlConflict, match="pending crawl"):
        f.apply(newer, scope="other-account")
    raw = json.loads(newer)
    manifest = f.journal.decode_manifest(raw["manifest"], "selected", components=True)
    other = FileEntry("items/3/body.md", "New item must not be partially applied.", "markdown", SOURCE)
    manifest = replace(
        manifest, upserts=[other, replace(manifest.upserts[0], content="Conflict.")],
        component_versions=[
            ComponentVersion(SOURCE, other.path, 1, "body/v1"),
            ComponentVersion(SOURCE, PATH, 2, "comment/v1"),
        ],
    )
    conflict = json.dumps({
        "version": 8, "full": False, "manifest": f.journal.manifest_data(manifest),
    })
    f.journal.compare_and_save(
        "selected", scope=SCOPE, payload=conflict, expected=newer, family=SOURCE,
    )
    with pytest.raises(ValueError, match="Equal component versions"):
        f.apply(conflict)
    assert f.client.calls == 1
    assert f.domain.receipt() == before
    assert f.store.get_document(SOURCE, other.path) is None


def test_receiver_cannot_certify_an_enclosing_native_write(receiver: Receiver) -> None:
    f = receiver
    payload = f.pin(1)
    with f.domain.write(f.store.content_store.db):
        with pytest.raises(ValueError, match="enclosing native write"):
            f.apply(payload)
    assert f.client.calls == 0
    assert f.store.content_store.get_chunks_for_file(SOURCE, PATH, strict=True) == []
    assert f.journal.read_payload("selected", SCOPE) == payload


@pytest.mark.parametrize("stage", ["native-write", "ledger-commit"])
def test_interrupted_application_retains_claim_and_never_replays_uncertainty(
    receiver: Receiver, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    from agent_index.indexing.component_application import _initialize

    f = receiver
    payload = f.pin(1)
    cursor = f.config.state_file.read_bytes()
    expected: type[Exception]
    if stage == "native-write":
        def interrupted(*args: object, **kwargs: object) -> int:
            raise RuntimeError("fixture interrupted vector publication")

        monkeypatch.setattr(f.store.get_vector_table("small"), "_upsert", interrupted)
        expected = RuntimeError
    else:
        with closing(f.journal._paths._connect()) as conn, conn:
            _initialize(conn)
            conn.execute(
                "CREATE TRIGGER fixture_abort_evidence BEFORE INSERT ON applied_components "
                "BEGIN SELECT RAISE(ABORT, 'fixture interrupted evidence commit'); END",
            )
        expected = sqlite3.IntegrityError
    with pytest.raises(expected, match="fixture interrupted"):
        f.apply(payload)
    assert f.records() == []
    assert f.config.state_file.read_bytes() == cursor
    assert f.journal.read_payload("selected", SCOPE) == payload
    with closing(f.journal._paths._connect()) as conn:
        attempt = conn.execute("SELECT * FROM component_application_attempts").fetchone()
        assert (attempt["source"], attempt["scope"], attempt["payload"]) == ("selected", SCOPE, payload)
    f.journal = PendingCrawls(PathIndex(f.config.data_dir / "paths.db"))
    if stage == "native-write":
        assert f.domain.receipt()["state"] == "dirty"
        with pytest.raises(SourceLayoutError):
            f.apply(payload)
    else:
        assert f.domain.receipt()["state"] == "clean"
        assert f.store.content_store.get_chunks_for_file(SOURCE, PATH, strict=True)
        with pytest.raises(PendingCrawlConflict, match="owner repair"):
            f.apply(payload)
    with pytest.raises(sqlite3.IntegrityError, match="owner repair"):
        f.pin(2)
    with pytest.raises(ValueError, match="completion is not supported"):
        f.journal.clear("selected")


@pytest.mark.parametrize(
    "fault", ["canonical", "vector", "missing", "scope", "domain", "protocol", "model-policy"],
)
def test_missing_or_changed_native_evidence_is_not_already_covered(
    receiver: Receiver, fault: str,
) -> None:
    f = receiver
    f.apply(f.pin(2))
    if fault == "canonical":
        f.store.delete_by_file(SOURCE, PATH)
    elif fault == "vector":
        rows = f.store.content_store.get_chunks_for_file(SOURCE, PATH, strict=True)
        f.store.get_vector_table("small").delete_by_chunk_ids([row.chunk_id for row in rows])
    elif fault == "model-policy":
        f.config.model_profiles["small"] = replace(f.config.model_profiles["small"], revision="next")
    else:
        with closing(f.journal._paths._connect()) as conn, conn:
            if fault == "missing":
                conn.execute("DELETE FROM applied_components")
            else:
                record = json.loads(f.records()[0]["payload"])
                record[fault] = 99 if fault == "protocol" else "other-binding"
                conn.execute("UPDATE applied_components SET payload = ?", (json.dumps(record),))
    with pytest.raises(ValueError, match="projection changed|no applied-version|different configuration|Unsupported"):
        f.apply(f.pin(1))
    if fault != "missing":
        assert json.loads(f.records()[0]["payload"])["content"]["component"]["version"] == 2
