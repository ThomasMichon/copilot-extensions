"""Committed previews through the actual existing CLI, native and Gateway owners."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from single_instance_lease import OperationLockedError

from agent_pull_requests.review_selection_store import ReviewSelectionStore
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_tools import WorkspaceTools, WorkspaceToolUnavailable
from test_review_executor import environment as environment
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_integration import integration as integration, prepared_workspace as prepared_workspace
from test_workspace_tools import invoke_cli

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def selection_workspace(environment: tuple, prepared_workspace: tuple, comment_preparer: tuple) -> tuple:
    store, selected, _, _, _, _ = environment
    _, _, native, _, _, _ = prepared_workspace
    prepare, state = comment_preparer
    ReviewSelectionStore(store).adopt(
        lifecycle_checkpoint=lambda: None, expires_at=native.expires_at(),
    )
    state["resolutions"] = 0

    def resolve(generation: dict, *, checkpoint: Callable[[], None]) -> Callable[..., dict]:
        checkpoint()
        assert generation["scope"] == selected["scope"]
        # A real same-owner transaction here would refuse if policy resolution
        # were incorrectly performed inside the selection transaction.
        store.review_intent_versions(expires_at=native.expires_at(), checkpoint=checkpoint)
        state["resolutions"] += 1
        return prepare

    operations = native.operations
    operations.__init__(
        store, operations.cache, scope=operations.scope, read_owner=operations.read_owner,
        binding_ref=operations.binding_ref, write_context=operations.write_context,
        checkpoint=operations.checkpoint, max_work_seconds=operations.max_work_seconds,
        prepared_reviews=True, selection_preparer=resolve,
    )
    draft = copy.deepcopy(selected)
    draft["version"] = 1
    draft["actions"][0].pop("preparation")
    draft["actions"][0]["body"] = "Selected note."
    return draft, state


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("kind", ["comment", "reply", "vote"])
@pytest.mark.parametrize("lost", [False, True])
def test_committed_cli_preview_recovers_then_exact_submission_keeps_original_receipt(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    transport: str, kind: str, lost: bool,
) -> None:
    from agent_pull_requests.review_workspace import _digest, _record

    store, _, generation, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, state = selection_workspace
    if kind == "reply":
        thread = {"id": 50, "status": 1, "comments": [{
            "id": 1, "parentCommentId": 0, "commentType": 1,
            "content": "Parent note.", "author": {"id": draft["scope"]["principal_id"]},
        }]}
        backend.threads = [copy.deepcopy(thread)]
        old_id = generation["id"]
        generation["sections"]["discussions"]["data"] = [_record("discussions", thread)]
        generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
        draft["generation_id"] = attachment["generation_id"] = generation["id"]
        native.operations.cache.publish(generation, expected_current=old_id)
        draft["actions"][0].update(kind="reply", thread_id=50)
    if kind == "vote":
        draft["actions"] = draft["actions"][-1:]
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    original = copy.deepcopy(request)
    if lost:
        execute = native.operations.execute

        def drop(operation: str, payload: dict, **kwargs: object) -> dict:
            result = execute(operation, payload, **kwargs)
            if operation == "allocate":
                raise RuntimeError("synthetic lost committed preview")
            return result

        monkeypatch.setattr(native.operations, "execute", drop)
        code, _ = invoke_cli(host, "allocate", request, monkeypatch, capsys)
        assert code != 0
        monkeypatch.setattr(native.operations, "execute", execute)
    code, preview = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and preview["result"]["approval_required"] is True, preview
    chosen = preview["result"]["intent"]
    assert chosen["version"] == 2 and backend.calls == [] and request == original
    assert state["resolutions"] == 1
    retained = ReviewSelectionStore(store).recover(draft, expires_at=native.expires_at())
    assert retained["intent"] == chosen and retained["selection"]["stage"] == "prepared"
    if kind == "vote":
        assert state["results"] == [] and chosen["actions"] == draft["actions"]
    else:
        identity = chosen["actions"][0]["preparation"]["identity"]
        assert identity["comment_id"] != identity["write_id"]
        assert chosen["actions"][0]["body"] == state["results"][0]["body"]
    with pytest.raises(ValueError, match="not execution-admitted"):
        store.inspect_review(draft["operation_id"], draft["scope"], expires_at=native.expires_at())
    before = store.path.read_bytes()
    native.operations.store = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    state["profile"]["rules"][0]["header"] = "[Changed after preview] "
    native.operations.selection_preparer = lambda *a, **k: pytest.fail("recovered preview re-resolved policy")
    code, retry = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and retry == preview and store.path.read_bytes() == before
    changed = copy.deepcopy(chosen)
    changed["source_id"] = "9" * 64
    submission = {"number": 42, "intent": changed, "source_attachment": attachment, "budget_seconds": 60}
    code, _ = invoke_cli(host, "submit", submission, monkeypatch, capsys)
    assert code != 0 and backend.calls == [] and store.path.read_bytes() == before
    submission["intent"] = draft
    code, _ = invoke_cli(host, "submit", submission, monkeypatch, capsys)
    assert code != 0 and backend.calls == [] and store.path.read_bytes() == before
    submission["intent"] = chosen
    code, result = invoke_cli(host, "submit", submission, monkeypatch, capsys)
    assert code == 0 and result["result"]["status"] == "complete", result
    writes = len([call for call in backend.calls if call[0] != "GET"])
    code, result = invoke_cli(host, "submit", submission, monkeypatch, capsys)
    assert code == 0 and result["result"]["entered_writes"] == 0, result
    assert len([call for call in backend.calls if call[0] != "GET"]) == writes
    if kind != "vote":
        code, receipt = invoke_cli(host, "receipt", {
            "number": 42, "operation_id": draft["operation_id"], "action_id": chosen["actions"][0]["id"],
        }, monkeypatch, capsys)
        assert code == 0 and receipt["result"]["identity"] == identity, receipt
        assert receipt["result"]["action_state"] == "confirmed"
    code, after_admission = invoke_cli(host, "allocate", request, monkeypatch, capsys)
    assert code == 0 and after_admission == preview
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("change", ["body", "revision", "source", "scope", "target", "caller_policy", "edit", "budget"])
def test_changed_selection_and_unsupported_edits_refuse_before_native_effects(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, change: str,
) -> None:
    store, _, _, _, backend, _ = environment
    gateway, _, native, _, _, _ = prepared_workspace
    draft, state = selection_workspace
    request = {"number": 42, "intent": draft, "source_attachment": environment[3], "budget_seconds": 60}

    async def scenario() -> None:
        host = WorkspaceTools(native)
        await host.invoke("allocate", request)
        altered = copy.deepcopy(request)
        if change == "body":
            altered["intent"]["actions"][0]["body"] += " changed"
        elif change == "revision":
            altered["intent"]["revision"]["iteration"] += 1
        elif change == "source":
            altered["source_attachment"]["source"]["complete"] = False
        elif change == "scope":
            altered["intent"]["scope"]["principal_id"] = "other"
        elif change == "target":
            altered["intent"]["actions"][0].update(kind="reply", thread_id=50)
        elif change == "caller_policy":
            altered["profile"] = {}
        elif change == "edit":
            altered["intent"]["operation_id"] += "-edit"
            altered["intent"]["actions"][0].update(
                kind="edit_owned_comment", thread_id=50, comment_id=1, expected_content_id="a" * 64,
            )
        else:
            altered["budget_seconds"] = float("inf")
        before = store.path.read_bytes()
        with pytest.raises(ValueError):
            await host.invoke("allocate", altered)
        assert store.path.read_bytes() == before and backend.calls == []
        assert len(state["results"]) == 1
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_native_same_key_contention_reconciles_one_preview_without_another_allocation(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple,
) -> None:
    store, _, _, _, backend, _ = environment
    _, _, native, _, _, _ = prepared_workspace
    draft, _ = selection_workspace
    native.exchange("capabilities", {})
    request = {"number": 42, "intent": draft, "source_attachment": environment[3], "budget_seconds": 60}

    def allocate(_: int) -> dict | None:
        try:
            return native.exchange("allocate", request)
        except OperationLockedError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(allocate, range(2)))
    committed = [result for result in results if result is not None]
    assert committed and all(result == committed[0] for result in committed)
    assert native.exchange("allocate", request) == committed[0]
    assert len(json.loads(store.path.read_bytes())["reviews"]) == 1 and backend.calls == []


@pytest.mark.parametrize("version", [1, 2])
def test_upgraded_receiver_keeps_explicit_submission_without_allocation(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, version: int,
) -> None:
    store, selected, _, attachment, _, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, state = selection_workspace

    async def scenario() -> None:
        await client.capabilities()
        chosen = draft if version == 1 else selected
        result = await client.exchange("submit", {
            "number": 42, "intent": chosen, "source_attachment": attachment, "budget_seconds": 60,
        })
        assert result["result"]["status"] == "complete", result
        retained = store.inspect_review(chosen["operation_id"], chosen["scope"], expires_at=native.expires_at())
        assert retained["intent"] == chosen and "selection" not in retained
        assert state["resolutions"] == 0 and state["results"] == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("malformed", ["capability", "response", "rollback"])
def test_gateway_negotiates_and_validates_actual_selection_contract(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple,
    monkeypatch: pytest.MonkeyPatch, malformed: str,
) -> None:
    from agent_pull_requests.workspace_client import WorkspaceClientError

    store, _, _, _, backend, _ = environment
    gateway, client, native, _, caller, _ = prepared_workspace
    draft, state = selection_workspace
    exchange = client._exchange

    async def corrupt(operation: str, payload: dict, **kwargs: object) -> dict:
        result = await exchange(operation, payload, **kwargs)
        if malformed == "capability" and operation == "capabilities":
            result["selection_contract"]["owned_edits"] = 0
        elif malformed == "response" and operation == "allocate":
            result["result"]["intent"]["source_id"] = "9" * 64
        return result

    monkeypatch.setattr(client, "_exchange", corrupt)

    async def scenario() -> None:
        host = WorkspaceTools(client)
        capabilities = await host.capabilities()
        request = {"number": 42, "intent": draft, "source_attachment": environment[3], "budget_seconds": 60}
        before = store.path.read_bytes()
        if malformed == "capability":
            assert "allocate" not in capabilities["operations"]
            with pytest.raises(WorkspaceToolUnavailable):
                await host.invoke("allocate", request)
            assert not any(path.endswith("/allocate") for path in caller.calls)
        else:
            assert "allocate" in capabilities["operations"]
            definitions = await host.definitions()
            assert any(row["name"] == "pr_workspace_allocate" for row in definitions)
            if malformed == "rollback":
                native.operations.prepared_reviews = False
            with pytest.raises(WorkspaceClientError):
                await host.invoke("allocate", request)
        if malformed == "response":
            monkeypatch.setattr(client, "_exchange", exchange)
            result = await host.invoke("allocate", request)
            retained = ReviewSelectionStore(store).recover(draft, expires_at=native.expires_at())
            assert retained["intent"] == result["result"]["intent"]
            assert state["resolutions"] == 1
        else:
            assert store.path.read_bytes() == before and state["results"] == []
        assert backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["binding", "mode", "storage"])
def test_optional_receiver_support_and_rollback_preserve_old_paths(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, missing: str,
) -> None:
    store, _, _, _, backend, _ = environment
    gateway, client, native, _, caller, _ = prepared_workspace
    draft, state = selection_workspace
    if missing == "binding":
        native.operations.selection_preparer = None
    elif missing == "mode":
        native.operations.prepared_reviews = False
    else:
        document = json.loads(store.path.read_bytes())
        document.pop("review_selection_version")
        store.path.write_text(json.dumps(document), encoding="utf-8")
    before = store.path.read_bytes()

    async def scenario() -> None:
        host = WorkspaceTools(client)
        available = await host.capabilities()
        assert "allocate" not in available["operations"] and "submit" in available["operations"]
        calls = len(caller.calls)
        with pytest.raises(WorkspaceToolUnavailable):
            await host.invoke("allocate", {
                "number": 42, "intent": draft, "source_attachment": environment[3], "budget_seconds": 60,
            })
        assert not any(path.endswith("/allocate") for path in caller.calls[calls:])
        native.exchange("capabilities", {})
        with pytest.raises(RuntimeError, match="capability required"):
            native.exchange("allocate", {
                "number": 42, "intent": draft, "source_attachment": environment[3], "budget_seconds": 60,
            })
        assert state["results"] == [] and backend.calls == [] and store.path.read_bytes() == before
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("limited", ["preview", "submission"])
def test_gateway_rejects_oversized_final_envelopes_before_commit_and_recovers_same_key(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, comment_preparer: tuple,
    limited: str,
) -> None:
    from fleet_manager.gateway_service_channel import BODY_BYTES
    from agent_pull_requests.workspace_client import WorkspaceClientError
    from agent_pull_requests.workspace_operations import PROTOCOL, encoded
    from agent_pull_requests.workspace_selection import selection_contract

    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, binding = prepared_workspace
    draft, state = selection_workspace
    prepare, _ = comment_preparer
    state["profile"]["rules"][0].update(header="H" * 500, footer="F" * 500)
    probe = copy.deepcopy(draft)
    probe["version"] = 2
    probe["actions"][0]["body"] = "x"
    composed = prepare(
        "comment", "x", scope=draft["scope"], previous=None,
        comment_id="11111111-1111-4111-8111-111111111111",
        write_id="22222222-2222-4222-8222-222222222222",
    )
    probe["actions"][0].update(
        body=composed["body"], preparation={
            "identity": composed["identity"], "body_id": composed["body_id"],
        },
    )
    state["results"].clear()
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    if limited == "preview":
        framed = json.dumps({
            "binding": binding.document(), "protocol": PROTOCOL, "scope": draft["scope"],
            "result": {
                "protocol": selection_contract()["protocol"], "intent": probe,
                "effects": "none", "approval_required": True,
            },
        }, allow_nan=False).encode("utf-8")
    else:
        framed = encoded({"binding": binding.document(), "request": {**request, "intent": probe}})
    draft["actions"][0]["body"] = "x" * (BODY_BYTES - len(framed) + 2)
    assert len(encoded({"binding": binding.document(), "request": request})) <= BODY_BYTES
    before = store.path.read_bytes()

    async def scenario() -> None:
        host = WorkspaceTools(client)
        with pytest.raises(WorkspaceClientError):
            await host.invoke("allocate", request)
        assert store.path.read_bytes() == before and backend.calls == []
        assert ReviewSelectionStore(store).recover(draft, expires_at=native.expires_at()) is None
        state["profile"]["rules"][0].update(header="[Selected AI] ", footer="")
        preview = await host.invoke("allocate", request)
        retained = ReviewSelectionStore(store).recover(draft, expires_at=native.expires_at())
        assert retained["intent"] == preview["result"]["intent"]
        assert await host.invoke("allocate", request) == preview
        assert backend.calls == []
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
