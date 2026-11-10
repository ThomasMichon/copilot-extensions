"""Original foreground writer custody through native and SDK delivery cleanup."""

from __future__ import annotations

import asyncio
import base64
from importlib import import_module
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable

import pytest
from agent_procutil.owned_process import OwnedCleanupError
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_pull_requests.watch_replay_client import ReplayClientError
from agent_pull_requests.watch_service import WatchService
from agent_pull_requests_service import hosting
from fleet_manager.gateway_service_channel import RESPONSE_BYTES

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
    from services.pr.tests.test_report_host import native_support as native_support
    from services.pr.tests.test_report_host_transport import (
        foreground, report_client, selected_native as selected_native,
    )
else:
    from test_report_host_transport import (
        Configuration, foreground, report_client, selected_native as selected_native,
    )
    from test_host import configuration as configuration
    from test_report_host import native_support as native_support


@pytest.fixture
def instances(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    services: list[Any] = []
    actual = WatchService

    def create(*args: Any, **kwargs: Any) -> Any:
        service = actual(*args, **kwargs)
        services.append(service)
        return service

    monkeypatch.setattr(hosting, "WatchService", create)
    return services


def expire_next_caller(rig: Any, monkeypatch: pytest.MonkeyPatch) -> int:
    identity = import_module("test_runtime_identity")
    original = rig.access
    expires = int(time.time()) + 20

    def access(subject: str, key: int) -> str:
        if subject != "caller-a":
            return str(original(subject, key))
        value = identity.pair(
            (rig.h.keys[0], rig.h.keys[key]), SimpleNamespace(nonce="unused"),
            now=rig.h.clock.value, access={"sub": subject, "exp": expires},
        )
        return str(value[0])

    monkeypatch.setattr(rig, "access", access)
    return expires


def expire_admitted_request(
    service: Any, expires: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = import_module("fleet_manager.gateway_service_handler")
    # Advance only the executing request owners, not process-wide wall time.
    monkeypatch.setattr(service.store, "clock", lambda: float(expires))
    monkeypatch.setattr(sdk, "time", SimpleNamespace(time=lambda: float(expires)))


@pytest.mark.parametrize("loss", ["stop", "cancel", "policy", "expiry"])
def test_native_cleanup_keeps_actual_foreground_writer_after_withdrawal(
    selected_native: tuple[Configuration, Any, Callable[[], list[Any]]],
    instances: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loss: str,
) -> None:
    configuration, _, calls = selected_native
    configuration[1]["reports"]["response_bytes"] = RESPONSE_BYTES
    transport = import_module("gateway_service_transport_fixtures")
    account = import_module("agent_worktrees.github_account_observation")
    original = account.run_caller_owned
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def cleanup(deadline: float) -> None:
        assert threading.get_ident() != loop_thread
        entered.set()
        if not release.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError
        settled.set()

    def delayed(argv: list[str], **kwargs: Any) -> Any:
        if any("/pulls/" in argument for argument in argv):
            raise OwnedCleanupError(None, cleanup_retry=cleanup)
        return original(argv, **kwargs)

    monkeypatch.setattr(account, "run_caller_owned", delayed)

    async def scenario() -> None:
        async with transport.running(tmp_path) as rig:
            async with foreground(configuration, rig) as (config, stop, owner, caller):
                client = report_client(config, caller, RESPONSE_BYTES)
                await client.capabilities()
                before = config.store().path.read_bytes()
                expires = expire_next_caller(rig, monkeypatch) if loss == "expiry" else 0
                pending = asyncio.create_task(client.read("diff", {"number": 7, "budget_seconds": 15}))
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    if loss == "cancel":
                        pending.cancel()
                    elif loss == "policy":
                        rig.h.document["callers"] = []
                        await rig.update()
                    elif loss == "expiry":
                        expire_admitted_request(instances[0], expires, monkeypatch)
                    stop.set()
                    await transport.eventually(lambda: instances[0].status()["draining"])
                    assert not owner.done() and not settled.is_set()
                    assert len(instances[0].gateway._tasks) == 1
                    with pytest.raises(AlreadyRunningError):
                        SingleInstance(config.root, service="agent-pull-requests-watch").acquire()
                    assert not any(
                        any("/pulls/" in argument for argument in call) for call in calls()
                    )
                finally:
                    stop.set()
                    release.set()
                    result = await asyncio.gather(pending, return_exceptions=True)
                    await asyncio.wait_for(asyncio.shield(owner), 8)
                assert isinstance(result[0], (ReplayClientError, asyncio.CancelledError))
                assert settled.is_set() and not instances[0].gateway._tasks
                assert not instances[0].status()["writer_held"]
                assert config.store().path.read_bytes() == before
                with SingleInstance(config.root, service="agent-pull-requests-watch"):
                    pass

    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["disconnect", "expiry"])
def test_delayed_sdk_delivery_cleanup_cannot_release_foreground_writer_early(
    selected_native: tuple[Configuration, Any, Callable[[], list[Any]]],
    instances: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loss: str,
) -> None:
    configuration, _, _ = selected_native
    configuration[1]["reports"]["response_bytes"] = RESPONSE_BYTES
    monkeypatch.setenv("FIXTURE_LARGE", "1")
    transport = import_module("gateway_service_transport_fixtures")
    sdk = import_module("fleet_manager.gateway_service_handler")
    original = sdk.send
    armed = False
    frames: list[dict[str, Any]] = []

    async def scenario() -> None:
        nonlocal armed
        entered, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(ws: Any, frame: dict[str, Any]) -> None:
            if armed:
                frames.append(frame)
            await original(ws, frame)
            if armed and frame["type"] == "chunk" and b'"operation": "diff"' in base64.b64decode(
                frame["data"]
            ):
                entered.set()
                waiting = asyncio.create_task(release.wait())
                interrupted = False
                try:
                    while not waiting.done():
                        try:
                            await asyncio.shield(waiting)
                        except asyncio.CancelledError:
                            interrupted = True
                    await waiting
                finally:
                    settled.set()
                if interrupted:
                    raise asyncio.CancelledError

        monkeypatch.setattr(sdk, "send", send)
        async with transport.running(tmp_path) as rig:
            async with foreground(configuration, rig) as (config, stop, owner, caller):
                client = report_client(config, caller, RESPONSE_BYTES)
                await client.capabilities()
                before = config.store().path.read_bytes()
                armed = True
                expires = expire_next_caller(rig, monkeypatch) if loss == "expiry" else 0
                pending = asyncio.create_task(client.read("diff", {"number": 7, "budget_seconds": 15}))
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    if loss == "expiry":
                        expire_admitted_request(instances[0], expires, monkeypatch)
                    else:
                        pending.cancel()
                    stop.set()
                    await transport.eventually(lambda: instances[0].status()["draining"])
                    assert not owner.done() and not settled.is_set()
                    with pytest.raises(AlreadyRunningError):
                        SingleInstance(config.root, service="agent-pull-requests-watch").acquire()
                    assert not any(frame["type"] == "end" for frame in frames)
                finally:
                    stop.set()
                    release.set()
                    results = await asyncio.gather(pending, return_exceptions=True)
                    await asyncio.wait_for(asyncio.shield(owner), 8)
                assert isinstance(results[0], (ReplayClientError, asyncio.CancelledError))
                assert settled.is_set() and not instances[0].status()["writer_held"]
                assert not any(frame["type"] == "end" for frame in frames)
                assert config.store().path.read_bytes() == before

    asyncio.run(scenario())
