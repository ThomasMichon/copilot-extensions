"""Request-local protected factories; real owner/executor and synthetic native IO."""

from contextlib import contextmanager
import copy
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from test_review_executor import environment as environment, writes
from test_review_feedback import feedback_environment as feedback_environment
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_integration import (
    integration as integration, prepared_request, prepared_workspace as prepared_workspace,
)
from test_workspace_selection import selection_workspace as selection_workspace

from agent_pull_requests.review_feedback import FeedbackInputs
from agent_pull_requests.workspace_operations import WorkspaceOperations

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def select(operations, backend, *, before=None):
    acquisitions, contexts = [], []

    def resolve(scope, *, checkpoint, deadline):
        checkpoint()
        assert time.monotonic() < deadline <= time.monotonic() + 120
        material = backend.credential
        acquisitions.append((scope, material))
        if before is not None:
            before()

        @contextmanager
        def context():
            checkpoint()
            with backend.context() as native:
                # This synthetic owner pins exactly what this resolver acquired.
                native.credential_binding = lambda: material
                native.pinned = material
                original_check = native.checkpoint

                def check():
                    checkpoint()
                    original_check()

                native.checkpoint = check
                contexts.append(material)
                yield native

        return context

    operations.write_context = None
    operations.write_context_factory = resolve
    return acquisitions, contexts


def execute(operations, payload):
    return operations.execute(
        "submit", payload, expires_at=int(time.time()) + 600, checkpoint=lambda: None,
    )["result"]


def test_one_resolution_covers_multiple_actions_and_storage_only_replay(environment, integration):
    _, selected, _, _, backend, _ = environment
    operations = integration[2].operations
    acquired, contexts = select(operations, backend)
    payload = prepared_request(environment)
    assert "submit" in operations.execute(
        "capabilities", {}, expires_at=int(time.time()) + 600, checkpoint=lambda: None,
    )["operations"]
    assert acquired == []
    assert execute(operations, payload)["status"] == "complete"
    assert len(acquired) == 1 and len(contexts) == 2
    assert all(value is acquired[0][1] for value in contexts)
    before = len(backend.calls)
    assert execute(operations, payload)["status"] == "complete"
    operations.execute(
        "inspect", {"number": 42, "operation_id": selected["operation_id"]},
        expires_at=int(time.time()) + 600, checkpoint=lambda: None,
    )
    assert len(acquired) == 1 and len(backend.calls) == before


def test_preparation_and_original_receipts_never_resolve_the_writer(
    environment, prepared_workspace, selection_workspace,
):
    _, _, _, attachment, backend, _ = environment
    native = prepared_workspace[2]
    original, _ = selection_workspace
    acquired, _ = select(native.operations, backend)
    assert "allocate" in native.exchange("capabilities", {})["operations"]
    request = {
        "number": 42, "intent": original, "source_attachment": attachment, "budget_seconds": 60,
    }
    preview = native.exchange("allocate", request)
    assert native.exchange("allocate", request) == preview
    assert acquired == [] and backend.calls == []
    request["intent"] = preview["result"]["intent"]
    assert native.exchange("submit", request)["result"]["status"] == "complete"
    assert len(acquired) == 1
    native.exchange("receipt", {
        "number": 42, "operation_id": original["operation_id"], "action_id": "finding",
    })
    assert len(acquired) == 1


def test_feedback_uses_the_same_request_factory(feedback_environment, integration):
    _, selected, generation, attachment, backend, _, _, interests = feedback_environment
    operations = integration[2].operations
    prior = operations.cache.lookup(generation["scope"])["current_id"]
    operations.cache.publish(generation, expected_current=prior)
    operations.conversations = True
    operations.feedback_owner = lambda value: FeedbackInputs(value["id"], interests[:1], 60)
    operations._generation_positions[generation["id"]] = {
        "epoch": backend.positions[generation["id"]].epoch,
        "sequence": backend.positions[generation["id"]].sequence,
    }
    acquired, contexts = select(operations, backend)
    result = execute(operations, {
        "number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 60,
    })
    assert result["status"] == "complete" and result["feedback"]["status"] == "handled", result
    assert len(acquired) == 1 and len(contexts) >= 2
    assert all(value is acquired[0][1] for value in contexts)


@pytest.mark.parametrize("failure", ["rotation", "withdrawal"])
def test_changed_material_or_owner_never_reacquires(environment, integration, failure):
    _, _, _, _, backend, credential = environment
    operations = integration[2].operations
    acquired, _ = select(operations, backend)

    def change(_backend):
        if failure == "rotation":
            backend.credential = credential()
        else:
            operations.write_context_factory = None

    backend.after_write = change
    result = execute(operations, prepared_request(environment))
    assert result["status"] == "unknown"
    assert len(acquired) == 1 and len(writes(backend)) == 1 and backend.vote is None


def test_unknown_reconciliation_gets_one_new_read_factory_without_resend(environment, integration):
    _, selected, _, _, backend, _ = environment
    operations = integration[2].operations
    selected["actions"] = selected["actions"][:1]
    acquired, _ = select(operations, backend)
    backend.lose_reply = True
    payload = prepared_request(environment)
    assert execute(operations, payload)["status"] == "unknown"
    assert len(writes(backend)) == 1 and len(acquired) == 1
    backend.lose_reply = False
    backend.threads[0]["properties"] = {}
    assert execute(operations, payload)["status"] == "unknown"
    assert len(acquired) == 2 and len(writes(backend)) == 1


def test_concurrent_executions_do_not_share_request_resolution(environment, integration):
    _, _, _, _, backend, _ = environment
    operations = integration[2].operations
    entered, both = threading.Event(), threading.Barrier(2)

    def deny_after_overlap():
        entered.set()
        both.wait(timeout=5)
        raise PermissionError("synthetic concurrent owner refusal")

    acquired, _ = select(operations, backend, before=deny_after_overlap)
    payload = prepared_request(environment)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, operations, copy.deepcopy(payload))
        assert entered.wait(3)
        second = pool.submit(execute, operations, copy.deepcopy(payload))
        for result in (first, second):
            with pytest.raises(PermissionError, match="concurrent owner refusal"):
                result.result(timeout=6)
    assert len(acquired) == 2
    assert writes(backend) == []


@pytest.mark.parametrize("failure", ["none", "raises"])
def test_failed_resolution_is_not_retried_by_feedback(feedback_environment, integration, failure):
    _, selected, generation, attachment, backend, _, _, interests = feedback_environment
    operations = integration[2].operations
    prior = operations.cache.lookup(generation["scope"])["current_id"]
    operations.cache.publish(generation, expected_current=prior)
    operations.conversations = True
    operations.feedback_owner = lambda value: FeedbackInputs(value["id"], interests[:1], 60)
    operations._generation_positions[generation["id"]] = {
        "epoch": backend.positions[generation["id"]].epoch,
        "sequence": backend.positions[generation["id"]].sequence,
    }
    acquired = []

    def resolve(scope, *, checkpoint, deadline):
        acquired.append(scope)
        if failure == "raises":
            raise OSError("synthetic unavailable owner")
        return None

    operations.write_context = None
    operations.write_context_factory = resolve
    result = execute(operations, {
        "number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 60,
    })
    assert result["status"] == "unknown"
    assert len(acquired) == 1 and writes(backend) == []


def test_constructor_rejects_ambiguous_writers_and_accepts_explicit_factory(environment, integration):
    store = environment[0]
    existing = integration[2].operations
    fields = {
        "scope": existing.scope, "read_owner": existing.read_owner,
        "binding_ref": existing.binding_ref, "checkpoint": existing.checkpoint,
        "max_work_seconds": existing.max_work_seconds,
    }
    with pytest.raises(ValueError, match="actual workspace owner"):
        WorkspaceOperations(
            store, existing.cache, **fields, write_context=existing.write_context,
            write_context_factory=lambda *args, **kwargs: None,
        )
    selected = WorkspaceOperations(
        store, existing.cache, **fields, write_context_factory=lambda *args, **kwargs: None,
    )
    assert "submit" in selected.execute(
        "capabilities", {}, expires_at=int(time.time()) + 600, checkpoint=lambda: None,
    )["operations"]
