"""The actual engine lifespan retains admitted native inference through teardown."""

import asyncio
import ast
from contextlib import asynccontextmanager, suppress
import logging
import os
import signal
import sys
import threading
import time
from types import ModuleType
import pytest
from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances

from test_engine_priority import APP_PATH, _load_module


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    with daemon_test_lifecycle(owner="engine-lifespan", registry_root=registry) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("AGENT_INDEX_ENGINE_IDLE_TIMEOUT", "0")
        yield _load_module(), registry


def state(registry):
    records = [
        record for record in inspect_instances(registry)
        if record["service"] == "agent-index-engine"
    ]
    assert len(records) == 1
    return records[0]["state"]


@pytest.mark.parametrize("stage", ["gpu", "response"])
@pytest.mark.parametrize("ending", [
    "cancel-body", "normal", "body-error", "cancel-close", "cancel-all",
    "body-error-cancel", "body-error-unload", "idle-error", "unload-error",
])
def test_lifespan_retains_real_admitted_inference(engine, monkeypatch, stage, ending):
    module, registry = engine
    entered, release, unloaded = threading.Event(), threading.Event(), threading.Event()
    errors, results = [], []
    original = RuntimeError("application failure")
    idle_error = RuntimeError("idle monitor failed")
    unload_error = RuntimeError("unload failed after retirement")
    if ending == "idle-error":
        async def fail_idle(_timeout):
            raise idle_error

        monkeypatch.setenv("AGENT_INDEX_ENGINE_IDLE_TIMEOUT", "60")
        monkeypatch.setattr(module, "_idle_monitor", fail_idle)

    class Vector(list):
        def tolist(self):
            if stage == "response":
                entered.set()
                assert release.wait(8)
            return list(self)

    class Pipeline:
        is_loaded = True

        def embed_query(self, text):
            if stage == "gpu":
                entered.set()
                assert release.wait(8)
            return Vector([0.25, 0.75])

        def unload(self):
            assert module._inflight == 0
            self.is_loaded = False
            unloaded.set()
            if ending in {"unload-error", "body-error-unload"}:
                raise unload_error

    module._pipeline = Pipeline()

    def infer():
        try:
            results.append(module.embed(module.EmbedRequest(text="native fixture")))
        except BaseException as error:
            errors.append(error)

    async def exercise():
        ready, leave = asyncio.Event(), asyncio.Event()

        async def host():
            async with module.app.router.lifespan_context(module.app):
                ready.set()
                await leave.wait()
                if ending.startswith("body-error"):
                    raise original

        hosting = asyncio.create_task(host())
        worker = threading.Thread(target=infer)
        try:
            await ready.wait()
            worker.start()
            await until(entered.is_set)
            if ending == "cancel-body":
                hosting.cancel()
            else:
                leave.set()
            await until(lambda: getattr(module, "_stopping", False) or hosting.done())
            if ending in {"cancel-close", "cancel-all", "body-error-cancel"}:
                for _ in range(3):
                    targets = (
                        asyncio.all_tasks() - {asyncio.current_task()}
                        if ending == "cancel-all" else {hosting}
                    )
                    for task in targets:
                        task.cancel()
                    await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            assert not hosting.done(), "lifespan returned before admitted native inference"
            assert module._inflight == 1
            assert not unloaded.is_set()
            assert state(registry) == "running"
            with pytest.raises(module.HTTPException) as refused:
                module.embed(module.EmbedRequest(text="refused after shutdown"))
            assert refused.value.status_code == 503
            assert module._inflight == 1
            release.set()
            expected = (
                original if ending.startswith("body-error")
                else idle_error if ending == "idle-error"
                else unload_error if ending == "unload-error"
                else asyncio.CancelledError() if ending.startswith("cancel")
                else None
            )
            if expected is None:
                await asyncio.wait_for(asyncio.shield(hosting), 5)
            else:
                with pytest.raises(type(expected)) as raised:
                    await asyncio.wait_for(asyncio.shield(hosting), 5)
                if not isinstance(expected, asyncio.CancelledError):
                    assert raised.value is expected
            assert unloaded.is_set()
            assert module._idle_task is None
            assert state(registry) == "finished"
            assert not errors
            assert results[0].vector == [0.25, 0.75]
        finally:
            release.set()
            leave.set()
            if worker.ident is not None:
                worker.join(5)
                assert not worker.is_alive()
            if not hosting.done():
                hosting.cancel()
            await asyncio.gather(hosting, return_exceptions=True)

    asyncio.run(exercise())


def test_startup_failure_settles_created_idle_task_and_model(engine, monkeypatch):
    module, registry = engine
    original = RuntimeError("startup logging failed")
    info = module.logger.info
    unloaded, tasks = [], []

    class Pipeline:
        def unload(self):
            unloaded.append(True)

    def log(message, *args, **kwargs):
        if message.startswith("Idle auto-stop enabled"):
            tasks.append(module._idle_task)
            raise original
        info(message, *args, **kwargs)

    module._pipeline = Pipeline()
    monkeypatch.setattr(module.logger, "info", log)
    monkeypatch.setenv("AGENT_INDEX_ENGINE_IDLE_TIMEOUT", "60")

    async def exercise():
        with pytest.raises(RuntimeError) as raised:
            await module.app.router.lifespan_context(module.app).__aenter__()
        assert raised.value is original
        assert unloaded == [True]
        assert len(tasks) == 1 and tasks[0].done()
        assert module._idle_task is None
        assert state(registry) == "finished"

    asyncio.run(exercise())


def test_idle_task_retirement_precedes_model_unload_and_admission(engine, monkeypatch):
    module, registry = engine

    async def exercise():
        entered, retiring, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        unloaded = []

        class Pipeline:
            def unload(self):
                unloaded.append(True)

        async def idle(_timeout):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                retiring.set()
                await release.wait()

        module._pipeline = Pipeline()
        monkeypatch.setattr(module, "_idle_monitor", idle)
        monkeypatch.setenv("AGENT_INDEX_ENGINE_IDLE_TIMEOUT", "60")
        lifespan = module.app.router.lifespan_context(module.app)
        await lifespan.__aenter__()
        await entered.wait()
        idle_task = module._idle_task
        closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await retiring.wait()
            for _ in range(3):
                closing.cancel()
                await asyncio.sleep(0.01)
            assert not idle_task.done()
            assert not closing.done() and not unloaded
            assert state(registry) == "running"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert idle_task.done() and unloaded == [True]
            assert module._idle_task is None
            assert state(registry) == "finished"
        finally:
            release.set()
            await asyncio.gather(closing, return_exceptions=True)

    asyncio.run(exercise())


def test_engine_retirement_does_not_close_the_embedding_loops_executor(engine):
    module, registry = engine
    entered, release = threading.Event(), threading.Event()
    unloaded = []

    class Pipeline:
        def unload(self):
            unloaded.append(True)

    module._pipeline = Pipeline()

    def unrelated():
        entered.set()
        assert release.wait(8)

    async def exercise():
        cohost = asyncio.get_running_loop().run_in_executor(None, unrelated)
        try:
            await until(entered.is_set)
            async with module.app.router.lifespan_context(module.app):
                pass
            assert unloaded == [True]
            assert state(registry) == "finished"
            assert not cohost.done()
        finally:
            release.set()
            await cohost

    asyncio.run(exercise())


def native_lifespan_module():
    """Exercise exact source owners without importing the optional HTTP framework."""
    names = {
        "_activity_begin", "_activity_end", "_is_idle", "_idle_timeout_seconds",
        "_idle_monitor", "lifespan", "_lifespan", "_settle_engine_future",
        "_unload_after_inference", "_shutdown_engine",
    }
    fields = {
        "_gpu_lock", "_pipeline", "_config", "_activity_lock", "_activity_changed",
        "_last_activity", "_inflight", "_stopping", "_idle_task",
    }
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    selected = ast.parse("from __future__ import annotations\n")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            selected.body.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in fields for target in node.targets
        ):
            selected.body.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in fields:
                selected.body.append(node)
    assert {
        node.name for node in selected.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } == names
    module = ModuleType("native_engine_lifespan")
    module.__dict__.update(
        __file__=str(APP_PATH), asyncio=asyncio, asynccontextmanager=asynccontextmanager,
        suppress=suppress, logging=logging, os=os, signal=signal, sys=sys,
        threading=threading, time=time, logger=logging.getLogger(__name__),
    )
    exec(compile(selected, str(APP_PATH), "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("ending", ["normal", "cancel", "error", "repeat", "all-tasks"])
@pytest.mark.parametrize("stage", ["gpu", "response"])
def test_native_source_lifespans_retain_real_worker(tmp_path, monkeypatch, ending, stage):
    module = native_lifespan_module()
    registry = tmp_path / "registry"
    entered, release, unloaded = threading.Event(), threading.Event(), threading.Event()
    original = RuntimeError("native source application failure")

    class Pipeline:
        def unload(self):
            assert module._inflight == 0
            unloaded.set()

    module._pipeline = Pipeline()

    def infer():
        module._activity_begin()
        try:
            with module._gpu_lock:
                if stage == "gpu":
                    entered.set()
                    assert release.wait(8)
            if stage == "response":
                entered.set()
                assert release.wait(8)
        finally:
            module._activity_end()

    async def exercise():
        ready, leave = asyncio.Event(), asyncio.Event()

        async def host():
            async with module.lifespan(None):
                ready.set()
                await leave.wait()
                if ending == "error":
                    raise original

        hosting = asyncio.create_task(host())
        worker = threading.Thread(target=infer)
        try:
            await ready.wait()
            worker.start()
            await until(entered.is_set)
            if ending == "cancel":
                hosting.cancel()
            else:
                leave.set()
            await until(lambda: module._stopping or hosting.done())
            if ending in {"repeat", "all-tasks"}:
                for _ in range(3):
                    targets = (
                        asyncio.all_tasks() - {asyncio.current_task()}
                        if ending == "all-tasks" else {hosting}
                    )
                    for task in targets:
                        task.cancel()
                    await asyncio.sleep(0.01)
            await asyncio.sleep(0.03)
            assert not hosting.done()
            assert module._inflight == 1 and not unloaded.is_set()
            assert state(registry) == "running"
            release.set()
            if ending == "normal":
                await hosting
            elif ending == "error":
                with pytest.raises(RuntimeError) as raised:
                    await hosting
                assert raised.value is original
            else:
                with pytest.raises(asyncio.CancelledError):
                    await hosting
            assert unloaded.is_set()
            assert state(registry) == "finished"
        finally:
            release.set()
            leave.set()
            if worker.ident is not None:
                worker.join(5)
                assert not worker.is_alive()
            if not hosting.done():
                hosting.cancel()
            await asyncio.gather(hosting, return_exceptions=True)

    with daemon_test_lifecycle(owner="native-engine-lifespan", registry_root=registry) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("AGENT_INDEX_ENGINE_IDLE_TIMEOUT", "0")
        asyncio.run(exercise())
