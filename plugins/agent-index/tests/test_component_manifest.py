"""Ordered recovery retains evidence without authorizing unordered application."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from agent_index.indexing.component_manifest import ComponentVersion
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recover_source
from agent_index.indexing.state import IndexState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile


def component_manifest() -> CrawlManifest:
    return CrawlManifest(
        source="selected", accounting_family="sample:components", commit="poll-cursor",
        upserts=[FileEntry("items/1/body.md", "Fields", "markdown", "sample:components")],
        deletions=[DeletedFile("sample:components", "items/1/comments/2.md")],
        component_versions=[
            ComponentVersion("sample:components", "items/1/body.md", 7, "body/v1"),
            ComponentVersion("sample:components", "items/1/comments/2.md", 4, "comment/v1"),
        ],
    )


def test_component_journal_roundtrip_and_legacy_envelopes(tmp_path: Path) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = component_manifest()
    journal.save(manifest, scope="account/endpoint/query/representation", full=False)
    payload = journal.read_payload(manifest.source, "account/endpoint/query/representation")
    assert payload is not None
    raw = json.loads(payload)
    assert raw["version"] == 8
    reopened = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    assert reopened.load(manifest.source, "account/endpoint/query/representation") == (manifest, False)
    with pytest.raises(ValueError, match="configuration"):
        reopened.load(manifest.source, "other-account")
    # Ordered metadata cannot be smuggled into a legacy envelope.
    for version in (1, 3, 7):
        with pytest.raises(ValueError):
            journal.decode_pending(json.dumps(raw | {"version": version}), manifest.source)
    component_payloads: tuple[object, ...] = (None, [], {}, "unknown")
    for components in component_payloads:
        altered = raw | {"manifest": raw["manifest"] | {"component_versions": components}}
        with pytest.raises(ValueError):
            journal.decode_pending(json.dumps(altered), manifest.source)
    with pytest.raises(ValueError, match="full reconciliation"):
        journal.save(manifest, scope="different", full=True)
    assert journal.read_payload(manifest.source, "account/endpoint/query/representation") == payload

    legacy = replace(manifest, component_versions=None)
    legacy_data = journal.manifest_data(legacy)
    assert "component_versions" not in legacy_data
    assert "poll_refresh" not in legacy_data
    for version in (1, 3):
        value = legacy_data if version == 1 else (
            legacy_data | {"poll_refresh": {
                "retain": True, "paths": {"items/1/body.md": "a" * 32}, "history": {},
            }}
        )
        decoded = journal.decode_pending(
            json.dumps({"version": version, "full": False, "manifest": value}), manifest.source,
        )
        assert decoded is not None and decoded[0].component_versions is None


@pytest.mark.parametrize("retained", [False, True])
def test_recovery_refuses_components_before_prepare_publication_or_apply(
    tmp_path: Path, retained: bool,
) -> None:
    manifest = component_manifest()
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed("sample:legacy", "items/1.md", content_hash="retained", chunk_count=3)
    journal = PendingCrawls(paths)
    if retained:
        journal.save(manifest, scope="scope", full=False)
    state = IndexState()
    state.mark_source_indexed(manifest.source, "poll-cursor", 3)
    state.sources[manifest.source].acquisition_scope = "scope"
    state_file = tmp_path / "state.json"
    state.save(state_file)
    before_state = state_file.read_bytes()
    before_paths = paths.get_all_entries()
    callbacks = {name: Mock() for name in ("prepare", "publication", "apply")}
    acquire = Mock(return_value=manifest)
    with pytest.raises(ValueError, match="Ordered component application is not supported"):
        recover_source(
            manifest.source, scope="scope", state=state, state_file=state_file, paths=paths,
            full=False, acquire=acquire, family="sample:components", **callbacks,
        )
    assert all(not callback.called for callback in callbacks.values())
    assert acquire.call_count == (0 if retained else 1)
    assert state_file.read_bytes() == before_state
    assert state.sources[manifest.source].last_commit == "poll-cursor"
    assert paths.get_all_entries() == before_paths
    assert journal.load(manifest.source, "scope") == (manifest, False)
    before = journal.read_payload(manifest.source, "scope")
    with pytest.raises(ValueError, match="mandatory replay"):
        journal.discard_acquisition(
            manifest.source, scope="scope", expected_payload=cast(str, before),
            family="sample:components",
        )
    assert journal.read_payload(manifest.source, "scope") == before


def test_component_compare_and_save_validates_before_replacing_parent(tmp_path: Path) -> None:
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    manifest = component_manifest()
    payload = json.dumps({
        "version": 8, "full": False, "manifest": journal.manifest_data(manifest),
    })
    journal.compare_and_save(
        manifest.source, scope="scope", payload=payload, expected=None, family="sample:components",
    )
    assert journal.load(manifest.source, "scope") == (manifest, False)
    raw = json.loads(payload)
    raw["manifest"]["component_versions"][0]["version"] = True
    with pytest.raises(ValueError, match="positive integer"):
        journal.compare_and_save(
            manifest.source, scope="scope", payload=json.dumps(raw), expected=payload,
            family="sample:components",
        )
    assert journal.read_payload(manifest.source, "scope") == payload
    with closing(journal._paths._connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pending_crawls").fetchone()[0] == 1


def test_direct_application_boundaries_refuse_before_touching_dependencies() -> None:
    from agent_index.indexing.engine import _apply_source_manifest
    from agent_index.indexing.prepared_application import PreparedApplication, publish_prepared_manifest

    manifest = component_manifest()
    untouched = Mock()
    with pytest.raises(ValueError, match="Ordered component application"):
        _apply_source_manifest(
            manifest.source, manifest=manifest, config=untouched, state=untouched,
            path_index=untouched, multi_store=untouched, multi_clients={}, full=False,
        )
    with pytest.raises(ValueError, match="Ordered component application"):
        PreparedApplication(
            manifest, scope="scope", config=untouched, paths=untouched,
            store=untouched, clients={}, full=False,
        )
    with pytest.raises(ValueError, match="Ordered component application"):
        publish_prepared_manifest(
            manifest=manifest, full=False, application=untouched, store=untouched, prepared=untouched,
        )
    assert untouched.mock_calls == []


@pytest.mark.parametrize("version", [1, 3, 7])
def test_component_parent_cannot_downgrade_into_legacy_recovery(
    tmp_path: Path, version: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = component_manifest()
    journal.save(manifest, scope="scope", full=False)
    original = journal.read_payload(manifest.source, "scope")
    assert original is not None
    legacy = journal.manifest_data(replace(manifest, component_versions=None))
    if version == 3:
        legacy["poll_refresh"] = {
            "retain": True, "paths": {"items/1/body.md": "a" * 32}, "history": {},
        }
    data = {"version": version, "full": False, "manifest": legacy}
    if version == 7:
        data["page_protocol"] = "incremental"
    decode = json.loads

    def decode_without_writer_lock(payload: str) -> object:
        if payload == original:
            with closing(paths._connect()) as probe:
                probe.execute("PRAGMA busy_timeout = 0")
                probe.execute("BEGIN IMMEDIATE")
                probe.rollback()
        return decode(payload)

    with monkeypatch.context() as patch:
        patch.setattr("agent_index.indexing.pending_crawl.json.loads", decode_without_writer_lock)
        with pytest.raises(ValueError, match="cannot be downgraded"):
            journal.compare_and_save(
                manifest.source, scope="scope", payload=json.dumps(data),
                expected=original, family="sample:components",
            )
    reopened = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    assert reopened.read_payload(manifest.source, "scope") == original
    with closing(paths._connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM poll_refresh").fetchone()[0] == 0
    untouched = Mock()
    state = IndexState()
    state_file = tmp_path / "state.json"
    state.save(state_file)
    before = state_file.read_bytes()
    with pytest.raises(ValueError, match="Ordered component application"):
        recover_source(
            manifest.source, scope="scope", state=state, state_file=state_file, paths=paths,
            full=False, acquire=untouched, prepare=untouched, publication=untouched,
            apply=untouched, family="sample:components",
        )
    assert untouched.mock_calls == []
    assert state_file.read_bytes() == before
    assert reopened.read_payload(manifest.source, "scope") == original


@pytest.mark.parametrize("with_manifest", [False, True])
def test_component_parent_cannot_be_cleared_before_ordered_completion(
    tmp_path: Path, with_manifest: bool,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = component_manifest()
    journal.save(manifest, scope="scope", full=False)
    original = journal.read_payload(manifest.source, "scope")
    with pytest.raises(ValueError, match="completion is not supported"):
        if with_manifest:
            journal.clear(
                manifest.source, manifest=manifest, scope="scope", expected_payload=original,
            )
        else:
            journal.clear(manifest.source)
    reopened = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    assert reopened.read_payload(manifest.source, "scope") == original
