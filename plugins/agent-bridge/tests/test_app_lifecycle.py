"""Admission follows actual writer/close retirement, not an async facade."""

import asyncio
from contextlib import asynccontextmanager
import os
import threading
from types import SimpleNamespace

import pytest

from agent_bridge import app as bridge_app
from agent_bridge import app_lifecycle
from agent_bridge.db import Database
from agent_procutil.daemon_instances import (
    DaemonLeasePolicy, daemon_test_lifecycle, inspect_instances,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
        reason="actual admission and writer require canonical containment",
    ),
]


@pytest.fixture
def admission(tmp_path, monkeypatch):
    registry = tmp_path / "instances"
    with daemon_test_lifecycle(
        owner="bridge-writer-test", registry_root=registry,
        lease=DaemonLeasePolicy(30, 60),
    ) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        yield registry


def state(registry):
    records = inspect_instances(registry)
    assert len(records) == 1
    return records[0]["state"]


def host_fixture(tmp_path, monkeypatch, *, blocked=False, startup_error=False, short_join=True):
    db = Database(tmp_path / "host.db")
    retiring, release, refused = threading.Event(), threading.Event(), threading.Event()
    if blocked:
        original = db._event_writer_loop

        def writer():
            try:
                original()
            finally:
                retiring.set()
                assert release.wait(20), "fixture writer was not released"

        monkeypatch.setattr(db, "_event_writer_loop", writer)
    original_close = db.close

    def close():
        try:
            return original_close()
        except RuntimeError:
            if app_lifecycle.writer_running(db):
                refused.set()
            raise

    monkeypatch.setattr(db, "close", close)
    app = SimpleNamespace(state=SimpleNamespace())

    @asynccontextmanager
    async def inner(selected):
        selected.state.db = db
        db.start_writer()
        if blocked and short_join:
            join = db._writer_thread.join
            monkeypatch.setattr(db._writer_thread, "join", lambda timeout=None: join(.02))
        if startup_error:
            raise RuntimeError("startup refused")
        yield

    monkeypatch.setattr(bridge_app, "_lifespan", inner)
    return app, db, retiring, release, refused


async def wait(event, seconds=3):
    assert await asyncio.to_thread(event.wait, seconds)


async def finish(task):
    return await asyncio.wait_for(asyncio.shield(task), 5)


async def test_real_writer_timeout_retains_admission_until_close_settles(
    tmp_path, monkeypatch, admission,
):
    app, db, retiring, release, refused = host_fixture(
        tmp_path, monkeypatch, blocked=True, short_join=False,
    )

    async def run():
        async with app_lifecycle.lifespan(app):
            assert app_lifecycle.writer_running(db)

    task = asyncio.create_task(run())
    try:
        await wait(retiring)
        await wait(refused, 7)  # Exercise the real five-second stop_writer join.
        assert app_lifecycle.writer_running(db)
        assert not task.done()
        assert state(admission) == "running"
    finally:
        release.set()
        await finish(task)
    assert not app_lifecycle.writer_running(db)
    assert state(admission) == "finished"
    db.close()


@pytest.mark.parametrize("phase", ["body", "close", "facade"])
async def test_cancelled_owner_retains_native_close_and_admission(
    tmp_path, monkeypatch, admission, phase,
):
    app, db, retiring, release, refused = host_fixture(tmp_path, monkeypatch, blocked=True)
    entered, stop = asyncio.Event(), asyncio.Event()
    owners = []
    loop = asyncio.get_running_loop()
    submit = loop.run_in_executor

    def run_in_executor(executor, function, *args):
        owner = submit(executor, function, *args)
        if function is app_lifecycle._close_database:
            owners.append(owner)
        return owner

    monkeypatch.setattr(loop, "run_in_executor", run_in_executor)

    async def run():
        async with app_lifecycle.lifespan(app):
            entered.set()
            await stop.wait()

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if phase == "body":
            task.cancel()
        else:
            stop.set()
        await wait(retiring)
        await wait(refused)
        if phase == "facade":
            assert len(owners) == 1
            owners[0].cancel()
            assert owners[0].done() and owners[0].cancelled()
        else:
            task.cancel()
        await asyncio.sleep(.05)
        task.cancel()  # Repeated cancellation still cannot release native custody.
        await asyncio.sleep(.05)
        assert not task.done()
        assert app_lifecycle.writer_running(db)
        assert state(admission) == "running"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await finish(task)
    assert not app_lifecycle.writer_running(db)
    assert state(admission) == "finished"
    db.close()


async def test_startup_failure_keeps_admission_until_created_writer_retires(
    tmp_path, monkeypatch, admission,
):
    app, db, retiring, release, refused = host_fixture(
        tmp_path, monkeypatch, blocked=True, startup_error=True,
    )

    async def run():
        async with app_lifecycle.lifespan(app):
            pytest.fail("startup must not yield")

    task = asyncio.create_task(run())
    try:
        await wait(retiring)
        await wait(refused)
        assert app_lifecycle.writer_running(db) and not task.done()
        assert state(admission) == "running"
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="startup refused"):
            await finish(task)
    assert not app_lifecycle.writer_running(db)
    assert state(admission) == "finished"
    db.close()


@pytest.mark.parametrize("historical_error", [False, True])
async def test_normal_close_and_dead_writer_error_do_not_hold_admission(
    tmp_path, monkeypatch, admission, caplog, historical_error,
):
    app, db, _, _, _ = host_fixture(tmp_path, monkeypatch)
    async with app_lifecycle.lifespan(app):
        assert app_lifecycle.writer_running(db)
        if historical_error:
            db._record_writer_error(RuntimeError("historical writer failure"))
    assert not app_lifecycle.writer_running(db)
    assert state(admission) == "finished"
    assert sum(
        record.message == "Failed to stop event writer cleanly" for record in caplog.records
    ) == int(historical_error)
    db.close()  # The original read-and-clear writer-error contract is preserved.


async def test_startup_failure_before_database_does_not_leak_admission(monkeypatch, admission):
    @asynccontextmanager
    async def inner(_app):
        raise RuntimeError("startup refused")
        yield

    monkeypatch.setattr(bridge_app, "_lifespan", inner)
    with pytest.raises(RuntimeError, match="startup refused"):
        async with app_lifecycle.lifespan(SimpleNamespace(state=SimpleNamespace())):
            pytest.fail("startup must not yield")
    assert state(admission) == "finished"
