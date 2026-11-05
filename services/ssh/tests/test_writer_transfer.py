"""Serialized activation, preserved state, and stale-generation write fencing."""
import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from services.ssh.tests.conftest import enrollment
else:
    from conftest import enrollment
from agent_ssh_service.config import Document, ServiceConfig
from agent_ssh_service.store import DirectoryStore
from agent_ssh_service.writer import FencedWriter
from fleet_manager.private_state import create_private_file
import os


@pytest.fixture
def writer() -> type[FencedWriter]:
    from agent_ssh_service.writer import FencedWriter

    return FencedWriter


def test_activation_fences_previous_writer_and_preserves_directory(
    config: ServiceConfig, writer: type[FencedWriter],
) -> None:
    first = writer(config, "generation-a")
    store_a = first.activate()
    before = store_a.snapshot()
    second = writer(config, "generation-b")
    store_b = second.activate()
    assert store_b.snapshot() == before
    with pytest.raises(ValueError, match="stale-service-writer"):
        store_a.snapshot()
    assert second.current() and not first.current()
    with pytest.raises(ValueError, match="managed-writer-requires-transfer-owner"):
        DirectoryStore(config)


@pytest.mark.parametrize("case", ["matching", "foreign", "query-failure"])
def test_ownership_probe_closes_real_retained_connections(
    config: ServiceConfig, writer: type[FencedWriter], monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    active = writer(config, "active-owner")
    active.activate()
    path = Path(config.to_dict()["data_dir"]) / "directory.sqlite3"
    if case == "query-failure":
        with closing(sqlite3.connect(path)) as db:
            db.execute("ALTER TABLE service_writer RENAME COLUMN owner TO unavailable_owner")
            db.commit()
    connect = sqlite3.connect
    retained: list[sqlite3.Connection] = []
    def capture(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        db = cast(sqlite3.Connection, connect(*args, **kwargs))
        retained.append(db)
        return db
    monkeypatch.setattr(sqlite3, "connect", capture)
    probe = writer(config, "foreign-owner" if case == "foreign" else "active-owner")
    for _ in range(3):
        assert probe.current() is (case == "matching")
    assert len(retained) == 3
    try:
        for db in retained:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                db.execute("SELECT 1")
    finally:
        for db in retained:
            db.close()


def test_invalid_configuration_activation_keeps_original_authority(
    config: ServiceConfig, writer: type[FencedWriter],
) -> None:
    first = writer(config, "generation-a")
    store = first.activate()
    document = config.to_dict()
    document["fleet"] = "different-fleet"
    other = type(config).from_dict(document)
    with pytest.raises(ValueError, match="store-owner-mismatch"):
        writer(other, "generation-b").activate()
    assert first.current()
    assert store.snapshot()["directory"]["revision"] == 1


@pytest.mark.parametrize("refusal", ["claim", "commit"])
def test_fresh_activation_refusal_does_not_poison_retry(
    config: ServiceConfig, writer: type[FencedWriter], refusal: str,
) -> None:
    calls = 0
    def selected() -> None:
        nonlocal calls
        calls += 1
        if calls == (1 if refusal == "claim" else 2):
            raise ValueError("fixture-selection-moved")
    path = Path(config.to_dict()["data_dir"]) / "directory.sqlite3"
    with pytest.raises(ValueError, match="selection-moved"):
        writer(config, "refused-owner", selected=selected).activate()
    assert not path.exists(), "only this failed fresh creation must be settled"
    valid = writer(config, "valid-owner")
    assert valid.activate().snapshot()["directory"]["revision"] == 1
    assert valid.current()


def test_existing_store_refusal_preserves_data_identity_and_owner(
    config: ServiceConfig, writer: type[FencedWriter],
) -> None:
    first = writer(config, "original")
    store = first.activate()
    store.enroll("principal-worker-a", enrollment())
    before = store.snapshot()
    path = Path(config.to_dict()["data_dir"]) / "directory.sqlite3"
    identity = path.stat()
    def refused() -> None:
        raise ValueError("fixture-selection-moved")
    with pytest.raises(ValueError, match="selection-moved"):
        writer(config, "refused", selected=refused).activate()
    assert path.stat().st_ino == identity.st_ino
    assert first.current() and store.snapshot() == before
    successor = writer(config, "successor")
    assert successor.activate().snapshot() == before
    with pytest.raises(ValueError, match="stale-service-writer"):
        store.snapshot()
    assert path.stat().st_ino == identity.st_ino


@pytest.mark.parametrize("foreign_schema", [False, True])
def test_existing_unowned_database_is_not_deleted_or_reinitialized(
    config: ServiceConfig, writer: type[FencedWriter], foreign_schema: bool,
) -> None:
    path = Path(config.to_dict()["data_dir"]) / "directory.sqlite3"
    os.close(create_private_file(path))
    if foreign_schema:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
            db.execute("INSERT INTO unrelated VALUES ('retained')")
    identity, content = path.stat(), path.read_bytes()
    with pytest.raises(sqlite3.Error, match="metadata"):
        writer(config, "not-an-owner").activate()
    assert path.stat().st_ino == identity.st_ino and path.read_bytes() == content
    if foreign_schema:
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT value FROM unrelated").fetchone() == ("retained",)


def test_fresh_cleanup_cannot_delete_committed_or_different_file(
    config: ServiceConfig, writer: type[FencedWriter], tmp_path: Path,
) -> None:
    owner = writer(config, "current-owner")
    store = owner.activate()
    before = store.snapshot()
    with owner.guard():
        store._discard_empty_creation(store.path.stat())
    assert owner.current() and store.snapshot() == before
    other = tmp_path / "other.sqlite3"
    os.close(create_private_file(other))
    with owner.guard():
        store._discard_empty_creation(other.stat())
    assert owner.current() and store.snapshot() == before
    assert other.exists()


def test_legacy_lifetime_lease_is_not_displaced(
    config: ServiceConfig, writer: type[FencedWriter],
) -> None:
    from single_instance_lease import SingleInstance

    lease = SingleInstance(config.to_dict()["data_dir"], service="agent-ssh-service")
    os.close(create_private_file(lease.lock_path))
    lease.acquire()
    try:
        with pytest.raises(RuntimeError, match="writer-guard-unavailable"):
            writer(config, "generation-a", timeout=0).activate()
    finally:
        lease.release()


def test_activation_waits_for_held_work_then_fences_queued_old_work(
    config: ServiceConfig, writer: type[FencedWriter],
) -> None:
    first = writer(config, "generation-a")
    store = first.activate()
    entered, finish, attempted = threading.Event(), threading.Event(), threading.Event()

    def held_work() -> tuple[Document, bool]:
        def checkpoint() -> None:
            entered.set()
            assert finish.wait(5)
        return store.enroll("principal-worker-a", enrollment(), checkpoint=checkpoint)

    def activate() -> DirectoryStore:
        attempted.set()
        return writer(config, "generation-b").activate()

    with ThreadPoolExecutor(max_workers=2) as workers:
        old = workers.submit(held_work)
        try:
            assert entered.wait(5)
            incoming = workers.submit(activate)
            assert attempted.wait(5)
            assert not incoming.done()
        finally:
            finish.set()
        expected, changed = old.result(timeout=5)
        assert changed
        current = incoming.result(timeout=5)
    assert current.snapshot() == expected
    with pytest.raises(ValueError, match="stale-service-writer"):
        store.enroll("principal-worker-b", enrollment(1))


def test_route_ticket_returned_after_fencing_is_not_used(
    config: ServiceConfig, writer: type[FencedWriter], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh_service.hosting import FencedConnector
    from fleet_manager.gateway_service_connector import ServiceConnector

    first = writer(config, "generation-a")
    first.activate()

    async def late_ticket(_self: Any, _session: Any) -> str:
        writer(config, "generation-b").activate()
        return "fixture-ticket"

    monkeypatch.setattr(ServiceConnector, "_admit", late_ticket)
    connector = object.__new__(FencedConnector)
    connector.writer = first
    with pytest.raises(ValueError, match="stale-service-writer"):
        asyncio.run(connector._admit(cast(Any, None)))


def test_stale_selection_and_relay_refuse_before_fence_mutation(
    config: ServiceConfig, writer: type[FencedWriter],
) -> None:
    first = writer(config, "generation-a")
    first.activate()

    def stale() -> None:
        raise ValueError("fixture-selection-moved")

    with pytest.raises(ValueError, match="selection-moved"):
        writer(config, "generation-b", selected=stale).activate()
    relay_config = SimpleNamespace(to_dict=lambda: {**config.to_dict(), "relay": {}})
    with pytest.raises(ValueError, match="native-relay-transfer-unsupported"):
        writer(cast(ServiceConfig, relay_config), "generation-relay").activate()
    assert first.current()


def test_fenced_connector_preserves_native_lifetime_until_explicit_stop(
    config: ServiceConfig, writer: type[FencedWriter], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh_service.config import SSHServiceError
    from agent_ssh_service.hosting import FencedConnector
    from fleet_manager.gateway_service_connector import ServiceConnector

    first = writer(config, "generation-a")
    first.activate()
    calls: list[str] = []

    async def replaced(_self: Any, _stop: asyncio.Event) -> None:
        calls.append("admission")
        writer(config, "generation-b").activate()
        raise SSHServiceError("stale-service-writer")

    monkeypatch.setattr(ServiceConnector, "run", replaced)
    connector = object.__new__(FencedConnector)
    connector.writer = first

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(connector.run(stop))
        outcome: object
        try:
            await asyncio.sleep(0)
            assert not task.done(), "DB fencing must not implicitly retire the native owner"
        finally:
            stop.set()
            outcome, = await asyncio.gather(task, return_exceptions=True)
        assert outcome is None

    asyncio.run(scenario())
    assert calls == ["admission"]


@pytest.mark.parametrize("finish", ["stop", "cancel"])
def test_unavailable_connector_retry_remains_interruptible(
    config: ServiceConfig, writer: type[FencedWriter], monkeypatch: pytest.MonkeyPatch,
    finish: str,
) -> None:
    from agent_ssh_service.hosting import FencedConnector
    from fleet_manager.gateway_service_connector import ServiceConnector

    active = writer(config, "current-owner")
    active.activate()
    connector = object.__new__(FencedConnector)
    connector.writer = active

    async def scenario() -> None:
        unavailable, stop = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        observed_owner = active.observed_owner
        def observe() -> str | None:
            owner = observed_owner()
            if owner is None:
                loop.call_soon_threadsafe(unavailable.set)
            return owner
        async def connect(current: Any, _stop: asyncio.Event) -> None:
            await current._admit(cast(Any, None))
            raise AssertionError("locked ownership must not admit")
        monkeypatch.setattr(active, "observed_owner", observe)
        monkeypatch.setattr(ServiceConnector, "run", connect)
        with closing(sqlite3.connect(active.root / "directory.sqlite3")) as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            task = asyncio.create_task(connector.run(stop))
            try:
                await asyncio.wait_for(unavailable.wait(), 4)
                if finish == "stop":
                    stop.set()
                else:
                    task.cancel()
                result, = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
                assert result is None if finish == "stop" else isinstance(result, asyncio.CancelledError)
            finally:
                blocker.rollback()
                stop.set()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("position", ["before-admission", "after-ticket"])
def test_current_connector_resumes_after_real_database_lock(
    config: ServiceConfig, writer: type[FencedWriter], monkeypatch: pytest.MonkeyPatch,
    position: str,
) -> None:
    from agent_ssh_service.hosting import FencedConnector
    from fleet_manager.gateway_service_connector import ServiceConnector

    active = writer(config, "current-owner")
    store = active.activate()
    baseline = store.snapshot()
    connector = object.__new__(FencedConnector)
    connector.writer = active
    path = Path(config.to_dict()["data_dir"]) / "directory.sqlite3"

    async def scenario() -> None:
        stop, unavailable, admitted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        probe_lock = threading.Lock()
        probes: list[tuple[float, float, str | None]] = []
        tickets: list[str] = []
        used: list[str] = []
        observed_owner = active.observed_owner
        blocker = sqlite3.connect(path)

        def observe() -> str | None:
            started = time.monotonic()
            owner = observed_owner()
            with probe_lock:
                probes.append((started, time.monotonic(), owner))
                ready = sum(value is None for _start, _end, value in probes) >= 2
            if ready:
                loop.call_soon_threadsafe(unavailable.set)
            return owner

        async def ticket(_self: Any, _session: Any) -> str:
            tickets.append(f"ticket-{len(tickets)}")
            if position == "after-ticket" and len(tickets) == 1:
                blocker.execute("BEGIN EXCLUSIVE")
            return tickets[-1]

        async def connect(current: Any, stopping: asyncio.Event) -> None:
            used.append(await current._admit(cast(Any, None)))
            admitted.set()
            await stopping.wait()

        monkeypatch.setattr(active, "observed_owner", observe)
        monkeypatch.setattr(ServiceConnector, "_admit", ticket)
        monkeypatch.setattr(ServiceConnector, "run", connect)
        if position == "before-admission":
            blocker.execute("BEGIN EXCLUSIVE")
        task = asyncio.create_task(connector.run(stop))
        try:
            await asyncio.wait_for(unavailable.wait(), 5)
            assert not used and not task.done()
            assert len(tickets) == (0 if position == "before-admission" else 1)
            blocker.rollback()
            await asyncio.wait_for(admitted.wait(), 3)
            assert used == [tickets[-1]]
            assert active.current() and store.snapshot() == baseline
            with probe_lock:
                failed = [(start, end) for start, end, owner in probes if owner is None]
            assert failed[1][0] - failed[0][1] >= 0.2
        finally:
            blocker.rollback()
            blocker.close()
            stop.set()
            outcome, = await asyncio.gather(task, return_exceptions=True)
        assert outcome is None

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", [
    "pre-admission", "post-admission", "pre-credential", "post-credential", "stale-recheck",
])
def test_real_locked_ownership_probe_keeps_loop_responsive_and_settles(
    config: ServiceConfig, writer: type[FencedWriter], monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from agent_ssh_service.config import SSHServiceError
    from agent_ssh_service.hosting import FencedConnector, _fenced_access
    from fleet_manager.gateway_service_connector import ServiceConnector

    active = writer(config, "current-owner")
    active.activate()
    if phase == "stale-recheck":
        writer(config, "different-owner").activate()
        assert active.observed_owner() == "different-owner"
    connector = object.__new__(FencedConnector)
    connector.writer = active

    async def scenario() -> None:
        stop = asyncio.Event()
        locked, started, finished = threading.Event(), threading.Event(), threading.Event()
        observed = active.observed_owner
        timings: list[float] = []
        outcomes: list[str | None] = []
        thread_ids: list[int] = []
        loop_thread = threading.get_ident()
        with closing(sqlite3.connect(active.root / "directory.sqlite3")) as blocker:
            def lock() -> None:
                blocker.execute("BEGIN EXCLUSIVE")
                locked.set()
            def observe() -> str | None:
                slow = locked.is_set()
                if slow:
                    thread_ids.append(threading.get_ident())
                    timings.append(time.monotonic())
                    started.set()
                try:
                    owner = observed()
                    if slow:
                        outcomes.append(owner)
                    return owner
                finally:
                    if slow:
                        timings.append(time.monotonic())
                        finished.set()
            async def ticket(_self: Any, _session: Any) -> str:
                if phase == "post-admission":
                    lock()
                return "fixture-ticket"
            async def access() -> str:
                if phase == "post-credential":
                    lock()
                return "fixture-access"
            async def stale(_self: Any, _stop: asyncio.Event) -> None:
                raise SSHServiceError("stale-service-writer")
            async def operation() -> Any:
                if "credential" in phase:
                    return await _fenced_access(active, access)
                if phase == "stale-recheck":
                    return await connector.run(stop)
                return await connector._admit(cast(Any, None))
            async def progress() -> None:
                deadline = time.monotonic() + 4
                while not started.is_set():
                    assert time.monotonic() < deadline, "actual locked probe did not start"
                    await asyncio.sleep(0.005)
                assert not finished.is_set(), "SQLite busy timeout blocked another coroutine"
            monkeypatch.setattr(active, "observed_owner", observe)
            monkeypatch.setattr(ServiceConnector, "_admit", ticket)
            monkeypatch.setattr(ServiceConnector, "run", stale)
            if phase.startswith("pre-") or phase == "stale-recheck":
                lock()
            monitor = asyncio.create_task(progress())
            task = asyncio.create_task(operation())
            outcome: object
            try:
                await asyncio.wait_for(monitor, 4)
                task.cancel()
                await asyncio.sleep(0.02)
                assert not finished.is_set(), "cancelled caller must drain the real pending probe"
                task.cancel()
            finally:
                task.cancel()
                outcome, = await asyncio.gather(task, return_exceptions=True)
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
                blocker.rollback()
                stop.set()
            assert isinstance(outcome, asyncio.CancelledError)
            assert finished.is_set() and outcomes == [None]
            assert len(thread_ids) == 1 and thread_ids[0] != loop_thread
            assert timings[-1] - timings[0] >= 0.9

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["fixture-service-error", "stale-service-writer"])
def test_current_connector_does_not_hide_unrelated_or_inconsistent_failure(
    config: ServiceConfig, writer: type[FencedWriter], monkeypatch: pytest.MonkeyPatch, code: str,
) -> None:
    from agent_ssh_service.config import SSHServiceError
    from agent_ssh_service.hosting import FencedConnector
    from fleet_manager.gateway_service_connector import ServiceConnector

    current = writer(config, "generation-a")
    current.activate()

    async def failed(_self: Any, _stop: asyncio.Event) -> None:
        raise SSHServiceError(code)

    monkeypatch.setattr(ServiceConnector, "run", failed)
    connector = object.__new__(FencedConnector)
    connector.writer = current
    with pytest.raises(SSHServiceError, match=code):
        asyncio.run(connector.run(asyncio.Event()))
