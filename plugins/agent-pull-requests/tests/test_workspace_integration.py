"""Real storage, handlers, protocol framing and protected synthetic transport."""

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest

from test_review_executor import environment as environment

from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.owner_read import OwnerReadBinding, OwnerReadHandler, PROFILE, PRINCIPAL_SEMANTICS
from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_binding import ReplayBinding
from agent_pull_requests.watch_replay_client import ReplayClient
from agent_pull_requests.workspace_client import WorkspaceClient, WorkspaceClientError
from agent_pull_requests.workspace_operations import NativeWorkspaceClient, WorkspaceOperations

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def integration(environment):
    store, selected, generation, attachment, backend, _ = environment
    scope = dict(selected["scope"])
    scope.pop("number")
    cache = GenerationCache(store.root, store.namespace, max_bytes=4 * 1024 * 1024,
                            max_generations=20, service_identity=store.service_identity)
    cache.provision()
    cache.publish(generation, expected_current=None)
    evidence = json.loads((Path(__file__).parents[1] / "docs/review-evidence.example.json").read_text())
    evidence["sections"]["changes"]["data"] = []
    evidence["sections"]["discussions"] = copy.deepcopy(generation["sections"]["discussions"])
    evidence["acquisition"] = {
        "source": PROFILE, "effects": "none", "principal_semantics": PRINCIPAL_SEMANTICS,
    }

    def collect(number, remaining):
        remaining()
        value = copy.deepcopy(evidence)
        value["scope"]["number"] = number
        value["sections"]["pull_request"]["data"] = copy.deepcopy(backend.pr)
        value["sections"]["iterations"]["data"] = copy.deepcopy(backend.iterations)
        value["sections"]["discussions"]["data"] = copy.deepcopy(backend.threads)
        return value

    reader = OwnerReadHandler(
        lambda ref, remaining: OwnerReadBinding(scope, "synthetic-existing-context", backend.checkpoint, collect),
        max_work_seconds=120, max_payload_bytes=2 * 1024 * 1024,
    )
    operations = WorkspaceOperations(
        store, cache, scope=scope, read_owner=reader, binding_ref="selected",
        write_context=lambda number: backend.context(), checkpoint=backend.checkpoint, max_work_seconds=120,
    )
    watch_scope = ConsumerScope("consumer-a", "account-a", "example/project")
    gateway = WatchGateway(
        store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        bindings=(WatchScopeBinding("caller-a", "project", watch_scope),),
        workspaces={("caller-a", "project"): operations},
    )
    binding = ReplayBinding(
        "fleet-a", "gateway-a", "service-a", store.namespace,
        "caller-a", "project", watch_scope.consumer, watch_scope.account, watch_scope.repo,
    )

    class Caller:
        corrupt = None
        after_chunk = None
        calls = []

        async def request(self, method, target, *, request_headers, body, response_limit):
            self.calls.append(target)
            response = await gateway.handle(ServiceRequest(
                CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", int(time.time()) + 600),
                method, target, tuple((k.lower(), v) for k, v in request_headers), body,
            ))
            if self.after_chunk is not None and target.endswith("/chunk"):
                callback, self.after_chunk = self.after_chunk, None
                callback()
            if self.corrupt == "missing_descriptor" and target.endswith("/lookup"):
                from dataclasses import replace
                value = json.loads(response.body)
                value["result"].pop("descriptor", None)
                return replace(response, body=json.dumps(value).encode())
            if self.corrupt and target.endswith("/chunk"):
                from dataclasses import replace
                data = json.loads(response.body)
                data["result"]["offset"] += 1
                response = replace(response, body=json.dumps(data).encode())
            return response

    caller = Caller()
    client = WorkspaceClient(caller, route_prefix="", binding=binding, scope=scope)
    native = NativeWorkspaceClient(operations, checkpoint=backend.checkpoint,
                                   expires_at=lambda: int(time.time()) + 600)
    return gateway, client, native, evidence, caller, binding


def test_native_and_wire_materialize_same_generation_and_strict_watch_remains_compatible(environment, integration):
    _, selected, generation, _, _, _ = environment
    gateway, client, native, _, caller, binding = integration

    async def scenario():
        with pytest.raises(WorkspaceClientError, match="capability"):
            await client.exchange("lookup", {"number": 42})
        await client.capabilities()
        remote = await client.materialize(42, generation["id"])
        assert remote == native.materialize(42, generation["id"]) == generation
        legacy = ReplayClient(caller, route_prefix="", target="project", repo=binding.repo, binding=binding)
        assert (await legacy.capabilities()).binding == binding
        result = await client.exchange("sync", {
            "number": 42, "previous_id": selected["generation_id"],
            "expected_current": generation["id"], "budget_seconds": 120,
        })
        assert result["result"]["comparison"]["changed_sections"] == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["reply", "edit", "delete", "head", "target", "partial"])
def test_real_read_owner_sync_preserves_deltas_and_incomplete_evidence(environment, integration, change):
    _, _, generation, _, backend, _ = environment
    gateway, client, _, evidence, _, _ = integration
    if change in ("reply", "edit", "delete"):
        backend.threads = [{"id": 10, "status": 1, "comments": [
            {"id": 1, "content": change, "author": {"id": "another-author"}, "isDeleted": change == "delete"}]}]
    elif change == "head":
        latest = max(backend.iterations, key=lambda row: row["id"])
        latest["sourceRefCommit"]["commitId"] = backend.pr["lastMergeSourceCommit"]["commitId"] = "f" * 40
    elif change == "target":
        backend.pr["lastMergeTargetCommit"]["commitId"] = "e" * 40
    else:
        evidence["sections"]["discussions"].update(complete=False, errors=["synthetic_partial"])

    async def scenario():
        await client.capabilities()
        result = (await client.exchange("sync", {
            "number": 42, "previous_id": generation["id"], "expected_current": generation["id"], "budget_seconds": 120,
        }))["result"]
        observed = await client.materialize(42, result["descriptor"]["id"])
        assert observed["id"] != generation["id"]
        assert observed["sections"]["discussions"]["observed_from"] == evidence["sections"]["discussions"]["observed_from"]
        if change == "partial":
            assert observed["sections"]["discussions"]["complete"] is False
            assert observed["sections"]["discussions"]["errors"] == ["synthetic_partial"]
        assert result["publication"]["status"] == "published"
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_explicit_wire_submit_uses_actual_merged_executor_and_unknowns_are_not_reposted(environment, integration):
    _, selected, _, attachment, backend, _ = environment
    gateway, client, _, _, _, _ = integration
    backend.lose_reply = True

    async def scenario():
        await client.capabilities()
        payload = {"number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 120}
        first = (await client.exchange("submit", payload))["result"]
        assert first["status"] == "unknown"
        assert len(backend.threads) == 1 and backend.vote is None
        inspected = (await client.exchange("inspect", {"number": 42, "operation_id": selected["operation_id"]}))["result"]
        assert inspected["actions"][0]["state"] == "unknown"
        backend.lose_reply = False
        assert (await client.exchange("submit", payload))["result"]["status"] == "complete"
        assert len(backend.threads) == 1 and backend.vote == 10
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_transport_corruption_and_oversized_submit_refuse_without_effect(environment, integration):
    _, selected, generation, attachment, backend, _ = environment
    gateway, client, _, _, caller, _ = integration

    async def scenario():
        await client.capabilities()
        caller.corrupt = True
        with pytest.raises(WorkspaceClientError, match="chunk_changed"):
            await client.materialize(42, generation["id"])
        caller.corrupt = False
        too_large = copy.deepcopy(selected)
        too_large["actions"][0]["body"] = "x" * 40000
        before = len(caller.calls)
        with pytest.raises(WorkspaceClientError) as error:
            await client.exchange("submit", {"number": 42, "intent": too_large, "source_attachment": attachment, "budget_seconds": 120})
        assert not error.value.outcome_unknown and len(caller.calls) == before
        assert backend.threads == [] and backend.vote is None
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_large_generation_is_framed_and_old_reader_remains_pinned_after_current_moves(environment, integration, monkeypatch):
    _, _, generation, _, backend, _ = environment
    gateway, client, _, _, _, _ = integration
    backend.pr["description"] = "Synthetic large description " * 3000

    async def scenario():
        await client.capabilities()
        loaded = (await client.exchange("load", {
            "number": 42, "expected_current": generation["id"], "budget_seconds": 120,
        }))["result"]
        assert loaded["descriptor"]["bytes"] > 32768
        original = GenerationCache._load_generation
        reads = []

        def counted(cache, identity, scope):
            reads.append(identity)
            return original(cache, identity, scope)

        monkeypatch.setattr(GenerationCache, "_load_generation", counted)
        expanded = await client.materialize(42, loaded["descriptor"]["id"])
        assert reads == [expanded["id"]]
        assert expanded["sections"]["pull_request"]["data"]["description"] == backend.pr["description"]
        assert await client.materialize(42, generation["id"]) == generation
        conflict = (await client.exchange("load", {
            "number": 42, "expected_current": generation["id"], "budget_seconds": 120,
        }))["result"]
        assert conflict["publication"]["status"] == "conflict"
        assert conflict["publication"]["confirmed_current_id"] == expanded["id"]
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_disconnected_load_remains_owned_until_drain_and_cannot_publish(environment, integration):
    _, _, generation, _, backend, _ = environment
    gateway, _, native, _, _, binding = integration
    entered, release = threading.Event(), threading.Event()
    owner = native.operations.read_owner
    resolve = owner.resolve_binding

    def blocked(ref, remaining):
        value = resolve(ref, remaining)

        def collect(number, budget):
            entered.set()
            assert release.wait(5)
            return value.collect(number, budget)

        return OwnerReadBinding(value.scope, value.context_id, value.checkpoint, collect)

    owner.resolve_binding = blocked
    backend.pr["title"] = "A source refresh never published after disconnect"

    async def scenario():
        request = ServiceRequest(
            CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", int(time.time()) + 600),
            "POST", "/v2/review/project/load", (("content-type", "application/json"),),
            json.dumps({"binding": binding.document(), "request": {
                "number": 42, "expected_current": generation["id"], "budget_seconds": 120,
            }}).encode(),
        )
        task = asyncio.create_task(gateway.handle(request))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(TimeoutError):
            await gateway.drain(timeout=0.05)
        assert gateway._tasks
        release.set()
        await gateway.drain(timeout=3)
        assert not gateway._tasks
        assert native.operations.cache.lookup(generation["scope"])["current_id"] == generation["id"]

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_unexpanded_peer_and_absent_write_context_do_not_claim_support(environment, integration):
    _, _, _, _, backend, _ = environment
    gateway, client, native, _, _, _ = integration
    native.operations.write_context = None

    async def scenario():
        assert "submit" not in (await client.capabilities())["operations"]
        with pytest.raises(WorkspaceClientError, match="capability"):
            await client.exchange("submit", {"number": 42})
        gateway.workspaces = {}
        with pytest.raises(WorkspaceClientError, match="unsupported"):
            await client.capabilities()
        with pytest.raises(WorkspaceClientError, match="capability"):
            await client.exchange("lookup", {"number": 42})
        assert backend.threads == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_missing_lookup_descriptor_is_a_structured_client_refusal(environment, integration):
    _, _, generation, _, backend, _ = environment
    gateway, client, _, _, caller, _ = integration

    async def scenario():
        await client.capabilities()
        caller.corrupt = "missing_descriptor"
        with pytest.raises(WorkspaceClientError, match="invalid_workspace_descriptor"):
            await client.materialize(42, generation["id"])
        assert backend.threads == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("replacement", ["identical", "corrupt"])
def test_pinned_chunks_revalidate_republication_but_reject_changed_content(environment, integration, replacement):
    _, _, initial, _, backend, _ = environment
    gateway, client, native, _, caller, _ = integration
    backend.pr["description"] = "Synthetic long description " * 3000

    async def scenario():
        await client.capabilities()
        descriptor = (await client.exchange("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 120,
        }))["result"]["descriptor"]
        generation = native.operations.cache.lookup(initial["scope"], generation_id=descriptor["id"])["generation"]

        def replace_content():
            if replacement == "identical":
                assert native.operations.cache.publish(generation, expected_current=generation["id"])["status"] == "published"
            else:
                path = native.operations.cache.generations / (generation["id"] + ".json")
                corrupted = copy.deepcopy(generation)
                corrupted["sections"]["pull_request"]["data"]["description"] = "Changed without a matching content ID"
                path.write_text(json.dumps(corrupted), encoding="utf-8")

        caller.after_chunk = replace_content
        if replacement == "identical":
            assert await client.materialize(42, descriptor["id"]) == generation
        else:
            with pytest.raises(WorkspaceClientError):
                await client.materialize(42, descriptor["id"])
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_concurrent_owner_refresh_cas_preserves_one_complete_current_generation(environment, integration):
    from single_instance_lease import OperationLockedError
    _, _, generation, _, backend, _ = environment
    _, _, native, _, _, _ = integration
    rendezvous = threading.Barrier(2)
    reader = native.operations.read_owner
    resolver = reader.resolve_binding

    def resolve(ref, remaining):
        bound = resolver(ref, remaining)

        def collect(number, budget):
            result = bound.collect(number, budget)
            rendezvous.wait(timeout=5)
            return result

        return OwnerReadBinding(bound.scope, bound.context_id, bound.checkpoint, collect)

    reader.resolve_binding = resolve
    backend.pr["title"] = "Synthetic concurrent changed generation"

    def refresh():
        try:
            return native.exchange("load", {
                "number": 42, "expected_current": generation["id"], "budget_seconds": 120,
            })["result"]["publication"]["status"]
        except OperationLockedError:
            return "busy"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: refresh(), range(2)))
    assert outcomes.count("published") == 1
    assert set(outcomes) <= {"published", "conflict", "busy"}
    current = native.operations.cache.lookup(generation["scope"])
    assert current["generation"]["id"] == current["current_id"] != generation["id"]
    assert native.materialize(42, generation["id"]) == generation
