"""Explicit finite-budget backfill; changed input requires an explicit restart."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_index.chunking.base import Chunk
from agent_index.indexing.embedding_batch import (
    _require_current_input,
    embed_and_store_pairs,
    routed_model_chunks,
)
from agent_index.indexing.embedding_coverage import EmbeddingCoverage, embedding_profile_key
from agent_index.indexing.pending_crawl import PendingCrawls, PendingCrawlConflict, recovery_scope
from agent_index.indexing.state import IndexState
from agent_index.store.content_snapshot import ContentSnapshot, ContentSnapshotUnavailable
from agent_index.store.chunk_id_index import ChunkIdIndexPreparationRequired
from agent_index.store.source_layout import SourceLayoutError

if TYPE_CHECKING:
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.engine import SourceSpec
    from agent_index.indexing.path_index import PathIndex
    from agent_index.sources.base import SourceConnector
    from agent_index.store.content_store import ChunkRecord
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.vector_table import VectorTable


class BackfillRepairRequired(RuntimeError):
    """Do not acknowledge or silently restart changed or incomplete backfill input."""


@dataclass(frozen=True)
class BackfillProgress:
    offset: int
    total_rows: int
    rows_read: int
    scoped_chunks: int
    vector_rows_upserted: int
    complete: bool


def _binding(
    spec: SourceSpec, connector: SourceConnector, config: IndexConfig, store: MultiModelStore,
) -> dict[str, str]:
    from agent_index.indexing.engine import SourceSpec, source_scope_hash

    if not isinstance(spec, SourceSpec):
        raise ValueError("Backfill requires a resolved source specification")
    if (store._source_domain is None
            or Path(store._db_path).resolve() != config.lance_dir.resolve()
            or store.content_store._table_name != config.content_table):
        raise BackfillRepairRequired("Backfill requires the configured participating source store")
    if getattr(connector, "recovery_identity", None) is None:
        raise BackfillRepairRequired("Backfill requires explicit connector account/target identity")
    scope = recovery_scope(
        source_scope_hash(spec), connector,
    )
    state = IndexState.load(config.state_file)
    source = state.sources.get(spec.name)
    family = state.stored_families.get(spec.name)
    if (source is None or source.acquisition_scope != scope
            or not family or family != connector.source_name):
        raise BackfillRepairRequired("Backfill source/account identity is unproven; repair required")
    if not config.model_profiles:
        raise ValueError("Backfill requires configured model profiles")
    profiles = {}
    for name, profile in sorted(config.model_profiles.items()):
        table = store.get_vector_table(name)
        if (name != profile.model_id or table.table_name != profile.table_name
                or table._dim != profile.dim):
            raise BackfillRepairRequired("Registered backfill model mapping changed")
        profiles[name] = [
            embedding_profile_key(profile), profile.table_name, sorted(profile.content_types),
        ]
    signature = hashlib.sha256(json.dumps(profiles, sort_keys=True).encode()).hexdigest()
    return {"source": spec.name, "scope": scope, "family": family, "profiles": signature}


def _snapshot(payload: dict) -> ContentSnapshot:
    raw = payload.get("snapshot")
    if not isinstance(raw, dict):
        raise BackfillRepairRequired("Malformed backfill snapshot")
    try:
        raw = {**raw, "storage": tuple(raw["storage"])}
        return ContentSnapshot(**raw)
    except (TypeError, ValueError, KeyError) as exc:
        raise BackfillRepairRequired("Malformed backfill snapshot") from exc


def _decode(raw: str, complete: int) -> dict:
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {
            "version", "generation", "binding", "snapshot", "offset", "vectors",
        } or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["generation"], str) or not value["generation"]
                or not isinstance(value["binding"], dict)
                or type(value["offset"]) is not int
                or not isinstance(value["vectors"], dict)
                or any(not isinstance(name, str) or not name or type(version) is not int
                       or version < 1 for name, version in value["vectors"].items())):
            raise ValueError("Invalid backfill envelope")
        snapshot = _snapshot(value)
        if (not 0 <= value["offset"] <= snapshot.rows
                or complete not in (0, 1)
                or bool(complete) != (value["offset"] == snapshot.rows)):
            raise ValueError("Invalid backfill offset or completion")
        return value
    except (TypeError, ValueError) as exc:
        raise BackfillRepairRequired("Malformed backfill progress; repair required") from exc


def _vector_versions(config: IndexConfig, store: MultiModelStore) -> dict[str, int]:
    return {
        name: store.content_store.db.open_table(store.get_vector_table(name).table_name).version
        for name in sorted(config.model_profiles)
    }


def _require_current(
    payload: dict, spec: SourceSpec, connector: SourceConnector,
    config: IndexConfig, store: MultiModelStore,
) -> None:
    if payload["binding"] != _binding(spec, connector, config, store):
        raise BackfillRepairRequired("Backfill source/account/profile binding changed; restart required")
    try:
        if store.content_store.capture_snapshot() != _snapshot(payload):
            raise BackfillRepairRequired("Canonical backfill input changed; restart required")
    except ContentSnapshotUnavailable as exc:
        raise BackfillRepairRequired("Canonical backfill input unavailable; repair required") from exc


def _chunk(record: ChunkRecord) -> Chunk:
    value = Chunk(
        content=record.content, file_path=record.file_path, chunk_type=record.chunk_type,
        language=record.language, line_start=record.line_start, line_end=record.line_end,
        source=record.source, metadata=record.metadata,
    )
    if value.chunk_id != record.chunk_id or value.content_hash != record.content_hash:
        raise BackfillRepairRequired("Canonical chunk identity is incompatible; repair required")
    return value


def _save_progress(paths: PathIndex, source: str, raw: str, payload: dict) -> str:
    updated = json.dumps(payload, sort_keys=True)
    with closing(paths._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            "UPDATE embedding_backfills SET payload = ?, complete = ? "
            "WHERE source = ? AND payload = ?",
            (updated, int(payload["offset"] == _snapshot(payload).rows), source, raw),
        )
        if result.rowcount != 1:
            raise BackfillRepairRequired("Backfill progress changed concurrently; retry required")
    return updated


def discard_embedding_backfill(
    source: str, *, store: MultiModelStore, paths: PathIndex, expected_payload: str,
) -> None:
    """Explicitly discard matching derived intent, never canonical data or coverage.

    Call only when the caller authorizes abandoning/restarting this backfill.
    Compare-before-delete preserves a newer job; unsettled store effects block.
    """
    if not isinstance(source, str) or not source or not isinstance(expected_payload, str) or not expected_payload:
        raise ValueError("Discard requires a source and its exact current backfill payload")
    domain = store._source_domain
    if (domain is None or Path(paths._db_path).resolve()
            != Path(store._db_path).resolve().parent / "path_index.db"):
        raise BackfillRepairRequired("Discard requires the owning participating store and path database")
    with domain.read(store.content_store.db):
        with closing(paths._connect()) as conn, conn:
            result = conn.execute(
                "DELETE FROM embedding_backfills WHERE source = ? AND payload = ?",
                (source, expected_payload),
            )
            if result.rowcount != 1:
                raise BackfillRepairRequired("Backfill intent changed; discard refused")


def backfill_embedding_slice(
    spec: SourceSpec, connector: SourceConnector, *, config: IndexConfig,
    store: MultiModelStore, paths: PathIndex, clients: dict[str, EngineClient],
    row_budget: int, after_store: Callable[[VectorTable, int], None],
    before_embed: Callable[[], None] | None = None, restart: bool = False,
) -> BackfillProgress:
    """Process one bounded global offset range, selecting only the accepted family.

    Ordinary indexing remains active. Restart resets only derived backfill
    intent, never canonical data, acquisition identity or source checkpoints.
    The preparation callback owns its explicit physical-vector-tail budget.
    """
    if type(row_budget) is not int or row_budget < 1 or type(restart) is not bool:
        raise ValueError("Backfill requires a positive integer row budget and explicit restart mode")
    if set(clients) != set(config.model_profiles) or not callable(after_store):
        raise ValueError("Backfill clients and preparation must cover the configured models")
    if Path(paths._db_path).resolve() != (config.data_dir / "path_index.db").resolve():
        raise BackfillRepairRequired("Backfill progress belongs to a different path database")
    PendingCrawls(paths)
    domain = store._source_domain
    if domain is None:
        raise BackfillRepairRequired("Backfill requires a participating source store")

    with domain.read(store.content_store.db):
        binding = _binding(spec, connector, config, store)
        with closing(paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM pending_crawls LIMIT 1").fetchone():
                raise PendingCrawlConflict("Finish acquired crawl work before starting a backfill slice")
            row = conn.execute(
                "SELECT payload, complete FROM embedding_backfills WHERE source = ?", (spec.name,),
            ).fetchone()
            if row is None or restart:
                snapshot = store.content_store.capture_snapshot()
                payload = {
                    "version": 1, "generation": uuid.uuid4().hex, "binding": binding,
                    "snapshot": asdict(snapshot), "offset": 0,
                    "vectors": _vector_versions(config, store),
                }
                raw = json.dumps(payload, sort_keys=True)
                conn.execute(
                    "INSERT INTO embedding_backfills VALUES (?, ?, ?) "
                    "ON CONFLICT(source) DO UPDATE SET payload=excluded.payload, complete=excluded.complete",
                    (spec.name, raw, int(snapshot.rows == 0)),
                )
            else:
                raw = row["payload"]
                payload = _decode(raw, row["complete"])
            _require_current(payload, spec, connector, config, store)
            if payload["vectors"] != _vector_versions(config, store):
                raise BackfillRepairRequired("Backfill vector coverage changed; restart required")

    snapshot = _snapshot(payload)
    start = payload["offset"]
    if type(start) is not int:
        raise BackfillRepairRequired("Malformed backfill offset")
    if start == snapshot.rows:
        return BackfillProgress(start, snapshot.rows, 0, 0, 0, True)
    records = store.content_store.read_snapshot(snapshot, start=start, limit=row_budget)
    family = binding["family"]
    chunks = [_chunk(record) for record in records
              if record.source == family or record.source.startswith(family + ":")]

    def prepare() -> None:
        with domain.read(store.content_store.db):
            _require_current(payload, spec, connector, config, store)
        if before_embed is not None:
            before_embed()

    coverage = EmbeddingCoverage(paths)
    refusal = None
    with domain.write(store.content_store.db):
        try:
            with closing(paths._connect()) as conn:
                row = conn.execute(
                    "SELECT payload FROM embedding_backfills WHERE source = ?", (spec.name,),
                ).fetchone()
            if row is None or row["payload"] != raw:
                raise BackfillRepairRequired("Backfill intent changed concurrently; retry required")
            _require_current(payload, spec, connector, config, store)
            if payload["vectors"] != _vector_versions(config, store):
                raise BackfillRepairRequired("Backfill vector coverage changed; restart required")
            _require_current_input(store, chunks)
            for name, profile in config.model_profiles.items():
                coverage.missing(
                    store.get_vector_table(name), routed_model_chunks(chunks, name, config.model_profiles),
                    profile, bounded=True,
                )
        except (
            BackfillRepairRequired, ValueError, KeyError,
            ChunkIdIndexPreparationRequired, SourceLayoutError,
        ) as exc:
            # Only these metadata checks precede every backfill effect.
            refusal = exc
        if refusal is None:
            written = embed_and_store_pairs(
                chunks, store, clients, config.model_profiles, stream_batch_size=row_budget,
                coverage=coverage, bounded=True,
                before_embed=prepare, after_store=after_store,
            )
            for name, profile in config.model_profiles.items():
                selected = routed_model_chunks(chunks, name, config.model_profiles)
                if coverage.missing(store.get_vector_table(name), selected, profile, bounded=True):
                    raise BackfillRepairRequired("Backfill pair coverage is incomplete")
            vectors = _vector_versions(config, store)
            # Retain proven vector versions without acknowledging the offset. A
            # restart after the clean writer exit can replay this same covered page.
            payload = {**payload, "vectors": vectors}
            raw = _save_progress(paths, spec.name, raw, payload)
    if refusal is not None:
        raise refusal
    end = min(start + row_budget, snapshot.rows)
    with domain.read(store.content_store.db):
        _require_current(payload, spec, connector, config, store)
        if vectors != _vector_versions(config, store):
            raise BackfillRepairRequired("Backfill vector coverage changed before progress")
        _save_progress(paths, spec.name, raw, {**payload, "offset": end})
    return BackfillProgress(end, snapshot.rows, len(records), len(chunks), written, end == snapshot.rows)
