"""Original active-operation recovery over the real store and protected reader."""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest

from agent_pull_requests.review_recovery import reconcile_review
from agent_pull_requests.terminal_retention import TerminalAdmissionClosed
from agent_pull_requests.watch_replay_store import ReplayCallerExpired, ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain
from test_terminal_recovery import compact, expires, matching_thread, prepare
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_terminal_retention import (
    environment as environment, integration as integration, workspace as workspace,
    terminal,
)

__all__ = ["environment", "integration", "workspace", "prepared_intent"]
pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)
Document = dict[str, Any]
Fixture = tuple[Any, ...]


def recover(prepared: Fixture, **kwargs: Any) -> Document:
    operations, _, intent, _, _, _, backend = prepared
    return reconcile_review(
        operations.store, intent["scope"], intent["operation_id"],
        context_factory=kwargs.pop("context_factory", backend.context),
        checkpoint=backend.checkpoint, expires_at=kwargs.pop("expires_at", expires()),
        budget_seconds=kwargs.pop("budget_seconds", 10), **kwargs,
    )


def no_send(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("recovery must not enter execution admission or a sender")


@pytest.mark.parametrize("reopen", [False, True])
def test_original_terminal_inspection_and_readback_preserve_attempt_without_execution(
    workspace: Fixture, monkeypatch: pytest.MonkeyPatch, reopen: bool,
) -> None:
    prepared = prepare(workspace)
    operations, _, intent, admitted, _, _, backend = prepared
    owner = operations.store
    before = json.loads(owner.path.read_bytes())
    original = copy.deepcopy(before["reviews"][0])
    backend.threads = [matching_thread(intent, admitted)]
    if reopen:
        generation = operations.cache.lookup(intent["scope"])["generation"]
        terminal(operations, generation, state="active", second=30)
    before_recovery = json.loads(owner.path.read_bytes())
    fresh = ReplayStore(
        owner.root, owner.namespace, service_identity=owner.service_identity,
        terminal_retention=True, terminal_recovery=True,
    )
    assert fresh.inspect_retained_review(
        intent["operation_id"], intent["scope"], expires_at=expires(),
    ) == original
    with pytest.raises(TerminalAdmissionClosed):
        fresh.inspect_review(intent["operation_id"], intent["scope"], expires_at=expires())
    monkeypatch.setattr(owner, "admit_review", no_send)
    monkeypatch.setattr(owner, "begin_review_action", no_send)
    from agent_worktrees.providers.azure_owned_review import AdoReviewContext
    monkeypatch.setattr(AdoReviewContext, "perform", no_send)
    calls = len(backend.calls)
    result = recover(prepared)
    assert result["effects"] == "none" and result["entered_writes"] == 0
    assert result["status"] == "retained" and result["errors"] == []
    assert [row["state"] for row in result["actions"]] == ["confirmed", "unattempted"]
    assert all(call[0] == "GET" for call in backend.calls[calls:])
    after = json.loads(owner.path.read_bytes())
    recovered = after["reviews"][0]
    assert recovered["actions"][0]["attempt_id"] == original["actions"][0]["attempt_id"]
    assert recovered["actions"][0]["receipt"]["origin"] == "attributable_readback"
    assert recovered["actions"][0]["receipt"]["platform_ids"]["thread_id"] == 100
    assert recovered["actions"][1] == original["actions"][1]
    assert {key: value for key, value in recovered.items() if key != "actions"} == {
        key: value for key, value in original.items() if key != "actions"
    }
    assert {key: value for key, value in after.items() if key != "reviews"} == {
        key: value for key, value in before_recovery.items() if key != "reviews"
    }
    calls = len(backend.calls)
    retained = owner.path.read_bytes()
    assert recover(prepared)["actions"] == result["actions"]
    assert owner.path.read_bytes() == retained and len(backend.calls) == calls


@pytest.mark.parametrize("control", ["wrong_marker", "current_text", "duplicate", "wrong_actor", "changed_body"])
def test_unattributable_native_state_remains_unknown_without_changing_original_bytes(
    workspace: Fixture, control: str,
) -> None:
    prepared = prepare(workspace)
    operations, _, intent, admitted, _, _, backend = prepared
    thread = matching_thread(intent, admitted)
    if control == "current_text":
        thread["properties"] = {}
    elif control == "wrong_marker":
        thread["properties"]["agent-pull-requests.action"]["$value"] = "different-action"
    elif control == "wrong_actor":
        thread["comments"][0]["author"]["id"] = "different-principal"
    elif control == "changed_body":
        thread["comments"][0]["content"] = "Changed native content"
    backend.threads = [thread]
    if control == "duplicate":
        other = copy.deepcopy(thread)
        other["id"] = 101
        backend.threads.append(other)
    before = operations.store.path.read_bytes()
    calls = len(backend.calls)
    result = recover(prepared)
    assert result["status"] == "unknown"
    assert operations.store.path.read_bytes() == before
    assert all(call[0] == "GET" for call in backend.calls[calls:])


@pytest.mark.parametrize("kind", ["verdict", "reply", "edit_owned_comment"])
def test_unsupported_original_actions_keep_unknown_identity(workspace: Fixture, kind: str) -> None:
    selected = copy.deepcopy(workspace[3])
    action: Document = {"id": "unsupported", "kind": kind}
    if kind == "verdict":
        action["verdict"] = "APPROVED"
    else:
        action.update(body="Selected native content", thread_id=10)
        if kind == "edit_owned_comment":
            action.update(comment_id=1, expected_content_id="b" * 64)
    selected["actions"] = [action]
    prepared = prepare(workspace, selected=selected)
    owner, backend = prepared[0].store, prepared[-1]
    before = owner.path.read_bytes()
    calls = len(backend.calls)
    result = recover(prepared)
    assert result["status"] == "unknown" and result["errors"] == []
    assert owner.path.read_bytes() == before
    assert all(call[0] == "GET" for call in backend.calls[calls:])


def test_wrong_scope_and_compact_representation_refuse_before_native_io(
    workspace: Fixture,
) -> None:
    prepared = prepare(workspace)
    operations, _, intent, _, _, _, backend = prepared
    scope = {**intent["scope"], "principal_id": "different-principal"}
    calls = len(backend.calls)
    before = operations.store.path.read_bytes()
    with pytest.raises(ValueError, match="scope"):
        reconcile_review(
            operations.store, scope, intent["operation_id"], context_factory=backend.context,
            checkpoint=backend.checkpoint, expires_at=expires(), budget_seconds=10,
        )
    assert operations.store.path.read_bytes() == before
    assert len(backend.calls) == calls
    compact(prepared)
    before = operations.store.path.read_bytes()
    with pytest.raises(ValueError, match="missing"):
        recover(prepared)
    assert operations.store.path.read_bytes() == before
    assert len(backend.calls) == calls


def test_prepared_operation_retains_its_original_reader(
    workspace: Fixture, prepared_intent: Document,
) -> None:
    prepared = prepare(workspace, selected=prepared_intent)
    owner, backend = prepared[0].store, prepared[-1]
    before, calls = owner.path.read_bytes(), len(backend.calls)
    with pytest.raises(ReplayStoreInvalid, match="original v1"):
        recover(prepared)
    assert owner.path.read_bytes() == before and len(backend.calls) == calls


def test_native_identity_conflict_preserves_original_partial_receipt(workspace: Fixture) -> None:
    prepared = prepare(workspace)
    operations, _, intent, admitted, _, _, backend = prepared
    owner = operations.store
    original = owner.inspect_retained_review(intent["operation_id"], intent["scope"], expires_at=expires())
    owner.record_review_receipt(
        intent["operation_id"], intent["scope"], original["actions"][0]["id"],
        original["actions"][0]["attempt_id"],
        {"origin": "response", "platform_ids": {"thread_id": 999}, "proof": None},
        expires_at=expires(), confirm=False,
    )
    backend.threads = [matching_thread(intent, admitted)]
    before = owner.path.read_bytes()
    result = recover(prepared)
    assert result["status"] == "unknown" and result["errors"]
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("control", [
    "authority", "context", "scope", "binding", "receipt_binding", "expiry", "budget", "receipt_origin",
])
def test_current_authority_and_invocation_budget_cannot_be_substituted(
    workspace: Fixture, monkeypatch: pytest.MonkeyPatch, control: str,
) -> None:
    prepared = prepare(workspace)
    operations, _, intent, admitted, _, _, backend = prepared
    backend.threads = [matching_thread(intent, admitted)]
    before = operations.store.path.read_bytes()
    factory = backend.context
    kwargs: Document = {}
    if control == "authority":
        backend.denied = PermissionError("original authority withdrawn")
    elif control in ("context", "scope"):
        @contextmanager
        def factory() -> Iterator[Any]:
            if control == "context":
                yield object()
            else:
                with backend.context() as context:
                    context._scope["principal_id"] = "different-principal"
                    yield context
    elif control == "expiry":
        kwargs["expires_at"] = 0
    elif control == "budget":
        ticks = iter([0.0, 0.0, 20.0])
        kwargs["clock"] = lambda: next(ticks, 20.0)
    elif control == "receipt_binding":
        validate = operations.store._validate_owner

        def validate_receipt(document: Document) -> None:
            validate(document)
            if document["reviews"][0]["actions"][0]["state"] == "confirmed":
                backend.credential = object()

        monkeypatch.setattr(operations.store, "_validate_owner", validate_receipt)
    elif control in ("binding", "receipt_origin"):
        from agent_worktrees.providers.azure_owned_review import AdoReviewContext
        original_reconcile = cast(
            Callable[[Any, Document, Document], Document | None], AdoReviewContext.reconcile,
        )

        def reconcile(context: Any, operation: Document, action: Document) -> Document | None:
            proof = original_reconcile(context, operation, action)
            if control == "binding":
                backend.credential = object()
            elif proof is not None:
                proof.update(origin="response", proof=None)
            return proof

        monkeypatch.setattr(AdoReviewContext, "reconcile", reconcile)
    if control in ("authority", "expiry", "budget"):
        expected = PermissionError if control == "authority" else (
            ReplayCallerExpired if control == "expiry" else TimeoutError
        )
        with pytest.raises(expected):
            recover(prepared, context_factory=factory, **kwargs)
    else:
        result = recover(prepared, context_factory=factory, **kwargs)
        assert result["status"] == "unknown" and result["errors"]
    assert operations.store.path.read_bytes() == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_receipt_replace_readback_reuses_original_attempt(
    workspace: Fixture, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    from agent_pull_requests import watch_replay_store as storage

    prepared = prepare(workspace)
    operations, _, intent, admitted, _, _, backend = prepared
    backend.threads = [matching_thread(intent, admitted)]
    owner = operations.store
    original = json.loads(owner.path.read_bytes())["reviews"][0]
    write = storage._atomic_write_json

    def interrupt(*args: Any, **kwargs: Any) -> None:
        if uncertain:
            write(*args, **kwargs)
            raise StateCommitUncertain("synthetic original receipt replace unknown")
        raise OSError("synthetic before original receipt replace")

    monkeypatch.setattr(storage, "_atomic_write_json", interrupt)
    first = recover(prepared)
    assert first["status"] == ("retained" if uncertain else "unknown")
    assert first["errors"] == ["original_protected_recovery_unavailable"]
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    result = recover(prepared)
    assert [row["state"] for row in result["actions"]] == ["confirmed", "unattempted"]
    assert result["actions"][0]["attempt_id"] == original["actions"][0]["attempt_id"]
    assert json.loads(owner.path.read_bytes())["reviews"][0]["intent"] == intent


@pytest.mark.parametrize("stage", ["initial", "final"])
def test_original_deadline_bounds_both_actual_inspection_lock_waits(
    workspace: Fixture, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    prepared = prepare(workspace, attempt=stage == "final")
    owner: ReplayStore = prepared[0].store
    before = owner.path.read_bytes()
    errors: list[Exception] = []
    results: list[Document] = []
    final_ready, proceed = threading.Event(), threading.Event()
    inspect = owner.inspect_retained_review
    calls = 0

    def inspect_original(*args: Any, **kwargs: Any) -> Document:
        nonlocal calls
        calls += 1
        if stage == "final" and calls == 2:
            final_ready.set()
            assert proceed.wait(3), "final lock fixture was not admitted"
        return inspect(*args, **kwargs)

    def run() -> None:
        try:
            results.append(recover(prepared, budget_seconds=0.25))
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(owner, "inspect_retained_review", inspect_original)
    worker = threading.Thread(target=run)
    try:
        if stage == "initial":
            with owner._lock():
                worker.start()
                worker.join(3)
                assert not worker.is_alive(), "initial inspection outlived its original budget"
        else:
            worker.start()
            assert final_ready.wait(3), "recovery did not reach final readback"
            with owner._lock():
                proceed.set()
                worker.join(3)
                assert not worker.is_alive(), "final inspection outlived its original budget"
    finally:
        proceed.set()
        worker.join(3)
    assert not results and len(errors) == 1
    assert isinstance(errors[0], TimeoutError) and "budget exhausted" in str(errors[0])
    assert owner.path.read_bytes() == before
