"""Real shared-engine hint acquisition, durable queue retry and scope refusal."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from test_consumer_runtime import Client
from test_github_hints import SOURCE, connector, hints, issue

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine, worker
from agent_index.indexing.change_hints_contract import execution_versions
from agent_index.indexing.change_hints import MetadataRevision, RefRange
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.indexing.task_store import TaskStore
from agent_index.sources.delta import SourceDelta
from agent_index.sources.base import FileEntry
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT


def forbidden(*args, **kwargs):
    pytest.fail("hinted round attempted storage, lifecycle or whole-corpus fallback")


@pytest.mark.parametrize("case", [
    "full", "wrong-source", "alias", "unconfigured", "duplicate-config", "unsupported",
    "module-only", "account", "family", "wrong-type",
])
def test_preflight_refuses_before_storage_or_clients(tmp_path, monkeypatch, case):
    config = IndexConfig(data_dir=tmp_path / "untouched")
    spec = engine.SourceSpec(SOURCE, type="github")
    selected = SOURCE
    full = False
    envelope = hints()

    def discover(*args, **kwargs):
        return SourceDelta()

    discover.change_hints_versions = (1,)
    provider = SimpleNamespace(
        source_name=SOURCE, hint_scope=(SOURCE, "123"), discover_hints=discover,
        recovery_identity=("fixture",),
    )
    specs = [spec]
    if case == "full":
        full = True
    elif case == "wrong-source":
        selected = None
    elif case == "alias":
        selected = SOURCE + ":issues"
        envelope = replace(envelope, source=selected)
    elif case == "unconfigured":
        specs = []
    elif case == "duplicate-config":
        specs.append(spec)
    elif case == "unsupported":
        provider.discover_hints = None
    elif case == "module-only":
        provider.change_hints_versions = (1,)
        provider.discover_hints = lambda *a, **k: forbidden()
    elif case == "account":
        envelope = replace(envelope, account_id="other")
    elif case == "family":
        envelope = replace(envelope, canonical_family="github:other/repo")
    elif case == "wrong-type":
        envelope = envelope.to_dict()
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *a, **k: provider)
    monkeypatch.setattr("agent_index.indexing.embedding_runtime.open_embedding_clients", forbidden)
    monkeypatch.setattr("agent_index.store.multi_model_store.MultiModelStore", forbidden)
    monkeypatch.setattr(engine, "_connector_kwargs", lambda _: {})
    with pytest.raises(ValueError):
        engine.run_reindex(
            source=selected, full=full, config=config, source_specs=specs, change_hints=envelope,
        )
    assert not config.data_dir.exists()


def test_cancelled_run_never_constructs_connector_or_opens_storage(tmp_path, monkeypatch):
    from agent_index.indexing.runner import IndexingCancelled

    def cancelled():
        raise IndexingCancelled("cancel")

    config = IndexConfig(data_dir=tmp_path / "untouched")
    monkeypatch.setattr("agent_index.sources.get_connector", forbidden)
    with pytest.raises(IndexingCancelled):
        engine.run_reindex(
            source=SOURCE, config=config, source_specs=[engine.SourceSpec(SOURCE)],
            progress_cb=SimpleNamespace(check_cancelled=cancelled), change_hints=hints(),
        )
    assert not config.data_dir.exists()


@pytest.mark.parametrize("delta", [
    SourceDelta(commit="new-poll"), SourceDelta(complete=False),
    SourceDelta(upserts=[FileEntry("one.md", "wrong family", "markdown", "other")]),
    {"invalid": "result"},
])
def test_hinted_delta_refusal_preserves_checkpoint(tmp_path, delta):
    def discover(*args, **kwargs):
        return delta

    discover.change_hints_versions = (1,)
    provider = SimpleNamespace(
        source_name=SOURCE, hint_scope=(SOURCE, "123"), discover_hints=discover,
        recovery_identity=("fixture",), list_paths=forbidden, discover_delta=forbidden,
    )
    state = IndexState(sources={SOURCE: SourceState(last_commit="prior")})
    state.sources[SOURCE].acquisition_scope = recovery_scope(SOURCE, provider)
    with pytest.raises(ValueError):
        engine._crawl_source(
            SOURCE, config=None, state=state, path_index=PathIndex(tmp_path / "paths.db"),
            full=False, connector=provider, change_hints=hints(),
        )
    assert state.sources[SOURCE].last_commit == "prior"


@pytest.mark.parametrize("corpus_size", [100, 10_000, 100_000])
def test_scoped_envelope_reaches_connector_without_unchanged_path_reads(
    tmp_path, monkeypatch, corpus_size,
):
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch([
        (SOURCE + ":issues", f"issues/{i}.md", "old", 1) for i in range(corpus_size)
    ])
    monkeypatch.setattr(paths, "get_paths_by_family", forbidden)
    observed = []
    envelope = hints(
        event_ids=("delivery",), ref_ranges=(RefRange("base", "target"),),
        metadata_revisions=(MetadataRevision("1", "revision"),),
    )

    def discover(value, prior, *, cancel_check):
        observed.append((value, prior))
        return SourceDelta(
            upserts=[FileEntry("issues/1.md", "changed", "markdown", SOURCE + ":issues")],
            commit=prior,
        )

    discover.change_hints_versions = (1,)
    provider = SimpleNamespace(
        source_name=SOURCE, hint_scope=(SOURCE, "123"), discover_hints=discover,
        recovery_identity=("fixture",), list_paths=forbidden, discover_delta=forbidden,
    )
    state = IndexState(sources={SOURCE: SourceState(last_commit="poll")})
    state.sources[SOURCE].acquisition_scope = recovery_scope(SOURCE, provider)
    result = engine._crawl_source(
        SOURCE, config=None, state=state, path_index=paths, full=False,
        connector=provider, change_hints=envelope,
    )
    assert observed == [(envelope, "poll")]
    assert observed[0][0] is envelope
    assert result.stats.files_scanned == result.stats.files_changed == 1
    assert result.commit == "poll" and not result.deletions


@pytest.fixture(params=[False, True], ids=["legacy", "source-aware"])
def native(request, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if request.param else "")
    config = IndexConfig(
        data_dir=tmp_path / "native", cluster_enabled=False,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2)},
    )
    calls = []
    item_updates = {}

    def handler(req):
        calls.append(req)
        if req.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if req.url.path.endswith("/comments"):
            return httpx.Response(200, json=[])
        number = int(req.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=issue(number, **item_updates))

    provider = connector(handler)
    constructions = []

    def construct(*args, **kwargs):
        constructions.append((args, kwargs))
        return provider

    monkeypatch.setattr("agent_index.sources.get_connector", construct)
    monkeypatch.setattr(provider, "list_paths", forbidden)
    monkeypatch.setattr(provider, "discover_changed", forbidden)
    monkeypatch.setattr(provider, "discover", forbidden)
    monkeypatch.setattr(provider, "current_commit", forbidden)
    monkeypatch.setattr(PathIndex, "get_paths_by_family", forbidden)
    monkeypatch.setattr(engine, "_connector_kwargs", lambda _: {})
    events = []
    clients = []
    callbacks = []

    class OwnedClient(Client):
        def embed_texts(self, texts):
            if callbacks:
                callbacks.pop(0)()
            return super().embed_texts(texts)

    def create(profile):
        client = OwnedClient(profile.model_id, events)
        clients.append(client)
        return client

    runtime = EmbeddingRuntime(
        create, lambda *a: events.append("ensure") or True,
        lambda *a: events.append("stop"),
    )
    spec = engine.SourceSpec(SOURCE, type="github")

    def run(**kwargs):
        return engine.run_reindex(
            config=config, source_specs=[spec], embedding_runtime=runtime, **kwargs,
        )

    run.change_hints_versions = (1,)
    run.item_updates = item_updates
    return config, run, calls, clients, callbacks, events, constructions


def test_actual_engine_noop_retains_polling_cursor_and_vectors(native):
    config, run, calls, clients, _, events, constructions = native
    assert execution_versions(engine.run_reindex) == [1]
    assert run(source=SOURCE, change_hints=hints())["chunks_total"] > 0
    state = IndexState.load(config.state_file)
    assert state.sources[SOURCE].last_commit is None
    state.sources[SOURCE].last_commit = "prior-poll"
    state.save(config.state_file)
    db = MultiModelStore(config.lance_dir)
    table = db.content_store._get_or_create_table()
    version = table.version
    assert run(source=SOURCE, change_hints=hints())["chunks_total"] == 0
    assert db.content_store._get_or_create_table().version == version
    assert not clients[-1].texts
    assert events.count("ensure") == events.count("stop") == 1
    assert IndexState.load(config.state_file).sources[SOURCE].last_commit == "prior-poll"
    assert len(constructions) == 2  # Preflight and crawl use the same instance each round.
    assert len(calls) == 5  # One account probe; two item/comment requests each round.
    run.item_updates["title"] = "Changed title"
    assert run(source=SOURCE, change_hints=hints())["chunks_total"] > 0
    assert db.content_store._get_or_create_table().version > version


def _worker_fixture(native, monkeypatch):
    config, run, *_ = native
    store = TaskStore(config.data_dir / "tasks.db")
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda _: None)
    monkeypatch.setattr("agent_index.indexing.priority.lower_current_process_priority", lambda _: None)
    return config, run, store


def test_worker_duplicate_union_and_active_trailing_burst_use_real_connector(native, monkeypatch):
    config, run, store = _worker_fixture(native, monkeypatch)
    task = store.enqueue(SOURCE, change_hints=hints(item_ids=("1",)))
    assert store.enqueue(SOURCE, change_hints=hints(item_ids=("2",))).id == task.id
    store.dequeue_next(accept_change_hints=True)
    trailing = []

    def burst():
        trailing.append(store.enqueue(SOURCE, change_hints=hints(item_ids=("3",))))
        assert store.enqueue(SOURCE, change_hints=hints(item_ids=("3",))).id == trailing[0].id

    native[4].append(burst)
    assert worker.run_worker(task.id, config=config, indexer=run) == 0
    assert store.get_task(task.id).status == "complete"
    next_task = store.dequeue_next(accept_change_hints=True)
    assert next_task.id == trailing[0].id != task.id
    assert next_task.change_hints.item_ids == ("3",)
    assert worker.run_worker(next_task.id, config=config, indexer=run) == 0
    fetched = [req.url.path for req in native[2] if req.url.path.endswith(("/1", "/2", "/3"))]
    assert fetched == [f"/repos/acme/widgets/issues/{i}" for i in (1, 2, 3)]


def test_worker_failure_restart_replays_pending_then_reacquires_exact_hints(native, monkeypatch):
    config, run, store = _worker_fixture(native, monkeypatch)
    task = store.enqueue(SOURCE, change_hints=hints())
    store.dequeue_next(accept_change_hints=True)

    def fail():
        raise RuntimeError("interrupted embed")

    native[4].append(fail)
    assert worker.run_worker(task.id, config=config, indexer=run) == 1
    assert store.get_task(task.id).status == "failed"  # Never terminal PARTIAL.
    assert store.get_task(task.id).change_hints == hints()
    assert SOURCE not in IndexState.load(config.state_file).sources
    restarted = TaskStore(config.data_dir / "tasks.db")
    assert restarted.retry(task.id).change_hints == hints()
    restarted.dequeue_next(accept_change_hints=True)
    assert worker.run_worker(task.id, config=config, indexer=run) == 0
    assert restarted.get_task(task.id).status == "complete"
    assert IndexState.load(config.state_file).sources[SOURCE].last_commit is None
    assert len(native[2]) == 5  # Failed acquisition and fresh retry, not history.
