"""Actual workspace admission/drain races, with isolated storage and synthetic IO."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest
from single_instance_lease import OperationLockedError

from test_workspace_integration import environment as environment, integration as integration
from test_review_workspace import section as evidence_section

from agent_pull_requests.attention import Cursor
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.review_workspace import EVIDENCE_SCHEMA, build_generation
from agent_pull_requests.terminal_retention import TerminalAdmissionClosed, TerminalRetentionStore
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests.watch_replay_store import ReplayStore

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def workspace(environment, integration):
    owner, intent, initial, attachment, backend, _ = environment
    _, _, native, evidence, _, _ = integration
    operations = native.operations
    for name in ("reviewers", "policies"):
        evidence["sections"][name] = evidence_section([])
    # This fixture has no running host or old reader; select the capable reader
    # before explicit storage adoption, retaining the synthetic sender's owner.
    owner.terminal_retention = True
    expires = int(time.time()) + 600
    ConversationStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    TerminalRetentionStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    operations.conversations = True
    operations.retention_target = lambda scope: ObservationTarget(
        "account-a", "example/project", scope["number"], frozenset(),
    )
    return operations, evidence, backend, intent, initial, attachment


def execute(workspace, operation, payload):
    return workspace.execute(
        operation, payload, expires_at=int(time.time()) + 600, checkpoint=lambda: None,
    )["result"]


def load(workspace, expected):
    return execute(workspace, "load", {
        "number": 42, "expected_current": expected, "budget_seconds": 10,
    })


def terminal(workspace, current, *, state="completed", second=20):
    evidence = {
        "schema": EVIDENCE_SCHEMA, "version": 1,
        "scope": copy.deepcopy(current["scope"]), "sections": copy.deepcopy(current["sections"]),
    }
    evidence["sections"]["pull_request"]["data"]["status"] = state
    for section in evidence["sections"].values():
        section.update(
            observed_from=f"2026-10-30T00:00:{second:02d}Z",
            observed_to=f"2026-10-30T00:00:{second + 1:02d}Z",
        )
    value = build_generation(evidence)["generation"]
    result = ConversationStore(workspace.store).observe_workspace(
        value, expires_at=int(time.time()) + 600,
        terminal_target=workspace.retention_target(value["scope"]),
    )
    return result, value


def drain(workspace, scope, cursor, deadline):
    return workspace.retention.drain(
        scope, Cursor(**cursor), expires_at=int(time.time()) + 600,
        checkpoint=lambda: None, lifecycle_checkpoint=lambda: None, deadline=deadline,
    )


def test_real_load_stops_terminal_publication_and_reopen_rejects_old_buffers(workspace):
    operations, evidence, backend, _, initial, _ = workspace
    first = load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    assert first["descriptor"]["id"] == current["id"]
    execute(operations, "lookup", {"number": 42, "generation_id": current["id"]})
    retained_buffers = copy.deepcopy(operations._materializations)
    backend.pr["status"] = "completed"
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:00:20Z", observed_to="2026-10-30T00:00:21Z")
    stopped = load(operations, current["id"])
    assert stopped["publication"]["status"] == "retired" and stopped["descriptor"] is None
    assert operations.cache.lookup(initial["scope"])["generation"] == current
    for operation, payload in (
        ("lookup", {"number": 42}),
        ("chunk", {"number": 42, "generation_id": current["id"], "offset": 0}),
    ):
        with pytest.raises(TerminalAdmissionClosed):
            execute(operations, operation, payload)
    drained = drain(operations, current["scope"], stopped["conversation"]["cursor"], time.monotonic() + 1)
    assert drained["drain"] == "drained"
    # T1 fences/drains, not T2's physical compactor.
    assert operations._materializations == retained_buffers
    backend.pr["status"] = "active"
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:00:30Z", observed_to="2026-10-30T00:00:31Z")
    reopened = load(operations, current["id"])
    assert reopened["conversation"]["cursor"] == {"epoch": 2, "sequence": 1}
    assert reopened["publication"]["status"] == "published"
    with pytest.raises(TerminalAdmissionClosed):
        execute(operations, "chunk", {"number": 42, "generation_id": current["id"], "offset": 0})
    with pytest.raises(TerminalAdmissionClosed):
        drain(operations, current["scope"], stopped["conversation"]["cursor"], time.monotonic() + 1)


def test_already_admitted_acquisition_must_join_before_drain_and_cannot_republish(
    workspace, monkeypatch,
):
    operations, _, _, _, initial, _ = workspace
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    entered, release = threading.Event(), threading.Event()
    read = operations.read_owner.read

    def paused(*args, **kwargs):
        value = read(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(operations.read_owner, "read", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(load, operations, current["id"])
        try:
            assert entered.wait(5)
            result, _ = terminal(operations, current)
            with pytest.raises(TimeoutError, match="incomplete"):
                drain(operations, current["scope"], result["cursor"], time.monotonic() + 0.02)
            retained = TerminalRetentionStore(operations.store).inspect(
                current["scope"], Cursor(**result["cursor"]), expires_at=int(time.time()) + 600,
            )
            assert retained["drain"] == "pending" and not future.done()
        finally:
            release.set()
        assert future.result(timeout=5)["publication"]["status"] == "not_published"
    assert operations.cache.lookup(current["scope"])["generation"] == current
    assert drain(operations, current["scope"], result["cursor"], time.monotonic() + 1)["drain"] == "drained"


@pytest.mark.parametrize("boundary", ["cache", "materialization"])
def test_replay_fence_serializes_actual_cache_and_materialization_boundaries(
    workspace, monkeypatch, boundary,
):
    operations, _, _, _, initial, _ = workspace
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    entered, release = threading.Event(), threading.Event()
    original = GenerationCache.publish if boundary == "cache" else operations._materialized

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    if boundary == "cache":
        monkeypatch.setattr(GenerationCache, "publish", paused)
        operation = lambda: load(operations, current["id"])
    else:
        monkeypatch.setattr(operations, "_materialized", paused)
        operation = lambda: execute(operations, "lookup", {"number": 42})

    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(operation)
        try:
            assert entered.wait(5)
            before = operations.store.path.read_bytes()
            with pytest.raises(OperationLockedError):
                terminal(operations, current)
            assert operations.store.path.read_bytes() == before
        finally:
            release.set()
        reader.result(timeout=5)
        result, _ = terminal(operations, current)
    assert drain(operations, current["scope"], result["cursor"], time.monotonic() + 1)["drain"] == "drained"
    with pytest.raises(TerminalAdmissionClosed):
        execute(operations, "lookup", {"number": 42})
    assert len(json.loads(operations.store.path.read_bytes())["terminal_retention"]["records"]) == 1


def test_admitted_native_sender_is_joined_and_retains_receipt_without_starting_next_action(workspace):
    operations, _, backend, intent, initial, attachment = workspace
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    intent = {**intent, "generation_id": current["id"]}
    attachment = {**attachment, "generation_id": current["id"]}
    entered, release = threading.Event(), threading.Event()

    def after_write(_backend):
        entered.set()
        assert release.wait(5)

    backend.after_write = after_write
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, operations, "submit", {
            "number": 42, "intent": intent, "source_attachment": attachment, "budget_seconds": 10,
        })
        try:
            assert entered.wait(5)
            result, _ = terminal(operations, current)
            with pytest.raises(TimeoutError):
                drain(operations, current["scope"], result["cursor"], time.monotonic() + 0.02)
        finally:
            release.set()
        with pytest.raises(TerminalAdmissionClosed):
            future.result(timeout=5)
    operation = json.loads(operations.store.path.read_bytes())["reviews"][0]
    assert operation["actions"][0]["state"] == "confirmed"
    assert operation["actions"][0]["receipt"]["platform_ids"]
    assert operation["actions"][1]["state"] == "unattempted"
    assert len(backend.threads) == 1 and backend.vote is None
    assert drain(operations, current["scope"], result["cursor"], time.monotonic() + 1)["drain"] == "drained"


def test_partial_refresh_preserves_authoritative_live_reads_and_sync_reacquisition(workspace):
    operations, evidence, _, _, initial, _ = workspace
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    evidence["sections"]["reviewers"]["complete"] = False
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:00:20Z", observed_to="2026-10-30T00:00:21Z")
    partial = load(operations, current["id"])
    assert partial["publication"]["status"] == "not_published"
    assert execute(operations, "lookup", {"number": 42})["descriptor"]["id"] == current["id"]
    assert execute(operations, "chunk", {
        "number": 42, "generation_id": current["id"], "offset": 0,
    })["data"]
    request = {
        "number": 42, "expected_current": current["id"],
        "previous_id": current["id"], "budget_seconds": 10,
    }
    assert execute(operations, "sync", request)["publication"]["status"] == "not_published"
    evidence["sections"]["reviewers"]["complete"] = True
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:00:30Z", observed_to="2026-10-30T00:00:31Z")
    complete = execute(operations, "sync", request)
    assert complete["publication"]["status"] == "published"
    assert complete["conversation"]["cursor"]["epoch"] == 1


@pytest.mark.parametrize("status", ["full", "conflict", "failed", "unconfirmed"])
def test_failed_publications_do_not_consume_generation_binding_capacity(
    workspace, monkeypatch, status,
):
    operations, evidence, _, _, initial, _ = workspace
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    before = json.loads(operations.store.path.read_bytes())["terminal_retention"]["generations"]
    monkeypatch.setattr(GenerationCache, "publish", lambda *a, **k: {"status": status})
    for second in (10, 20, 30):
        for section in evidence["sections"].values():
            section.update(
                observed_from=f"2026-10-30T00:00:{second:02d}Z",
                observed_to=f"2026-10-30T00:00:{second + 1:02d}Z",
            )
        assert load(operations, current["id"])["publication"]["status"] == status
    retained = json.loads(operations.store.path.read_bytes())["terminal_retention"]["generations"]
    if status == "unconfirmed":
        assert len(retained) == len(before) + 3
    else:
        assert retained == before


@pytest.mark.parametrize("failure", ["after_publication", "owner_write"])
def test_cache_pointer_never_precedes_its_durable_epoch_binding(workspace, monkeypatch, failure):
    from agent_pull_requests import watch_replay_store as storage

    operations, evidence, _, _, initial, _ = workspace
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:00:20Z", observed_to="2026-10-30T00:00:21Z")
    published = False
    publish, write = GenerationCache.publish, storage._atomic_write_json

    def interrupted(cache, generation, **kwargs):
        nonlocal published
        result = publish(cache, generation, **kwargs)
        assert result["status"] == "published"
        published = True
        if failure == "after_publication":
            raise OSError("synthetic stop immediately after durable cache pointer")
        return result

    def fail_owner_write(path, document):
        if published:
            raise OSError("synthetic later owner-file write failure")
        write(path, document)

    with monkeypatch.context() as patch:
        patch.setattr(GenerationCache, "publish", interrupted)
        patch.setattr(storage, "_atomic_write_json", fail_owner_write)
        with pytest.raises(OSError):
            load(operations, current["id"])
    surviving = operations.cache.lookup(initial["scope"])["generation"]
    assert surviving["id"] != current["id"]
    owner = operations.store
    operations.store = ReplayStore(
        owner.root, owner.namespace, service_identity=owner.service_identity, terminal_retention=True,
    )
    assert execute(operations, "lookup", {"number": 42})["descriptor"]["id"] == surviving["id"]
    assert execute(operations, "chunk", {
        "number": 42, "generation_id": surviving["id"], "offset": 0,
    })["data"]
