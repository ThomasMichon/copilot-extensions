"""Single/multi-model consumers share one pipeline and worker ownership path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import numpy as np
import pytest

from agent_index.engine.client import EngineClient
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine, worker
from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.embedding_runtime import EmbeddingRuntime, open_embedding_clients
from agent_index.indexing.state import IndexState
from agent_index.indexing.task_store import TaskStore
from agent_index.sources import register_connector
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import SourceDelta
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store

initialize_store = cast(Callable[[IndexConfig], MultiModelStore], initialize_source_store)


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("consumer used ambient defaults instead of its explicit selection")


class Client(EngineClient):
    def __init__(self, model: str, events: list[str]) -> None:
        self.model_id = model
        self.events = events
        self.texts: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> np.ndarray[Any, Any]:
        self.texts.append(texts)
        return np.array([[len(text), 1] for text in texts], dtype=np.float32)

    def close(self) -> None:
        self.events.append(f"close:{self.model_id}")


class Connector:
    def __init__(self, source: str) -> None:
        self.source_name = source
        self.recovery_identity = ("https://example.test/feed", "account-1")
        self.cursor = "first"
        self.entries = [FileEntry("one.md", "# Example\n\nUseful text.", "markdown", source)]
        self.observed: list[str | None] = []

    def discover_delta(self, previous: str | None, **kwargs: object) -> SourceDelta:
        self.observed.append(previous)
        return SourceDelta(
            commit=self.cursor, upserts=self.entries if previous != self.cursor else [],
        )

    def current_commit(self) -> str:
        return self.cursor

    def discover(self, **kwargs: object) -> list[FileEntry]:
        return self.entries


@pytest.mark.parametrize("models", [1, 2])
def test_injected_consumer_uses_shared_pipeline_and_reuses_metadata_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, models: int,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    profiles = {
        name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
        for name in ("a", "b")[:models]
    }
    config = IndexConfig(data_dir=tmp_path / "consumer", model_profiles=profiles, cluster_enabled=False)
    connector = Connector("sample:repo")
    from agent_index import sources

    monkeypatch.setattr(sources, "_CONNECTORS", dict(sources._CONNECTORS))
    register_connector("sample", cast(Any, lambda **kwargs: connector))
    monkeypatch.setattr(engine, "configured_source_specs", forbidden)
    monkeypatch.setattr("agent_index.indexing.embedding_runtime.default_embedding_runtime", forbidden)
    events: list[str] = []
    clients: list[Client] = []

    def create(profile: ModelProfile) -> Client:
        client = Client(profile.model_id, events)
        clients.append(client)
        return client

    def ensure(profile: ModelProfile, client: EngineClient) -> bool:
        assert client.model_id == profile.model_id
        events.append(f"ensure:{profile.model_id}")
        return True

    runtime = EmbeddingRuntime(create, ensure, lambda profile: events.append(f"stop:{profile.model_id}"))

    def run(**kwargs: Any) -> dict[str, object]:
        return engine.run_reindex(
            config=config, source_specs=[engine.SourceSpec("sample:repo", type="sample")],
            embedding_runtime=runtime, **kwargs,
        )

    first = run(source="sample:repo")
    assert "sources_failed" not in first and first["files_crawled"] == 1
    assert connector.observed == [None]
    assert events.count("ensure:a") == events.count("stop:a") == 1
    assert all(len(client.texts) == 1 for client in clients)
    previous_events = list(events)
    assert run()["chunks_total"] == 0
    assert events[len(previous_events):] == [f"close:{name}" for name in reversed(profiles)]
    connector.entries = [replace(connector.entries[0], metadata={"state": "closed"})]
    connector.cursor = "second"
    assert "sources_failed" not in run()
    assert all(not client.texts for client in clients[models:])
    store = MultiModelStore(config.lance_dir)
    assert store.get_document(connector.source_name, "one.md").metadata == {"state": "closed"}
    assert IndexState.load(config.state_file).sources[connector.source_name].last_commit == "second"

    # Full reconciliation preserves registered consumer schemes in both GC paths.
    monkeypatch.setattr(MultiModelStore, "compact", lambda _self: {})
    assert "sources_failed" not in run(full=True)
    assert MultiModelStore(config.lance_dir).get_document(connector.source_name, "one.md")


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("manage", [False, True])
def test_readiness_is_scoped_to_pending_model_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool, manage: bool,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    config = IndexConfig(
        data_dir=tmp_path, cluster_enabled=False, stream_batch_size=1,
        model_profiles={
            name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
            for name in ("a", "b")
        },
    )
    connector = Connector("sample:repo")
    connector.entries = [
        FileEntry(f"{name}.md", f"# {name}\n\nUseful {name} text.", "markdown", connector.source_name)
        for name in ("one", "two", "retained")
    ]
    from agent_index import sources

    monkeypatch.setattr(sources, "_CONNECTORS", dict(sources._CONNECTORS))
    register_connector("sample", cast(Any, lambda **kwargs: connector))
    events: list[str] = []
    clients: dict[str, Client] = {}

    def create(profile: ModelProfile) -> Client:
        client = Client(profile.model_id, events)
        clients[profile.model_id] = client
        return client

    def ensure(profile: ModelProfile, _client: EngineClient) -> bool:
        events.append(f"ensure:{profile.model_id}")
        return True

    runtime = EmbeddingRuntime(
        create, ensure,
        lambda profile: events.append(f"stop:{profile.model_id}"),
        manage_engines=manage,
    )

    def run() -> None:
        result = engine.run_reindex(
            config=config, source=connector.source_name,
            source_specs=[engine.SourceSpec(connector.source_name, type="sample")],
            embedding_runtime=runtime,
        )
        assert "sources_failed" not in result

    run()
    config.model_profiles["c"] = ModelProfile("c", "fixture", dim=2, table_name="vectors_c")
    events.clear()
    run()
    assert not any(event.startswith(("ensure:", "stop:")) for event in events)
    assert all(not client.texts for client in clients.values())

    connector.entries = connector.entries[:2]
    connector.cursor = "selected-pairs"
    events.clear()
    run()
    assert [event for event in events if event.startswith("ensure:")] == (
        ["ensure:c"] if manage else []
    )
    assert [event for event in events if event.startswith("stop:")] == (
        ["stop:c"] if manage else []
    )
    assert not clients["a"].texts and not clients["b"].texts
    assert [len(batch) for batch in clients["c"].texts] == [1, 1]

    store = initialize_store(config) if selected else MultiModelStore(config.lance_dir)
    for profile in config.model_profiles.values():
        store.register_model(profile)
    ids = {
        path: store.content_store.get_chunk_ids_by_file(connector.source_name, f"{path}.md")
        for path in ("one", "two", "retained")
    }
    vectors = store.get_vector_table("c").get_vectors(
        [chunk_id for values in ids.values() for chunk_id in values], strict=True,
    )
    assert set(vectors) == set(ids["one"]) | set(ids["two"])
    assert not set(vectors).intersection(ids["retained"])


def test_client_construction_and_close_failures_keep_cleanup_owned() -> None:
    events: list[str] = []
    profiles = {name: ModelProfile(name, "fixture") for name in ("a", "b")}
    store = cast(MultiModelStore, SimpleNamespace(register_model=lambda _profile: None))

    def create(profile: ModelProfile) -> Client:
        if profile.model_id == "b":
            raise RuntimeError("second factory failed")
        return Client("a", events)

    runtime = EmbeddingRuntime(create, forbidden, forbidden)
    with pytest.raises(RuntimeError, match="second factory failed"):
        open_embedding_clients(store, profiles, runtime)
    assert events == ["close:a"]

    class BadClose(Client):
        def close(self) -> None:
            super().close()
            raise RuntimeError("close failed")

    runtime = replace(runtime, create_client=lambda profile: BadClose(profile.model_id, events))
    _clients, cleanup = open_embedding_clients(store, profiles, runtime)
    with pytest.raises(RuntimeError, match="close failed"):
        cleanup.close()
    assert events == ["close:a", "close:b", "close:a"]


@pytest.mark.parametrize("manage, bad_close", [(False, False), (True, False), (True, True)])
def test_lifecycle_cleanup_on_source_failure_and_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manage: bool, bad_close: bool,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(
        data_dir=tmp_path, cluster_enabled=False,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2)},
    )
    events: list[str] = []

    class OwnedClient(Client):
        def close(self) -> None:
            super().close()
            if bad_close:
                raise RuntimeError("close failed")

    def ensure(_profile: ModelProfile, _client: EngineClient) -> bool:
        events.append("ensure")
        return True

    runtime = EmbeddingRuntime(
        lambda profile: OwnedClient(profile.model_id, events),
        ensure,
        lambda profile: events.append("stop"), manage_engines=manage,
    )

    def fail_source(*args: object, **kwargs: Any) -> NoReturn:
        kwargs["before_model_embed"]("a")
        raise RuntimeError("source failed")

    monkeypatch.setattr(engine, "_index_source", fail_source)
    kwargs: dict[str, Any] = {
        "config": config, "source_specs": [engine.SourceSpec("sample:repo", type="sample")],
        "source": "sample:repo", "embedding_runtime": runtime,
    }
    if bad_close:
        with pytest.raises(RuntimeError, match="close failed"):
            engine.run_reindex(**kwargs)
    else:
        failures = engine.run_reindex(**kwargs)["sources_failed"]
        assert isinstance(failures, list)
        assert failures[0]["source"] == "sample:repo"
    assert events == (["ensure", "close:a", "stop"] if manage else ["close:a"])


@pytest.mark.parametrize("capable", [False, True])
def test_worker_checks_exact_injected_callable_and_uses_injected_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capable: bool,
) -> None:
    config = IndexConfig(data_dir=tmp_path, indexer_nice=7)
    store = TaskStore(tmp_path / "tasks.db")
    hints = ChangeHints(source="sample:repo", canonical_family="sample:repo", account_id="one")
    task = store.enqueue(source="sample:repo", change_hints=hints)
    store.dequeue_next(accept_change_hints=True)
    monkeypatch.setattr("agent_index.index_config.IndexConfig", forbidden)
    monkeypatch.setattr(engine, "run_reindex", forbidden)
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda _cancelled: None)
    priorities: list[int] = []
    monkeypatch.setattr(
        "agent_index.indexing.priority.lower_current_process_priority", priorities.append,
    )
    calls: list[dict[str, object]] = []

    def indexer(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"chunks_total": 1, "custom_stat": 2}

    if capable:
        setattr(indexer, "change_hints_versions", (1,))
    assert worker.run_worker(task.id, config=config, indexer=indexer) == (0 if capable else 2)
    result = store.get_task(task.id)
    assert result is not None
    assert result.status == ("complete" if capable else "queued_hints")
    assert len(calls) == int(capable)
    assert priorities == ([7] if capable else [])
    if capable:
        assert calls[0]["change_hints"] == hints
        assert result.result_stats is not None
        assert result.result_stats["custom_stat"] == 2


@pytest.mark.parametrize("result, expected", [
    ({"sources_failed": [{"source": "sample:repo", "error": "failed"}]}, "partial"),
    ({}, "complete"),
])
def test_injected_worker_preserves_terminal_status_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict[str, object], expected: str,
) -> None:
    config = IndexConfig(data_dir=tmp_path, indexer_nice=0)
    store = TaskStore(tmp_path / "tasks.db")
    task = store.enqueue(source="sample:repo")
    store.dequeue_next()
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda _cancelled: None)
    monkeypatch.setattr("agent_index.indexing.priority.lower_current_process_priority", lambda _: None)

    def indexer(**kwargs: Any) -> dict[str, object]:
        kwargs["progress_cb"].phase("fixture", msg="consumer running")
        return result

    assert worker.run_worker(task.id, config=config, indexer=indexer) == 0
    stored = store.get_task(task.id)
    assert stored is not None and stored.status == expected
    assert worker.run_worker(task.id, config=config, indexer=forbidden) == 0


def test_worker_forwards_explicit_config_to_default_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = IndexConfig(data_dir=tmp_path, indexer_nice=0)
    store = TaskStore(tmp_path / "tasks.db")
    task = store.enqueue(source="sample:repo")
    store.dequeue_next()
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda _cancelled: None)
    monkeypatch.setattr("agent_index.indexing.priority.lower_current_process_priority", lambda _: None)
    observed: list[object] = []

    def default_indexer(**kwargs: object) -> dict[str, object]:
        observed.append(kwargs["config"])
        return {}

    monkeypatch.setattr(engine, "run_reindex", default_indexer)
    assert worker.run_worker(task.id, config=config) == 0
    assert observed == [config]
