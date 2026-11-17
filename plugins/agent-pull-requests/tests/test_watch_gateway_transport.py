"""Actual PR operations through synthetic issuer, TLS, PoP and the SDK channel."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from aiohttp import ClientSession
from fleet_manager.gateway_service_handler import (
    BoundedServiceResponse,
    RequestDisconnected,
    ServiceRequest,
    ServiceResponse,
)
from single_instance_lease import OperationLock, OperationLockedError

from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore

if TYPE_CHECKING:
    from fleet_manager.gateway_service_connector import ServiceConnector
    from fleet_manager.gateway_service_server import ServiceRuntime


class FixtureSigningKey(Protocol):
    def get_op_key(self, operation: str) -> object: ...


class FixtureHarness(Protocol):
    document: dict[str, Any]
    keys: tuple[FixtureSigningKey, ...]


class FixtureBackend(Protocol):
    requests: list[tuple[str, str, dict[str, str], bytes]]


class FixtureRig(Protocol):
    h: FixtureHarness
    origin: str
    ca: str
    session: ClientSession
    state: ServiceRuntime
    tasks: list[asyncio.Task[None]]
    connectors: list[ServiceConnector]
    backends: list[FixtureBackend]

    def access(self, subject: str, key: int) -> str: ...

    async def update(self) -> None: ...

    async def auth(
        self, path: str, method: str = "GET", *, subject: str = "caller-a", key: int = 3,
        proof_method: str | None = None, proof_target: str | None = None,
    ) -> dict[str, str]: ...


class RunningFixture(Protocol):
    def __call__(self, root: Path) -> AbstractAsyncContextManager[FixtureRig]: ...


class Eventually(Protocol):
    def __call__(
        self, predicate: Callable[[], bool], timeout: float = 3,
    ) -> Awaitable[None]: ...


class ConnectHandler(Protocol):
    def __call__(
        self, rig: FixtureRig,
        handle: Callable[[ServiceRequest], Awaitable[ServiceResponse | BoundedServiceResponse]], *,
        fleet: str = "fleet-a", gateway: str = "gateway-a", response_limit: int = ...,
    ) -> Awaitable[ServiceConnector]: ...


def test_real_gateway_scopes_reconcile_restart_ack_and_policy_withdrawal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reuse Fleet's transport fixtures, not a second authentication/test server.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running as legacy_running
    from test_gateway_service_handler import connect_handler as legacy_connect_handler

    running = cast(RunningFixture, legacy_running)
    connect_handler = cast(ConnectHandler, legacy_connect_handler)

    async def scenario() -> None:
        store = ReplayStore((tmp_path / "replay").resolve(), "fleet/service/installation")
        store.provision()
        bindings = (
            WatchScopeBinding("caller-a", "project",
                              ConsumerScope("coordinator-a", "account-a", "example/project")),
            WatchScopeBinding("caller-b", "project",
                              ConsumerScope("coordinator-b", "account-b", "example/project")),
        )

        def adapter() -> WatchGateway:
            return WatchGateway(store, fleet="fleet-a", gateway="gateway-a",
                                slot="service-a", bindings=bindings)

        owner = adapter()
        async with running(tmp_path) as rig:
            rig.h.document["callers"].append({
                "principal": "caller-b", "key_source": "caller-a",
                "slots": ["service-a"], "methods": ["GET", "POST"],
            })
            await rig.update()
            await connect_handler(rig, owner.handler_binding().handle)

            async def send(
                operation: str, payload: dict[str, Any] | None = None, *,
                caller: str = "caller-a", expected: int = 200,
            ) -> dict[str, Any]:
                method = "GET" if operation == "capabilities" else "POST"
                path = "/api/v1/watch/project/" + operation
                headers = await rig.auth(path, method, subject=caller)
                headers["X-Identity"] = "caller-b"
                options = {"json": payload} if payload is not None else {}
                async with rig.session.request(
                    method, rig.origin + path, headers=headers, **options,
                ) as response:
                    assert response.status == expected
                    assert response.headers["Cache-Control"] == "no-store"
                    value: dict[str, Any] = await response.json()
                    return value

            assert (await send("capabilities"))["protocol"] == "agent-pull-requests.watch/v1"
            original: dict[str, Any] = {
                "request_id": "original", "issued_at": time.time(), "number": 1,
                "until": ["merged"], "deadline_at": time.time() - 1,
            }
            rejected = await send("register", dict(original, account="foreign"), expected=400)
            assert rejected == {"error": "invalid_watch_request"}
            before = store.path.read_bytes()
            assert await send(
                "register", dict(original, deadline_at=2**53 + 1), expected=400,
            ) == {"error": "invalid_watch_request"}
            assert store.path.read_bytes() == before
            assert await send("pending", caller="caller-b") == {"events": [], "more": False}
            # Deliberately discard the initial success and reconcile its original request.
            await send("register", original)
            receipt = await send("register", original)
            assert receipt["status"] == "active"
            inspect = {key: original[key] for key in ("request_id", "issued_at")}
            assert await send("inspect", inspect, caller="caller-b") == {"status": "uncertain"}
            assert await send("pending", caller="caller-b") == {"events": [], "more": False}
            pending = await send("pending")
            event = pending["events"][0]
            assert event["timed_out"] is True
            # Replace the adapter and TLS connector; only disk carries replay state.
            await owner.drain(timeout=3)
            rig.tasks[-1].cancel()
            await asyncio.gather(rig.tasks[-1], return_exceptions=True)
            owner = adapter()
            await connect_handler(rig, owner.handle)
            assert await send("inspect", inspect) == receipt
            assert await send("pending") == pending
            completion = dict(inspect, registration_id=receipt["registration_id"],
                              event_id=event["event_id"])
            assert await send("acknowledge", completion, caller="caller-b") == {
                "status": "uncertain",
            }
            ack = await send("acknowledge", completion)
            assert ack["status"] == "acknowledged"
            assert await send("acknowledge", completion) == ack
            assert await send("pending") == {"events": [], "more": False}
            assert rig.backends[0].requests == []
            before = store.path.read_bytes()
            rig.h.document["callers"] = []
            await rig.update()
            await send("register", dict(original, request_id="after-withdrawal"), expected=403)
            assert store.path.read_bytes() == before
            assert len(json.loads(before)["records"]) == 1
            await owner.drain(timeout=3)
    asyncio.run(scenario())


def test_owned_runtime_observes_reconnects_and_restarts_over_real_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually as legacy_eventually
    from gateway_service_transport_fixtures import running as legacy_running

    from agent_pull_requests.watch_observer import NativeObservationBinding
    from agent_pull_requests.watch_service import WatchService

    running = cast(RunningFixture, legacy_running)
    eventually = cast(Eventually, legacy_eventually)

    async def scenario() -> None:
        store = ReplayStore((tmp_path / "runtime").resolve(), "fleet/service/installation")
        store.provision()
        scope = ConsumerScope("coordinator-a", "account-a", "example/project")
        original: dict[str, Any] = {
            "request_id": "original", "issued_at": time.time(), "number": 1, "until": ("merged",),
        }
        receipt = store.execute(scope, "register", original, expires_at=int(time.time()) + 60)
        store.execute(
            ConsumerScope("coordinator-b", scope.account, scope.repo), "register", original,
            expires_at=int(time.time()) + 60,
        )
        calls = tmp_path / "native-calls"
        snapshot = {
            "schema": "agent-worktrees.pr-snapshot", "version": 1, "repo": scope.repo, "pr": 1,
            "provider": "github", "pr_state": "closed", "merged": True, "head_sha": "head",
            "base_ref": "main", "mergeable": True, "checks_state": "success",
            "title": "Fixture", "draft": False,
        }
        code = (
            "import json,pathlib,sys;"
            "assert sys.argv[1:]==['pr-watch','snapshot','example/project','1','--json'];"
            f"pathlib.Path({str(calls)!r}).open('a').write('read\\n');"
            f"print(json.dumps({snapshot!r}))"
        )
        async with running(tmp_path) as rig:
            async def access() -> str:
                token: str = rig.access("service-a", 1)
                return token

            def make() -> WatchService:
                return WatchService(
                    store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
                    bindings=(WatchScopeBinding("caller-a", "project", scope),),
                    observations=(NativeObservationBinding(
                        scope.account, scope.repo, sys.executable,
                        prefix=(sys.executable, "-c", code),
                    ),),
                    gateway_origin=rig.origin, gateway_ca=rig.ca,
                    access_token_callback=access, proof_key=rig.h.keys[1].get_op_key("sign"),
                    authority=lambda: int(time.time()) + 60, checkpoint=lambda: None,
                    interval=1,
                )

            async def send(
                service: WatchService, operation: str, payload: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                path = "/api/v1/watch/project/" + operation
                for _ in range(100):
                    await eventually(lambda: service.status()["ready"])
                    async with rig.session.post(
                        rig.origin + path, headers=await rig.auth(path, "POST"), json=payload or {},
                    ) as response:
                        value: dict[str, Any] = await response.json()
                        if response.status != 503 or value != {"error": "watch_store_busy"}:
                            assert response.status == 200, value
                            return value
                    # Busy precedes transaction entry; retain original identity.
                    await asyncio.sleep(.05)
                raise AssertionError(f"{operation} remained busy")

            owner = make()
            await owner.start()
            try:
                pending = await send(owner, "pending")
                event = pending["events"][0]
                assert event["registration_id"] == receipt["registration_id"]
                assert not event["timed_out"] and calls.read_text() == "read\n"
                # The runtime owns reconnection; the client reconciles the same IDs.
                await rig.state.channels["service-a"].close()
                await eventually(lambda: not owner.connector.connected.is_set())
                assert await send(owner, "pending") == pending
            finally:
                await owner.close(timeout=5)
            assert owner.status()["closed"] and not owner.status()["writer_held"]
            successor = make()
            await successor.start()
            try:
                assert await send(successor, "pending") == pending
                completion = {
                    key: original[key] for key in ("request_id", "issued_at")
                } | {"registration_id": receipt["registration_id"], "event_id": event["event_id"]}
                assert (await send(successor, "acknowledge", completion))["status"] == "acknowledged"
                assert await send(successor, "pending") == {"events": [], "more": False}
                assert calls.read_text() == "read\n"
                assert rig.backends[0].requests == []
            finally:
                await successor.close(timeout=5)

    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["policy", "channel"])
@pytest.mark.parametrize("stage", ["before-lookup", "commit-started"])
def test_real_revocation_and_disconnect_preserve_uncertainty_and_original_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loss: str, stage: str,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually as legacy_eventually
    from gateway_service_transport_fixtures import running as legacy_running
    from test_gateway_service_handler import connect_handler as legacy_connect_handler

    from agent_pull_requests import watch_replay_store

    running = cast(RunningFixture, legacy_running)
    eventually = cast(Eventually, legacy_eventually)
    connect_handler = cast(ConnectHandler, legacy_connect_handler)

    async def scenario() -> None:
        store = ReplayStore((tmp_path / "replay").resolve(), "fleet/service/installation")
        store.provision()
        owner = WatchGateway(
            store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
            bindings=(WatchScopeBinding(
                "caller-a", "project", ConsumerScope("coordinator-a", "account-a", "example/project"),
            ),),
        )
        started, release = threading.Event(), threading.Event()
        received: list[ServiceRequest] = []
        decode, write = watch_replay_store.json_object, watch_replay_store._atomic_write_json

        def hold() -> None:
            if not started.is_set():
                started.set()
                assert release.wait(10)

        def held_lookup(raw: bytes) -> dict[str, Any]:
            hold()
            return decode(raw)

        def held_commit(path: Path, state: object) -> None:
            hold()
            write(path, state)

        async def handle(request: ServiceRequest) -> ServiceResponse | BoundedServiceResponse:
            received.append(request)
            return await owner.handle(request)

        async with running(tmp_path) as rig:
            await connect_handler(rig, handle)
            original: dict[str, Any] = {
                "request_id": "original", "issued_at": time.time(), "number": 1, "until": ["merged"],
            }
            path = "/api/v1/watch/project/register"
            pending = asyncio.create_task(rig.session.post(
                rig.origin + path, headers=await rig.auth(path, "POST"), json=original,
            ))
            # Pause only inside the real ReplayStore's operation lock.
            monkeypatch.setattr(
                watch_replay_store, "json_object" if stage == "before-lookup" else "_atomic_write_json",
                held_lookup if stage == "before-lookup" else held_commit,
            )
            try:
                assert await asyncio.to_thread(started.wait, 3)
                callers = rig.h.document["callers"]
                if loss == "policy":
                    rig.h.document["callers"] = []
                    await rig.update()
                    # Policy publication does not wait for the remote disk effect.
                    assert rig.state.authority.status().revision == rig.h.document["manifest"]["revision"]
                else:
                    await rig.state.channels["service-a"].close()
                async with await pending as response:
                    assert response.status != 200
                    assert await response.json() == {"error": "outcome-unknown"}
                await eventually(lambda: not rig.connectors[-1].active)
                with pytest.raises(RequestDisconnected):
                    received[0].lifetime.check()
                assert owner._tasks
                with pytest.raises(OperationLockedError):
                    with OperationLock(store.root / "watch-replay.lock"):
                        pass
                release.set()
                await eventually(lambda: not owner._tasks)
                if loss == "policy":
                    rig.h.document["callers"] = callers
                    await rig.update()
                else:
                    await connect_handler(rig, handle)

                async def send(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
                    selected = "/api/v1/watch/project/" + operation
                    async with rig.session.post(
                        rig.origin + selected, headers=await rig.auth(selected, "POST"), json=payload,
                    ) as response:
                        assert response.status == 200
                        value: dict[str, Any] = await response.json()
                        return value

                inspect = {key: original[key] for key in ("request_id", "issued_at")}
                reconciled = await send("inspect", inspect)
                if stage == "before-lookup":
                    assert reconciled == {"status": "uncertain"}
                    assert json.loads(store.path.read_bytes())["records"] == []
                else:
                    assert reconciled["status"] == "active"
                receipt = await send("register", original)
                assert await send("register", original) == receipt
                if stage == "commit-started":
                    assert receipt == reconciled
                assert len(json.loads(store.path.read_bytes())["records"]) == 1
            finally:
                release.set()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await owner.drain(timeout=3)
    asyncio.run(scenario())
