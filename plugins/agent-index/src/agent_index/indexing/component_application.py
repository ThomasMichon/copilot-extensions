"""Receiver-side ordering evidence subordinate to an exact pending crawl.

Ordinary recovery and v2 executors do not select this receiver yet. It neither
advances a source checkpoint nor retires its pending acquisition.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from agent_index.indexing.component_manifest import ComponentContent, ComponentVersion
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.pending_crawl import PendingCrawlConflict
from agent_index.sources.base import FileEntry

if TYPE_CHECKING:
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()


def _initialize(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS applied_components (
            source TEXT NOT NULL, path TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(source, path)
        );
        CREATE TABLE IF NOT EXISTS component_application_attempts (
            family TEXT PRIMARY KEY, source TEXT NOT NULL, scope TEXT NOT NULL,
            payload TEXT NOT NULL, domain TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS component_attempt_preserve_update
        BEFORE UPDATE ON pending_crawls
        WHEN EXISTS (
            SELECT 1 FROM component_application_attempts WHERE source = old.source
        )
        BEGIN SELECT RAISE(ABORT, 'Unresolved component application requires owner repair'); END;
        CREATE TRIGGER IF NOT EXISTS component_attempt_preserve_delete
        BEFORE DELETE ON pending_crawls
        WHEN EXISTS (
            SELECT 1 FROM component_application_attempts WHERE source = old.source
        )
        BEGIN SELECT RAISE(ABORT, 'Unresolved component application requires owner repair'); END;
    """)


def _require_parent(
    conn: sqlite3.Connection, source: str, scope: str, payload: str,
) -> None:
    row = conn.execute(
        "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
    ).fetchone()
    if row is None or (row["scope"], row["payload"]) != (scope, payload):
        raise PendingCrawlConflict("Component application no longer matches its pending crawl")


def _snapshot(
    store: MultiModelStore, source: str, path: str, previous_ids: list[str],
) -> dict[str, Any]:
    rows = store.content_store.get_chunks_for_file(source, path, strict=True)
    identities = sorted(row.chunk_id for row in rows)
    if len(set(identities)) != len(identities):
        raise ValueError("Component projection contains duplicate chunks")
    vectors = {}
    keys = sorted(set(identities) | set(previous_ids))
    for model, table in sorted(store._vector_tables.items()):
        selected = table.get_vectors(keys, strict=True) if keys else {}
        if set(selected) - set(identities):
            raise ValueError("Component projection retains stale vectors")
        vectors[model] = {
            key: vector.tolist() for key, vector in sorted(selected.items())
        }
    return {
        "ids": identities,
        "digest": _digest({
            "content": [asdict(row) for row in sorted(rows, key=lambda row: row.chunk_id)],
            "vectors": vectors,
        }),
    }


def _decode_applied(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        "protocol", "scope", "domain", "policy", "content", "projection",
    } or type(value["protocol"]) is not int or value["protocol"] != 1:
        raise ValueError("Unsupported applied component record")
    content = value["content"]
    if not isinstance(content, dict) or set(content) != {"component", "fingerprint", "deleted"}:
        raise ValueError("Invalid applied component content")
    component = ComponentVersion.from_dict(content["component"])
    if (
        type(content["deleted"]) is not bool
        or not isinstance(content["fingerprint"], str) or len(content["fingerprint"]) != 64
        or any(not isinstance(value[key], str) or not value[key] for key in ("scope", "domain", "policy"))
    ):
        raise ValueError("Invalid applied component binding")
    projection = value["projection"]
    if (
        not isinstance(projection, dict) or set(projection) != {"ids", "digest"}
        or not isinstance(projection["ids"], list)
        or any(not isinstance(key, str) or not key for key in projection["ids"])
        or len(set(projection["ids"])) != len(projection["ids"])
        or not isinstance(projection["digest"], str) or len(projection["digest"]) != 64
        or content["deleted"] and projection["ids"]
    ):
        raise ValueError("Invalid applied component projection")
    value["content"] = ComponentContent(
        component, content["fingerprint"], content["deleted"],
    )
    return value


def apply_components(
    journal: PendingCrawls, *, source: str, scope: str, expected_payload: str,
    store: MultiModelStore, config: IndexConfig, clients: dict[str, EngineClient],
) -> tuple[int, int, int]:
    """Apply only newer components; retain uncertainty and all pending ownership.

    Preparation and publication use the existing shared engine. No source
    checkpoint, claim or pending record is completed by this receiver.
    """
    from agent_index.indexing.engine import _apply_source_manifest
    from agent_index.indexing.prepared_application import (
        PreparedApplication, publish_prepared_manifest,
    )
    from agent_index.indexing.state import IndexState
    from agent_index.store.ingestion_index import prepare_ingestion_indices

    decoded = journal.decode_pending(expected_payload, source)
    if decoded is None or decoded[1] or decoded[0].component_versions is None:
        raise ValueError("Component application requires an incremental v8 pending crawl")
    manifest = decoded[0]
    assert manifest.component_versions is not None
    domain = store._source_domain
    if domain is None:
        raise ValueError("Component application requires a source-aware store")
    if domain.writing:
        raise ValueError("Component application cannot certify an enclosing native write")
    if (
        config.lance_dir.resolve() != domain.path
        or config.content_table != store.content_store._table_name
        or set(clients) != set(config.model_profiles)
    ):
        raise ValueError("Component application requires its configured store and every model")
    policy = file_fingerprint(
        FileEntry("", "", "", ""), config.model_profiles, chunker_revision=config.chunker_revision,
    )
    family = manifest.accounting_family or manifest.stored_family or source
    entries = {(entry.source, entry.path): entry for entry in manifest.upserts}
    incoming = {
        (component.source, component.path): ComponentContent(
            component,
            _digest(asdict(entries[(component.source, component.path)]))
            if (component.source, component.path) in entries else _digest({"deleted": True}),
            (component.source, component.path) not in entries,
        )
        for component in manifest.component_versions
    }
    with domain.operation(store.content_store.db):
        binding = domain.receipt()["domain"]
        with closing(journal._paths._connect()) as conn, conn:
            _initialize(conn)
            conn.execute("BEGIN IMMEDIATE")
            _require_parent(conn, source, scope, expected_payload)
            journal._assert_family_available(conn, source, family)
            if conn.execute(
                "SELECT 1 FROM component_application_attempts WHERE family = ?", (family,),
            ).fetchone() is not None:
                raise PendingCrawlConflict("Unresolved component application requires owner repair")
            retained = {
                key: row["payload"] if (row := conn.execute(
                    "SELECT payload FROM applied_components WHERE source = ? AND path = ?", key,
                ).fetchone()) is not None else None
                for key in incoming
            }
        selected = set()
        snapshots = {}
        checkpoints = journal._paths.get_entries(list(incoming))
        for key, current in incoming.items():
            raw = retained[key]
            previous = _decode_applied(raw) if raw is not None else None
            if previous is not None and (
                previous["scope"], previous["domain"], previous["policy"],
            ) != (scope, binding, policy):
                raise ValueError("Applied component belongs to a different configuration or store")
            snapshot = _snapshot(
                store, *key, previous["projection"]["ids"] if previous is not None else [],
            )
            snapshots[key] = snapshot
            if previous is None:
                if snapshot["ids"] or key in checkpoints:
                    raise ValueError("Existing component has no applied-version evidence")
            elif snapshot != previous["projection"]:
                raise ValueError("Component projection changed outside its applied-version owner")
            if previous is None or not current.covered_by(previous["content"]):
                selected.add(key)
        if not selected:
            return (0, 0, 0)
        upserts = [entry for entry in manifest.upserts if (entry.source, entry.path) in selected]
        deletions = [entry for entry in manifest.deletions if (entry.source, entry.path) in selected]
        application_manifest = replace(
            manifest, component_versions=None, upserts=upserts, deletions=deletions,
            stats=replace(
                manifest.stats, files_changed=len(upserts), files_deleted=len(deletions),
                files_unchanged=manifest.stats.files_unchanged + len(manifest.upserts) - len(upserts),
            ),
        )
        prepare_ingestion_indices(store, max_rows=0)
        prepared = PreparedApplication(
            application_manifest, scope=scope, config=config, paths=journal._paths,
            store=store, clients=clients, full=False,
        )
        with closing(journal._paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            _require_parent(conn, source, scope, expected_payload)
            conn.execute(
                "INSERT INTO component_application_attempts VALUES (?, ?, ?, ?, ?)",
                (family, source, scope, expected_payload, binding),
            )
        result = publish_prepared_manifest(
            manifest=application_manifest, full=False, store=store, prepared=prepared,
            application=partial(
                _apply_source_manifest, source, config=config, state=IndexState(),
                path_index=journal._paths, multi_store=store, multi_clients=clients,
            ),
        )
        # A returned callback cannot validate an interrupted native write.
        with domain.read(store.content_store.db):
            completed = {}
            for key in selected:
                projection = _snapshot(store, *key, snapshots[key]["ids"])
                if incoming[key].deleted and projection["ids"]:
                    raise ValueError("Component deletion did not remove its canonical content")
                completed[key] = json.dumps({
                    "protocol": 1, "scope": scope, "domain": binding, "policy": policy,
                    "content": asdict(incoming[key]), "projection": projection,
                })
            with closing(journal._paths._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                _require_parent(conn, source, scope, expected_payload)
                conn.executemany(
                    "INSERT INTO applied_components VALUES (?, ?, ?) "
                    "ON CONFLICT(source, path) DO UPDATE SET payload = excluded.payload",
                    [(src, path, payload) for (src, path), payload in completed.items()],
                )
                conn.execute(
                    "DELETE FROM component_application_attempts WHERE family = ?", (family,),
                )
        return result
