"""Terminal reads negotiate at the actual Native/Gateway and service boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import sys
import time
from types import MappingProxyType

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest

from test_workspace_terminal_retention import (
    environment as environment, integration as integration, workspace as workspace,
)
from agent_pull_requests.attention import Cursor
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.workspace_client import WorkspaceClientError
from agent_pull_requests.workspace_operations import WorkspaceOperations, WorkspaceUnavailable
from agent_pull_requests.workspace_terminal_read import PROTOCOL, SELECTION, OPERATIONS

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def expires() -> int:
    return int(time.time()) + 600


@pytest.fixture
def terminal_reader(workspace: tuple, integration: tuple) -> tuple:
    previous, evidence, backend, _, initial, _ = workspace
    gateway, client, native, _, caller, binding = integration
    owner = previous.store
    owner.terminal_recovery = owner.terminal_cache_retirement = True
    for selected in (TerminalRecoveryStore(owner), TerminalCacheRetirementStore(owner)):
        selected.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    operations = WorkspaceOperations(
        owner, previous.cache, scope=previous.scope, read_owner=previous.read_owner,
        binding_ref=previous.binding_ref, checkpoint=previous.checkpoint, max_work_seconds=120,
        conversations=True, retention_target=previous.retention_target, terminal_reads=True,
    )
    native.operations = operations
    gateway.workspaces = MappingProxyType({("caller-a", "project"): operations})
    return operations, gateway, client, native, evidence, backend, caller, binding, initial


def request(binding, operation: str, payload: dict, *, selected: bool = True) -> ServiceRequest:
    return ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", expires()),
        "POST", "/v2/review/project/" + operation, (("content-type", "application/json"),),
        json.dumps({
            "binding": binding.document(), "request": payload,
            **({"workspace_contract": SELECTION} if selected else {}),
        }).encode(),
    )


@pytest.mark.parametrize("withdrawn", [False, True])
def test_target_capture_uses_request_budget_before_native_acquisition(
    terminal_reader: tuple, monkeypatch: pytest.MonkeyPatch, withdrawn: bool,
) -> None:
    operations, _, _, native, _, _, _, _, initial = terminal_reader
    original_target, original_read = operations.retention_target, operations.read_owner.read
    admitted_expiry = int(time.time()) + 60
    native.expires_at = lambda: admitted_expiry
    calls = []

    def capture(scope, *, expires_at, checkpoint):
        assert expires_at == admitted_expiry
        checkpoint()
        calls.append("capture")
        if withdrawn:
            raise RuntimeError("capture withdrawn")
        return original_target(scope)

    def read(*args, **kwargs):
        assert calls == ["capture"]
        calls.append("read")
        return original_read(*args, **kwargs)

    operations.retention_target, operations.retention_target_deadline = capture, True
    monkeypatch.setattr(operations.read_owner, "read", read)
    native.exchange("capabilities", {})
    before = operations.store.path.read_bytes()
    payload = {"number": 42, "expected_current": initial["id"], "budget_seconds": 10}
    if withdrawn:
        with pytest.raises(RuntimeError, match="capture withdrawn"):
            native.exchange("load", payload)
        assert calls == ["capture"] and operations.store.path.read_bytes() == before
    else:
        assert native.exchange("load", payload)["result"]["descriptor"]["id"]
        assert calls == ["capture", "read"]


@pytest.mark.parametrize("version", [3, 4, 5, 6])
def test_negotiated_native_and_gateway_reads_retire_then_reopen_without_serving_old_bytes(
    terminal_reader: tuple, version: int,
) -> None:
    operations, gateway, client, native, evidence, backend, _, _, initial = terminal_reader
    scope = initial["scope"]
    from agent_pull_requests.attention_store import AttentionStore
    from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore
    if version >= 4:
        AttentionStore(operations.store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    if version >= 5:
        FeedbackCaptureStore(operations.store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    if version == 6:
        FeedbackCaptureStore(operations.store).adopt_readback(
            lifecycle_checkpoint=lambda: None, expires_at=expires(),
        )

    async def scenario() -> None:
        capabilities = await client.capabilities()
        assert capabilities["protocol"] == PROTOCOL
        assert capabilities["operations"] == list(OPERATIONS)
        assert capabilities["owner_store_versions"] == [version]
        assert native.exchange("capabilities", {}) == capabilities
        loaded = await client.exchange("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 10,
        })
        current = loaded["result"]["descriptor"]["id"]
        assert await client.materialize(42, current) == native.materialize(42, current)
        old_cursor = loaded["result"]["conversation"]["cursor"]
        backend.pr["status"] = "completed"
        for section in evidence["sections"].values():
            section.update(observed_from="2026-10-30T00:00:20Z", observed_to="2026-10-30T00:00:21Z")
        terminal = (await client.exchange("load", {
            "number": 42, "expected_current": current, "budget_seconds": 10,
        }))["result"]
        assert terminal["publication"]["status"] == "retired" and terminal["descriptor"] is None
        cursor = Cursor(**terminal["conversation"]["cursor"])
        operations.retention.compact_recovery(
            scope, cursor, {}, expires_at=expires(), checkpoint=lambda: None,
            lifecycle_checkpoint=lambda: None, deadline=time.monotonic() + 5,
        )
        operations.retention.retire_cache(
            scope, cursor, expires_at=expires(), checkpoint=lambda: None,
            lifecycle_checkpoint=lambda: None, deadline=time.monotonic() + 5,
        )
        before = operations.store.path.read_bytes()
        page = (await client.exchange("events", {"number": 42, "after": None, "limit": 16}))["result"]
        assert page["current"]["sections"] == {} and page["current"]["state"] == "merged"
        assert operations.store.path.read_bytes() == before and not operations._materializations
        with pytest.raises(WorkspaceClientError):
            await client.exchange("chunk", {"number": 42, "generation_id": current, "offset": 0})
        backend.pr["status"] = "active"
        for section in evidence["sections"].values():
            section.update(observed_from="2026-10-30T00:00:30Z", observed_to="2026-10-30T00:00:31Z")
        reopened = (await client.exchange("load", {
            "number": 42, "expected_current": None, "budget_seconds": 10,
        }))["result"]
        assert reopened["conversation"]["cursor"] == {"epoch": 2, "sequence": 1}
        assert reopened["descriptor"]["id"] != current
        resync = native.exchange("events", {"number": 42, "after": old_cursor, "limit": 16})["result"]
        assert resync["status"] == "resync_required"
        assert not backend.calls and backend.vote is None
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_unselected_and_effectful_frontdoors_refuse_before_native_io_or_state_change(
    terminal_reader: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations, gateway, client, native, _, backend, caller, binding, _ = terminal_reader
    before = operations.store.path.read_bytes()

    def forbidden(*args, **kwargs) -> None:
        pytest.fail("unselected or effectful request reached native acquisition")

    monkeypatch.setattr(operations.read_owner, "read", forbidden)
    with pytest.raises(WorkspaceUnavailable, match="capability required"):
        native.exchange("load", {"number": 42, "expected_current": None, "budget_seconds": 10})

    async def scenario() -> None:
        for operation, payload, selected in (
            ("load", {"number": 42, "expected_current": None, "budget_seconds": 10}, False),
            ("submit", {"number": 42}, True),
            ("allocate", {"number": 42}, True),
            ("inspect", {"number": 42, "operation_id": "original"}, True),
        ):
            response = await gateway.handle(request(binding, operation, payload, selected=selected))
            assert response.status == 503
        await client.capabilities()
        calls = len(caller.calls)
        with pytest.raises(WorkspaceClientError) as refused:
            await client.exchange("submit", {"number": 42})
        assert refused.value.outcome_unknown is False and len(caller.calls) == calls
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
    assert operations.store.path.read_bytes() == before and not backend.calls


@pytest.mark.parametrize("change", ["protocol", "layout", "optional", "boolean"])
def test_unsupported_discovery_never_selects_or_sends_optional_form(
    terminal_reader: tuple, change: str,
) -> None:
    operations, gateway, client, _, _, _, caller, _, _ = terminal_reader
    original = caller.request
    before = operations.store.path.read_bytes()

    async def altered(*args, **kwargs):
        response = await original(*args, **kwargs)
        value = json.loads(response.body)
        if change == "protocol":
            value["protocol"] = "unsupported"
        elif change == "layout":
            value["owner_store_versions"] = [7]
        elif change == "optional":
            value["terminal_read_contract"]["protocol"] = "unsupported"
        else:
            value["terminal_read_contract"]["terminal_retention_version"] = True
        return replace(response, body=json.dumps(value).encode())

    caller.request = altered

    async def scenario() -> None:
        with pytest.raises(WorkspaceClientError):
            await client.capabilities()
        count = len(caller.calls)
        with pytest.raises(WorkspaceClientError):
            await client.exchange("load", {"number": 42, "expected_current": None, "budget_seconds": 10})
        assert len(caller.calls) == count
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
    assert operations.store.path.read_bytes() == before


def test_legacy_serving_and_rolled_back_reader_refuse_adopted_state_without_rewrite(
    terminal_reader: tuple,
) -> None:
    operations, gateway, _, native, _, _, _, _, _ = terminal_reader
    before = operations.store.path.read_bytes()
    operations.terminal_reads = False
    with pytest.raises(WorkspaceUnavailable, match="not selected"):
        native.exchange("capabilities", {})
    with pytest.raises(ReplayStoreInvalid, match="serving contract unavailable"):
        ConversationStore(operations.store).capabilities(expires_at=expires())
    prior = ReplayStore(
        operations.store.root, operations.store.namespace,
        service_identity=operations.store.service_identity,
    )
    with pytest.raises(ReplayStoreInvalid):
        prior._review_transaction(lambda doc: None, expires_at=expires())
    assert operations.store.path.read_bytes() == before
    asyncio.run(gateway.drain(timeout=2))


def test_selected_reader_refuses_unadopted_extensions_without_provisioning(
    workspace: tuple, integration: tuple,
) -> None:
    previous = workspace[0]
    previous.store.terminal_recovery = previous.store.terminal_cache_retirement = True
    before = previous.store.path.read_bytes()
    operations = WorkspaceOperations(
        previous.store, previous.cache, scope=previous.scope, read_owner=previous.read_owner,
        binding_ref=previous.binding_ref, checkpoint=previous.checkpoint, max_work_seconds=120,
        conversations=True, retention_target=previous.retention_target, terminal_reads=True,
    )
    with pytest.raises(ReplayStoreInvalid, match="explicit storage adoption"):
        operations.validate_storage(expires_at=expires(), checkpoint=lambda: None)
    assert previous.store.path.read_bytes() == before
    asyncio.run(integration[0].drain(timeout=2))


def test_service_restart_validates_real_selected_reader_without_adopting_or_losing_cursor(
    terminal_reader: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_service
    from agent_pull_requests.watch_observer import NativeObservationBinding
    from test_watch_service import Connector

    operations, gateway, _, native, _, _, _, binding, initial = terminal_reader
    native.exchange("capabilities", {})
    loaded = native.exchange("load", {
        "number": 42, "expected_current": initial["id"], "budget_seconds": 10,
    })
    cursor = loaded["result"]["conversation"]["cursor"]
    before = operations.store.path.read_bytes()
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)

    async def access() -> str:
        return "synthetic"

    def make() -> watch_service.WatchService:
        return watch_service.WatchService(
            operations.store, fleet=binding.fleet, gateway=binding.gateway, slot=binding.slot,
            bindings=tuple(
                WatchScopeBinding(principal, target, scope)
                for (principal, target), scope in gateway.bindings.items()
            ),
            observations=(NativeObservationBinding(binding.account, binding.repo, sys.executable),),
            gateway_origin="https://gateway.invalid", access_token_callback=access,
            proof_key=object(), authority=expires, checkpoint=lambda: None,
            workspaces={("caller-a", "project"): operations},
        )

    async def scenario() -> None:
        nonlocal operations
        for index in range(2):
            if index:
                previous = operations
                owner = ReplayStore(
                    previous.store.root, previous.store.namespace,
                    service_identity=previous.store.service_identity, terminal_retention=True,
                    terminal_recovery=True, terminal_cache_retirement=True,
                )
                operations = WorkspaceOperations(
                    owner, previous.cache, scope=previous.scope, read_owner=previous.read_owner,
                    binding_ref=previous.binding_ref, checkpoint=previous.checkpoint, max_work_seconds=120,
                    conversations=True, retention_target=previous.retention_target, terminal_reads=True,
                )
            service = make()
            await service.start()
            try:
                assert service.status()["writer_held"]
                capabilities = await service.gateway.handle(request(binding, "capabilities", {}, selected=False))
                assert capabilities.status == 200 and json.loads(capabilities.body)["protocol"] == PROTOCOL
                page = await service.gateway.handle(request(binding, "events", {
                    "number": 42, "after": None, "limit": 16,
                }))
                assert page.status == 200 and json.loads(page.body)["result"]["high_water"] == cursor
                assert operations.store.path.read_bytes() == before
            finally:
                await service.close(timeout=3)
            assert not service.status()["writer_held"]
        def unavailable(**kwargs) -> None:
            raise ReplayStoreInvalid("synthetic terminal reader unavailable")

        monkeypatch.setattr(operations, "validate_storage", unavailable)
        service = make()
        with pytest.raises(ReplayStoreInvalid, match="terminal reader unavailable"):
            await service.start()
        assert not service.connector.entered.is_set() and not service.status()["writer_held"]
        assert operations.store.path.read_bytes() == before
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_existing_cli_tools_select_terminal_reader_without_exposing_write_operations(
    terminal_reader: tuple, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    import io
    from agent_pull_requests import workspace_cli
    from agent_pull_requests.workspace_tools import WorkspaceTools

    operations, gateway, _, native, _, _, _, _, _ = terminal_reader
    host = WorkspaceTools(native)
    before = operations.store.path.read_bytes()
    assert workspace_cli.main(["workspace", "capabilities"], host=host) == 0
    capabilities = json.loads(capsys.readouterr().out)
    assert capabilities["backend_contract"]["protocol"] == PROTOCOL
    assert "events" in capabilities["operations"] and "submit" not in capabilities["operations"]
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps({
        "number": 42, "after": None, "limit": 16,
    }).encode())))
    assert workspace_cli.main(["workspace", "events", "--request", "-"], host=host) == 0
    assert json.loads(capsys.readouterr().out)["result"]["status"] == "unavailable"
    assert operations.store.path.read_bytes() == before
    asyncio.run(gateway.drain(timeout=2))
