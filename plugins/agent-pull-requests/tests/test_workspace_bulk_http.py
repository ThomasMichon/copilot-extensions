"""Bulk observer qualification over real fresh-challenge HTTP and WebSocket."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_pull_requests import workspace_bulk
from agent_pull_requests import workspace_observer as observer
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.review_workspace import MAX_GENERATION_BYTES, _digest, build_generation
from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore
from agent_pull_requests.workspace_operations import WorkspaceOperations
from test_native_read import native as native
from test_review_owner_store import prepared_intent as prepared_intent
from test_review_workspace import evidence as evidence
from test_workspace_integration import environment as environment, integration as integration
from test_workspace_terminal_retention import workspace as workspace
from test_workspace_terminal_read import terminal_reader as terminal_reader
from test_workspace_observer import (
    NativeFixture, _fleet_fixtures, connection, write_profiles,
)


class _WallClock:
    @property
    def value(self) -> int:
        return int(time.time())

    def __call__(self) -> int:
        return self.value


def sized_generation(evidence: dict[str, Any], size: int) -> dict[str, Any]:
    generation: dict[str, Any] = build_generation(evidence)["generation"]
    pull = generation["sections"]["pull_request"]["data"]
    pull["description"] = ""
    overhead = len(json.dumps(generation, allow_nan=False).encode("utf-8"))
    pull["description"] = "x" * (size - overhead)
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    assert len(json.dumps(generation, allow_nan=False).encode("utf-8")) == size
    return generation


@pytest.mark.parametrize(("size", "mode"), [
    pytest.param(4 * 1024 * 1024, "new", id="4m-new"),
    pytest.param(MAX_GENERATION_BYTES, "new", id="16m-new"),
    pytest.param(128 * 1024, "legacy-caller", id="legacy-wire-caller-new-provider"),
    pytest.param(128 * 1024, "direct-exchange", id="public-chunk-exchange-stays-single-frame"),
    pytest.param(128 * 1024, "dishonest-metadata", id="invalid-optional-diagnostics-are-merged"),
    pytest.param(128 * 1024, "legacy-provider", id="new-caller-legacy-wire-provider"),
    pytest.param(128 * 1024, "rollback", id="selected-provider-legacy-wire-rollback"),
    pytest.param(128 * 1024, "dishonest-discovery", id="invalid-optional-discovery"),
    pytest.param(128 * 1024, "partial-group", id="partial-group-continues-without-replay"),
    pytest.param(128 * 1024, "escaping", id="encoded-escaping-splits-group"),
    pytest.param(128 * 1024, "retained", id="retained-generation-after-pointer-change"),
    pytest.param(128 * 1024, "empty-group", id="empty-group-refused"),
    pytest.param(128 * 1024, "missing-frame", id="missing-middle-frame-refused"),
    pytest.param(128 * 1024, "offset", id="out-of-order-frame-refused"),
    pytest.param(128 * 1024, "scope", id="foreign-member-scope-refused"),
    pytest.param(128 * 1024, "base64", id="invalid-member-encoding-refused"),
    pytest.param(128 * 1024, "truncated", id="truncated-member-refused"),
    pytest.param(128 * 1024, "digest", id="complete-but-dishonest-body-refused"),
    pytest.param(1024 * 1024, "failed-group", id="failed-later-group-not-complete"),
    pytest.param(1024 * 1024, "interrupted", id="interrupted-later-group-not-complete"),
])
@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.timeout(90)
def test_full_size_cli_bulk_uses_existing_authenticated_and_immutable_owners(
    native: NativeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    size: int, mode: str, record_property: Callable[[str, object], None],
) -> None:
    from cryptography.hazmat.primitives import serialization
    from fleet_manager.gateway_service_channel import BODY_BYTES, RESPONSE_BYTES
    from fleet_manager.gateway_service_connector import ServiceConnector
    from fleet_manager.gateway_service_handler import BoundedServiceResponse, ServiceRequest, ServiceResponse

    reader, _, _, evidence_document = native
    if mode == "escaping":
        principal = "reviewer-" + "\U0001f600" * 1000
        evidence_document["evidence"]["scope"]["principal_id"] = principal
        scope = {**reader.scope, "principal_id": principal}
        reader = type(reader)(
            command=reader.command, credential_command=reader.credential_command,
            repo=reader.repo, scope=scope, binding_ref=reader.binding_ref,
            max_work_seconds=reader.max_work_seconds,
        )
    generation = sized_generation(evidence_document["evidence"], size)
    root = tmp_path / "client"
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(root))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT"):
        monkeypatch.delenv(key, raising=False)
    store = ReplayStore(
        tmp_path / "owner", "namespace",
        service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a"),
    )
    store.provision(conversations=True)
    cache = GenerationCache(
        store.root, store.namespace, max_bytes=2 * MAX_GENERATION_BYTES, max_generations=20,
        service_identity=store.service_identity,
    )
    cache.provision()
    assert cache.publish(generation, expected_current=None)["status"] == "published"
    assert (cache.generations / (generation["id"] + ".json")).stat().st_size == size
    if mode == "retained":
        later = json.loads(json.dumps(generation))
        later["sections"]["pull_request"]["data"]["title"] = "Later synthetic change"
        later["id"] = _digest({key: value for key, value in later.items() if key != "id"})
        assert cache.publish(later, expected_current=generation["id"])["status"] == "published"
    workspace = WorkspaceOperations(
        store, cache, scope=dict(reader.scope), read_owner=reader, binding_ref="selected",
        checkpoint=lambda: None, max_work_seconds=10, conversations=True,
    )
    gateway = WatchGateway(
        store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        bindings=(WatchScopeBinding(
            "caller-a", "project", ConsumerScope("consumer-a", "account-a", "project/repository"),
        ),), workspaces={("caller-a", "project"): workspace},
    )

    async def scenario() -> None:
        tls = tmp_path / "tls"
        tls.mkdir()
        rig = _fleet_fixtures()(tls)
        rig.h.clock = _WallClock()
        requests: list[tuple[str, str | None]] = []
        sizes: list[int] = []
        offsets: list[int] = []
        reply_counts: list[int] = []
        limit = 128 * 1024 if mode == "escaping" else RESPONSE_BYTES
        async def handle(request: ServiceRequest) -> ServiceResponse | BoundedServiceResponse:
            requests.append((request.target, dict(request.headers).get("accept")))
            chunk = request.target.endswith("/chunk")
            if chunk:
                offsets.append(json.loads(request.body)["request"]["offset"])
                if len(offsets) == 2 and mode == "failed-group":
                    return ServiceResponse(503)
                if len(offsets) == 2 and mode == "interrupted":
                    request.lifetime.close()
            if mode == "legacy-provider" or mode == "rollback" and chunk:
                request = replace(request, headers=tuple(
                    (key, value) for key, value in request.headers if key != "accept"
                ))
            result = await gateway.handle(request)
            if mode in ("dishonest-discovery", "dishonest-metadata") and request.target.endswith("/capabilities"):
                value = json.loads(result.body)
                value[workspace_bulk.FIELD]["response_bytes"] = RESPONSE_BYTES + 1
                if mode == "dishonest-metadata":
                    value["conversation_contract"] = {}
                result = workspace_bulk.response(json.dumps(value).encode("utf-8"))
            if chunk and result.status == 200 and mode not in (
                "legacy-provider", "rollback", "dishonest-discovery", "legacy-caller",
                "direct-exchange", "dishonest-metadata",
            ):
                value = json.loads(result.body)
                frames = value["result"]["chunks"]
                if mode == "partial-group" and len(frames) > 1:
                    frames.pop()
                elif mode == "empty-group":
                    frames.clear()
                elif mode == "missing-frame":
                    frames.pop(1)
                elif mode == "offset":
                    frames[0]["offset"] += 1
                elif mode == "scope":
                    frames[0]["descriptor"]["scope"]["principal_id"] = "foreign-reviewer"
                elif mode == "base64":
                    frames[0]["data"] = "!"
                elif mode == "truncated":
                    frames[0]["data"] = ""
                elif mode == "digest":
                    frames[0]["data"] = base64.b64encode(b"z" * 8192).decode("ascii")
                reply_counts.append(len(frames))
                result = workspace_bulk.response(json.dumps(value, allow_nan=False).encode("utf-8"))
            sizes.append(len(result.body))
            return result
        async def access() -> str:
            return rig.access("service-a", 1)
        task: asyncio.Task[None] | None = None
        try:
            await rig.start()
            from fleet_manager.gateway_service_handler import HandlerBinding
            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin,
                handler=HandlerBinding("fleet-a", "gateway-a", handle, response_limit=limit),
                access_token_callback=access, proof_key=rig.h.keys[1].get_op_key("sign"),
                gateway_ca=rig.ca,
            )
            task = asyncio.create_task(connector.connect_once())
            await asyncio.wait_for(connector.connected.wait(), 3)
            selected = connection(root)
            selected.update(gateway_origin=rig.origin, gateway_ca=rig.ca)
            selected["scope"] = dict(reader.scope)
            write_profiles(root, selected)
            (root / "proof.pem").write_bytes(rig.h.keys[3].get_op_key("sign").private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            ))
            (root / "access").write_text(rig.access("caller-a", 3), encoding="utf-8")
            if mode in ("legacy-caller", "direct-exchange", "dishonest-metadata"):
                from fleet_manager.gateway_service_caller import ServiceCaller
                from fleet_manager.gateway_service_channel import CONTROL_RATE
                from agent_pull_requests.workspace_operations import encoded, materialize

                async def caller_access() -> str:
                    return rig.access("caller-a", 3)
                caller = ServiceCaller(
                    gateway_origin=rig.origin, proof_key=rig.h.keys[3].get_op_key("sign"),
                    access_token_callback=caller_access, gateway_ca=rig.ca,
                )
                if mode in ("direct-exchange", "dishonest-metadata"):
                    from agent_pull_requests.workspace_client import WorkspaceClient
                    from agent_pull_requests.watch_replay_binding import ReplayBinding
                    client = WorkspaceClient(
                        caller, route_prefix="/api",
                        binding=ReplayBinding.from_document(selected["binding"]), scope=dict(reader.scope),
                    )
                    capabilities = await client.capabilities()
                    if mode == "dishonest-metadata":
                        assert capabilities["unavailable_operations"] == {
                            "bulk_materialization": "unsupported_workspace_bulk_contract",
                            "events": "unsupported_conversation_contract",
                        }
                    else:
                        assert workspace_bulk.supports(capabilities[workspace_bulk.FIELD])
                    chunk = await client.exchange("chunk", {
                        "number": 42, "generation_id": generation["id"], "offset": 0,
                    })
                    assert set(chunk["result"]) == {"descriptor", "offset", "data"}
                    assert chunk["result"]["offset"] == 0
                    assert len(base64.b64decode(chunk["result"]["data"])) == 8192
                    assert requests[-1][1] is None
                    return
                async def baseline(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
                    result = await caller.request(
                        "POST", "/api/v2/review/project/" + operation,
                        request_headers=(("Content-Type", "application/json"),),
                        body=encoded({"binding": selected["binding"], "request": payload}),
                        response_limit=BODY_BYTES,
                    )
                    assert result.status == 200
                    value: dict[str, Any] = json.loads(result.body)
                    assert value.pop("binding") == selected["binding"]
                    return value
                async def legacy() -> None:
                    capabilities = await baseline("capabilities", {})
                    assert workspace_bulk.FIELD not in capabilities
                    assert capabilities == workspace.execute(
                        "capabilities", {}, expires_at=int(time.time()) + 60, checkpoint=lambda: None,
                    )
                    lookup = await baseline("lookup", {"number": 42, "generation_id": generation["id"]})
                    descriptor = lookup["result"]["descriptor"]
                    blocks: dict[int, dict[str, Any]] = {}
                    for offset in range(0, descriptor["bytes"], 8192):
                        await asyncio.sleep(2 / CONTROL_RATE)
                        chunk = await baseline("chunk", {
                            "number": 42, "generation_id": generation["id"], "offset": offset,
                        })
                        assert set(chunk["result"]) == {"descriptor", "offset", "data"}
                        blocks[offset] = chunk["result"]
                    assert materialize(descriptor, blocks.__getitem__) == generation
                    assert all(accept is None for _, accept in requests)
                await asyncio.wait_for(legacy(), 60)
                return
            started = time.monotonic()
            result = await asyncio.to_thread(
                run_caller_owned,
                [sys.executable, "-m", "agent_pull_requests", "workspace",
                 "--client-profile", "selected", "materialize", "--request", "-"],
                timeout=60, max_stdout_bytes=observer.OUTPUT_BYTES, max_stderr_bytes=65536,
                input_text=json.dumps({"number": 42, "generation_id": generation["id"]}),
                env=dict(os.environ),
            )
            elapsed = time.monotonic() - started
            chunks = [entry for entry in requests if entry[0].endswith("/chunk")]
            record_property("bulk_storage_generation_bytes", size)
            record_property("bulk_wire_generation_bytes", workspace._descriptor(generation)["bytes"])
            record_property("bulk_cli_elapsed_seconds", elapsed)
            record_property("bulk_authenticated_chunk_requests", len(chunks))
            if mode in (
                "empty-group", "missing-frame", "offset", "scope", "base64",
                "truncated", "digest", "failed-group", "interrupted",
            ):
                assert result.returncode != 0
                assert isinstance(result.stdout, str)
                assert json.loads(result.stdout)["effect_completion"] == "not_inferred"
                assert generation["id"] not in result.stdout
                if mode in ("failed-group", "interrupted"):
                    assert len(offsets) == 2 and offsets[1] > offsets[0]
                return
            assert result.returncode == 0, (result.stderr, result.stdout)
            assert result.stderr == "" and isinstance(result.stdout, str)
            assert json.loads(result.stdout) == generation
            assert elapsed < 60
            assert chunks and max(sizes) <= limit
            if mode in ("legacy-provider", "dishonest-discovery"):
                assert all(accept is None for _, accept in chunks)
            else:
                assert all(accept == workspace_bulk.MEDIA_TYPE for _, accept in chunks)
            if mode in ("legacy-provider", "dishonest-discovery", "rollback"):
                assert all(right - left == 8192 for left, right in zip(offsets, offsets[1:]))
            elif mode == "partial-group":
                assert len(chunks) > 1
            elif mode == "escaping":
                assert max(reply_counts) < workspace_bulk.MAX_CHUNKS
                assert len(chunks) > 1 and max(sizes) > BODY_BYTES
            else:
                assert len(chunks) < 64
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await gateway.drain(timeout=2)
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("environment", [True], indirect=True)
@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.timeout(90)
def test_terminal_retirement_during_a_bulk_group_never_returns_prior_frames(
    terminal_reader: tuple[Any, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_manager.gateway_service_caller import ServiceCaller
    from fleet_manager.gateway_service_channel import RESPONSE_BYTES
    from fleet_manager.gateway_service_connector import ServiceConnector
    from agent_pull_requests.workspace_client import WorkspaceClient, WorkspaceClientError
    from test_workspace_terminal_retention import terminal

    operations, gateway, _, native_client, _, backend, _, binding, initial = terminal_reader
    backend.pr["description"] = "x" * (128 * 1024)
    native_client.exchange("capabilities", {})
    loaded = native_client.exchange("load", {
        "number": 42, "expected_current": initial["id"], "budget_seconds": 10,
    })
    identity = loaded["result"]["descriptor"]["id"]
    generation = operations.cache.lookup(initial["scope"], generation_id=identity)["generation"]
    execute = operations.execute
    retired: list[dict[str, Any]] = []
    def retire_after_member(operation: str, payload: dict[str, Any], **options: Any) -> dict[str, Any]:
        value: dict[str, Any] = execute(operation, payload, **options)
        if operation == "chunk" and payload["offset"] == 8192:
            observed, _ = terminal(operations, generation)
            retired.append(observed)
        return value
    monkeypatch.setattr(operations, "execute", retire_after_member)

    async def scenario() -> None:
        tls = tmp_path / "terminal-tls"
        tls.mkdir()
        rig = _fleet_fixtures()(tls)
        rig.h.clock = _WallClock()
        async def service_access() -> str:
            return rig.access("service-a", 1)
        async def caller_access() -> str:
            return rig.access("caller-a", 3)
        task: asyncio.Task[None] | None = None
        try:
            await rig.start()
            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin,
                handler=gateway.handler_binding(response_limit=RESPONSE_BYTES),
                access_token_callback=service_access, proof_key=rig.h.keys[1].get_op_key("sign"),
                gateway_ca=rig.ca,
            )
            task = asyncio.create_task(connector.connect_once())
            await asyncio.wait_for(connector.connected.wait(), 3)
            caller = ServiceCaller(
                gateway_origin=rig.origin, proof_key=rig.h.keys[3].get_op_key("sign"),
                access_token_callback=caller_access, gateway_ca=rig.ca,
            )
            client = WorkspaceClient(
                caller, route_prefix="/api", binding=binding, scope=dict(operations.scope),
            )
            capabilities = await client.capabilities()
            assert workspace_bulk.supports(capabilities[workspace_bulk.FIELD])
            with pytest.raises(WorkspaceClientError):
                await asyncio.wait_for(client.materialize(42, identity), 60)
            assert retired and retired[0]["status"] == "observed"
            with pytest.raises(WorkspaceClientError):
                await client.exchange("chunk", {"number": 42, "generation_id": identity, "offset": 0})
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await gateway.drain(timeout=2)
            await rig.close()
    asyncio.run(scenario())
