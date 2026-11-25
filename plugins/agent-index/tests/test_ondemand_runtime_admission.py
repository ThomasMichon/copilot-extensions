"""On-demand admission precedes process effects and retains native cleanup custody."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import partial
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError

from agent_index import index_command, sources
from agent_index.engine import lifecycle
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine, worker
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.source_ownership import source_writer
from agent_index.indexing.task_store import TaskStatus, TaskStore
from test_consumer_runtime import Client, Connector


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    pytest.fail("Failed on-demand admission must not create an engine or retry")


@pytest.mark.parametrize("mode", ["reachable", "external", "systemd"])
def test_non_subprocess_paths_do_not_require_uv(
    monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from agent_index.engine.client import EngineUnavailableError

    monkeypatch.setattr(lifecycle, "resolve_runtime_python", forbidden)
    monkeypatch.setattr(lifecycle, "_reachable", lambda client: mode == "reachable")
    monkeypatch.setattr(lifecycle, "_systemctl", lambda *args: True)
    monkeypatch.setattr(lifecycle, "_await_reachable", lambda *args: True)
    profile = ModelProfile(
        "code", "fixture", engine_mode="subprocess" if mode == "reachable" else mode,
        systemd_unit="fixture.service",
    )
    if mode == "external":
        with pytest.raises(EngineUnavailableError, match="external"):
            lifecycle.ensure_engine(profile, cast(Any, object()))
    else:
        assert lifecycle.ensure_engine(profile, cast(Any, object())) is (mode == "systemd")


@pytest.mark.parametrize("failure", ["missing", "mismatch", "timeout", "output", "decode", "cleanup"])
def test_refusal_precedes_root_log_and_process_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    process: Any = SimpleNamespace(pid=123, returncode=None)
    job, resource = object(), object()
    errors = {
        "missing": FileNotFoundError("UV is unavailable"),
        "mismatch": RuntimeError("UV selected another runtime"),
        "timeout": subprocess.TimeoutExpired(["uv"], 10),
        "output": OutputLimitExceeded("stdout", 65536),
        "decode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
        "cleanup": OwnedCleanupError(
            process, job, cleanup_resources=(("output", resource),),
        ),
    }
    original = errors[failure]
    root = tmp_path / "not-created"
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(root))
    monkeypatch.setenv("AGENT_INDEX_UV", str(tmp_path / "bound uv"))
    monkeypatch.setattr(lifecycle, "_reachable", lambda client: False)
    monkeypatch.setattr(lifecycle, "spawn_daemon", forbidden)
    calls = []

    def refuse(python: str, *, uv: str | None) -> str:
        calls.append((python, uv))
        raise original

    monkeypatch.setattr(lifecycle, "resolve_runtime_python", refuse)
    with pytest.raises(type(original)) as caught:
        lifecycle.ensure_engine(
            ModelProfile("code", "fixture", engine_mode="subprocess"), cast(Any, object()),
        )
    assert caught.value is original
    assert calls == [(sys.executable, str(tmp_path / "bound uv"))]
    assert not root.exists() and not lifecycle._spawned
    if isinstance(original, OwnedCleanupError):
        assert original.process is process and original.job is job
        assert original.cleanup_resources == (("output", resource),)


@pytest.mark.parametrize("entry", ["library", "cli", "worker"])
def test_real_pipeline_does_not_serialize_unconfirmed_uv_custody(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entry: str,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(
        data_dir=tmp_path / "data", cluster_enabled=False,
        model_profiles={"code": ModelProfile("code", "fixture", dim=2, engine_mode="subprocess")},
    )
    connectors = {name: Connector(f"sample:{name}") for name in ("first", "second")}
    monkeypatch.setattr(sources, "_CONNECTORS", dict(sources._CONNECTORS))
    sources.register_connector("sample", cast(Any, lambda name, **kwargs: connectors[name]))
    monkeypatch.setattr(engine, "_connector_kwargs", lambda spec: {"name": spec.name.split(":")[1]})
    monkeypatch.setattr(lifecycle, "_reachable", lambda client: False)
    monkeypatch.setattr(lifecycle, "spawn_daemon", forbidden)
    process: Any = SimpleNamespace(pid=123, returncode=None)
    job, resource = object(), object()
    original = OwnedCleanupError(
        process, job, cleanup_resources=(("output", resource),), cleanup_retry=forbidden,
    )
    calls = []

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        calls.append("lookup")
        raise original

    monkeypatch.setattr(lifecycle, "resolve_runtime_python", refuse)
    events: list[str] = []
    clients: list[Client] = []

    def create(profile: ModelProfile) -> Client:
        client = Client(profile.model_id, events)
        clients.append(client)
        return client

    runtime = EmbeddingRuntime(create, lifecycle.ensure_engine, forbidden)
    run = partial(
        engine.run_reindex, config=config, embedding_runtime=runtime,
        source_specs=[engine.SourceSpec(c.source_name, type="sample") for c in connectors.values()],
    )
    store = TaskStore(config.data_dir / "tasks.db")
    task = None
    invoke: Callable[[], object]
    if entry == "cli":
        monkeypatch.setattr(index_command, "selected_engine", lambda: run)
        invoke = partial(
            index_command.run_index_command, argparse.Namespace(full=False, source=None),
            forbidden, forbidden,
        )
    elif entry == "worker":
        from agent_index.indexing import priority

        monkeypatch.setattr(priority, "lower_current_process_priority", lambda value: None)
        monkeypatch.setattr(worker, "_install_signal_handlers", lambda cancelled: None)
        task = store.enqueue(source="all", full=False)
        store.dequeue_next()
        invoke = partial(worker.run_worker, task.id, config=config, indexer=run)
    else:
        invoke = run
    with pytest.raises(OwnedCleanupError) as caught:
        invoke()
    assert caught.value is original
    assert original.process is process and original.job is job
    assert original.cleanup_resources == (("output", resource),)
    assert calls == ["lookup"] and not lifecycle._spawned
    assert clients and not clients[0].texts and events == ["close:code"]
    assert not connectors["second"].observed
    with source_writer(config.data_dir, allow_reentry=False):
        pass
    if task is not None:
        record = store.get_task(task.id)
        assert record is not None and record.status == TaskStatus.FAILED
        assert record.result_stats is None
