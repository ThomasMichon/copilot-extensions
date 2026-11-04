"""Serialized activation, preserved state, and stale-generation write fencing."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
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


def test_legacy_lifetime_lease_is_not_displaced(
    config: ServiceConfig, writer: type[FencedWriter],
) -> None:
    from single_instance_lease import SingleInstance

    lease = SingleInstance(config.to_dict()["data_dir"], service="agent-ssh-service")
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
