"""Negotiated current/prior consumers through actual CLI, cache and Gateway."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from agent_pull_requests.review_comment_observation import contract
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_client import WorkspaceClientError
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


def test_storage_mapping_adoption_does_not_expand_baseline_inspection_wire(
    environment: tuple, prepared_workspace: tuple,
) -> None:
    from agent_pull_requests.conversation_store import ConversationStore
    from agent_pull_requests.review_comment_mapping import CommentMappingStore, FIELD

    store, selected, _, _, backend, _ = environment
    gateway, _, native, _, _, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=native.expires_at())
    CommentMappingStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=native.expires_at())
    result = native.exchange("inspect", {"number": 42, "operation_id": selected["operation_id"]})
    assert FIELD in store.inspect_review(selected["operation_id"], selected["scope"], expires_at=native.expires_at())
    assert FIELD not in result["result"]
    assert backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["direct", "native", "gateway"])
def test_receiver_rechecks_actual_prepared_storage_for_direct_and_stale_clients(
    environment: tuple, integration: tuple, transport: str,
) -> None:
    store, selected, generation, _, backend, _ = environment
    gateway, client, native, _, _, _ = integration
    store.admit_review(selected, expires_at=native.expires_at())
    legacy = store.path.read_bytes()
    store.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=native.expires_at())
    native.operations.prepared_reviews = True
    payload = {"number": 42, "operation_id": selected["operation_id"],
               "action_id": selected["actions"][0]["id"], "generation_id": generation["id"]}

    async def scenario() -> None:
        discovered = (await client.capabilities() if transport == "gateway"
                      else native.exchange("capabilities", {}))
        assert "observe-comment" in discovered["operations"]
        # A fixture-owned receiver replacement selects intact prior state, with
        # only legacy operations; its caller still retains the previous capability.
        store.path.write_bytes(legacy)
        before = retained_bytes(store)
        with pytest.raises((WorkspaceUnavailable, WorkspaceClientError)) as refused:
            if transport == "gateway":
                await client.exchange("observe-comment", payload)
            elif transport == "native":
                native.exchange("observe-comment", payload)
            else:
                native.operations.execute(
                    "observe-comment", payload, expires_at=native.expires_at(), checkpoint=backend.checkpoint,
                )
        if isinstance(refused.value, WorkspaceClientError):
            assert refused.value.outcome_unknown is False
        actual = native.operations.execute(
            "capabilities", {}, expires_at=native.expires_at(), checkpoint=backend.checkpoint,
        )
        assert "observe-comment" not in actual["operations"]
        assert retained_bytes(store) == before and backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
def test_actual_cli_original_receipts_and_acquisition_join_without_writer_or_more_reads(
    environment: tuple, prepared_workspace: tuple, owned_workspace: tuple,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], transport: str, kind: str,
) -> None:
    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, _ = owned_workspace
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}

    def send() -> dict:
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
            draft["actions"] = [{
                "id": "owned-finding", "kind": "reply", "body": "Selected reply.", "thread_id": 100,
            }]
        chosen = send()
    current = native.operations.cache.lookup(chosen["scope"])["current_id"]
    code, loaded = invoke_cli(host, "load", {
        "number": 42, "expected_current": current, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0, loaded
    observed = native.operations.cache.lookup(chosen["scope"])["generation"]
    native.operations.store = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    native.operations.write_context = None
    native.operations.selection_preparer = None
    before, calls = retained_bytes(store), list(backend.calls)
    payload = {
        "number": 42, "operation_id": chosen["operation_id"],
        "action_id": chosen["actions"][0]["id"], "generation_id": observed["id"],
    }
    for _ in range(2):
        code, result = invoke_cli(host, "observe-comment", payload, monkeypatch, capsys)
        assert code == 0 and result["result"]["status"] == "correlated", result
        projection = result["result"]
        assert projection["generation_id"] == observed["id"]
        assert projection["identity"] == chosen["actions"][0]["preparation"]["identity"]
        assert projection["observation"]["observed_to"] == observed["sections"]["discussions"]["observed_to"]
        assert projection["action_state"] == "confirmed" and projection["fresh_native_read"] is False
        assert projection["original_version_proven"] is False and projection["feedback_progress"] is False
        assert chosen["actions"][0]["body"] not in json.dumps(projection)
    assert retained_bytes(store) == before and backend.calls == calls
    native.operations.prepared_reviews = False
    code, _ = invoke_cli(host, "observe-comment", payload, monkeypatch, capsys)
    assert code != 0 and retained_bytes(store) == before and backend.calls == calls
    native.operations.prepared_reviews = True
    code, restored = invoke_cli(host, "observe-comment", payload, monkeypatch, capsys)
    assert code == 0 and restored == result and retained_bytes(store) == before
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
def test_current_matching_acquisition_cannot_confirm_actual_lost_native_response(
    environment: tuple, prepared_workspace: tuple, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], transport: str,
) -> None:
    store, selected, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    host = WorkspaceTools(native if transport == "native" else client)
    backend.lose_reply = True
    code, submitted = invoke_cli(host, "submit", {
        "number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and submitted["result"]["status"] != "complete"
    assert backend.threads[0]["comments"][0]["content"] == selected["actions"][0]["body"]
    current = native.operations.cache.lookup(selected["scope"])["current_id"]
    code, loaded = invoke_cli(host, "load", {
        "number": 42, "expected_current": current, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0, loaded
    observed = native.operations.cache.lookup(selected["scope"])["current_id"]
    before, calls = retained_bytes(store), list(backend.calls)
    code, result = invoke_cli(host, "observe-comment", {
        "number": 42, "operation_id": selected["operation_id"],
        "action_id": selected["actions"][0]["id"], "generation_id": observed,
    }, monkeypatch, capsys)
    assert code == 0 and result["result"]["status"] == "unavailable"
    assert result["result"]["action_state"] == "unknown"
    assert result["result"]["reason"] == "original_action_not_confirmed"
    assert retained_bytes(store) == before and backend.calls == calls
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("support", ["prior", "missing-operation", "malformed", "withdrawn", "refreshed"])
def test_optional_contract_precedes_strict_request_and_receiver_rechecks_withdrawal(
    environment: tuple, prepared_workspace: tuple, monkeypatch: pytest.MonkeyPatch,
    transport: str, support: str,
) -> None:
    store, selected, generation, _, backend, _ = environment
    gateway, client, native, _, caller, _ = prepared_workspace
    execute = native.operations.execute
    dispatched = []

    def receiver(operation: str, payload: dict, **kwargs: object) -> dict:
        if operation == "observe-comment":
            dispatched.append(operation)
        result = execute(operation, payload, **kwargs)
        if operation == "capabilities":
            if support == "prior":
                result.pop("comment_observation_contract", None)
            elif support == "missing-operation":
                result["operations"].remove("observe-comment")
            elif support == "malformed":
                result["comment_observation_contract"]["generation_versions"] = [True]
        return result

    monkeypatch.setattr(native.operations, "execute", receiver)
    payload = {"number": 42, "operation_id": selected["operation_id"],
               "action_id": selected["actions"][0]["id"], "generation_id": generation["id"]}

    async def scenario() -> None:
        host = WorkspaceTools(native if transport == "native" else client)
        available = await host.capabilities()
        assert ("observe-comment" in available["operations"]) == (support in ("withdrawn", "refreshed"))
        if support in ("withdrawn", "refreshed"):
            native.operations.prepared_reviews = False
            if support == "refreshed":
                if transport == "native":
                    native.exchange("capabilities", {})
                else:
                    await client.capabilities()
        before, calls = retained_bytes(store), len(caller.calls)
        with pytest.raises((WorkspaceClientError, WorkspaceUnavailable)) as refused:
            if transport == "native":
                native.exchange("observe-comment", payload)
            else:
                await client.exchange("observe-comment", payload)
        if isinstance(refused.value, WorkspaceClientError):
            assert refused.value.outcome_unknown is False
        assert dispatched == (["observe-comment"] if support == "withdrawn" else [])
        assert len(caller.calls) - calls == (1 if transport == "gateway" and support == "withdrawn" else 0)
        assert retained_bytes(store) == before and backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    "generation_id", "operation_id", "action_id", "scope", "protocol", "effects",
    "original_version_proven", "feedback_progress", "fresh_native_read",
])
def test_gateway_response_is_bound_to_the_selected_operation_and_cached_generation(
    environment: tuple, prepared_workspace: tuple, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    store, selected, generation, _, backend, _ = environment
    gateway, client, native, _, caller, _ = prepared_workspace
    store.admit_review(selected, expires_at=native.expires_at())
    request = caller._request

    async def corrupted(*args: object, **kwargs: object) -> object:
        response = await request(*args, **kwargs)
        if args[1].endswith("/observe-comment"):
            value = json.loads(response.body)
            if change == "scope":
                value["result"]["scope"]["principal_id"] = "different-native-principal"
            else:
                value["result"][change] = "different"
            return replace(response, body=json.dumps(value).encode())
        return response

    monkeypatch.setattr(caller, "_request", corrupted)
    before = retained_bytes(store)

    async def scenario() -> None:
        available = await client.capabilities()
        assert available["comment_observation_contract"] == contract()
        with pytest.raises(WorkspaceClientError) as refused:
            await client.exchange("observe-comment", {
                "number": 42, "operation_id": selected["operation_id"],
                "action_id": selected["actions"][0]["id"], "generation_id": generation["id"],
            })
        assert refused.value.code == "invalid_comment_observation_response"
        assert refused.value.outcome_unknown is False
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
    assert retained_bytes(store) == before and backend.calls == []
