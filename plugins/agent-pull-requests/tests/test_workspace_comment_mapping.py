"""Optional acquired mapping through the actual workspace, parser and Gateway."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.review_comment_mapping import CommentMappingStore
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_client import WorkspaceClientError
from agent_pull_requests.workspace_comment_mapping import contract
from agent_pull_requests.workspace_operations import WorkspaceUnavailable
from agent_pull_requests.workspace_tools import WorkspaceTools
from test_review_comment_observation import retained_bytes
from test_review_executor import environment as environment
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_integration import integration as integration, prepared_workspace as prepared_workspace
from test_workspace_owned_edit_selection import owned_workspace as owned_workspace, select_edit
from test_workspace_selection import selection_workspace as selection_workspace
from test_workspace_tools import invoke_cli

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def enable(native) -> None:
    ConversationStore(native.operations.store).adopt(
        lifecycle_checkpoint=lambda: None, expires_at=native.expires_at(),
    )
    CommentMappingStore(native.operations.store).adopt(
        lifecycle_checkpoint=lambda: None, expires_at=native.expires_at(),
    )
    native.operations.conversations = True
    native.operations.comment_mappings = True


def later(evidence) -> None:
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:02:00Z", observed_to="2026-10-30T00:02:01Z")


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
@pytest.mark.parametrize("acquisition", ["load", "sync"])
def test_actual_acquisition_and_retained_cli_read_survive_restart_without_cache_or_writer(
    environment, prepared_workspace, owned_workspace, monkeypatch, capsys, transport, kind, acquisition,
) -> None:
    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, _ = owned_workspace
    enable(native)
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}

    def send():
        code, preview = invoke_cli(host, "allocate", request, monkeypatch, capsys)
        assert code == 0, preview
        chosen = preview["result"]["intent"]
        code, result = invoke_cli(host, "submit", {**request, "intent": chosen}, monkeypatch, capsys)
        assert code == 0 and result["result"]["status"] == "complete", result
        return chosen

    chosen = send()
    if kind != "comment":
        select_edit(environment, prepared_workspace, draft)
        if kind == "reply":
            draft["actions"] = [{"id": "owned-finding", "kind": "reply", "body": "Reply.", "thread_id": 100}]
        chosen = send()
    action_id = chosen["actions"][0]["id"]
    original = store.prepared_review_receipt(
        chosen["operation_id"], chosen["scope"], action_id, expires_at=native.expires_at(),
    )
    read = native.operations.read_owner.read
    reads = []

    def acquire(*args, **kwargs):
        reads.append(1)
        return read(*args, **kwargs)

    monkeypatch.setattr(native.operations.read_owner, "read", acquire)
    current = native.operations.cache.lookup(chosen["scope"])["current_id"]
    calls = list(backend.calls)
    code, loaded = invoke_cli(host, acquisition, {
        "number": 42, "expected_current": current, "budget_seconds": 60,
        **({"previous_id": current} if acquisition == "sync" else {}),
    }, monkeypatch, capsys)
    assert code == 0 and loaded["result"]["publication"]["status"] == "published", loaded
    assert loaded["result"]["conversation"]["status"] == "observed"
    acquired_generation = native.operations.cache.lookup(chosen["scope"])["generation"]
    native.operations.store = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    native.operations.write_context = native.operations.selection_preparer = None

    def no_cache(*args, **kwargs):
        raise AssertionError("retained mapping must not reacquire or depend on cached bodies")

    monkeypatch.setattr(GenerationCache, "lookup", no_cache)
    before = retained_bytes(store)
    for _ in range(2):
        code, result = invoke_cli(host, "retained-comment", {
            "number": 42, "operation_id": chosen["operation_id"], "action_id": action_id,
        }, monkeypatch, capsys)
        assert code == 0, result
        retained = result["result"]
        assert retained["mapping"]["status"] == "correlated"
        assert retained["mapping"]["generation_id"] == acquired_generation["id"]
        assert retained["mapping"]["observation"]["observed_to"] == acquired_generation["sections"]["discussions"]["observed_to"]
        assert retained["identity"] == original["identity"] and retained["receipt"] == original["receipt"]
        assert retained["action_state"] == "confirmed" and retained["effects"] == "none"
        assert all(retained[name] is False for name in (
            "fresh_native_read", "original_version_proven", "feedback_progress",
        ))
        assert chosen["actions"][0]["body"] not in json.dumps(retained)
    assert reads == [1] and backend.calls == calls and retained_bytes(store) == before
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("selection", ["default", "unadopted", "enabled"])
def test_mapping_storage_and_explicit_embedding_selection_are_both_required(
    environment, prepared_workspace, selection,
) -> None:
    store, selected, _, _, backend, _ = environment
    gateway, _, native, _, _, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=native.expires_at())
    native.operations.conversations = True
    if selection != "unadopted":
        CommentMappingStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=native.expires_at())
    native.operations.comment_mappings = selection != "default"
    available = native.exchange("capabilities", {})
    assert ("retained-comment" in available["operations"]) == (selection == "enabled")
    current = native.operations.cache.lookup(selected["scope"])["current_id"]
    result = native.exchange("load", {"number": 42, "expected_current": current, "budget_seconds": 60})
    assert result["result"]["conversation"]["status"] == "observed"
    if selection != "unadopted":
        retained = CommentMappingStore(store).read(
            selected["operation_id"], selected["scope"], selected["actions"][0]["id"],
            expires_at=native.expires_at(),
        )
        assert (retained["mapping"] is not None) == (selection == "enabled")
    else:
        assert not CommentMappingStore(store).supported(expires_at=native.expires_at())
    assert backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["direct", "native", "gateway"])
@pytest.mark.parametrize("withdrawal", ["selection", "storage"])
def test_executing_receiver_rechecks_selection_and_actual_storage_after_discovery(
    environment, prepared_workspace, transport, withdrawal,
) -> None:
    store, selected, _, _, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=native.expires_at())
    prior = store.path.read_bytes()
    enable(native)
    payload = {"number": 42, "operation_id": selected["operation_id"], "action_id": selected["actions"][0]["id"]}

    async def scenario():
        discovered = await client.capabilities() if transport == "gateway" else native.exchange("capabilities", {})
        assert discovered["comment_mapping_contract"] == contract()
        if withdrawal == "selection":
            native.operations.comment_mappings = False
        else:
            store.path.write_bytes(prior)
        before = retained_bytes(store)
        with pytest.raises((WorkspaceUnavailable, WorkspaceClientError)):
            if transport == "gateway":
                await client.exchange("retained-comment", payload)
            elif transport == "native":
                native.exchange("retained-comment", payload)
            else:
                native.operations.execute(
                    "retained-comment", payload, expires_at=native.expires_at(), checkpoint=backend.checkpoint,
                )
        baseline = native.operations.execute(
            "capabilities", {}, expires_at=native.expires_at(), checkpoint=backend.checkpoint,
        )
        assert "retained-comment" not in baseline["operations"] and "inspect" in baseline["operations"]
        assert retained_bytes(store) == before and backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("support", ["prior", "missing-operation", "malformed", "withdrawn", "refreshed"])
def test_optional_negotiation_precedes_new_strict_requests(
    environment, prepared_workspace, monkeypatch, transport, support,
) -> None:
    store, selected, _, _, backend, _ = environment
    gateway, client, native, _, caller, _ = prepared_workspace
    enable(native)
    execute, dispatched = native.operations.execute, []

    def receiver(operation, payload, **kwargs):
        if operation == "retained-comment":
            dispatched.append(operation)
        result = execute(operation, payload, **kwargs)
        if operation == "capabilities":
            if support == "prior":
                result.pop("comment_mapping_contract", None)
            elif support == "missing-operation":
                result["operations"].remove("retained-comment")
            elif support == "malformed":
                result["comment_mapping_contract"]["effects"] = "send"
        return result

    monkeypatch.setattr(native.operations, "execute", receiver)

    async def scenario():
        host = WorkspaceTools(native if transport == "native" else client)
        available = await host.capabilities()
        assert ("retained-comment" in available["operations"]) == (support in ("withdrawn", "refreshed"))
        assert "load" in available["operations"] and "inspect" in available["operations"]
        if support in ("withdrawn", "refreshed"):
            native.operations.comment_mappings = False
            if support == "refreshed":
                if transport == "native":
                    native.exchange("capabilities", {})
                else:
                    await client.capabilities()
        before, calls = retained_bytes(store), len(caller.calls)
        with pytest.raises((WorkspaceClientError, WorkspaceUnavailable)):
            payload = {"number": 42, "operation_id": selected["operation_id"], "action_id": selected["actions"][0]["id"]}
            if transport == "native":
                native.exchange("retained-comment", payload)
            else:
                await client.exchange("retained-comment", payload)
        assert dispatched == (["retained-comment"] if support == "withdrawn" else [])
        assert len(caller.calls) - calls == (1 if transport == "gateway" and support == "withdrawn" else 0)
        assert retained_bytes(store) == before and backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("transport", ["native", "gateway"])
def test_additive_discovery_metadata_keeps_the_actual_retained_reader_available(
    environment, prepared_workspace, monkeypatch, capsys, transport,
) -> None:
    store, selected, _, _, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    enable(native)
    execute = native.operations.execute

    def receiver(operation, payload, **kwargs):
        result = execute(operation, payload, **kwargs)
        if operation == "capabilities":
            result["comment_mapping_contract"]["additional_hint"] = "optional_future_metadata"
        return result

    monkeypatch.setattr(native.operations, "execute", receiver)
    before = retained_bytes(store)
    code, retained = invoke_cli(WorkspaceTools(native if transport == "native" else client), "retained-comment", {
        "number": 42, "operation_id": selected["operation_id"], "action_id": selected["actions"][0]["id"],
    }, monkeypatch, capsys)
    assert code == 0 and retained["result"]["mapping"] is None, retained
    assert retained_bytes(store) == before and backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("value", [None, [], {}, {"result": []}, {"result": {}, "scope": []}])
def test_retained_result_validator_returns_false_for_malformed_envelopes(environment, value) -> None:
    from agent_pull_requests.workspace_comment_mapping import valid_result
    assert valid_result(value, {"operation_id": "selected", "action_id": "finding"}) is False


@pytest.mark.parametrize("change", [
    "operation_id", "action_id", "scope", "protocol", "effects",
    "original_version_proven", "feedback_progress", "fresh_native_read",
    "missing-result", "result-list", "missing-scope", "scope-list",
])
def test_gateway_retained_response_is_bound_and_cannot_claim_live_or_handled_progress(
    environment, prepared_workspace, monkeypatch, change,
) -> None:
    store, selected, _, _, backend, _ = environment
    gateway, client, native, _, caller, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    enable(native)
    request = caller._request

    async def corrupt(*args, **kwargs):
        response = await request(*args, **kwargs)
        if args[1].endswith("/retained-comment"):
            value = json.loads(response.body)
            if change.startswith("missing-"):
                value.pop(change.removeprefix("missing-"))
            elif change.endswith("-list"):
                value[change.removesuffix("-list")] = []
            elif change == "scope":
                value["result"]["scope"]["principal_id"] = "another-principal"
            else:
                value["result"][change] = "different"
            return replace(response, body=json.dumps(value).encode())
        return response

    monkeypatch.setattr(caller, "_request", corrupt)
    before = retained_bytes(store)

    async def scenario():
        await client.capabilities()
        with pytest.raises(WorkspaceClientError) as refused:
            await client.exchange("retained-comment", {
                "number": 42, "operation_id": selected["operation_id"], "action_id": selected["actions"][0]["id"],
            })
        assert refused.value.code == (
            "workspace_scope_changed" if change.startswith("missing-") or change.endswith("-list")
            else "invalid_retained_comment_response"
        )
        assert refused.value.outcome_unknown is False
        assert retained_bytes(store) == before and backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("publication", ["conflict", "full", "unconfirmed"])
def test_unconfirmed_or_rejected_cache_publication_never_ingests_mapping(
    environment, prepared_workspace, monkeypatch, publication,
) -> None:
    from agent_pull_requests import generation_cache
    from agent_pull_requests.watch_storage import StateCommitUncertain

    store, selected, _, _, backend, _ = environment
    gateway, _, native, evidence, _, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    enable(native)
    later(evidence)
    current = native.operations.cache.lookup(selected["scope"])["current_id"]
    if publication == "full":
        native.operations.cache.max_generations = 1
        native.operations.cache.expected_descriptor["limits"]["max_generations"] = 1
        native.operations.cache.descriptor.write_text(json.dumps(native.operations.cache.expected_descriptor))
    if publication == "unconfirmed":
        write = generation_cache._atomic_write_json

        def uncertain(path, value):
            write(path, value)
            if path.parent == native.operations.cache.targets:
                raise StateCommitUncertain("controlled cache pointer uncertainty")

        monkeypatch.setattr(generation_cache, "_atomic_write_json", uncertain)
    before = store.path.read_bytes()
    result = native.exchange("load", {
        "number": 42, "expected_current": None if publication == "conflict" else current, "budget_seconds": 60,
    })["result"]
    assert result["publication"]["status"] == publication
    assert result["conversation"] == {"status": "not_observed", "reason": "cache_publication_not_confirmed"}
    assert store.path.read_bytes() == before and backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("uncertain", [False, True])
def test_actual_load_failure_after_publication_reconciles_without_resending_native_write(
    environment, prepared_workspace, monkeypatch, uncertain,
) -> None:
    from agent_pull_requests import watch_replay_store as storage
    from agent_pull_requests.watch_storage import StateCommitUncertain

    store, selected, _, _, backend, _ = environment
    gateway, _, native, evidence, _, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    enable(native)
    later(evidence)
    native.exchange("capabilities", {})
    current = native.operations.cache.lookup(selected["scope"])["current_id"]
    before, write = store.path.read_bytes(), storage._atomic_write_json

    def failed(path, value):
        if uncertain:
            write(path, value)
            raise StateCommitUncertain("controlled journal replacement uncertainty")
        raise OSError("controlled journal replacement failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_atomic_write_json", failed)
        with pytest.raises(OSError):
            native.exchange("load", {"number": 42, "expected_current": current, "budget_seconds": 60})
    published = native.operations.cache.lookup(selected["scope"])["current_id"]
    assert published != current and (store.path.read_bytes() == before) is not uncertain
    retried = native.exchange("sync", {
        "number": 42, "expected_current": published, "previous_id": current, "budget_seconds": 60,
    })
    assert retried["result"]["conversation"]["status"] == ("unchanged" if uncertain else "observed")
    retained = native.exchange("retained-comment", {
        "number": 42, "operation_id": selected["operation_id"], "action_id": selected["actions"][0]["id"],
    })["result"]
    assert retained["mapping"]["generation_id"] == published
    assert retained["action_state"] == "unattempted" and retained["mapping"]["status"] == "unavailable"
    assert json.loads(store.path.read_bytes())["conversations"]["records"][0]["sequence"] == 1
    assert backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


def test_concurrent_load_and_sync_only_observe_the_confirmed_cache_winner(
    environment, prepared_workspace, monkeypatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import threading

    store, selected, _, _, backend, _ = environment
    gateway, _, native, evidence, _, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    enable(native)
    later(evidence)
    current = native.operations.cache.lookup(selected["scope"])["current_id"]
    publish, published, compared = GenerationCache.publish, threading.Event(), threading.Event()
    read, acquired = native.operations.read_owner.read, threading.Barrier(2)

    def together(*args, **kwargs):
        result = read(*args, **kwargs)
        acquired.wait(timeout=5)
        return result

    def ordered_publish(cache, generation, **kwargs):
        if threading.current_thread().name.endswith("_0"):
            result = publish(cache, generation, **kwargs)
            published.set()
            assert compared.wait(5)
        else:
            assert published.wait(5)
            result = publish(cache, generation, **kwargs)
            compared.set()
        return result

    monkeypatch.setattr(GenerationCache, "publish", ordered_publish)
    monkeypatch.setattr(native.operations.read_owner, "read", together)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="mapping") as pool:
        load = pool.submit(native.exchange, "load", {
            "number": 42, "expected_current": current, "budget_seconds": 60,
        })
        sync = pool.submit(native.exchange, "sync", {
            "number": 42, "expected_current": current, "previous_id": current, "budget_seconds": 60,
        })
        winner, loser = load.result(timeout=10)["result"], sync.result(timeout=10)["result"]
    assert winner["publication"]["status"] == "published" and winner["conversation"]["status"] == "observed"
    assert loser["publication"]["status"] == "conflict" and loser["conversation"]["status"] == "not_observed"
    assert json.loads(store.path.read_bytes())["conversations"]["records"][0]["sequence"] == 1
    retained = CommentMappingStore(store).read(
        selected["operation_id"], selected["scope"], selected["actions"][0]["id"], expires_at=native.expires_at(),
    )
    assert retained["mapping"]["generation_id"] == winner["descriptor"]["id"]
    assert backend.calls == []
    asyncio.run(gateway.drain(timeout=2))
