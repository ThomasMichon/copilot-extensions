"""Optional compact evidence through actual scoped Native/Gateway/CLI readers."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import json
import sys
import time
from types import MappingProxyType

import pytest

from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.workspace_client import WorkspaceClientError
from agent_pull_requests.workspace_operations import WorkspaceOperations, WorkspaceUnavailable
from agent_pull_requests.workspace_terminal_mapping import FIELD, SELECTION, contract
from agent_pull_requests.workspace_terminal_read import OPERATIONS, supports as supports_terminal
from agent_pull_requests.workspace_tools import WorkspaceTools
from test_review_edit_binding import edits as edits, allocate, refresh, submit
from test_review_owner_store import prepared_intent as prepared_intent
from test_terminal_comment_mapping import ready, read, finish, retire
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_terminal_retention import (
    environment as environment, integration as integration, workspace as workspace, load,
)
from test_workspace_tools import invoke_cli

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def expires() -> int:
    return int(time.time()) + 600


@pytest.fixture
def mapping_reader(workspace: tuple, integration: tuple, edits: tuple, request) -> tuple:
    owner = ready(workspace, edits)
    previous, evidence, backend, _, _, _ = workspace
    gateway, client, native, _, caller, binding = integration
    chosen = allocate(edits)
    assert submit(edits, chosen)["status"] == "complete"
    if getattr(request, "param", "comment") != "comment":
        (_, selected, value, attachment, _, _), _, _, _ = edits
        refresh(edits, "later-intentional-write")
        if request.param == "reply":
            selected["actions"] = [{"id": "finding", "kind": "reply", "thread_id": 100, "body": "Reply."}]
        for section in evidence["sections"].values():
            section.update(observed_from="2026-10-30T00:01:00Z", observed_to="2026-10-30T00:01:01Z")
        load(previous, previous.cache.lookup(chosen["scope"])["current_id"])
        current = previous.cache.lookup(chosen["scope"])["generation"]
        value.clear()
        value.update(copy.deepcopy(current))
        selected["generation_id"] = attachment["generation_id"] = current["id"]
        chosen = allocate(edits)
        assert submit(edits, chosen)["status"] == "complete"
    previous.prepared_reviews = previous.comment_mappings = True
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:02:00Z", observed_to="2026-10-30T00:02:01Z")
    load(previous, previous.cache.lookup(chosen["scope"])["current_id"])
    current = previous.cache.lookup(chosen["scope"])["generation"]
    original = read(owner, chosen)
    assert original["mapping"]["status"] == "correlated"
    previous.store.terminal_cache_retirement = True
    TerminalCacheRetirementStore(previous.store).adopt(
        lifecycle_checkpoint=lambda: None, expires_at=expires(),
    )
    operations = reader(previous, previous.store)
    native.operations = operations
    gateway.workspaces = MappingProxyType({("caller-a", "project"): operations})
    selected_workspace = (operations, *workspace[1:])
    yield selected_workspace, gateway, client, native, caller, binding, chosen, current, original
    asyncio.run(gateway.drain(timeout=2))


def reader(previous: WorkspaceOperations, owner: ReplayStore) -> WorkspaceOperations:
    return WorkspaceOperations(
        owner, previous.cache, scope=previous.scope, read_owner=previous.read_owner,
        binding_ref=previous.binding_ref, checkpoint=previous.checkpoint, max_work_seconds=120,
        conversations=True, retention_target=previous.retention_target, terminal_reads=True,
        terminal_mapping_reads=True,
    )


def compact(mapping_reader: tuple) -> None:
    workspace, _, _, _, _, _, _, current, _ = mapping_reader
    decision, policies = finish(workspace, current)
    retire(workspace, current, decision, policies)
    from agent_pull_requests.attention import Cursor
    workspace[0].retention.retire_cache(
        current["scope"], Cursor(**decision["cursor"]), expires_at=expires(), checkpoint=lambda: None,
        lifecycle_checkpoint=lambda: None, deadline=time.monotonic() + 2,
    )
    assert not workspace[0]._materializations
    assert not list(workspace[0].cache.generations.glob("*.json"))


@pytest.mark.parametrize("mapping_reader,transport,compacted,version", [
    ("comment", "native", False, 3), ("comment", "gateway", True, 4),
    ("reply", "native", True, 5), ("edit_owned_comment", "gateway", True, 6),
], indirect=["mapping_reader"])
def test_real_acquired_mapping_cli_read_after_retirement_and_fresh_reader_restart(
    mapping_reader: tuple, monkeypatch: pytest.MonkeyPatch, capsys, transport: str, compacted: bool, version: int,
) -> None:
    from agent_pull_requests.attention_store import AttentionStore
    from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore

    workspace, _, client, native, _, _, chosen, current, original = mapping_reader
    operations, _, backend, _, _, _ = workspace
    if version >= 4:
        AttentionStore(operations.store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    if version >= 5:
        FeedbackCaptureStore(operations.store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    if version == 6:
        FeedbackCaptureStore(operations.store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires())
    native.exchange("capabilities", {})
    assert native.materialize(42, current["id"]) == current
    assert operations._materializations
    if compacted:
        compact(mapping_reader)
    restarted = ReplayStore(
        operations.store.root, operations.store.namespace, service_identity=operations.store.service_identity,
        terminal_retention=True, terminal_recovery=True, terminal_cache_retirement=True, terminal_comment_mapping=True,
    )
    operations.store = restarted
    native.operations = reader(operations, restarted)
    # The Gateway keeps its own fresh receiver, sharing the same existing owner.
    mapping_reader[1].workspaces = MappingProxyType({("caller-a", "project"): native.operations})
    calls = list(backend.calls)

    def forbidden(*args, **kwargs) -> None:
        pytest.fail("retained mapping attempted acquisition or cached body access")

    monkeypatch.setattr(operations.read_owner, "read", forbidden)
    monkeypatch.setattr(type(operations.cache), "lookup", forbidden)
    before = restarted.path.read_bytes()
    host = WorkspaceTools(native if transport == "native" else client)
    code, capabilities = invoke_cli(host, "capabilities", {}, monkeypatch, capsys)
    assert code == 0 and capabilities["backend_contract"]["operations"] == list(OPERATIONS)
    assert capabilities["backend_contract"]["owner_store_versions"] == [version]
    assert supports_terminal(capabilities["backend_contract"])
    assert capabilities["backend_contract"][FIELD] == contract()
    assert "retained-comment" in capabilities["operations"]
    assert not {"prepare", "submit", "receipt", "allocate", "observe-comment"} & set(capabilities["operations"])
    payload = {"number": 42, "operation_id": chosen["operation_id"], "action_id": chosen["actions"][0]["id"]}
    for _ in range(2):
        code, result = invoke_cli(host, "retained-comment", payload, monkeypatch, capsys)
        assert code == 0, result
        assert result["result"] == {**original, "representation": "compact" if compacted else "active"}
    assert restarted.path.read_bytes() == before and backend.calls == calls


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("declaration", ["absent", "malformed"])
def test_old_or_invalid_optional_descriptor_keeps_baseline_but_never_dispatches_mapping(
    mapping_reader: tuple, monkeypatch: pytest.MonkeyPatch, transport: str, declaration: str,
) -> None:
    workspace, _, client, native, caller, _, chosen, _, _ = mapping_reader
    operations, _, backend, _, _, _ = workspace
    execute, dispatched = operations.execute, []

    def receiver(operation, payload, **kwargs):
        if operation == "retained-comment":
            dispatched.append(operation)
        result = execute(operation, payload, **kwargs)
        if operation == "capabilities":
            if declaration == "absent":
                result.pop(FIELD)
            else:
                result[FIELD]["terminal_comment_mapping_version"] = True
        return result

    monkeypatch.setattr(operations, "execute", receiver)
    before, calls = operations.store.path.read_bytes(), list(backend.calls)

    async def scenario() -> None:
        host = WorkspaceTools(native if transport == "native" else client)
        capabilities = await host.capabilities()
        assert "events" in capabilities["operations"] and "retained-comment" not in capabilities["operations"]
        count = len(caller.calls)
        with pytest.raises((WorkspaceUnavailable, WorkspaceClientError)):
            payload = {"number": 42, "operation_id": chosen["operation_id"], "action_id": "finding"}
            if transport == "native":
                native.exchange("retained-comment", payload)
            else:
                await client.exchange("retained-comment", payload)
        assert len(caller.calls) == count and not dispatched
        assert (await host.invoke("events", {"number": 42, "after": None, "limit": 16}))["result"]["status"] == "snapshot"

    asyncio.run(scenario())
    assert operations.store.path.read_bytes() == before and backend.calls == calls


@pytest.mark.parametrize("transport,withdrawal", [
    ("native", "selection"), ("gateway", "selection"), ("gateway", "storage"),
])
def test_actual_receiver_rechecks_withdrawal_without_fallback_or_rewrite(
    mapping_reader: tuple, transport: str, withdrawal: str,
) -> None:
    workspace, _, client, native, _, _, chosen, _, _ = mapping_reader
    operations, _, backend, _, _, _ = workspace

    async def scenario() -> None:
        if transport == "gateway":
            await client.capabilities()
        else:
            native.exchange("capabilities", {})
        if withdrawal == "selection":
            operations.terminal_mapping_reads = False
        else:
            operations.store._review_transaction(
                lambda document: document.pop("terminal_comment_mapping_version"), expires_at=expires(),
            )
        before, calls = operations.store.path.read_bytes(), list(backend.calls)
        with pytest.raises((WorkspaceUnavailable, WorkspaceClientError)):
            payload = {"number": 42, "operation_id": chosen["operation_id"], "action_id": "finding"}
            if transport == "gateway":
                await client.exchange("retained-comment", payload)
            else:
                native.exchange("retained-comment", payload)
        capabilities = native.exchange("capabilities", {})
        assert FIELD not in capabilities and capabilities["operations"] == list(OPERATIONS)
        native.exchange("events", {"number": 42, "after": None, "limit": 16})
        assert operations.store.path.read_bytes() == before and backend.calls == calls

    asyncio.run(scenario())


def test_legacy_selection_cannot_request_mapping_and_strict_rollback_preserves_compact_bytes(
    mapping_reader: tuple,
) -> None:
    from test_workspace_terminal_read import request
    from agent_pull_requests.workspace_terminal_read import SELECTION as BASELINE
    from agent_pull_requests.workspace_terminal_read import serve

    workspace, gateway, _, native, _, binding, chosen, _, _ = mapping_reader
    operations = workspace[0]
    compact(mapping_reader)
    before = operations.store.path.read_bytes()
    payload = {"number": 42, "operation_id": chosen["operation_id"], "action_id": "finding"}
    with pytest.raises(WorkspaceUnavailable):
        serve(operations, "retained-comment", payload, selection=BASELINE, expires_at=expires(), checkpoint=lambda: None)
    response = asyncio.run(gateway.handle(request(binding, "retained-comment", payload)))
    assert response.status == 503
    response = asyncio.run(gateway.handle(request(binding, "events", {"number": 42, "after": None, "limit": 16})))
    assert response.status == 200
    prior = ReplayStore(
        operations.store.root, operations.store.namespace, service_identity=operations.store.service_identity,
        terminal_retention=True, terminal_recovery=True, terminal_cache_retirement=True,
    )
    with pytest.raises(ReplayStoreInvalid, match="mapping reader"):
        prior._review_transaction(lambda document: None, expires_at=expires(), persist=False)
    operations.terminal_mapping_reads = False
    assert FIELD not in native.exchange("capabilities", {})
    assert operations.store.path.read_bytes() == before


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("change", [
    "representation", "protocol", "support", "identity", "identity-value", "revision",
    "prepared", "mapping", "correlated-interval", "correlated-state", "receipt", "no-send",
])
def test_clients_reject_malformed_optional_result_without_receipt_or_feedback_change(
    mapping_reader: tuple, monkeypatch: pytest.MonkeyPatch, transport: str, change: str,
) -> None:
    workspace, _, client, native, _, _, chosen, _, _ = mapping_reader
    original = workspace[0].execute
    before = workspace[0].store.path.read_bytes()

    def altered(operation, payload, **kwargs):
        response = original(operation, payload, **kwargs)
        if operation == "retained-comment":
            result = response["result"]
            if change == "representation":
                result["representation"] = "reconstructed"
            elif change == "protocol":
                result["protocol"] = "agent-pull-requests.retained-comment-observation/v1"
            elif change == "support":
                result["mapping_supported"] = 1
            elif change == "identity":
                result.pop("intent_id")
            elif change == "identity-value":
                result["intent_id"] = None
            elif change == "revision":
                result["revision"]["iteration"] = True
            elif change == "prepared":
                result["prepared"]["write_id"] = "invalid"
            elif change == "mapping":
                result["mapping"] = "not-an-observation"
            elif change == "correlated-interval":
                result["mapping"]["observation"] = None
            elif change == "correlated-state":
                result.update(action_state="retired", representation="compact", receipt=None)
            elif change == "no-send":
                result["receipt"] = {"origin": "no_send", "platform_ids": {}, "proof": None}
            else:
                result["receipt"]["platform_ids"] = {}
        return response

    monkeypatch.setattr(workspace[0], "execute", altered)

    async def scenario() -> None:
        payload = {"number": 42, "operation_id": chosen["operation_id"], "action_id": "finding"}
        if transport == "native":
            native.exchange("capabilities", {})
            with pytest.raises(WorkspaceUnavailable, match="invalid terminal comment mapping response"):
                native.exchange("retained-comment", payload)
        else:
            await client.capabilities()
            with pytest.raises(WorkspaceClientError, match="invalid_retained_comment_response"):
                await client.exchange("retained-comment", payload)

    asyncio.run(scenario())
    assert workspace[0].store.path.read_bytes() == before


@pytest.mark.parametrize("retained", [False, True])
def test_wire_distinguishes_unsupported_history_from_a_supported_null_slot(
    workspace: tuple, integration: tuple, prepared_intent: dict, retained: bool,
) -> None:
    from test_terminal_comment_mapping import enable
    from test_terminal_recovery import prepare, compact as compact_storage

    original = prepare(workspace, selected=prepared_intent, attempt=False)
    if retained:
        enable(workspace[0].store)
    compact_storage(original)
    if not retained:
        enable(workspace[0].store)
    previous = workspace[0]
    previous.store.terminal_cache_retirement = True
    TerminalCacheRetirementStore(previous.store).adopt(
        lifecycle_checkpoint=lambda: None, expires_at=expires(),
    )
    gateway, client, _, _, _, _ = integration
    gateway.workspaces = MappingProxyType({("caller-a", "project"): reader(previous, previous.store)})
    before = previous.store.path.read_bytes()

    async def scenario() -> None:
        host = WorkspaceTools(client)
        result = (await host.invoke("retained-comment", {
            "number": 42, "operation_id": original[2]["operation_id"], "action_id": original[2]["actions"][0]["id"],
        }))["result"]
        assert result["mapping"] is None and result["mapping_supported"] is retained
        assert result["prepared"] is not None and result["representation"] == "compact"
        assert result["action_state"] == "retired"
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
    assert previous.store.path.read_bytes() == before


def test_selected_service_restart_serves_compacted_mapping_without_adopting(
    mapping_reader: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_service
    from agent_pull_requests.watch_observer import NativeObservationBinding
    from test_watch_service import Connector
    from test_workspace_terminal_read import request

    workspace, gateway, _, _, _, binding, chosen, _, original = mapping_reader
    operations = workspace[0]
    compact(mapping_reader)
    before = operations.store.path.read_bytes()
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)

    async def access() -> str:
        return "synthetic"

    async def scenario() -> None:
        for _ in range(2):
            owner = ReplayStore(
                operations.store.root, operations.store.namespace, service_identity=operations.store.service_identity,
                terminal_retention=True, terminal_recovery=True, terminal_cache_retirement=True, terminal_comment_mapping=True,
            )
            selected = reader(operations, owner)
            service = watch_service.WatchService(
                owner, fleet=binding.fleet, gateway=binding.gateway, slot=binding.slot,
                bindings=tuple(WatchScopeBinding(principal, target, scope)
                               for (principal, target), scope in gateway.bindings.items()),
                observations=(NativeObservationBinding(binding.account, binding.repo, sys.executable),),
                gateway_origin="https://gateway.invalid", access_token_callback=access,
                proof_key=object(), authority=expires, checkpoint=lambda: None,
                workspaces={("caller-a", "project"): selected},
            )
            await service.start()
            try:
                assert service.status()["writer_held"]
                capabilities = await service.gateway.handle(request(binding, "capabilities", {}, selected=False))
                assert json.loads(capabilities.body)[FIELD] == contract()
                pending = request(binding, "retained-comment", {
                    "number": 42, "operation_id": chosen["operation_id"], "action_id": "finding",
                }, selected=False)
                payload = json.loads(pending.body)
                payload["workspace_contract"] = SELECTION
                response = await service.gateway.handle(replace(pending, body=json.dumps(payload).encode()))
                assert response.status == 200
                assert json.loads(response.body)["result"] == {**original, "representation": "compact"}
                assert owner.path.read_bytes() == before
            finally:
                await service.close(timeout=3)
            assert not service.status()["writer_held"]

    asyncio.run(scenario())
