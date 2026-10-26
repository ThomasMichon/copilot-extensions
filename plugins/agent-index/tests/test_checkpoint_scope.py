"""Completed cursors remain bound to the same acquisition identity after restart."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_provider_delta import FAMILY, Receiver

from agent_index.indexing import engine
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.indexing.state import IndexState
from agent_index.sources.providers import CliSourceConnector, ProviderError, ProviderManifest
from agent_index.sources.base import FileEntry


def round_fixture(tmp_path, monkeypatch, *, source_aware=True, new=True):
    receiver = Receiver(tmp_path, new=new)
    paths = PathIndex(tmp_path / "paths.db")
    config = SimpleNamespace(
        state_file=tmp_path / "state.json", model_profiles={}, stream_batch_size=2,
    )
    context = SimpleNamespace(
        state=IndexState(), receiver=receiver, paths=paths, config=config,
        requested="forge", command_changed=False, config_scope="configuration",
        writes=[], deletes=[], fail_apply=False,
    )

    def connector(*args, **kwargs):
        receiver.save()
        command = (sys.executable, str(receiver.script))
        if context.command_changed:
            command = (sys.executable, "-u", str(receiver.script))
        return CliSourceConnector(context.requested, ProviderManifest("forge", command))

    def embed(entries, **kwargs):
        if context.fail_apply:
            raise RuntimeError("fixture apply failed")
        for entry in entries:
            context.writes.append((entry.source, entry.path))
            paths.mark_indexed(entry.source, entry.path, chunk_count=1)
        return len(entries), {(entry.source, entry.path) for entry in entries}

    def delete(source, path):
        context.deletes.append((source, path))
        return []

    monkeypatch.setattr("agent_index.sources.get_connector", connector)
    monkeypatch.setattr(engine, "selected_layout", lambda: object() if source_aware else None)
    monkeypatch.setattr(engine, "_embed_and_store_files", embed)
    store = SimpleNamespace(
        delete_stale_by_file=lambda *a, **k: 0, delete_by_file=delete,
    )

    def run(*, full=False):
        return engine._index_source(
            "alias", config=config, state=context.state, path_index=paths,
            multi_store=store, multi_clients={}, full=full, source_scope=context.config_scope,
        )

    context.run, context.connector = run, connector
    return context


@pytest.mark.parametrize("source_aware", [False, True])
@pytest.mark.parametrize("field", [
    "account", "target", "family", "command", "requested", "configuration", "capability",
])
def test_completed_checkpoint_refuses_retarget_before_new_acquisition(
    tmp_path, monkeypatch, source_aware, field,
):
    context = round_fixture(tmp_path, monkeypatch, source_aware=source_aware)
    context.run(full=True)
    assert PendingCrawls(context.paths).sources() == []
    context.state = IndexState.load(context.config.state_file)
    before = deepcopy(context.state)
    if field in {"account", "target", "family"}:
        context.receiver.config["metadata"]["source_delta"][field] += ":different"
    elif field == "command":
        context.command_changed = True
    elif field == "requested":
        context.requested = FAMILY
    elif field == "configuration":
        context.config_scope = "other-configuration"
    else:
        context.receiver.config["metadata"]["source_delta"]["versions"] = [2]
    context.receiver.config["delta"] = {
        "version": 1, "upserts": [], "deletions": [], "commit": "later", "complete": True,
    }
    previous_calls = len(context.receiver.calls())
    previous_effects = (list(context.writes), list(context.deletes))
    with pytest.raises(ValueError, match="full reindex"):
        context.run()
    assert context.receiver.calls()[previous_calls:] == [
        ["content-current-commit", "--source", context.requested],
    ]
    assert (context.writes, context.deletes) == previous_effects
    assert context.state == before == IndexState.load(context.config.state_file)
    assert PendingCrawls(context.paths).sources() == []


@pytest.mark.parametrize("new", [False, True])
@pytest.mark.parametrize("source_aware", [False, True])
def test_successful_full_transition_publishes_scope_with_cursor_after_restart(
    tmp_path, monkeypatch, new, source_aware,
):
    context = round_fixture(tmp_path, monkeypatch, new=new, source_aware=source_aware)
    context.run(full=True)
    old_scope = context.state.sources["alias"].acquisition_scope
    assert old_scope == recovery_scope(context.config_scope, context.connector())
    if new:
        context.receiver.config["metadata"]["source_delta"]["account"] = "principal-b"
    else:
        context.command_changed = True
    context.receiver.config["metadata"]["commit"] = "target-b"
    family = FAMILY if new else "forge"
    context.receiver.config["snapshot_entries"] = [{
        "source": family, "path": "older.md", "content": "older item on new target",
        "language": "markdown", "metadata": {"created": "2000-01-01"},
    }]
    context.run(full=True)
    checkpoint = IndexState.load(context.config.state_file).sources["alias"]
    assert checkpoint.last_commit == "target-b"
    assert checkpoint.acquisition_scope != old_scope
    assert checkpoint.acquisition_scope == recovery_scope(context.config_scope, context.connector())
    assert context.paths.get_entry(family, "old.md") is None
    assert context.paths.get_entry(family, "older.md") is not None
    context.state = IndexState.load(context.config.state_file)
    context.run()
    assert context.paths.get_entry(family, "older.md") is not None
    assert PendingCrawls(context.paths).sources() == []


@pytest.mark.parametrize("failure", ["acquisition", "apply", "save"])
def test_failed_full_transition_does_not_publish_new_checkpoint_identity(
    tmp_path, monkeypatch, failure,
):
    context = round_fixture(tmp_path, monkeypatch)
    context.run(full=True)
    previous = deepcopy(context.state)
    context.receiver.config["metadata"]["source_delta"]["target"] += "/other"
    context.receiver.config["metadata"]["commit"] = "new-target"
    if failure == "acquisition":
        context.receiver.config["echo_snapshot_scope"] = False
    elif failure == "apply":
        context.fail_apply = True
    else:
        def fail_save(path):
            raise OSError("fixture checkpoint save failed")

        monkeypatch.setattr(context.state, "save", fail_save)
    with pytest.raises((ProviderError, RuntimeError, OSError)):
        context.run(full=True)
    assert context.state.sources == previous.sources
    assert context.state.stored_families == previous.stored_families
    assert IndexState.load(context.config.state_file) == previous
    if failure != "acquisition":
        assert PendingCrawls(context.paths).sources() == ["alias"]
    context.fail_apply = False
    context.receiver.config["echo_snapshot_scope"] = True
    context.state = IndexState.load(context.config.state_file)
    context.run(full=True)
    checkpoint = IndexState.load(context.config.state_file).sources["alias"]
    assert checkpoint.last_commit == "new-target"
    assert checkpoint.acquisition_scope == recovery_scope(context.config_scope, context.connector())
    assert PendingCrawls(context.paths).sources() == []


def test_older_state_requires_full_then_admits_empty_null_cursor_checkpoint(tmp_path, monkeypatch):
    context = round_fixture(tmp_path, monkeypatch)
    context.config.state_file.write_text(json.dumps({
        "sources": {"alias": {"last_commit": "previous", "chunk_count": 0}},
        "stored_families": {"alias": FAMILY},
    }))
    context.state = IndexState.load(context.config.state_file)
    assert context.state.sources["alias"].acquisition_scope is None
    with pytest.raises(ValueError, match="full reindex"):
        context.run()
    assert context.receiver.calls() == [["content-current-commit", "--source", "forge"]]
    context.receiver.config["snapshot_entries"] = []
    context.receiver.config["metadata"]["commit"] = None
    context.run(full=True)
    context.state = IndexState.load(context.config.state_file)
    checkpoint = context.state.sources["alias"]
    assert checkpoint.last_commit is None
    assert checkpoint.acquisition_scope == recovery_scope(context.config_scope, context.connector())
    context.run()
    assert "--last-commit" not in context.receiver.calls()[-1]


def test_matching_preexisting_journal_can_bind_an_older_checkpoint(tmp_path, monkeypatch):
    context = round_fixture(tmp_path, monkeypatch)
    context.run(full=True)
    context.state.sources["alias"].acquisition_scope = None
    context.state.save(context.config.state_file)
    scope = recovery_scope(context.config_scope, context.connector())
    journal = PendingCrawls(context.paths)
    journal.save(
        CrawlManifest(
            source="alias", accounting_family=FAMILY, commit="admitted",
            upserts=[FileEntry("pending.md", "pinned", "markdown", FAMILY)],
        ),
        scope=scope, full=False,
    )
    context.state = IndexState.load(context.config.state_file)
    context.run()
    assert (FAMILY, "pending.md") in context.writes
    assert journal.sources() == []
    assert context.state.sources["alias"].acquisition_scope == scope
    assert context.receiver.calls()[-1] == [
        "content-discover-changed", "--source", "forge", "--last-commit", "admitted",
        "--delta-version", "1",
    ]


@pytest.mark.parametrize("value", ["", 3, [], {}])
def test_corrupt_persisted_checkpoint_scope_is_not_accepted(tmp_path, value):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"sources": {"alias": {"acquisition_scope": value}}}))
    with pytest.raises(ValueError, match="acquisition"):
        IndexState.load(path)
