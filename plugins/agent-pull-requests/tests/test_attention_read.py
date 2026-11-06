"""Actual retained feedback, CLI/client and authenticated handler; no live effects."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import io
import json
import sys

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest
from single_instance_lease import OperationLock

from test_review_executor import environment as environment
from test_review_feedback import feedback_environment as feedback_environment, observe, run
from test_workspace_integration import integration as integration

from agent_pull_requests import __main__ as cli
from agent_pull_requests.attention_acquisition import AttentionInputs
from agent_pull_requests.attention_client import AttentionClient
from agent_pull_requests.attention_read import AttentionReader, AttentionUnavailable
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore
from agent_pull_requests.review_feedback import FeedbackInputs
from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_client import ReplayClient, ReplayClientError
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.workspace_conversation import conversation_key

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def reading(feedback_environment: tuple, integration: tuple) -> tuple:
    store, _, _, _, backend, _, expires, interests = feedback_environment
    _, _, native, _, _, binding = integration
    workspace = native.operations
    workspace.conversations = True
    workspace.attention_owner = lambda generation: AttentionInputs(
        generation["id"], interests, True, False, False, False, False,
    )
    workspace.feedback_owner = lambda generation: FeedbackInputs(generation["id"], interests, 60)
    reader = AttentionReader(workspace, consumer=binding.consumer, max_age_seconds=60, aging_seconds=30)
    scope = ConsumerScope(binding.consumer, binding.account, binding.repo)
    gateway = WatchGateway(
        store, fleet=binding.fleet, gateway=binding.gateway, slot=binding.slot,
        bindings=(WatchScopeBinding(binding.principal, binding.target, scope),),
        workspaces={(binding.principal, binding.target): workspace},
        attention={(binding.principal, binding.target): reader},
    )

    class Caller:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.change = None

        async def request(self, method, target, *, request_headers, body, response_limit):
            self.calls.append(target)
            response = await gateway.handle(ServiceRequest(
                CallerContext(binding.fleet, binding.gateway, binding.slot,
                              binding.principal, expires),
                method, target, tuple((name.lower(), value) for name, value in request_headers), body,
            ))
            if self.change is not None:
                value = json.loads(response.body)
                self.change(value)
                response = replace(response, body=json.dumps(value).encode())
            return response

    caller = Caller()
    client = AttentionClient(caller, route_prefix="", binding=binding,
                             key=conversation_key({**workspace.scope, "number": 1}, store.namespace))
    return reader, gateway, client, caller, binding


def read(reader: AttentionReader, expires: int, **changes) -> dict:
    return reader.execute("read", {"limit": 16, "after": None, **changes},
                          expires_at=expires, checkpoint=lambda: None)


def test_cli_client_gateway_restart_preserves_bytes_and_independent_legacy_watch(
    feedback_environment: tuple, reading: tuple, monkeypatch, capsys,
) -> None:
    store, _, _, _, backend, _, expires, _ = feedback_environment
    reader, gateway, client, caller, binding = reading
    before = store.path.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"limit":16,"after":null}')))
    assert cli.main(["attention", "read", "--request", "-"], attention_host=client) == 0
    value = json.loads(capsys.readouterr().out)
    assert len(value["items"]) == 1 and value["items"][0]["reasons"] == ["initial-review"]
    assert caller.calls == ["/v1/attention/project/capabilities", "/v1/attention/project/read"]
    assert backend.calls == [] and store.path.read_bytes() == before
    reader.store = ReplayStore(store.root, store.namespace, clock=store.clock,
                               service_identity=store.service_identity)
    reader.workspace.store = reader.store
    assert read(reader, expires)["snapshot"] == value["snapshot"]

    async def scenario() -> None:
        legacy = ReplayClient(caller, route_prefix="", target=binding.target,
                              repo=binding.repo, binding=binding)
        assert (await legacy.capabilities()).binding == binding
        await gateway.drain(timeout=1)

    asyncio.run(scenario())
    with pytest.raises(ReplayStoreInvalid, match="serving contract unavailable"):
        ConversationStore(store).capabilities(expires_at=expires)
    assert store.path.read_bytes() == before


def test_expanded_readback_owner_is_not_advertised_as_v5_attention(
    feedback_environment: tuple, reading: tuple, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    store, _, _, _, backend, _, expires, _ = feedback_environment
    reader, gateway, client, caller, _ = reading
    FeedbackCaptureStore(store).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="requires adopted owner v5"):
        reader.execute("capabilities", {}, expires_at=expires, checkpoint=lambda: None)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"limit":16,"after":null}')))
    assert cli.main(["attention", "read", "--request", "-"], attention_host=client) == 3
    capsys.readouterr()
    assert caller.calls == ["/v1/attention/project/capabilities"]
    assert not backend.calls and store.path.read_bytes() == before
    asyncio.run(gateway.drain(timeout=1))


def test_unclassified_self_echo_is_unavailable_only_for_captured_consumer(
    feedback_environment: tuple, reading: tuple,
) -> None:
    value = feedback_environment
    reader, _, _, _, _ = reading
    assert run(value)["feedback"]["status"] == "handled"
    assert read(reader, value[6])["items"][0]["reasons"] == ("handled-through-cursor",)
    observe(value, change="echo")
    before = value[0].path.read_bytes()
    item = read(reader, value[6])["items"][0]
    assert item["status"] == "unavailable" and item["reasons"] == ("feedback-change-unclassified",)
    other = AttentionReader(reader.workspace, consumer="consumer-b", max_age_seconds=60, aging_seconds=30)
    assert read(other, value[6])["items"][0]["status"] == "actionable"
    assert value[0].path.read_bytes() == before


def test_proven_original_version_echo_reads_waiting_without_rewriting_capture(
    feedback_environment: tuple, reading: tuple,
) -> None:
    value = feedback_environment
    reader = reading[0]
    backend = value[4]
    backend.versioned_reply = True
    backend.pr["reviewers"] = copy.deepcopy(value[2]["sections"]["reviewers"]["data"])
    assert run(value)["feedback"]["status"] == "handled"
    newer = observe(value, change="echo")
    result = run(value, current=lambda: (newer, backend.positions[newer["id"]]))
    assert result["feedback"]["echo"]["status"] == "covered"
    before = value[0].path.read_bytes()
    assert read(reader, value[6])["items"][0]["status"] == "waiting"
    assert value[0].path.read_bytes() == before


@pytest.mark.parametrize("kind", ["comment", "verdict"])
def test_another_operations_echo_cursor_cannot_prove_this_operations_readback(
    feedback_environment: tuple, reading: tuple, kind: str,
) -> None:
    value = feedback_environment
    reader = reading[0]
    backend = value[4]
    backend.versioned_reply = True
    backend.pr["reviewers"] = copy.deepcopy(value[2]["sections"]["reviewers"]["data"])
    assert run(value)["feedback"]["status"] == "handled"
    newer = observe(value, change="echo")
    assert run(value, current=lambda: (newer, backend.positions[newer["id"]]))[
        "feedback"
    ]["echo"]["status"] == "covered"
    first_proof = json.loads(value[0].path.read_bytes())["attention"]["records"][0]["provenance"]
    selected = value[1]
    selected.update(operation_id="second-operation", generation_id=newer["id"])
    selected["actions"] = [
        {"id": "second-action", "kind": "verdict", "verdict": "APPROVED"}
        if kind == "verdict" else
        {"id": "second-action", "kind": "comment", "body": "Second selected feedback"}
    ]
    attachment = copy.deepcopy(value[3])
    attachment["generation_id"] = newer["id"]
    backend.emit_vote_thread = True
    second = (value[0], selected, newer, attachment, *value[4:])
    result = run(second)
    assert all(action["state"] == "confirmed" for action in result["actions"])
    if kind == "verdict":
        assert result["feedback"]["reason"] == "exact_vote_readback_unavailable"
    document = json.loads(value[0].path.read_bytes())
    assert document["attention"]["records"][0]["provenance"] == first_proof
    assert document["feedback_captures"]["records"][1]["cursor"] == {"epoch": 1, "sequence": 2}
    before = value[0].path.read_bytes()
    item = read(reader, value[6])["items"][0]
    assert item["status"] == "unavailable"
    assert item["reasons"] == ("feedback-operation-readback-unavailable",)
    assert value[0].path.read_bytes() == before


def test_stale_observation_and_unknown_selection_are_not_actionable(
    feedback_environment: tuple, reading: tuple,
) -> None:
    value = feedback_environment
    reader = reading[0]
    value[5][0] += 60
    assert read(reader, value[6])["items"][0]["status"] == "unavailable"

    def change(document: dict) -> None:
        document["attention"]["records"][0]["interest"]["matches"] = None

    value[0]._review_transaction(change, expires_at=value[6])
    assert read(reader, value[6])["items"][0]["reasons"] == ("selection-unknown",)


def test_unknown_native_feedback_survives_reopen_without_masking_independent_consumers(
    feedback_environment: tuple, reading: tuple,
) -> None:
    value = feedback_environment
    reader = reading[0]
    value[4].lose_reply = True
    result = run(value)
    assert result["actions"][0]["state"] == "unknown"
    observe(value, change="completed")
    observe(value, change="active", second=12)
    before = value[0].path.read_bytes()
    item = read(reader, value[6])["items"][0]
    assert item["cursor"]["epoch"] == 2
    assert item["status"] == "unavailable" and item["reasons"] == ("feedback-reconciliation-pending",)
    other = AttentionReader(reader.workspace, consumer="consumer-b", max_age_seconds=60, aging_seconds=30)
    independent = read(other, value[6])["items"][0]
    assert independent["cursor"]["epoch"] == 2 and independent["reasons"] == ("initial-review",)
    assert independent["status"] == "actionable"
    assert value[0].path.read_bytes() == before
    assert value[0].inspect_review(value[1]["operation_id"], value[1]["scope"],
                                  expires_at=value[6])["actions"] == result["actions"]


def test_page_snapshot_refuses_changed_input_instead_of_skipping(
    feedback_environment: tuple, reading: tuple,
) -> None:
    value = feedback_environment
    reader = reading[0]

    def add(document: dict) -> None:
        second = copy.deepcopy(document["attention"]["records"][0])
        second["facts"]["key"]["number"] += 1
        document["attention"]["records"].append(second)

    value[0]._review_transaction(add, expires_at=value[6])
    first = read(reader, value[6], limit=1)
    assert first["next"] is not None
    second = read(reader, value[6], limit=1, after=first["next"])
    assert second["items"][0]["key"]["number"] != first["items"][0]["key"]["number"]
    observe(value, change="reply")
    before = value[0].path.read_bytes()
    with pytest.raises(AttentionUnavailable, match="page changed"):
        read(reader, value[6], limit=1, after=first["next"])
    assert value[0].path.read_bytes() == before


@pytest.mark.parametrize("change,status", [
    ({"consumer": "other"}, 400), ({"limit": True}, 400), ({"limit": 17}, 400),
    ({"after": {"snapshot": "x", "offset": 1}}, 400),
])
def test_request_cannot_override_scope_or_progress(
    feedback_environment: tuple, reading: tuple, change: dict, status: int,
) -> None:
    reader, gateway, _, _, binding = reading
    before = reader.store.path.read_bytes()

    async def scenario() -> None:
        response = await gateway.handle(ServiceRequest(
            CallerContext(binding.fleet, binding.gateway, binding.slot, binding.principal,
                          feedback_environment[6]),
            "POST", "/v1/attention/project/read", (("content-type", "application/json"),),
            json.dumps({"binding": binding.document(), "request": {
                "limit": 16, "after": None, **change,
            }}).encode(),
        ))
        assert response.status == status
        await gateway.drain(timeout=1)

    asyncio.run(scenario())
    assert reader.store.path.read_bytes() == before


@pytest.mark.parametrize("cause", ["missing", "old", "contract", "scope"])
def test_new_client_refuses_absent_or_incompatible_attention_without_retry(
    feedback_environment: tuple, reading: tuple, cause: str,
) -> None:
    reader, gateway, client, caller, _ = reading
    before = reader.store.path.read_bytes()
    if cause == "missing":
        gateway.attention = {}
    elif cause == "old":
        reader.store = ReplayStore(reader.store.root, "other", clock=reader.store.clock,
                                   service_identity=reader.store.service_identity)
    elif cause == "contract":
        caller.change = lambda value: value.update(owner_store_versions=[3])
    else:
        caller.change = lambda value: value["binding"].update(consumer="other")

    async def scenario() -> None:
        with pytest.raises(ReplayClientError):
            await client.capabilities()
        with pytest.raises(ReplayClientError, match="capability_required"):
            await client.read(limit=16, after=None)
        await gateway.drain(timeout=1)

    asyncio.run(scenario())
    assert len(caller.calls) == 1 and reader.store.path.read_bytes() == before


def test_real_owner_lock_and_revocation_cannot_be_bypassed(
    feedback_environment: tuple, reading: tuple,
) -> None:
    reader, gateway, client, _, _ = reading
    before = reader.store.path.read_bytes()

    async def scenario() -> None:
        await client.capabilities()
        with OperationLock(reader.store.root / "watch-replay.lock"):
            with pytest.raises(ReplayClientError, match="unavailable"):
                await client.read(limit=16, after=None)
        reader.workspace.feedback_owner = None
        with pytest.raises(ReplayClientError, match="unavailable"):
            await client.read(limit=16, after=None)
        await gateway.drain(timeout=1)

    asyncio.run(scenario())
    assert reader.store.path.read_bytes() == before


@pytest.mark.parametrize("cause", ["unknown", "unbound", "readback"])
def test_uncertain_tracking_retains_native_outcomes_without_an_initial_review(
    feedback_environment: tuple, reading: tuple, cause: str,
) -> None:
    value = feedback_environment
    reader = reading[0]
    if cause == "unknown":
        value[4].lose_reply = True
    elif cause == "readback":
        value[1]["actions"].append({"id": "verdict", "kind": "verdict", "verdict": "APPROVED"})
        value[4].emit_vote_thread = True
    result = run(value)
    if cause == "unbound":
        def remove_capture(document: dict) -> None:
            document["feedback_captures"]["records"] = []
            row = document["attention"]["records"][0]
            row["handled"] = row["provenance"] = None
        value[0]._review_transaction(remove_capture, expires_at=value[6])
    before = value[0].path.read_bytes()
    item = read(reader, value[6])["items"][0]
    assert item["status"] == "unavailable" and "initial-review" not in item["reasons"]
    assert value[0].path.read_bytes() == before
    assert value[0].inspect_review(value[1]["operation_id"], value[1]["scope"],
                                  expires_at=value[6])["actions"] == result["actions"]


@pytest.mark.parametrize("field", ["consumer", "account", "namespace", "candidate"])
def test_client_rejects_foreign_or_malformed_read_evidence(
    feedback_environment: tuple, reading: tuple, field: str,
) -> None:
    _, gateway, client, caller, _ = reading
    async def scenario() -> None:
        await client.capabilities()
        def change(value: dict) -> None:
            item = value["items"][0]
            if field == "consumer":
                item[field] = "other"
            elif field == "candidate":
                item[field]["head"] = "short"
            else:
                item["key"][field] = "other"
        caller.change = change
        with pytest.raises(ReplayClientError, match="invalid_attention_response"):
            await client.read(limit=16, after=None)
        await gateway.drain(timeout=1)
    asyncio.run(scenario())


def test_default_cli_is_explicitly_unavailable_and_unknown_flags_refuse(environment, capsys) -> None:
    assert cli.main(["attention", "capabilities"]) == 0
    assert json.loads(capsys.readouterr().out)["owner_binding"] == "unavailable"
    with pytest.raises(SystemExit) as error:
        cli.main(["attention", "read", "--request", "-", "--consumer", "other"])
    assert error.value.code == 2


@pytest.mark.parametrize("operation", ["capabilities", "read"])
def test_actual_gateway_authority_refusal_is_cli_exit_four(
    feedback_environment: tuple, reading: tuple, monkeypatch, capsys, operation: str,
) -> None:
    reader, _, client, caller, _ = reading
    before = reader.store.path.read_bytes()
    original = caller.request
    def refused() -> None:
        raise PermissionError("synthetic current authority refused")
    async def request(method, target, **kwargs):
        if target.endswith("/" + operation):
            reader.workspace.checkpoint = refused
        return await original(method, target, **kwargs)
    monkeypatch.setattr(caller, "request", request)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"limit":16,"after":null}')))
    assert cli.main(["attention", "read", "--request", "-"], attention_host=client) == 4
    assert json.loads(capsys.readouterr().out)["error"] == "attention_owner_refused"
    assert reader.store.path.read_bytes() == before


@pytest.mark.parametrize("cause,exit_code", [
    ("capabilities", 1), ("read", 1), ("missing-owner", 3),
])
def test_cli_distinguishes_invalid_successful_results_from_missing_owners(
    feedback_environment: tuple, reading: tuple, monkeypatch, capsys, cause: str, exit_code: int,
) -> None:
    reader, gateway, client, caller, _ = reading
    before = reader.store.path.read_bytes()
    if cause == "missing-owner":
        gateway.attention = {}
    else:
        def change(value: dict) -> None:
            if cause == "capabilities":
                value["protocol"] = "invalid"
            elif "items" in value:
                value["items"] = "invalid"
        caller.change = change
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"limit":16,"after":null}')))
    assert cli.main(["attention", "read", "--request", "-"], attention_host=client) == exit_code
    assert json.loads(capsys.readouterr().out)["error"] == (
        "attention_unsupported" if cause == "missing-owner" else "invalid_attention_response"
    )
    assert reader.store.path.read_bytes() == before


@pytest.mark.parametrize("refresh_finished", [True, False])
def test_overlapping_refresh_explicitly_supersedes_read_without_invalid_result(
    feedback_environment: tuple, reading: tuple, monkeypatch, refresh_finished: bool,
) -> None:
    reader, gateway, client, caller, _ = reading
    before = reader.store.path.read_bytes()
    async def scenario() -> None:
        await client.capabilities()
        original = caller.request
        entered, release, refresh_entered, refresh_release = (asyncio.Event() for _ in range(4))
        async def request(method, target, **kwargs):
            response = await original(method, target, **kwargs)
            if target.endswith("/read"):
                entered.set()
                await release.wait()
            elif not refresh_finished:
                refresh_entered.set()
                await refresh_release.wait()
            return response
        monkeypatch.setattr(caller, "request", request)
        pending = asyncio.create_task(client.read(limit=16, after=None))
        await entered.wait()
        refresh = asyncio.create_task(client.capabilities())
        try:
            if refresh_finished:
                await refresh
            else:
                await refresh_entered.wait()
            release.set()
            with pytest.raises(ReplayClientError, match="attention_discovery_superseded"):
                await pending
        finally:
            release.set()
            refresh_release.set()
            await asyncio.gather(pending, refresh, return_exceptions=True)
            await gateway.drain(timeout=1)
    asyncio.run(scenario())
    assert reader.store.path.read_bytes() == before


def test_service_writer_qualifies_v5_reader_without_claiming_v3_conversation_support(
    feedback_environment: tuple, reading: tuple, monkeypatch,
) -> None:
    from test_watch_service import Connector
    from agent_pull_requests import watch_service
    from agent_pull_requests.watch_observer import NativeObservationBinding
    from agent_pull_requests.watch_service import WatchService

    reader, _, _, _, binding = reading
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)
    async def access() -> str:
        return "synthetic"
    service = WatchService(
        reader.store, fleet=binding.fleet, gateway=binding.gateway, slot=binding.slot,
        bindings=(WatchScopeBinding(binding.principal, binding.target, ConsumerScope(
            binding.consumer, binding.account, binding.repo,
        )),),
        observations=(NativeObservationBinding(binding.account, binding.repo, sys.executable),),
        gateway_origin="https://gateway.invalid", access_token_callback=access,
        proof_key=object(), authority=lambda: feedback_environment[6], checkpoint=lambda: None,
        workspaces={(binding.principal, binding.target): reader.workspace},
        attention={(binding.principal, binding.target): reader},
    )
    monkeypatch.setattr(service.observer, "cycle", lambda **_kwargs: {
        "status": "observed", "unavailable": 0, "unbound": 0,
    })
    before = reader.store.path.read_bytes()
    async def scenario() -> None:
        await service.start()
        try:
            assert service._lease.held
            response = await service.gateway.handle(ServiceRequest(
                CallerContext(binding.fleet, binding.gateway, binding.slot, binding.principal,
                              feedback_environment[6]),
                "POST", "/v1/attention/project/capabilities", (("content-type", "application/json"),),
                json.dumps({"binding": binding.document(), "request": {}}).encode(),
            ))
            assert response.status == 200 and json.loads(response.body)["owner_store_versions"] == [5]
            with pytest.raises(ReplayStoreInvalid):
                ConversationStore(reader.store).capabilities(expires_at=feedback_environment[6])
        finally:
            await service.close(timeout=3)
        assert not service._lease.held and service.status()["closed"]
    asyncio.run(scenario())
    assert reader.store.path.read_bytes() == before
