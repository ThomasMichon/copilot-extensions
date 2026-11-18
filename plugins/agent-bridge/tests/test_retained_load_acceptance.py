"""Accepted native RPCs survive later failures without inventing CLI holds."""

from __future__ import annotations

from dataclasses import replace
import json
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge import retained_load_acceptance as acceptance
from agent_bridge import retained_session_recovery as recovery
from agent_bridge.acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from agent_bridge.models import RetainedRecoveryRequest
from agent_bridge.session_host.host_index import HostRecord

pytestmark = pytest.mark.contract("agent_bridge.retained_load_acceptance")


@pytest.fixture
def original(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    physical = {
        41: {"pid": 41, "start_time": "100", "parent_pid": 1},
        42: {"pid": 42, "start_time": "101", "parent_pid": 41},
    }
    observed_epoch = {"platform": "linux", "environment_id": "a" * 32, "boot_id": "original-boot"}
    monkeypatch.setattr(acceptance, "epoch", lambda: dict(observed_epoch))
    monkeypatch.setattr(acceptance, "process", lambda pid: dict(physical[pid]))
    request = RetainedRecoveryRequest(
        operation_id="original-operation", journal_key="a" * 64,
        native_session_id="11111111-1111-4111-8111-111111111111",
        head_revision=7, bridge_installation="/original-bridge",
    )
    writer = WorktreeWriterRequirement.model_validate({
        "version": 1, "identity": {
            "installation_root": "/original-worktrees", "runtime_generation": "b" * 32,
        }, "creation_nonce": "original-allocation",
    })
    monkeypatch.setattr(
        "agent_bridge.acquisition_worktree_writer.current",
        lambda: SimpleNamespace(execution_requirement=writer),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    session = SimpleNamespace(
        session_id="original-bridge-session", acp_session_id=request.native_session_id,
        target=SimpleNamespace(cwd="/original-workspace"),
        event_log=SimpleNamespace(append=lambda name, data: events.append((name, data))),
    )
    manager = SimpleNamespace(db=SimpleNamespace(flush=Mock()))
    owner = recovery.RetainedRecovery(manager, session, request, "{}", None)
    owner.host_record = HostRecord(
        session_id=session.session_id, port=1234, host_pid=41, child_pid=42,
        state_file="/original-state", created_at=1.0, nonce="original-nonce",
        extra={"host_process_start": "100", "child_process_start": "101"},
    )
    owner.host_process = SimpleNamespace(pid=41, poll=lambda: None)
    owner.socket, owner.streams = object(), object()
    owner.hosted_epoch = acceptance.negotiate({"retained_hosted_activation": 1})
    client = SimpleNamespace(session_host_client=owner.socket)
    session.client = client
    require = Mock()
    monkeypatch.setattr(owner, "require_native", require)
    monkeypatch.setattr(owner, "require_current", Mock())
    return SimpleNamespace(
        owner=owner, client=client, physical=physical, epoch=observed_epoch,
        events=events, require=require, writer=writer,
    )


@pytest.mark.asyncio
async def test_acknowledgement_is_durable_before_return_and_after_stop(original: SimpleNamespace) -> None:
    owner = original.owner
    response = object()

    async def rpc(**_kwargs: Any) -> object:
        original.require.side_effect = AcquisitionRefused("stopped", "stop arrived during RPC")
        return response

    result = await owner.native_method(original.client, owner.request.native_session_id, rpc)(
        session_id=owner.request.native_session_id, cwd=owner.session.target.cwd,
    )
    assert result is response
    owner.manager.db.flush.assert_called_once()
    assert len(original.events) == 1
    event, document = original.events[0]
    assert event == acceptance.EVENT
    assert document["request"] == owner.request.model_dump()
    assert document["writer"] == original.writer.model_dump()
    assert document["host"] == acceptance.host_binding(owner.host_record)
    assert document["activation"]["child"] == {"pid": 42, "start_time": "101"}
    assert document["activation"]["epoch"] == original.epoch
    assert owner.accepted_load == document
    with pytest.raises(AcquisitionRefused):
        await owner.native_method(original.client, owner.request.native_session_id, rpc)(
            session_id=owner.request.native_session_id, cwd=owner.session.target.cwd,
        )
    assert len(original.events) == 1


@pytest.mark.asyncio
async def test_lost_rpc_result_cannot_invent_acceptance_or_retry(original: SimpleNamespace) -> None:
    owner = original.owner
    rpc = AsyncMock(side_effect=TimeoutError("load may have happened"))
    method = owner.native_method(original.client, owner.request.native_session_id, rpc)
    with pytest.raises(TimeoutError):
        await method(session_id=owner.request.native_session_id, cwd=owner.session.target.cwd)
    assert original.events == [] and owner.accepted_load is None
    owner.manager.db.flush.assert_not_called()
    with pytest.raises(AcquisitionRefused, match="native request changed"):
        await method(session_id=owner.request.native_session_id, cwd=owner.session.target.cwd)
    rpc.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_journal_keeps_acknowledged_rpc_unknown(original: SimpleNamespace) -> None:
    owner = original.owner
    owner.manager.db.flush.side_effect = OSError("journal commit failed")
    rpc = AsyncMock(return_value=object())
    method = owner.native_method(original.client, owner.request.native_session_id, rpc)
    with pytest.raises(OSError, match="journal commit"):
        await method(session_id=owner.request.native_session_id, cwd=owner.session.target.cwd)
    assert owner.accepted_load is None and owner.native_rpc_started
    with pytest.raises(AcquisitionRefused):
        await method(session_id=owner.request.native_session_id, cwd=owner.session.target.cwd)
    rpc.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["transport", "host", "birth", "parent", "epoch", "remote"])
async def test_unqualified_association_refuses_before_native_rpc(
    original: SimpleNamespace, changed: str,
) -> None:
    owner = original.owner
    if changed == "transport":
        original.client.session_host_client = object()
    elif changed == "host":
        owner.host_process.pid = 99
    elif changed == "birth":
        original.physical[42]["start_time"] = "102"
    elif changed == "parent":
        original.physical[42]["parent_pid"] = 99
    elif changed == "epoch":
        original.epoch["boot_id"] = "another-boot"
    else:
        owner.host_record = replace(owner.host_record, boundary="ssh")
    rpc = AsyncMock()
    with pytest.raises(AcquisitionRefused):
        await owner.native_method(original.client, owner.request.native_session_id, rpc)(
            session_id=owner.request.native_session_id, cwd=owner.session.target.cwd,
        )
    rpc.assert_not_called()
    assert not owner.native_rpc_started and original.events == []


@pytest.mark.parametrize("capability", [None, False, True, 0, "1", 2])
def test_missing_or_unsupported_executor_does_not_observe_or_launch(
    monkeypatch: pytest.MonkeyPatch, capability: object,
) -> None:
    observe = Mock(side_effect=AssertionError("unsupported provider must refuse first"))
    monkeypatch.setattr(acceptance, "epoch", observe)
    with pytest.raises(AcquisitionRefused, match="lacks retained hosted"):
        acceptance.negotiate({"retained_hosted_activation": capability})
    observe.assert_not_called()


def test_unsupported_local_epoch_refuses_before_native_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "epoch", Mock(side_effect=ValueError("unsupported platform")))
    with pytest.raises(AcquisitionRefused, match="qualified local epoch"):
        acceptance.negotiate({"retained_hosted_activation": 1})


def test_registration_conveys_only_the_acknowledged_physical_activation(original: SimpleNamespace) -> None:
    owner = original.owner
    accepted = acceptance.prepare(owner, original.client)
    acceptance.record(owner, accepted)
    registered = acceptance.registration(owner, original.client)
    assert registered == accepted["activation"]
    registered["child"]["pid"] = 99
    assert accepted["activation"]["child"]["pid"] == 42
    owner.require_current.assert_called_once()


def test_registration_conveys_explicit_hosted_writer_option(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge import worktree_lineage

    captured: list[str] = []

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.extend(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(worktree_lineage, "_run", run)
    activation = {"version": 1, "child": {"pid": 42, "start_time": "100"}}
    assert worktree_lineage.register_session("workspace", "native", pid=42, hosted_activation=activation)
    assert json.loads(captured[captured.index("--retained-hosted-activation") + 1]) == activation


@pytest.mark.parametrize("changed", [
    "missing", "request", "writer", "host", "birth", "epoch", "transport", "stop",
])
def test_registration_cannot_turn_changed_or_unknown_load_into_cli_evidence(
    original: SimpleNamespace, changed: str,
) -> None:
    owner = original.owner
    acceptance.record(owner, acceptance.prepare(owner, original.client))
    if changed == "missing":
        owner.accepted_load = None
    elif changed == "request":
        owner.accepted_load["request"]["operation_id"] = "another-operation"
    elif changed == "writer":
        owner.accepted_load["writer"]["creation_nonce"] = "another-incarnation"
    elif changed == "host":
        owner.host_record = replace(owner.host_record, nonce="another-transport")
    elif changed == "birth":
        original.physical[42]["start_time"] = "102"
    elif changed == "epoch":
        original.epoch["boot_id"] = "another-boot"
    elif changed == "transport":
        original.client.session_host_client = object()
    else:
        owner.require_current.side_effect = AcquisitionRefused("stopped", "stop hold")
    with pytest.raises(AcquisitionRefused):
        acceptance.registration(owner, original.client)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["target-settings", "caller-settings"])
async def test_pending_configuration_preserves_original_intent_with_fresh_readback(
    source: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.acp_client import AcpClient
    from agent_bridge import retained_load_configuration as configuration

    options = [
        {"id": key, "currentValue": selected,
         "options": [{"value": value} for value in (selected, "other")]}
        for key, selected in (("model", "original-model"), ("reasoning_effort", "high"))
    ]
    first = AcpClient(preference_source=source, confirmed_preferences={
        "model": "original-model", "reasoning_effort": "high",
    })
    monkeypatch.setattr(first, "_resolve_caller_preferences", lambda: {
        "model": "original-model", "reasoning_effort": "high",
    })
    frozen = configuration.response(configuration.intent(first), SimpleNamespace(config_options=options))
    client = AcpClient()
    client.adopt_session("original-native")
    client._connection = SimpleNamespace(set_config_option=AsyncMock(return_value=SimpleNamespace(config_options=options)))
    fresh_defaults = Mock(side_effect=AssertionError("recovery must not resolve fresh defaults"))
    monkeypatch.setattr(client, "_resolve_caller_preferences", fresh_defaults)
    events: list[tuple[str, dict[str, Any]]] = []
    await configuration.finish(
        client, [{"event_type": acceptance.EVENT, "data": {"configuration": frozen}}],
        Mock(), lambda name, data: events.append((name, data)),
    )
    assert client._preferences_ready
    assert [call.kwargs for call in client._connection.set_config_option.await_args_list] == [
        {"config_id": key, "session_id": "original-native", "value": selected}
        for key, selected in (("model", "original-model"), ("reasoning_effort", "high"))
    ]
    fresh_defaults.assert_not_called()
    if source == "target-settings":
        assert ("preference_selected", {"model": "original-model", "reasoning_effort": "high"}) in events


@pytest.mark.asyncio
async def test_pending_target_configuration_cannot_use_stale_ack_as_current_readback() -> None:
    from agent_bridge.acp_client import AcpClient
    from agent_bridge import retained_load_configuration as configuration
    from agent_remote_driver.session_preferences import PreferenceApplicationError

    options = [{"id": "model", "currentValue": "original-model",
                "options": [{"value": "original-model"}, {"value": "other"}]}]
    client = AcpClient(preference_source="target-settings", confirmed_preferences={"model": "original-model"})
    frozen = configuration.response(configuration.intent(client), SimpleNamespace(config_options=options))
    client.adopt_session("original-native")
    client._connection = SimpleNamespace(set_config_option=AsyncMock(
        return_value=SimpleNamespace(config_options=[{**options[0], "currentValue": "other"}]),
    ))
    with pytest.raises(PreferenceApplicationError, match="required model/effort"):
        await configuration.finish(
            client, [{"event_type": acceptance.EVENT, "data": {"configuration": frozen}}],
            Mock(), Mock(),
        )
    assert not client._preferences_ready
    client._connection.set_config_option.assert_awaited_once()
