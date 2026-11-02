from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import importlib.util
import os
import sqlite3
import subprocess
import sys
import threading

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.daemon_instances import (
    admit_daemon_instance,
    daemon_test_lifecycle,
    inspect_instances,
)

from agent_index.indexing import runner as runner_module
from agent_index.indexing.runner import TaskRunner, _machine_id
from agent_index.indexing.task_store import TaskStore


class _ObservedStore(TaskStore):
    def __init__(self, path):
        self.statement = ""
        self.entered = threading.Event()
        self.dequeued = threading.Event()
        self.reaped = threading.Event()
        super().__init__(path)

    def _connect(self):
        conn = super()._connect()

        def trace(sql):
            if self.statement and self.statement in " ".join(sql.split()):
                self.entered.set()

        conn.set_trace_callback(trace)
        return conn

    def dequeue_next(self):
        try:
            return super().dequeue_next()
        finally:
            self.dequeued.set()

    def reap_orphaned(self, *args):
        try:
            return super().reap_orphaned(*args)
        finally:
            self.reaped.set()


class _EventBus:
    def publish(self, _event, _payload):
        return None


@pytest.fixture
def server():
    if importlib.util.find_spec("fastapi") is None:
        pytest.skip("real ASGI service requires unavailable offline FastAPI dependencies")
    from agent_index import server

    return server


@asynccontextmanager
async def _runner_host(runner):
    with admit_daemon_instance(
        "agent-index", source=runner_module.__file__, purpose="production",
    ):
        try:
            await runner.start()
            yield
        finally:
            await runner.stop()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    with daemon_test_lifecycle(owner="index-native-settlement", registry_root=root) as env:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
        yield root


def _state(registry):
    records = [
        record for record in inspect_instances(registry)
        if record["service"] == "agent-index"
    ]
    assert len(records) == 1
    return records[0]["state"]


async def _until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(0.01)


async def _cancel_twice(task):
    task.cancel()
    await asyncio.sleep(0.03)
    task.cancel()
    await asyncio.sleep(0.03)
    assert not task.done()


async def _finish(task, cancelled):
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task


@pytest.mark.parametrize("cancelled", [False, True])
def test_service_guard_retains_blocked_dequeue_and_returns_claim(
    tmp_path, registry, server, cancelled,
):
    async def exercise():
        store = _ObservedStore(tmp_path / "tasks.db")
        queued = store.enqueue(source="native-proof")
        runner = TaskRunner(store, server._EventBus())
        app = server.build_app(passive=True)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        writer = None
        stopping = None
        try:
            await runner.start()
            app.state.task_store, app.state.task_runner = store, runner
            writer = sqlite3.connect(store._db_path)
            writer.execute("BEGIN IMMEDIATE")
            store.statement = "BEGIN IMMEDIATE"
            await _until(store.entered.is_set)
            assert not store.dequeued.is_set()
            stopping = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await _until(lambda: runner._shutdown)
            if cancelled:
                await _cancel_twice(stopping)
            assert not stopping.done()
            assert _state(registry) == "running"
            assert store.get_task(queued.id).status == "queued"
            writer.rollback()
            writer.close()
            writer = None
            await _finish(stopping, cancelled)
            assert store.dequeued.is_set()
            record = store.get_task(queued.id)
            assert record.status == "queued"
            assert record.worker_pid is None
            assert not runner._monitors
            assert app.state.task_runner is None
            assert _state(registry) == "finished"
        finally:
            if writer is not None:
                writer.rollback()
                writer.close()
            if stopping is not None:
                with suppress(asyncio.CancelledError):
                    await stopping
            else:
                await lifespan.__aexit__(None, None, None)
            await runner.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancelled", [False, True])
def test_startup_recovery_settles_before_cancellation_or_failure_unwinds(
    tmp_path, registry, server, monkeypatch, cancelled,
):
    class RecoveryBus(server._EventBus):
        def publish(self, event, payload):
            if not cancelled and event == "queue_update":
                raise RuntimeError("recovery publication refused")

    async def exercise():
        store = _ObservedStore(tmp_path / "tasks.db")
        queued = store.enqueue(source="orphan")
        store.dequeue_next()
        runner = TaskRunner(store, RecoveryBus())
        monkeypatch.setattr(server, "_create_task_runner", lambda: (store, runner))
        writer = sqlite3.connect(store._db_path)
        writer.execute("BEGIN IMMEDIATE")
        store.statement = "SET status = 'interrupted'"
        app = server.build_app()
        lifespan = app.router.lifespan_context(app)
        starting = asyncio.create_task(lifespan.__aenter__())
        try:
            await _until(store.entered.is_set)
            if cancelled:
                await _cancel_twice(starting)
            assert not starting.done()
            assert not store.reaped.is_set()
            assert _state(registry) == "running"
            writer.rollback()
            writer.close()
            writer = None
            await _finish(starting, cancelled)
            assert store.reaped.is_set()
            assert store.get_task(queued.id).status == "interrupted"
            assert runner._stop_task.done()
            assert not runner._monitors
            assert runner._task is None or runner._task.done()
            if not cancelled:
                await lifespan.__aexit__(None, None, None)
            assert _state(registry) == "finished"
        finally:
            if writer is not None:
                writer.rollback()
                writer.close()
            with suppress(asyncio.CancelledError):
                await starting
            await runner.stop()
            await lifespan.__aexit__(None, None, None)

    asyncio.run(exercise())


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("refused", [False, True])
def test_launch_registration_or_refusal_write_settles_before_guard_exit(
    tmp_path, registry, monkeypatch, cancelled, refused,
):
    async def exercise():
        store = _ObservedStore(tmp_path / "tasks.db")
        queued = store.enqueue(source="native-registration")
        runner = TaskRunner(store, _EventBus())
        writers = []
        children = []

        def spawn(_task_id):
            writer = sqlite3.connect(store._db_path, check_same_thread=False)
            writer.execute("BEGIN IMMEDIATE")
            writers.append(writer)
            if refused:
                raise OSError("positively refused test spawn")
            child = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **no_window_kwargs(),
            )
            children.append(child)
            return child

        monkeypatch.setattr(runner, "_spawn_worker", spawn)
        store.statement = "worker spawn failed" if refused else "SET worker_pid ="
        lifespan = _runner_host(runner)
        await lifespan.__aenter__()
        stopping = None
        try:
            await _until(store.entered.is_set)
            stopping = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await _until(lambda: runner._shutdown)
            if cancelled:
                await _cancel_twice(stopping)
            assert not stopping.done()
            assert _state(registry) == "running"
            for writer in writers:
                writer.rollback()
                writer.close()
            writers.clear()
            await _finish(stopping, cancelled)
            record = store.get_task(queued.id)
            if refused:
                assert record.status == "failed"
                assert record.worker_pid is None
            else:
                child, = children
                assert record.status == "processing"
                assert record.worker_pid == child.pid
                assert record.worker_host == _machine_id()
                assert child.poll() is None
                successor = TaskRunner(store, _EventBus())
                try:
                    await successor.start()
                    assert queued.id in successor._active_workers
                finally:
                    await successor.stop()
                assert child.poll() is None
                assert store.get_task(queued.id).worker_pid == child.pid
            assert not runner._monitors
            assert _state(registry) == "finished"
        finally:
            for writer in writers:
                writer.rollback()
                writer.close()
            if stopping is not None:
                with suppress(asyncio.CancelledError):
                    await stopping
            else:
                await lifespan.__aexit__(None, None, None)
            await runner.stop()
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)

    asyncio.run(exercise())


@pytest.mark.parametrize("failed", [False, True])
def test_stop_withdraws_unlaunched_source_lock_claim(tmp_path, registry, failed):
    async def exercise():
        store = TaskStore(tmp_path / "tasks.db")
        queued = store.enqueue(source="locked")
        runner = TaskRunner(store, _EventBus())
        lock = asyncio.Lock()
        await lock.acquire()

        async def acquire(_source):
            if failed:
                raise RuntimeError("source lock refused")
            await lock.acquire()
            return lock

        runner.set_source_lock_fn(acquire)
        try:
            await runner.start()
            if failed:
                await _until(runner._task.done)
                with pytest.raises(RuntimeError, match="source lock refused"):
                    await runner.stop()
            else:
                await _until(lambda: runner._source_lock_task is not None)
                await runner.stop()
            assert store.get_task(queued.id).status == "queued"
            assert store.get_task(queued.id).worker_pid is None
            assert lock.locked()
        finally:
            with suppress(RuntimeError):
                await runner.stop()
            lock.release()

    asyncio.run(exercise())


def test_startup_failure_settles_adopted_monitor_without_changing_worker_custody(
    tmp_path, registry,
):
    class FailingStore(_ObservedStore):
        def get_pending_count(self):
            super().get_pending_count()
            raise RuntimeError("startup queue read refused")

    async def exercise():
        store = FailingStore(tmp_path / "tasks.db")
        queued = store.enqueue(source="adopted")
        store.dequeue_next()
        store.set_worker(queued.id, os.getpid(), _machine_id(), "test")
        runner = TaskRunner(store, _EventBus())
        lifespan = _runner_host(runner)
        with pytest.raises(RuntimeError, match="startup queue read refused"):
            await lifespan.__aenter__()
        assert runner._stop_task.done()
        assert not runner._monitors
        assert not runner._active_workers
        record = store.get_task(queued.id)
        assert record.status == "processing"
        assert record.worker_pid == os.getpid()
        assert _state(registry) == "finished"

    asyncio.run(exercise())


def test_monitor_native_write_settles_before_guard_and_lock_release(
    tmp_path, registry,
):
    async def exercise():
        store = _ObservedStore(tmp_path / "tasks.db")
        queued = store.enqueue(source="post-index")
        store.dequeue_next()
        store.set_worker(queued.id, os.getpid(), _machine_id(), "test")
        runner = TaskRunner(store, _EventBus())
        native_finished = threading.Event()

        def post_index():
            try:
                store.update_status(queued.id, "complete")
            finally:
                native_finished.set()

        runner.set_post_index_fn(post_index)
        lifespan = _runner_host(runner)
        await lifespan.__aenter__()
        writer = None
        stopping = None
        lock = asyncio.Lock()
        await lock.acquire()
        try:
            runner._worker_locks[queued.id] = lock
            store.update_status(queued.id, "complete")
            writer = sqlite3.connect(store._db_path)
            writer.execute("BEGIN IMMEDIATE")
            store.statement = "SET status ="
            await _until(store.entered.is_set)
            stopping = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await _until(lambda: runner._shutdown)
            await _cancel_twice(stopping)
            assert not native_finished.is_set()
            assert lock.locked()
            assert _state(registry) == "running"
            writer.rollback()
            writer.close()
            writer = None
            await _finish(stopping, True)
            assert native_finished.is_set()
            assert not lock.locked()
            assert store.get_task(queued.id).status == "complete"
            assert _state(registry) == "finished"
        finally:
            if writer is not None:
                writer.rollback()
                writer.close()
            if stopping is not None:
                with suppress(asyncio.CancelledError):
                    await stopping
            else:
                await lifespan.__aexit__(None, None, None)
            await runner.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancelled", [False, True])
def test_runner_admission_retains_native_claim_until_existing_release(
    tmp_path, registry, cancelled,
):
    """Dependency-light native proof, independent of an ASGI host's dependencies."""
    async def exercise():
        store = _ObservedStore(tmp_path / "tasks.db")
        queued = store.enqueue(source="runner-native")
        runner = TaskRunner(store, _EventBus())
        writer = None
        stopping = None

        async def host():
            with admit_daemon_instance(
                "agent-index", source=runner_module.__file__, purpose="production",
            ):
                try:
                    await runner.start()
                    await asyncio.Event().wait()
                finally:
                    await runner.stop()

        hosting = asyncio.create_task(host())
        try:
            await _until(lambda: runner._task is not None)
            writer = sqlite3.connect(store._db_path)
            writer.execute("BEGIN IMMEDIATE")
            store.statement = "BEGIN IMMEDIATE"
            await _until(store.entered.is_set)
            hosting.cancel()
            await _until(lambda: runner._shutdown)
            stopping = hosting
            if cancelled:
                await _cancel_twice(stopping)
            assert not store.dequeued.is_set()
            assert not stopping.done()
            assert _state(registry) == "running"
            writer.rollback()
            writer.close()
            writer = None
            with pytest.raises(asyncio.CancelledError):
                await stopping
            assert store.dequeued.is_set()
            record = store.get_task(queued.id)
            assert record.status == "queued"
            assert record.worker_pid is None
            assert _state(registry) == "finished"
        finally:
            if writer is not None:
                writer.rollback()
                writer.close()
            if stopping is None:
                hosting.cancel()
            with suppress(asyncio.CancelledError):
                await hosting
            await runner.stop()

    asyncio.run(exercise())
