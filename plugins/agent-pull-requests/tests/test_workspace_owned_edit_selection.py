"""Optional edit capability through the existing actual CLI and transport path."""

from __future__ import annotations

import asyncio
import copy

import pytest

from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.review_selection_store import ReviewSelectionStore
from agent_pull_requests.review_workspace import _digest, _record
from agent_pull_requests.workspace_client import WorkspaceClientError
from agent_pull_requests.workspace_operations import WorkspaceUnavailable
from agent_pull_requests.workspace_selection import selection_contract, supports_selection
from agent_pull_requests.workspace_tools import WorkspaceTools
from test_review_executor import environment as environment
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_integration import integration as integration, prepared_workspace as prepared_workspace
from test_workspace_selection import selection_workspace as selection_workspace
from test_workspace_tools import invoke_cli

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def owned_workspace(prepared_workspace: tuple, selection_workspace: tuple) -> tuple:
    _, _, native, _, _, _ = prepared_workspace
    operations = native.operations
    operations.__init__(
        operations.store, operations.cache, scope=operations.scope, read_owner=operations.read_owner,
        binding_ref=operations.binding_ref, write_context=operations.write_context,
        checkpoint=operations.checkpoint, max_work_seconds=operations.max_work_seconds,
        prepared_reviews=True, selection_preparer=operations.selection_preparer, selection_owned_edits=True,
    )
    draft, state = selection_workspace
    draft["actions"] = draft["actions"][:1]
    return draft, state


def select_edit(environment: tuple, prepared_workspace: tuple, draft: dict, *, content: str | None = None) -> None:
    _, selected, generation, attachment, backend, _ = environment
    _, _, native, _, _, _ = prepared_workspace
    if content is not None:
        backend.threads = [{"id": 50, "status": 1, "comments": [{
            "id": 1, "parentCommentId": 0, "commentType": 1, "content": content,
            "author": {"id": draft["scope"]["principal_id"]},
        }]}]
    old_id = generation["id"]
    generation["sections"]["discussions"]["data"] = [_record("discussions", row) for row in backend.threads]
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    attachment["generation_id"] = draft["generation_id"] = generation["id"]
    native.operations.cache.publish(generation, expected_current=old_id)
    selected["operation_id"] = draft["operation_id"] = "selected-intentional-edit"
    draft["actions"] = [{
        "id": "owned-finding", "kind": "edit_owned_comment", "body": "Selected intentional correction.",
        "thread_id": backend.threads[0]["id"], "comment_id": backend.threads[0]["comments"][0]["id"],
        "expected_content_id": _digest(backend.threads[0]["comments"][0]["content"]),
    }]


@pytest.mark.parametrize("transport", ["native", "gateway"])
def test_preparation_only_owned_edit_uses_exact_baseline_without_enabling_submit(
    environment: tuple, prepared_workspace: tuple, owned_workspace: tuple,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], transport: str,
) -> None:
    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, _ = owned_workspace
    select_edit(environment, prepared_workspace, draft, content="Original legacy note.")
    native.operations.write_context = None
    host = WorkspaceTools(native if transport == "native" else client)
    capabilities = asyncio.run(host.capabilities())
    assert "allocate" in capabilities["operations"] and "submit" not in capabilities["operations"]
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    code, preview = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and preview["result"]["intent"]["actions"][0]["kind"] == "edit_owned_comment", preview
    before = store.path.read_bytes()
    code, retry = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and retry == preview
    code, _ = invoke_cli(host, "submit", {**request, "intent": preview["result"]["intent"]}, monkeypatch, capsys)
    assert code != 0 and store.path.read_bytes() == before and backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("lost", [False, True])
def test_actual_cli_edit_preview_recovery_submission_and_original_receipts(
    environment: tuple, prepared_workspace: tuple, owned_workspace: tuple,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    transport: str, legacy: bool, lost: bool,
) -> None:
    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, state = owned_workspace
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    original_identity = None
    if not legacy:
        code, first = invoke_cli(host, "allocate", request, monkeypatch, capsys)
        assert code == 0, first
        chosen = first["result"]["intent"]
        original_identity = chosen["actions"][0]["preparation"]["identity"]
        code, result = invoke_cli(host, "submit", {**request, "intent": chosen}, monkeypatch, capsys)
        assert code == 0 and result["result"]["status"] == "complete", result
    select_edit(environment, prepared_workspace, draft, content="Original legacy note." if legacy else None)
    calls = len(backend.calls)
    if lost:
        execute = native.operations.execute

        def drop(operation: str, payload: dict, **kwargs: object) -> dict:
            result = execute(operation, payload, **kwargs)
            if operation == "allocate":
                raise RuntimeError("synthetic committed edit preview lost")
            return result

        with monkeypatch.context() as failure:
            failure.setattr(native.operations, "execute", drop)
            code, _ = invoke_cli(host, "allocate", request, monkeypatch, capsys)
            assert code != 0
    code, preview = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and preview["result"]["approval_required"] is True, preview
    chosen = preview["result"]["intent"]
    identity = chosen["actions"][0]["preparation"]["identity"]
    if original_identity is not None:
        assert identity["comment_id"] == original_identity["comment_id"]
        assert identity["write_id"] != original_identity["write_id"]
    assert len(backend.calls) == calls
    assert ReviewSelectionStore(store).recover(
        draft, expires_at=native.expires_at(),
    )["selection"]["stage"] == "prepared"
    original = copy.deepcopy(chosen)
    native.operations.selection_preparer = lambda *a, **k: pytest.fail("edit recovery re-resolved policy")
    with monkeypatch.context() as unavailable:
        unavailable.setattr(GenerationCache, "lookup", lambda *a, **k: pytest.fail("edit recovery needs old cache"))
        code, retry = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and retry == preview and chosen == original
    assert state["resolutions"] == (1 if legacy else 2)
    code, submitted = invoke_cli(host, "submit", {**request, "intent": chosen}, monkeypatch, capsys)
    assert code == 0 and submitted["result"]["status"] == "complete", submitted
    writes = len([row for row in backend.calls if row[0] != "GET"])
    code, retry = invoke_cli(host, "submit", {**request, "intent": chosen}, monkeypatch, capsys)
    assert code == 0 and retry["result"]["entered_writes"] == 0, retry
    assert len([row for row in backend.calls if row[0] != "GET"]) == writes
    code, receipt = invoke_cli(host, "receipt", {
        "number": 42, "operation_id": draft["operation_id"], "action_id": "owned-finding",
    }, monkeypatch, capsys)
    assert code == 0 and receipt["result"]["identity"] == identity, receipt
    before, calls = store.path.read_bytes(), len(backend.calls)
    native.operations.selection_owned_edits = False
    code, _ = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code != 0 and store.path.read_bytes() == before and len(backend.calls) == calls
    native.operations.selection_owned_edits = True
    code, restored = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and restored == preview and store.path.read_bytes() == before
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("support", ["missing", "malformed", "withdrawn", "refreshed"])
def test_actual_edit_support_is_selected_before_dispatch_and_rechecked_by_receiver(
    environment: tuple, prepared_workspace: tuple, owned_workspace: tuple,
    monkeypatch: pytest.MonkeyPatch, transport: str, support: str,
) -> None:
    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, caller, _ = prepared_workspace
    draft, state = owned_workspace
    select_edit(environment, prepared_workspace, draft, content="Legacy note.")
    if support == "missing":
        native.operations.selection_owned_edits = False
    execute = native.operations.execute
    dispatched = []

    def observe(operation: str, payload: dict, **kwargs: object) -> dict:
        if operation == "allocate":
            dispatched.append(operation)
        result = execute(operation, payload, **kwargs)
        if operation == "capabilities" and support == "malformed":
            result["owned_edit_selection_contract"]["approval_required"] = 1
        return result

    monkeypatch.setattr(native.operations, "execute", observe)

    async def scenario() -> None:
        host = WorkspaceTools(native if transport == "native" else client)
        capabilities = await host.capabilities()
        assert capabilities["backend_contract"]["selection_contract"] == selection_contract()
        assert "allocate" in capabilities["operations"]
        if support in ("withdrawn", "refreshed"):
            native.operations.selection_owned_edits = False
            if support == "refreshed":
                if transport == "gateway":
                    await client.capabilities()
                else:
                    native.exchange("capabilities", {})
        before, calls = store.path.read_bytes(), len(caller.calls)
        failure = WorkspaceClientError if transport == "gateway" else (
            ValueError if support == "withdrawn" else WorkspaceUnavailable
        )
        with pytest.raises(failure) as denied:
            request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
            if transport == "native" and support == "withdrawn":
                # Tool invocation refreshes native discovery; direct cached exchange
                # separately exercises the receiver's current-policy recheck.
                native.exchange("allocate", request)
            else:
                await host.invoke("allocate", request)
        if support != "withdrawn":
            assert dispatched == []
            assert not any(path.endswith("/allocate") for path in caller.calls[calls:])
            if transport == "gateway":
                assert denied.value.outcome_unknown is False
        else:
            assert dispatched == ["allocate"]
        assert store.path.read_bytes() == before and backend.calls == [] and state["resolutions"] == 0
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_extended_receiver_keeps_prior_fresh_selection_contract_usable(
    environment: tuple, prepared_workspace: tuple, owned_workspace: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, _ = owned_workspace
    execute = native.operations.execute

    def prior_discovery(operation: str, payload: dict, **kwargs: object) -> dict:
        result = execute(operation, payload, **kwargs)
        if operation == "capabilities":
            assert supports_selection(result["selection_contract"])
            result.pop("owned_edit_selection_contract")
        return result

    monkeypatch.setattr(native.operations, "execute", prior_discovery)

    async def scenario() -> None:
        host = WorkspaceTools(client)
        preview = await host.invoke("allocate", {
            "number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60,
        })
        assert preview["result"]["intent"]["version"] == 2 and backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
