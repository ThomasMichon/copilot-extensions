"""Exact acquisition qualification precedes every subordinate cache mutation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_index.index_config import IndexConfig
from agent_index.indexing.component_manifest import ComponentVersion
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.prepared_application import PreparedApplication
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.sources.base import FileEntry


@pytest.fixture
def acquired_parent(tmp_path: Path) -> tuple[PathIndex, PendingCrawls, CrawlManifest, str]:
    paths = PathIndex(tmp_path / "paths.db")
    pending = PendingCrawls(paths)
    manifest = CrawlManifest(
        source="sample:repo", accounting_family="sample:repo", commit="pinned",
        upserts=[FileEntry(
            "one.md", "Useful text.", "markdown", "sample:repo",
            metadata={"a": 1, "z": [True, 1.0]},
        )],
        component_versions=[ComponentVersion("sample:repo", "one.md", 7, "body/v1")],
    )
    pending.save(manifest, scope="account", full=False)
    payload = pending.read_payload(manifest.source, "account")
    assert payload is not None
    return paths, pending, manifest, payload


@pytest.mark.parametrize(
    "mismatch", ["content", "version", "source", "mode", "scope", "payload", "missing"],
)
def test_parent_mismatch_refuses_before_creating_any_prepared_schema(
    acquired_parent: tuple[PathIndex, PendingCrawls, CrawlManifest, str], mismatch: str,
) -> None:
    paths, _pending, manifest, payload = acquired_parent
    supplied, scope, full = manifest, "account", False
    if mismatch == "content":
        supplied = replace(manifest, upserts=[replace(manifest.upserts[0], content="Different")])
    elif mismatch == "version":
        supplied = replace(
            manifest, component_versions=[ComponentVersion("sample:repo", "one.md", 8, "body/v1")],
        )
    elif mismatch == "source":
        supplied = replace(manifest, source="sample:other")
    elif mismatch == "mode":
        full = True
    elif mismatch == "scope":
        scope = "other-account"
    elif mismatch == "payload":
        payload += " "
    with closing(paths._connect()) as conn, conn:
        if mismatch == "missing":
            conn.execute("DELETE FROM pending_crawls")
        before = list(conn.iterdump())
    with pytest.raises(PendingCrawlConflict):
        PreparedJournal(
            paths, manifest.source, scope, payload, manifest=supplied, full=full,
        )
    with closing(paths._connect()) as conn:
        assert list(conn.iterdump()) == before


@pytest.mark.parametrize("invalid", ["full", "components", "envelope"])
def test_invalid_component_parent_refuses_without_derived_writes(
    acquired_parent: tuple[PathIndex, PendingCrawls, CrawlManifest, str], invalid: str,
) -> None:
    paths, _pending, manifest, payload = acquired_parent
    data = json.loads(payload)
    if invalid == "full":
        data["full"] = True
    elif invalid == "components":
        data["manifest"]["component_versions"] = None
    else:
        data["unexpected"] = True
    payload = json.dumps(data)
    with closing(paths._connect()) as conn, conn:
        conn.execute("UPDATE pending_crawls SET payload = ?", (payload,))
        before = list(conn.iterdump())
    with pytest.raises(ValueError):
        PreparedJournal(
            paths, manifest.source, "account", payload, manifest=manifest, full=False,
        )
    with closing(paths._connect()) as conn:
        assert list(conn.iterdump()) == before


def test_exact_component_parent_reuses_incarnation_but_cannot_complete_acquisition(
    acquired_parent: tuple[PathIndex, PendingCrawls, CrawlManifest, str],
) -> None:
    paths, pending, manifest, payload = acquired_parent
    first = PreparedJournal(
        paths, manifest.source, "account", payload, manifest=manifest, full=False,
    )
    first.put("vector", [("pair", b"fixture")])
    reordered = replace(manifest, upserts=[
        replace(manifest.upserts[0], metadata={"z": [True, 1.0], "a": 1}),
    ])
    reopened = PreparedJournal(
        paths, manifest.source, "account", payload, manifest=reordered, full=False,
    )
    assert reopened.generation == first.generation
    assert reopened.get("vector", ["pair"]) == {"pair": b"fixture"}
    with pytest.raises(ValueError, match="completion is not supported"):
        pending.clear(manifest.source, manifest=manifest, scope="account", expected_payload=payload)
    assert pending.load(manifest.source, "account") == (manifest, False)


def test_parent_changed_during_decode_is_rechecked_before_cache_creation(
    acquired_parent: tuple[PathIndex, PendingCrawls, CrawlManifest, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, pending, manifest, payload = acquired_parent
    decode = PendingCrawls.decode_pending

    def replace_parent(observed: str, source: str) -> tuple[CrawlManifest, bool] | None:
        result = decode(observed, source)
        with closing(paths._connect()) as conn, conn:
            conn.execute("UPDATE pending_crawls SET payload = ?", (payload + " ",))
        return result

    monkeypatch.setattr(PendingCrawls, "decode_pending", replace_parent)
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        PreparedJournal(
            paths, manifest.source, "account", payload, manifest=manifest, full=False,
        )
    assert pending.read_payload(manifest.source, "account") == payload + " "
    with closing(paths._connect()) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'prepared_crawl_%'",
        ).fetchall() == []


def test_cache_creation_holds_parent_writer_exclusion(
    acquired_parent: tuple[PathIndex, PendingCrawls, CrawlManifest, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _pending, manifest, payload = acquired_parent
    connect = paths._connect
    blocked: list[bool] = []

    def guarded_connection() -> sqlite3.Connection:
        conn = connect()

        def trace(statement: str) -> None:
            if statement.startswith("CREATE TABLE") and not blocked:
                with closing(connect()) as competitor:
                    competitor.execute("PRAGMA busy_timeout = 0")
                    try:
                        competitor.execute("BEGIN IMMEDIATE")
                    except sqlite3.OperationalError as error:
                        blocked.append("locked" in str(error))
                    else:
                        blocked.append(False)
                        competitor.rollback()

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(paths, "_connect", guarded_connection)
    PreparedJournal(paths, manifest.source, "account", payload, manifest=manifest, full=False)
    assert blocked == [True]


@pytest.mark.parametrize(
    "mismatch", ["content", "mode", "stripped-components", "bool-int", "int-float"],
)
def test_wrong_acquired_application_cannot_reset_cache_or_touch_store(
    tmp_path: Path, mismatch: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    pending = PendingCrawls(paths)
    manifest = CrawlManifest(
        source="sample:repo", accounting_family="sample:repo", commit="pinned",
        upserts=[FileEntry("one.md", "Useful text.", "markdown", "sample:repo")],
    )
    if mismatch == "stripped-components":
        manifest.component_versions = [ComponentVersion("sample:repo", "one.md", 7, "body/v1")]
    if mismatch in ("bool-int", "int-float"):
        manifest.upserts = [replace(
            manifest.upserts[0], metadata={"flag": True if mismatch == "bool-int" else 1},
        )]
    pending.save(manifest, scope="account", full=False)
    payload = pending.read_payload(manifest.source, "account")
    assert payload is not None
    cache = PreparedJournal(paths, manifest.source, "account", payload)
    cache.put("batch", [("0", b"retained batch")])
    cache.put("vector", [("pair", b"retained vector")])
    with closing(paths._connect()) as conn:
        before = list(conn.iterdump())
    supplied = replace(manifest, component_versions=None)
    if mismatch == "content":
        supplied.upserts = [replace(manifest.upserts[0], content="Different input.")]
    elif mismatch in ("bool-int", "int-float"):
        supplied.upserts = [replace(
            manifest.upserts[0], metadata={"flag": 1 if mismatch == "bool-int" else 1.0},
        )]
        assert manifest == supplied
        assert file_fingerprint(manifest.upserts[0], None) != file_fingerprint(supplied.upserts[0], None)
    untouched = Mock()
    monkeypatch.setattr(paths, "get_entries", untouched)
    with pytest.raises(PendingCrawlConflict, match="does not match"):
        PreparedApplication(
            supplied, scope="account", config=IndexConfig(data_dir=tmp_path),
            paths=paths, store=untouched, clients={}, full=mismatch == "mode",
        )
    assert untouched.mock_calls == []
    with closing(paths._connect()) as conn:
        assert list(conn.iterdump()) == before
