"""Running Vault lifetime -> real TLS Gateway -> exact source custodian."""

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "fleet-manager" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_vault import config, service, service_generation
from credential_relay.sources.az_login import AzLoginSource
from credential_relay.sources.gh_auth import GhAuthSource
from fleet_manager.gateway_connect_route import LoopbackRoute
from gateway_service_transport_fixtures import Rig, eventually as _eventually, running as _running
from test_gateway_connect_route import conduit as _conduit
from test_federated_relay_config import selection

running: Callable[[Path], AbstractAsyncContextManager[Rig]] = _running
conduit: Callable[
    [str], AbstractAsyncContextManager[tuple[LoopbackRoute, list[asyncio.StreamWriter]]],
] = _conduit
_wait: Callable[[Callable[[], object], float], Awaitable[None]] = _eventually


async def eventually(predicate: Callable[[], object], timeout: float = 3) -> None:
    await _wait(predicate, timeout)


class Publisher(Protocol):
    def __call__(self, **changes: object) -> None: ...


class GatewayRequest(Protocol):
    def __call__(
        self, path: str = "/api", method: str = "GET", **kwargs: Any,
    ) -> Awaitable[aiohttp.ClientResponse]: ...


def gateway_request(rig: Rig) -> GatewayRequest:
    request: GatewayRequest = rig.request
    return request

CAPABILITY = "synthetic-explicit-source-capability-0001"
MACHINE_AUDIENCE = "synthetic-vault-gateway"
FIELDS = {
    "source": "local", "account": "account-a", "recipient": "consumer-a",
    "host": "github.com", "action": "get-github-token",
}


def test_dependency_health_does_not_select_policy_or_construct_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import federated_relay, federated_relay_config

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Dependency qualification must not acquire deployment resources")

    monkeypatch.setattr(federated_relay_config, "load", forbidden)
    monkeypatch.setattr(federated_relay_config, "read_reference", forbidden)
    monkeypatch.setattr(federated_relay, "RelayRuntime", forbidden)
    federated_relay.dependency_health()
    federated_relay.ssh_dependency_health()


@pytest.mark.parametrize("cancel", [False, True])
def test_federated_shutdown_withdraws_before_pending_warm_worker_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    from agent_vault.cache_warm import CacheWarmRuntime, WarmRequest

    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)
    started, release = threading.Event(), threading.Event()

    def pending_worker(self: CacheWarmRuntime, request: WarmRequest) -> None:
        started.set()
        assert release.wait(15)

    monkeypatch.setattr(CacheWarmRuntime, "_run", pending_worker)

    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async with conduit(rig.origin) as (route, _):
                configure(root, rig, route, monkeypatch)
                vault = service.VaultService()
                vault.ttl_override = 0
                request = WarmRequest("synthetic.kdbx", "", 0, object(), time.monotonic() + 60, ())
                pending = [request]
                monkeypatch.setattr(vault._cache_warm, "take", lambda: pending.pop() if pending else None)
                task = asyncio.create_task(service.run_server(vault, tcp_port=0))
                try:
                    await eventually(started.is_set)
                    await eventually(lambda: bool(rig.state.service_offers("vault-available-v1")), 5)
                    vault._shutdown = True
                    if cancel:
                        task.cancel()
                    await eventually(lambda: vault._cache_warm.closed)
                    await eventually(lambda: not rig.state.service_offers("vault-available-v1"))
                    assert not task.done()
                    assert vault._generation.has_writer
                    assert not release.is_set()
                finally:
                    release.set()
                    vault._shutdown = True
                    await asyncio.gather(task, return_exceptions=cancel)
                assert not vault._generation.has_writer

    asyncio.run(scenario())


def isolate_vault(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root.mkdir()
    monkeypatch.setenv(config.HOME_ENV, str(root))
    monkeypatch.setattr(service, "run_dir", lambda: root / "run")
    monkeypatch.setattr(service_generation, "home_dir", lambda: root)
    monkeypatch.setattr(service, "PID_FILE", str(root / "vault.pid"))
    monkeypatch.setattr(service, "SOCKET_PATH", str(root / "vault.sock"))
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:20]
    monkeypatch.setattr(service, "PIPE_PATH", rf"\\.\pipe\vault-relay-test-{suffix}")
    monkeypatch.setattr(service, "resolve_context", lambda: config.ResolvedVault(
        None, None, None, 0, {"port": "default"}))
    monkeypatch.setattr(service.VaultService, "_check_password_ttl", lambda _: None)


def configure(
    root: Path, rig: Rig, route: LoopbackRoute, monkeypatch: pytest.MonkeyPatch,
    *, native_admission: bool = False, machine_auth: bool = False,
) -> tuple[list[tuple[str | None, str, dict[str, Any]]], Publisher]:
    from cryptography.hazmat.primitives import serialization

    calls: list[tuple[str | None, str, dict[str, Any]]] = []

    async def azure(
        self: AzLoginSource, action: str, fields: dict[str, Any], *, timeout: float,
    ) -> str:
        assert action == "get-azure-token" and fields == {
            "resource": "gateway-admission", "tenant": "test-tenant"}
        access: Callable[[str, int], str] = rig.access
        return "token=" + access("service-a", 1) + "\n"

    async def github(
        self: GhAuthSource, action: str, fields: dict[str, Any], *, timeout: float,
    ) -> str:
        calls.append((self._account, action, fields))
        return "protocol=https\nhost=github.com\nusername=account-a\ntoken=synthetic-secret\n\n"

    if not native_admission:
        monkeypatch.setattr(AzLoginSource, "resolve", azure)
    monkeypatch.setattr(GhAuthSource, "resolve", github)
    value = selection(root)
    value["gateway_origin"] = rig.origin
    value["gateway_ca"] = rig.ca
    value["auth"] = (
        {"kind": "machine", "principal": "service-a", "audience": MACHINE_AUDIENCE}
        if machine_auth else
        {"kind": "az-login", "resource": "gateway-admission", "tenant": "test-tenant"}
    )
    key = rig.h.keys[1].get_op_key("sign")
    (root / "proof.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    (root / "capability").write_text(CAPABILITY)
    policy = {
        "schema": "credential-relay.account-policy", "version": 1,
        "providers": {"native": {"kind": "gh-auth", "account": "account-a", "host": "github.com"}},
        "grants": [FIELDS | {
            "provider": "native", "capability_file": str(root / "capability"),
            "expires_at": time.time() + 300, "source_lease_seconds": 60,
        }],
    }
    (root / "policy.json").write_text(json.dumps(policy))
    (root / "federated-relay.json").write_text(json.dumps(value))

    def publish(**changes: object) -> None:
        (root / "conduit.json").write_text(json.dumps({
            "schema": "fleet-manager.gateway-conduit", "version": 2,
            "gateway_origin": rig.origin, "connect_host": route.host, "connect_port": route.port,
            "generation": "synthetic-controller-generation", "state": "ready",
            "expires_at": time.time() + 9, **changes,
        }))

    publish()
    return calls, publish


def test_running_vault_authorized_consumer_denial_conduit_loss_and_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)

    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async with conduit(rig.origin) as (route, _):
                calls, publish = configure(root, rig, route, monkeypatch)
                vault = service.VaultService()
                vault.ttl_override = 0
                task = asyncio.create_task(service.run_server(vault, tcp_port=0))
                try:
                    await eventually(lambda: bool(rig.state.service_offers("vault-available-v1")), 5)
                    assert not task.done()
                    assert vault._generation.has_writer and vault._generation.hooks_started
                    relay = vault._federated_relay
                    assert relay is not None
                    await eventually(lambda: relay.status()["channel_attached"])
                    pin = {"X-Agent-Index-Installation-Id": "vault-test"}

                    async def request(expected: int = 200, **changes: object) -> dict[str, Any]:
                        async with await gateway_request(rig)(
                            "/api/source-credentials", "POST", headers=pin,
                            json=FIELDS | {"capability": CAPABILITY,
                                           "lease_contract": "credential-relay.account-source-lease/v1"}
                            | changes,
                        ) as response:
                            assert response.status == expected
                            assert response.headers["Cache-Control"] == "no-store"
                            body: dict[str, Any] = await response.json()
                            return body

                    async with await gateway_request(rig)("/api/health", headers=pin) as response:
                        assert response.status == 200 and calls == []
                    result = await request()
                    assert result["token"] == "synthetic-secret"
                    assert 0 < result["lease_remaining_seconds"] <= 60
                    assert result["installationId"] == "vault-test"
                    await request(403, recipient="unapproved")
                    await request(503, capability="wrong-synthetic-capability-of-valid-size")
                    assert len(calls) == 1
                    publish(state="unavailable", connect_port=None, generation=None)
                    await eventually(lambda: not rig.state.service_offers("vault-available-v1"))
                    await request(503)
                    assert len(calls) == 1 and not task.done()
                    publish(generation="replacement-controller-generation")
                    await eventually(lambda: bool(rig.state.service_offers("vault-available-v1")), 5)
                    await request()
                    assert len(calls) == 2
                finally:
                    vault._shutdown = True
                    await asyncio.wait_for(task, 5)
                await eventually(lambda: not rig.state.service_offers("vault-available-v1"))
                assert relay.status() == {
                    "state": "stopped", "channel_attached": False}
                assert not (root / "account-relay-port.json").exists()
                assert rig.backends[0].requests == []

    asyncio.run(scenario())
    assert CAPABILITY not in caplog.text and "synthetic-secret" not in caplog.text


def test_running_machine_relay_signs_real_tls_admission_and_authorized_source_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_manager.gateway_service_caller import ServiceCaller
    from fleet_manager.machine_identity import PROFILE

    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)

    async def no_azure(
        self: AzLoginSource, action: str, fields: dict[str, Any], *, timeout: float,
    ) -> str:
        pytest.fail("Machine admission must not acquire an Azure token")

    async def scenario() -> None:
        rig = Rig(tmp_path)
        rig.h.document["version"] = 4
        rig.h.document["identity"] = {
            "profile": PROFILE, "audience": MACHINE_AUDIENCE,
            "trust_valid_until": rig.h.clock.value + 3600,
        }
        rig.h.document["enrollment"] = {"mappings": [
            {"principal": name, "kind": kind, "resource": "service-a",
             "key_source": name, "expires_at": rig.h.clock.value + 600}
            for name, kind in (("service-a", "service"), ("caller-a", "caller"))
        ]}
        start: Callable[[], Awaitable[Rig]] = rig.start
        close: Callable[[], Awaitable[None]] = rig.close
        try:
            await start()
            async with conduit(rig.origin) as (route, _):
                calls, _ = configure(root, rig, route, monkeypatch, machine_auth=True)
                monkeypatch.setattr(AzLoginSource, "resolve", no_azure)
                vault = service.VaultService()
                vault.ttl_override = 0
                task = asyncio.create_task(service.run_server(vault, tcp_port=0))
                try:
                    await eventually(lambda: bool(rig.state.service_offers("vault-available-v1")), 5)
                    assert not task.done() and not calls
                    assert rig.state.service_offers("vault-available-v1")[0].principal == "service-a"
                    caller = ServiceCaller(
                        gateway_origin=rig.origin, proof_key=rig.h.keys[3], gateway_ca=rig.ca,
                        machine_principal="caller-a", machine_audience=MACHINE_AUDIENCE,
                        machine_resource="service-a",
                    )
                    response = await caller.request(
                        "POST", "/api/source-credentials",
                        request_headers=(("X-Agent-Index-Installation-Id", "vault-test"),),
                        body=json.dumps(FIELDS | {
                            "capability": CAPABILITY,
                            "lease_contract": "credential-relay.account-source-lease/v1",
                        }).encode(),
                    )
                    assert response.status == 200 and isinstance(response.body, bytes)
                    body = json.loads(response.body)
                    assert body["token"] == "synthetic-secret"
                    assert body["installationId"] == "vault-test"
                    assert 0 < body["lease_remaining_seconds"] <= 60
                    assert len(calls) == 1 and rig.backends[0].requests == []
                finally:
                    vault._shutdown = True
                    await asyncio.wait_for(task, 5)
                await eventually(lambda: not rig.state.service_offers("vault-available-v1"))
        finally:
            await close()

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["missing", "malformed", "unreadable"])
def test_reconnect_ca_failure_is_bounded_and_preserves_local_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    from agent_vault import federated_relay

    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)

    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async with conduit(rig.origin) as (route, _):
                calls, publish = configure(root, rig, route, monkeypatch)
                wall, monotonic = time.time(), time.monotonic()
                monkeypatch.setattr(federated_relay, "time", SimpleNamespace(
                    time=lambda: wall, monotonic=lambda: monotonic,
                ))
                ca = Path(rig.ca)
                trusted = ca.read_bytes()
                vault = service.VaultService()
                vault.ttl_override = 0
                task = asyncio.create_task(service.run_server(vault, tcp_port=0))

                async def local_health() -> None:
                    health = await asyncio.to_thread(service.send_command, {
                        "action": "ping", "health_only": True,
                    })
                    assert health is not None and health["ok"] is True
                    assert health["pid"] == os.getpid() and health["accepting_work"] is True
                    assert not task.done()

                try:
                    await eventually(lambda: bool(rig.state.service_offers("vault-available-v1")), 5)
                    await local_health()
                    identity = Path(service.PID_FILE).read_bytes()
                    runtime = vault._federated_relay
                    assert runtime is not None
                    if damage == "malformed":
                        ca.write_bytes(b"synthetic invalid CA material")
                    else:
                        ca.unlink()
                        if damage == "unreadable":
                            ca.mkdir()
                    publish(generation="ca-failure-generation", expires_at=wall + 9)
                    await eventually(lambda: task.done() or runtime._failures == 1)
                    assert not task.done(), repr(task.exception())
                    await eventually(lambda: not rig.state.service_offers("vault-available-v1"))
                    await local_health()
                    assert runtime.status()["channel_attached"] is False
                    await asyncio.sleep(0.6)
                    assert runtime._failures == 1 and runtime._task is None
                    for failures in range(2, 6):
                        elapsed = runtime._retry_at - monotonic
                        wall += elapsed
                        monotonic += elapsed
                        publish(generation="ca-failure-generation", expires_at=wall + 9)
                        await eventually(lambda: runtime._failures == failures)
                        await local_health()
                    assert runtime.status() == {
                        "state": "reconnect-exhausted", "channel_attached": False,
                    }
                    if ca.is_dir():
                        ca.rmdir()
                    ca.write_bytes(trusted)
                    wall += 100
                    monotonic += 100
                    publish(generation="ca-failure-generation", expires_at=wall + 9)
                    await asyncio.sleep(0.6)
                    assert runtime._failures == 5 and runtime._task is None
                    assert not rig.state.service_offers("vault-available-v1")
                    await local_health()
                    publish(generation="restored-trust-generation", expires_at=wall + 9)
                    await eventually(lambda: bool(rig.state.service_offers("vault-available-v1")), 5)
                    await local_health()
                    assert runtime._failures == 0
                    assert runtime._origin == rig.origin and runtime._route == route
                    assert Path(service.PID_FILE).read_bytes() == identity
                    assert not calls and rig.backends[0].requests == []
                finally:
                    if ca.is_dir():
                        ca.rmdir()
                    ca.write_bytes(trusted)
                    vault._shutdown = True
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("observation", [
    "expired", "foreign", "unsupported", "huge-integer", "infinite", "nan", "boolean",
])
def test_invalid_conduit_never_authenticates_or_advertises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observation: str,
) -> None:
    from agent_vault.federated_relay import selected_relay

    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)

    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async with conduit(rig.origin) as (route, connections):
                calls, publish = configure(root, rig, route, monkeypatch)
                invalid: dict[str, dict[str, object]] = {
                    "expired": {"expires_at": time.time() - 1},
                    "foreign": {"gateway_origin": "https://foreign.invalid"},
                    "unsupported": {"version": 3},
                    "huge-integer": {"expires_at": 10**400},
                    "infinite": {"expires_at": float("inf")},
                    "nan": {"expires_at": float("nan")},
                    "boolean": {"expires_at": True},
                }
                publish(**invalid[observation])
                runtime = selected_relay()
                assert runtime is not None
                try:
                    await runtime.tick()
                    assert runtime.status() == {
                        "state": "waiting-conduit", "channel_attached": False}
                    assert not connections and not calls and not rig.state.channels
                finally:
                    await runtime.close()
    asyncio.run(scenario())


def test_unchanged_observation_cannot_outlive_monotonic_cap_after_clock_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import federated_relay

    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)

    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async with conduit(rig.origin) as (route, _):
                _, publish = configure(root, rig, route, monkeypatch)
                wall, monotonic = time.time(), time.monotonic()
                clock = SimpleNamespace(
                    time=lambda: wall, monotonic=lambda: monotonic)
                monkeypatch.setattr(federated_relay, "time", clock)
                runtime = federated_relay.selected_relay()
                assert runtime is not None
                try:
                    publish(expires_at=wall + 8)
                    runtime._conduit()
                    wall -= 1
                    monotonic += 8
                    with pytest.raises(ValueError, match="relay-conduit-unavailable"):
                        runtime._conduit()
                finally:
                    await runtime.close()
    asyncio.run(scenario())


def test_failed_channel_budget_is_bounded_and_new_controller_generation_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiohttp
    from agent_vault import federated_relay

    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)

    async def scenario() -> None:
        async with running(tmp_path) as rig:
            unused = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
            port = unused.sockets[0].getsockname()[1]
            unused.close()
            await unused.wait_closed()
            route = LoopbackRoute(host="127.0.0.1", port=port)
            calls, publish = configure(root, rig, route, monkeypatch)
            wall, monotonic = time.time(), time.monotonic()
            monkeypatch.setattr(federated_relay, "time", SimpleNamespace(
                time=lambda: wall, monotonic=lambda: monotonic))
            runtime = federated_relay.selected_relay()
            assert runtime is not None
            try:
                await runtime.tick()
                for failure in range(5):
                    with pytest.raises((aiohttp.ClientError, OSError)):
                        assert runtime._task is not None
                        await runtime._task
                    await runtime.tick()
                    previous = runtime._task
                    await runtime.tick()
                    assert runtime._task is previous
                    elapsed = min(4, .5 * 2 ** failure)
                    monotonic += elapsed
                    wall += elapsed
                    publish(expires_at=wall + 9)
                    await runtime.tick()
                assert runtime.status() == {
                    "state": "reconnect-exhausted", "channel_attached": False}
                assert not runtime._task and not calls
                publish(generation="new-controller-generation", expires_at=wall + 9)
                await runtime.tick()
                assert runtime._task is not None
                assert runtime.status()["state"] == "connecting"
            finally:
                await runtime.close()
    asyncio.run(scenario())
