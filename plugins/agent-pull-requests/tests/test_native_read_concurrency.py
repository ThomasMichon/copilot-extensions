"""Selected snapshot concurrency shares the existing service and drain owner."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_pull_requests import watch_observer, watch_service
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_observer import NativeObservationBinding, ReplayObserver
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.watch_service import WatchService

SCOPE = ConsumerScope("consumer", "account", "example/project")


class Connector:
    def __init__(self, **_kwargs: Any) -> None:
        self.connected = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        self.connected.set()
        try:
            await stop.wait()
        finally:
            self.connected.clear()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReplayStore:
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)
    result = ReplayStore(tmp_path.resolve(), "test/service")
    result.provision()
    return result


def service(store: ReplayStore, **options: Any) -> WatchService:
    async def access() -> str:
        return "synthetic"

    return WatchService(
        store, fleet="test", gateway="gateway", slot="service",
        bindings=(WatchScopeBinding("reader", "repository", SCOPE),),
        observations=(NativeObservationBinding(SCOPE.account, SCOPE.repo, sys.executable),),
        gateway_origin="https://gateway.invalid", access_token_callback=access,
        proof_key=object(), authority=lambda: int(time.time()) + 60,
        checkpoint=lambda: None, interval=.01, **options,
    )


@pytest.mark.parametrize("selection", [1, 2, 4])
def test_service_forwards_native_read_concurrency_to_owned_observer(
    store: ReplayStore, selection: int,
) -> None:
    assert service(store, native_read_concurrency=selection).observer.native_read_concurrency == selection


def test_legacy_service_preserves_observer_call_shape(
    store: ReplayStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[NativeObservationBinding, ...]] = []

    def legacy(
        store: ReplayStore, *, bindings: tuple[NativeObservationBinding, ...],
    ) -> ReplayObserver:
        calls.append(bindings)
        return ReplayObserver(store, bindings=bindings)

    monkeypatch.setattr(watch_service, "ReplayObserver", legacy)
    assert service(store).observer.native_read_concurrency is None
    assert len(calls) == 1


@pytest.mark.parametrize("selection", [True, False, 0, 5, 1.0, "1"])
def test_service_refuses_invalid_native_read_concurrency_before_connector(
    store: ReplayStore, monkeypatch: pytest.MonkeyPatch, selection: Any,
) -> None:
    monkeypatch.setattr(watch_service, "ServiceConnector", lambda **_kwargs: pytest.fail("connector"))
    with pytest.raises(ValueError, match="native_read_concurrency"):
        service(store, native_read_concurrency=selection)


@pytest.mark.parametrize("cancel_drain", [False, True])
def test_selected_single_read_retains_writer_until_actual_native_completion(
    store: ReplayStore, monkeypatch: pytest.MonkeyPatch, cancel_drain: bool,
) -> None:
    intent = dict(request_id="original", issued_at=time.time(), number=1, until=("merged",))
    receipt = store.execute(SCOPE, "register", intent, expires_at=int(time.time()) + 60)
    entered, release = threading.Event(), threading.Event()

    def fetch(*_args: Any, **_kwargs: Any) -> PRSnapshot:
        entered.set()
        assert release.wait(5)
        return PRSnapshot(merged=True)

    monkeypatch.setattr(watch_observer, "fetch_snapshot", fetch)

    async def scenario() -> None:
        owner = service(store, native_read_concurrency=1)
        await owner.start()
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if cancel_drain:
                drain = asyncio.create_task(owner.close(timeout=3))

                async def draining() -> None:
                    while not owner.status()["draining"]:
                        await asyncio.sleep(.001)

                await asyncio.wait_for(draining(), 3)
                drain.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await drain
            else:
                with pytest.raises(TimeoutError):
                    await owner.close(timeout=.01)
            assert not owner.status()["ready"]
            with pytest.raises(AlreadyRunningError):
                SingleInstance(store.root, service="agent-pull-requests-watch").acquire()
        finally:
            release.set()
            await owner.close(timeout=3)
        with SingleInstance(store.root, service="agent-pull-requests-watch"):
            pass
        assert store.execute(SCOPE, "register", intent, expires_at=int(time.time()) + 60) == receipt
        assert store.execute(SCOPE, "pending", {}, expires_at=int(time.time()) + 60)["events"] == []

    asyncio.run(scenario())
