"""Existing WorkspaceClient/ServiceCaller native custody through the real frontdoor."""

import asyncio
from pathlib import Path
import threading

import pytest

from test_workspace_tools import environment as environment
from test_workspace_tools import integration as integration
from test_workspace_tools import tools as tools
from test_workspace_tools import execution_pool as execution_pool

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def test_native_request_reconciles_every_cleanup_failure_before_owner_denial(environment):
    from concurrent.futures import Future
    from agent_procutil.owned_process import OwnedCleanupError
    from fleet_manager._service_caller_native import RequestNativeScope
    scope = RequestNativeScope()
    first_handle, second_handle = object(), object()
    first, second = OwnedCleanupError(first_handle), OwnedCleanupError(second_handle)
    for error in (PermissionError("source ownership lost"), first, second):
        future = Future()
        future.set_exception(error)
        scope.register(future)
    with pytest.raises(OwnedCleanupError) as observed:
        asyncio.run(scope.join())
    primary = observed.value
    retained = [primary, *primary.request_native_cleanup_errors]
    assert {id(error) for error in retained} == {id(first), id(second)}
    assert {id(error.process) for error in retained} == {id(first_handle), id(second_handle)}


@pytest.mark.parametrize("fail_first_retry", [False, True])
def test_native_request_preserves_retryable_cleanups(environment, fail_first_retry):
    from concurrent.futures import Future
    import time
    from agent_procutil.owned_process import OwnedCleanupError
    from fleet_manager._service_caller_native import RequestNativeScope
    handles = (object(), object())
    resources = {handle: (("native-handle", handle),) for handle in handles}
    attempts = {handle: 0 for handle in handles}
    settled, notified = set(), []
    failure = RuntimeError("original cleanup remains unconfirmed")
    pending = {handles[0]} if fail_first_retry else set()
    def retry(handle):
        def cleanup(deadline):
            assert deadline > time.monotonic()
            attempts[handle] += 1
            if handle in pending:
                pending.remove(handle)
                raise failure
            settled.add(handle)
        return cleanup
    errors = [
        OwnedCleanupError(
            handle, cleanup_resources=resources[handle], cleanup_retry=retry(handle),
            on_settled=lambda handle=handle: notified.append(handle),
        )
        for handle in handles
    ]
    scope = RequestNativeScope()
    for error in (PermissionError("source ownership lost"), *errors):
        future = Future()
        future.set_exception(error)
        scope.register(future)
    with pytest.raises(OwnedCleanupError) as observed:
        asyncio.run(scope.join())
    primary = observed.value
    retained = [primary, *primary.request_native_cleanup_errors]
    assert {id(error) for error in retained} == {id(error) for error in errors}
    failures = []
    for error in retained:
        try:
            error.retry_cleanup(deadline=time.monotonic() + 2)
        except RuntimeError as caught:
            if caught is not failure:
                raise
            assert fail_first_retry and error is errors[0]
            failures.append(error)
    assert all(attempts[handle] == 1 for handle in handles)
    assert failures == ([errors[0]] if fail_first_retry else [])
    for error in failures:
        error.retry_cleanup(deadline=time.monotonic() + 2)
    assert settled == set(handles) and set(notified) == set(handles) and len(notified) == 2
    for error in retained:
        assert error.cleanup_resources is resources[error.process]
        error.retry_cleanup(deadline=time.monotonic() + 2)
    assert sum(attempts.values()) == 2 + int(fail_first_retry)
    assert len(notified) == 2


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_existing_service_caller_registered_native_request_consumer(
    environment, tmp_path, monkeypatch, cleanup_failure,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from test_gateway_service_caller import test_registered_native_credentials_join_before_request_cancellation_settles
    test_registered_native_credentials_join_before_request_cancellation_settles(tmp_path, cleanup_failure)


@pytest.mark.parametrize("boundary", ["credentials", "dns", "owner-loss", "submit-owner-loss"])
def test_workspace_exchange_uses_real_source_admission_and_request_native_join(
    environment, integration, tools, execution_pool, tmp_path, monkeypatch, boundary,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from fleet_manager.gateway_service_caller import ServiceCaller
    from fleet_manager.credential_references import ServiceCredentials
    from fleet_manager import credential_references
    from agent_pull_requests.workspace_client import WorkspaceClient
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    native_host, _, _ = tools
    _, existing, _, _, _, _ = integration
    _, _, initial, _, _, _ = environment
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    denied = PermissionError("selected source ownership lost")
    revoked = threading.Event()
    original_checkpoint = native_host.source_owner.checkpoint
    def current():
        if revoked.is_set():
            raise denied
        original_checkpoint()
    native_host.source_owner.checkpoint = current
    original_read = credential_references.read_credential_reference
    def read(path):
        entered.set()
        assert release.wait(10)
        value = original_read(path)
        if boundary == "owner-loss":
            revoked.set()
        return value
    monkeypatch.setattr(credential_references, "read_credential_reference", read)

    async def scenario():
        async with running(tmp_path) as rig:
            token = tmp_path / "synthetic-token"
            token.write_text(rig.access("caller-a", 3))
            credentials = ServiceCredentials(rig.h.keys[3].get_op_key("sign"), token.resolve())
            origin = rig.origin
            if boundary == "dns":
                import socket
                from urllib.parse import urlsplit
                origin = "https://review.example.test:" + str(urlsplit(rig.origin).port)
                def resolve(host, port, *args):
                    assert host == "review.example.test"
                    entered.set()
                    assert release.wait(10)
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
                monkeypatch.setattr(socket, "getaddrinfo", resolve)
            caller = ServiceCaller(
                gateway_origin=origin, gateway_ca=rig.ca, proof_key=credentials.proof_key,
                access_token_callback=credentials.access_token,
            )
            client = WorkspaceClient(
                caller, route_prefix="/api", binding=existing._binding, scope=initial["scope"],
            )
            async with owner.operation(60) as operation:
                if boundary == "submit-owner-loss":
                    original_submit = operation._submit_native
                    def submit(function, *args):
                        if function is read:
                            entered.set()
                            raise denied
                        return original_submit(function, *args)
                    operation._submit_native = submit
                task = asyncio.create_task(operation.call(client.capabilities, checkpoint=operation.remaining))
                try:
                    for _ in range(1000):
                        if entered.is_set():
                            break
                        await asyncio.sleep(.001)
                    assert entered.is_set()
                    if boundary in ("owner-loss", "submit-owner-loss"):
                        release.set()
                        with pytest.raises(PermissionError) as observed:
                            await task
                        assert observed.value is denied
                    else:
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                        with pytest.raises(TimeoutError):
                            await owner.drain(timeout=.001)
                        assert caller._active == 1 and not owner.status()["drained"]
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
            await owner.drain(timeout=2)
            assert caller._active == 0 and owner.status()["drained"]
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


def test_older_caller_retains_metadata_but_cannot_advertise_owned_async(
    environment, integration, tools, execution_pool,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.workspace_client import WorkspaceClient, WorkspaceClientError
    native_host, _, _ = tools
    _, existing, _, _, _, _ = integration
    _, _, initial, _, _, _ = environment
    class OlderCaller:
        async def request(self, *args, **kwargs):
            return await existing._caller.request(*args, **kwargs)
    client = WorkspaceClient(
        OlderCaller(), route_prefix="", binding=existing._binding, scope=initial["scope"],
        read_admission=existing._read_admission,
    )
    assert client.preparation_contract()["version"] == 1
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    async def scenario():
        assert (await client.capabilities())["generation_versions"] == [1]
        async with owner.operation(60) as operation:
            with pytest.raises(WorkspaceClientError, match="workspace_native_completion_unavailable"):
                await operation.call(client.capabilities, checkpoint=operation.remaining)
        await owner.drain(timeout=2)
    asyncio.run(scenario())


@pytest.mark.parametrize("qualified", [False, True, "admission"])
def test_actual_credential_owner_qualification_and_immutable_source_frontdoor(
    environment, integration, tools, execution_pool, tmp_path, monkeypatch, qualified,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from fleet_manager.gateway_service_caller import ServiceCaller
    from fleet_manager.credential_references import ServiceCredentials
    from agent_pull_requests.workspace_client import WorkspaceClient
    from agent_pull_requests.workspace_tools import WorkspaceTools, WorkspaceToolUnavailable
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler
    gateway, existing, native, _, _, _ = integration
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    calls = []

    async def scenario():
        loaded = native.exchange("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        identity = loaded["result"]["descriptor"]["id"]
        async with running(tmp_path) as rig:
            await connect_handler(rig, gateway.handle)
            path = tmp_path / "synthetic-source-token"
            path.write_text(rig.access("caller-a", 3))
            credentials = ServiceCredentials(rig.h.keys[3].get_op_key("sign"), path.resolve())
            def token():
                calls.append(True)
                return path.read_text()
            async def opaque():
                return await asyncio.to_thread(token)
            caller = ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca, proof_key=credentials.proof_key,
                access_token_callback=credentials.access_token if qualified else opaque,
            )
            client = WorkspaceClient(
                caller, route_prefix="/api", binding=existing._binding, scope=initial["scope"],
            )
            if qualified == "admission":
                assert client.preparation_contract()["version"] == 2
                async def admission():
                    await asyncio.to_thread(lambda: calls.append(True))
                client._read_admission = admission
            host = WorkspaceTools(client, source_owner=owner)
            request = {"number": 42, "generation_id": identity, "budget_seconds": 60}
            if qualified is not True:
                assert client.preparation_contract()["version"] == 1
                with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
                    await host.invoke("source", request)
                assert not calls and owner.status()["pending"] == 0
                assert (await client.capabilities())["generation_versions"] == [1]
                if qualified == "admission":
                    assert not calls
                    assert (await client.materialize(42, identity))["id"] == identity
                assert calls  # Ordinary released loop metadata callbacks still execute.
            else:
                assert client.preparation_contract()["version"] == 2
                attachment = await host.invoke("source", request)
                assert attachment["state"] == "ready"
                assert type(host._generations[identity][2]) is bytes
                result = await host.invoke("source-read", {
                    **request, "source_attachment": attachment, "path": "/note.txt", "side": "after",
                })
                assert result["content"] == "after"
            await owner.drain(timeout=2)
            await gateway.drain(timeout=2)
            assert owner.status()["drained"] and caller._active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,method", [
    ("native", "exchange"), ("native", "materialize"),
    ("native", "ensure_capabilities"),
    ("service", "exchange"), ("service", "materialize"), ("service", "ensure_capabilities"),
])
def test_overridden_preparation_methods_remain_metadata_only(
    environment, integration, tools, execution_pool, monkeypatch, kind, method,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from fleet_manager.gateway_service_caller import ServiceCaller
    from fleet_manager.credential_references import ServiceCredentials
    from agent_pull_requests.workspace_client import WorkspaceClient
    from agent_pull_requests.workspace_tools import WorkspaceTools, WorkspaceToolUnavailable
    native_host, _, _ = tools
    _, existing, native, _, _, _ = integration
    _, _, initial, _, _, _ = environment
    client = native
    if kind == "service":
        credentials = ServiceCredentials(None, Path(__file__).resolve())
        caller = ServiceCaller(gateway_origin="https://127.0.0.1:443", proof_key=None,
                               access_token_callback=credentials.access_token)
        client = WorkspaceClient(caller, route_prefix="", binding=existing._binding, scope=initial["scope"])
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(client, source_owner=owner)
    assert host._preparation_mode() is not None
    calls = []
    async def overridden(*args, **kwargs):
        calls.append(True)
        return {"metadata": True}
    monkeypatch.setattr(client, method, overridden, raising=False)
    assert client.preparation_contract()["version"] == 1
    async def scenario():
        with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
            await host.invoke("source", {"number": 42, "generation_id": initial["id"], "budget_seconds": 60})
        assert not calls and owner.status()["pending"] == 0
        assert await getattr(client, method)() == {"metadata": True}
        assert calls
        await owner.drain(timeout=2)
    asyncio.run(scenario())


@pytest.mark.parametrize("shape", ["coroutine", "wrapped"])
def test_native_discovery_offload_is_metadata_only_before_source_reservation(
    environment, tools, execution_pool, monkeypatch, shape,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.workspace_tools import WorkspaceTools, WorkspaceToolUnavailable
    native_host, _, _ = tools
    client = native_host.client
    _, _, initial, _, _, _ = environment
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(client, source_owner=owner)
    callbacks, reservations, workers = [], [], []
    exchange, operation = client.exchange, owner.operation
    def metadata():
        workers.append(True)
        return exchange("capabilities", {})
    async def discover(*, checkpoint=None):
        callbacks.append(True)
        if checkpoint is not None:
            checkpoint()
        return await asyncio.to_thread(metadata)
    def wrapped(**kwargs):
        return discover(**kwargs)
    def reserve(*args, **kwargs):
        reservations.append(True)
        return operation(*args, **kwargs)
    monkeypatch.setattr(client, "ensure_capabilities", discover if shape == "coroutine" else wrapped, raising=False)
    monkeypatch.setattr(owner, "operation", reserve)
    async def scenario():
        with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
            await host.invoke("source", {"number": 42, "generation_id": initial["id"], "budget_seconds": 60})
        assert not callbacks and not reservations and not workers
        assert owner.status()["pending"] == 0
        capabilities = await host.capabilities()
        assert "load" in capabilities["operations"] and "source" not in capabilities["operations"]
        assert capabilities["source_execution"] == "async_owner_unavailable"
        assert callbacks and workers
        await owner.drain(timeout=2)
        assert owner.status()["drained"]
    asyncio.run(scenario())


@pytest.mark.parametrize("method,shape", [
    ("ensure_capabilities", "property"), ("exchange", "property"), ("materialize", "descriptor"),
])
def test_native_descriptor_getters_are_metadata_only_before_source_reservation(
    environment, tools, execution_pool, monkeypatch, method, shape,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.workspace_tools import WorkspaceTools, WorkspaceToolUnavailable
    native_host, _, _ = tools
    client = native_host.client
    _, _, initial, _, _, _ = environment
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(client, source_owner=owner)
    getters, callbacks, reservations = [], [], []
    exchange, operation = client.exchange, owner.operation
    def discover(**options):
        callbacks.append(True)
        return exchange("capabilities", {}, **options)
    selected = discover if method == "ensure_capabilities" else getattr(client, method)
    def get(instance):
        assert instance is client
        getters.append(True)
        return selected
    class Dynamic:
        def __get__(self, instance, owner=None):
            return self if instance is None else get(instance)
    def reserve(*args, **kwargs):
        reservations.append(True)
        return operation(*args, **kwargs)
    monkeypatch.setattr(type(client), method, property(get) if shape == "property" else Dynamic(), raising=False)
    monkeypatch.setattr(owner, "operation", reserve)
    async def scenario():
        try:
            with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
                await host.invoke("source", {"number": 42, "generation_id": initial["id"], "budget_seconds": 60})
            assert not getters and not callbacks and not reservations
            assert owner.status()["pending"] == 0
            metadata = await host.invoke("capabilities", {})
            assert "load" in metadata["operations"] and "source" not in metadata["operations"]
            materialized = await host.invoke("materialize", {"number": 42, "generation_id": initial["id"]})
            assert materialized["id"] == initial["id"] and getters
            if method == "ensure_capabilities":
                assert callbacks
        finally:
            await owner.drain(timeout=2)
    asyncio.run(scenario())


def test_stock_instance_method_copies_keep_actual_async_source(
    environment, tools, execution_pool, monkeypatch,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.workspace_tools import WorkspaceTools
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    client = native_host.client
    for method in ("exchange", "materialize"):
        monkeypatch.setattr(client, method, getattr(client, method))
    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
        host = WorkspaceTools(client, source_owner=owner)
        try:
            assert client.preparation_contract()["version"] == 2
            attachment = await host.invoke("source", {
                "number": 42, "generation_id": loaded["result"]["descriptor"]["id"], "budget_seconds": 60,
            })
            assert attachment["state"] == "ready"
        finally:
            await owner.drain(timeout=2)
        assert owner.status()["drained"]
    asyncio.run(scenario())


@pytest.mark.parametrize("variation", ["subclass", "wrapped", "late-wrapped", "older"])
def test_request_overrides_and_older_callers_keep_metadata_without_source(
    environment, integration, tools, execution_pool, monkeypatch, variation,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from fleet_manager.gateway_service_caller import ServiceCaller
    from fleet_manager.credential_references import ServiceCredentials
    from agent_pull_requests.workspace_client import WorkspaceClient
    from agent_pull_requests.workspace_tools import WorkspaceTools, WorkspaceToolUnavailable
    _, existing, _, _, transport, _ = integration
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    calls = []
    async def wrapped(*args, **kwargs):
        calls.append(True)
        return await transport.request(*args, **kwargs)
    class Derived(ServiceCaller):
        async def request(self, *args, **kwargs):
            return await wrapped(*args, **kwargs)
    credentials = ServiceCredentials(None, Path(__file__).resolve())
    caller_type = Derived if variation == "subclass" else ServiceCaller
    caller = caller_type(gateway_origin="https://127.0.0.1:443", proof_key=None,
                         access_token_callback=credentials.access_token)
    if variation in ("wrapped", "older"):
        caller.request = wrapped
    if variation == "older":
        monkeypatch.delattr(ServiceCaller, "_bind_source_request")
    client = WorkspaceClient(caller, route_prefix="", binding=existing._binding, scope=initial["scope"])
    if variation == "late-wrapped":
        assert client.preparation_contract()["version"] == 2
        caller.request = wrapped
    assert client.preparation_contract()["version"] == 1
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(client, source_owner=owner)
    async def scenario():
        with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
            await host.invoke("source", {"number": 42, "generation_id": initial["id"], "budget_seconds": 60})
        assert not calls and owner.status()["pending"] == 0
        assert (await client.capabilities())["generation_versions"] == [1]
        assert calls
        await owner.drain(timeout=2)
    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["capabilities", "_materialize"])
def test_source_read_pins_valid_manifest_before_first_metadata_await(
    environment, integration, tools, execution_pool, monkeypatch, boundary,
):
    import copy
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_worktrees.pr_review_source import _digest
    from agent_pull_requests.workspace_tools import WorkspaceTools
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        identity = loaded["result"]["descriptor"]["id"]
        attachment = await native_host.invoke("source", {
            "number": 42, "generation_id": identity, "budget_seconds": 60,
        })
        replacement = copy.deepcopy(attachment)
        replacement["source"].pop("id")
        replacement["source"]["elapsed_seconds"] += 1
        replacement["source"]["id"] = _digest(replacement["source"])
        assert native_host.source_owner.read_retained(
            replacement["source"], "/note.txt", "after", budget=60,
        )["source_id"] == replacement["source"]["id"]
        owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
        host = WorkspaceTools(integration[2], source_owner=owner)
        entered, release = asyncio.Event(), asyncio.Event()
        original = getattr(WorkspaceTools, boundary)
        async def pause(self, *args, **kwargs):
            if self is host:
                entered.set()
                await release.wait()
            return await original(self, *args, **kwargs)
        monkeypatch.setattr(WorkspaceTools, boundary, pause)
        request = {"number": 42, "generation_id": identity, "budget_seconds": 60,
                   "source_attachment": attachment, "path": "/note.txt", "side": "after"}
        caller = asyncio.create_task(host.invoke("source-read", request))
        await asyncio.wait_for(entered.wait(), 2)
        attachment["source"] = replacement["source"]
        release.set()
        with pytest.raises(ValueError, match="source_selection_changed"):
            await caller
        await owner.drain(timeout=2)
    asyncio.run(scenario())
