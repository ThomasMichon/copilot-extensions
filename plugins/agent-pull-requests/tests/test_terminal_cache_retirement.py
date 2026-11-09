"""Scoped retirement uses real private storage and the workspace's actual buffers."""

from __future__ import annotations

import copy
import json
import os
import time

import pytest

from test_terminal_recovery import prepare, compact, expires
from test_workspace_terminal_retention import (
    environment as environment, integration as integration, workspace as workspace, load,
)
from agent_pull_requests.attention import Cursor
from agent_pull_requests.generation_cache import CacheInvalid
from agent_pull_requests.review_workspace import build_generation, _digest
from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.terminal_retention import TerminalAdmissionClosed, TerminalRetentionStore
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def setup(workspace):
    prepared = prepare(workspace, materialize=True)
    operations, _, intent, _, decision, _, _ = prepared
    compact(prepared)
    operations.store.terminal_cache_retirement = True
    state = TerminalCacheRetirementStore(operations.store)
    state.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    return prepared, state


def retire(prepared, **kwargs):
    operations, _, intent, _, decision, _, _ = prepared
    return operations.retention.retire_cache(
        intent["scope"], Cursor(**decision["cursor"]), expires_at=expires(),
        checkpoint=lambda: None, lifecycle_checkpoint=kwargs.pop("lifecycle_checkpoint", lambda: None),
        deadline=kwargs.pop("deadline", time.monotonic() + 5), **kwargs,
    )


def test_actual_scoped_retirement_removes_buffers_but_preserves_replay_recovery_and_other_scope(workspace):
    prepared, state = setup(workspace)
    operations, _, intent, _, decision, _, _ = prepared
    scope = intent["scope"]
    # Actual retained bytes populated before terminal observation by prepare/load.
    assert any(key[0] == _digest(scope) for key in operations._materializations)
    current = operations.cache.lookup(scope)["generation"]
    other = copy.deepcopy(current)
    evidence = {name: other[name] for name in ("schema", "version", "scope", "sections")}
    evidence["schema"] = "agent-pull-requests.review-evidence"
    evidence["scope"]["number"] += 1
    evidence["sections"]["pull_request"]["data"]["pullRequestId"] = evidence["scope"]["number"]
    other = build_generation(evidence)["generation"]
    operations.cache.publish(other, expected_current=None)
    operations._materialized(operations.cache, other["scope"], other["id"])
    operations._generation_positions[current["id"]] = {"epoch": 1, "sequence": 1}
    operations._generation_positions[other["id"]] = {"epoch": 1, "sequence": 1}
    other_bytes = (operations.cache.generations / (other["id"] + ".json")).read_bytes()
    before = json.loads(operations.store.path.read_bytes())
    result = retire(prepared)
    assert result["status"] == "complete"
    assert not result["delivery_settled"] and not result["recovery_settled"]
    after = json.loads(operations.store.path.read_bytes())
    for field in ("replay", "terminal_recovery", "terminal_retention"):
        assert after[field] == before[field]
    assert operations.cache.lookup(scope)["generation"] is None
    assert not any(key[0] == _digest(scope) for key in operations._materializations)
    assert current["id"] not in operations._generation_positions
    assert other["id"] in operations._generation_positions
    assert operations._materializations[(_digest(other["scope"]), other["id"])][1]
    assert (operations.cache.generations / (other["id"] + ".json")).read_bytes() == other_bytes
    assert state.inspect(scope, Cursor(**decision["cursor"]), expires_at=expires())["stage"] == "complete"
    assert retire(prepared)["generation_ids"] == result["generation_ids"]


def test_old_reader_refuses_adoption_without_rewrite(workspace):
    prepared, _ = setup(workspace)
    operations = prepared[0]
    before = operations.store.path.read_bytes()
    old = ReplayStore(
        operations.store.root, operations.store.namespace,
        service_identity=operations.store.service_identity, terminal_retention=True, terminal_recovery=True,
    )
    with pytest.raises(ReplayStoreInvalid, match="cache retirement reader"):
        old._review_transaction(lambda doc: None, expires_at=expires())
    assert operations.store.path.read_bytes() == before


def test_frozen_pending_body_and_exact_ack_survive_cache_retirement(workspace):
    from agent_pull_requests.watch_contract import PRSnapshot
    from agent_pull_requests.watch_replay import ConsumerScope, ObservationTarget

    operations = workspace[0]
    consumer = ConsumerScope("consumer-a", "account-a", "example/project")
    issued_at = int(time.time()) - 1
    receipt = operations.store.execute(consumer, "register", {
        "request_id": "frozen", "issued_at": issued_at, "number": 42,
        "until": ("review_changed",), "deadline_at": expires() - 10,
    }, expires_at=expires())
    target = ObservationTarget(
        consumer.account, consumer.repo, 42, frozenset({receipt["registration_id"]}),
    )
    operations.store.observe(
        target, PRSnapshot(review_decision="REVIEW_REQUIRED"), expires_at=expires(),
    )
    operations.store.observe(
        target, PRSnapshot(review_decision="APPROVED", native_snapshot={
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "repo": consumer.repo, "pr": 42, "provider": "azure-devops",
            "pr_state": "open", "merged": False, "mergeable": None,
            "head_sha": "a" * 40, "base_ref": "main", "checks_state": "",
            "title": "frozen event body", "draft": False,
        }),
        expires_at=expires(),
    )
    frozen = operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"][0]
    operations.retention_target = lambda scope: target
    prepared, _ = setup(workspace)
    assert retire(prepared)["stage"] == "complete"
    assert operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"] == [frozen]
    retained = prepared[1].inspect(prepared[2]["scope"], prepared[2]["operation_id"], expires_at=expires())
    assert retained["actions"][0]["state"] == "unknown"
    operations.store.execute(consumer, "acknowledge", {
        "request_id": "frozen", "issued_at": issued_at,
        "registration_id": receipt["registration_id"], "event_id": frozen["event_id"],
    }, expires_at=expires())
    assert not operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"]


@pytest.mark.parametrize("boundary", ["capture", "pointer", "object", "completion"])
def test_interrupted_retirement_reopens_same_progress_and_retries_durability(
    workspace, monkeypatch, boundary,
):
    import agent_pull_requests.generation_retirement as physical
    import agent_pull_requests.watch_replay_store as owner_module
    from pathlib import Path

    prepared, _ = setup(workspace)
    operations, _, intent, _, decision, _, _ = prepared
    originals = sorted(operations.cache.generations.iterdir())
    write_owner, write_pointer, unlink = owner_module._atomic_write_json, physical._atomic_write_json, Path.unlink
    interrupted = False

    def owner_write(path, document):
        nonlocal interrupted
        rows = document["terminal_cache_retirement"]["records"]
        if not interrupted and rows and rows[0]["stage"] == (
            "captured" if boundary == "capture" else "cache_retired"
        ) and boundary in ("capture", "completion"):
            write_owner(path, document)
            interrupted = True
            raise StateCommitUncertain("isolated owner interruption")
        return write_owner(path, document)

    def pointer_write(path, document):
        nonlocal interrupted
        result = write_pointer(path, document)
        if boundary == "pointer" and not interrupted:
            interrupted = True
            raise StateCommitUncertain("isolated pointer interruption")
        return result

    def remove(path, *args, **kwargs):
        nonlocal interrupted
        result = unlink(path, *args, **kwargs)
        if boundary == "object" and path in originals and not interrupted:
            interrupted = True
            raise OSError("isolated object interruption")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(owner_module, "_atomic_write_json", owner_write)
        patch.setattr(physical, "_atomic_write_json", pointer_write)
        patch.setattr(Path, "unlink", remove)
        with pytest.raises(OSError):
            retire(prepared)
        if boundary in ("capture", "pointer"):
            assert all(path.exists() for path in originals)
    assert interrupted
    operations.store = ReplayStore(
        operations.store.root, operations.store.namespace,
        service_identity=operations.store.service_identity, terminal_retention=True,
        terminal_recovery=True, terminal_cache_retirement=True,
    )
    assert retire(prepared)["stage"] == "complete"
    assert not list(operations.cache.generations.iterdir())
    assert TerminalCacheRetirementStore(operations.store).inspect(
        intent["scope"], Cursor(**decision["cursor"]), expires_at=expires(),
    )["stage"] == "complete"


@pytest.mark.parametrize("unsafe", ["hardlink", "unexpected", "namespace", "service"])
def test_unsafe_inventory_or_identity_never_removes_objects(workspace, unsafe):
    prepared, _ = setup(workspace)
    operations = prepared[0]
    paths = list(operations.cache.generations.iterdir())
    if unsafe == "hardlink":
        os.link(paths[0], operations.store.root / "fixture-link")
    elif unsafe == "unexpected":
        (operations.cache.generations / "unexpected.json").write_text("{}")
    else:
        descriptor = json.loads(operations.cache.descriptor.read_bytes())
        descriptor[unsafe] = "foreign"
        operations.cache.descriptor.write_text(json.dumps(descriptor))
    with pytest.raises(CacheInvalid):
        retire(prepared)
    assert all(path.exists() for path in paths)


def test_staging_files_are_not_retired(workspace):
    prepared, _ = setup(workspace)
    operations = prepared[0]
    path = operations.cache.generations / ("." + "a" * 64 + ".json.abcdefgh.tmp")
    path.write_text("fixture-owned staged bytes")
    assert retire(prepared)["stage"] == "complete"
    assert path.read_text() == "fixture-owned staged bytes"


def test_missing_compaction_and_capacity_refuse_before_physical_effects(workspace):
    prepared = prepare(workspace)
    operations = prepared[0]
    operations.store.terminal_cache_retirement = True
    TerminalCacheRetirementStore(operations.store).adopt(
        lifecycle_checkpoint=lambda: None, expires_at=expires(),
    )
    before = operations.store.path.read_bytes()
    paths = list(operations.cache.generations.iterdir())
    with pytest.raises(ValueError, match="compact recovery"):
        retire(prepared)
    assert operations.store.path.read_bytes() == before
    assert all(path.exists() for path in paths)
    compact(prepared)

    def full(document):
        from agent_pull_requests.review_intent import encoded
        document["capacities"]["terminal_retention_bytes"] = (
            len(encoded(document["terminal_retention"]))
            + len(encoded(document["terminal_cache_retirement"]))
        )

    operations.store._review_transaction(full, expires_at=expires())
    with pytest.raises(ReplayStoreInvalid, match="cache retirement"):
        retire(prepared)
    assert all(path.exists() for path in paths)
    assert operations.cache.lookup(prepared[2]["scope"])["current_id"] is not None


def test_actual_drain_is_rejoined_even_after_prior_compaction(workspace):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    prepared, _ = setup(workspace)
    operations = prepared[0]
    entered, release = threading.Event(), threading.Event()

    def active():
        with operations.retention.request(
            "load", {"number": 42}, expires_at=expires(), checkpoint=lambda: None,
        ):
            entered.set()
            assert release.wait(5)

    paths = list(operations.cache.generations.iterdir())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(active)
        try:
            assert entered.wait(5)
            with pytest.raises(TimeoutError, match="incomplete"):
                retire(prepared, deadline=time.monotonic() + 0.02)
            assert all(path.exists() for path in paths)
        finally:
            release.set()
        future.result(timeout=5)
    assert retire(prepared)["stage"] == "complete"


def test_lifecycle_withdrawal_after_pointer_clear_preserves_objects(workspace, monkeypatch):
    import agent_pull_requests.generation_retirement as physical

    prepared, _ = setup(workspace)
    operations = prepared[0]
    paths = list(operations.cache.generations.iterdir())
    write, owned = physical._atomic_write_json, True

    def lifecycle():
        if not owned:
            raise PermissionError("exclusive lifecycle withdrawn")

    def pointer(path, document):
        nonlocal owned
        write(path, document)
        owned = False

    with monkeypatch.context() as patch:
        patch.setattr(physical, "_atomic_write_json", pointer)
        with pytest.raises(PermissionError, match="withdrawn"):
            retire(prepared, lifecycle_checkpoint=lifecycle)
    assert operations.cache.lookup(prepared[2]["scope"])["current_id"] is None
    assert all(path.exists() for path in paths)
    assert operations._materializations
    assert retire(prepared)["stage"] == "complete"


def test_changed_cache_after_interruption_is_not_recaptured_or_deleted(workspace, monkeypatch):
    import agent_pull_requests.generation_retirement as physical

    prepared, _ = setup(workspace)
    operations = prepared[0]
    scope = prepared[2]["scope"]
    old = operations.cache.lookup(scope)["generation"]

    def fail(*args):
        raise OSError("isolated pointer write failed")

    with monkeypatch.context() as patch:
        patch.setattr(physical, "_atomic_write_json", fail)
        with pytest.raises(OSError):
            retire(prepared)
    evidence = {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": copy.deepcopy(scope), "sections": copy.deepcopy(old["sections"]),
    }
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:00:30Z", observed_to="2026-10-30T00:00:31Z")
    newer = build_generation(evidence)["generation"]
    assert newer["id"] != old["id"]
    operations.cache.publish(newer, expected_current=old["id"])
    paths = list(operations.cache.generations.iterdir())
    with pytest.raises(CacheInvalid, match="changed after capture"):
        retire(prepared)
    assert all(path.exists() for path in paths)
    assert operations.cache.lookup(scope)["current_id"] == newer["id"]


def test_reopen_preserves_new_pointer_and_rejects_delayed_old_epoch(workspace):
    prepared, _ = setup(workspace)
    operations, _, _, _, _, _, backend = prepared
    assert retire(prepared)["status"] == "complete"
    backend.pr["status"] = "active"
    evidence = workspace[1]
    for section in evidence["sections"].values():
        section.update(observed_from="2026-10-30T00:00:30Z", observed_to="2026-10-30T00:00:31Z")
    reopened = load(operations, None)
    assert reopened["publication"]["status"] == "published"
    pointer = operations.cache.lookup(prepared[2]["scope"])["current_id"]
    fresh = copy.deepcopy(prepared[2])
    fresh.update(operation_id="new-epoch-review", generation_id=pointer)
    admitted = operations.store.admit_review(fresh, expires_at=expires())["operation"]
    assert admitted["intent"]["operation_id"] == "new-epoch-review"
    assert all(action["state"] == "unattempted" for action in admitted["actions"])
    with pytest.raises(TerminalAdmissionClosed):
        retire(prepared)
    assert operations.cache.lookup(prepared[2]["scope"])["current_id"] == pointer
    assert operations.store.inspect_review(
        fresh["operation_id"], fresh["scope"], expires_at=expires(),
    ) == admitted


@pytest.mark.parametrize("kind", ["intent", "cache"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_adoption_rechecks_lifecycle_after_validation(workspace, monkeypatch, kind, boundary):
    operations = workspace[0]
    if kind == "cache":
        prepared = prepare(workspace)
        operations = prepared[0]
        operations.store.terminal_cache_retirement = True
        selected = TerminalCacheRetirementStore(operations.store)
        marker = "terminal_cache_retirement"
    else:
        selected = TerminalRetentionStore(operations.store)
        marker = "terminal_retention"
    owner = operations.store
    before = owner.path.read_bytes()
    validate, owned, count = owner._validate_owner, True, 0

    def lifecycle():
        if not owned:
            raise PermissionError("exclusive lifecycle withdrawn")

    def validation(document):
        nonlocal owned, count
        validate(document)
        if marker in document:
            count += 1
            # Existing intent was already adopted by the fixture; skip entry validation.
            if count == boundary + (1 if kind == "intent" else 0):
                owned = False

    monkeypatch.setattr(owner, "_validate_owner", validation)
    with pytest.raises(PermissionError, match="withdrawn"):
        selected.adopt(lifecycle_checkpoint=lifecycle, expires_at=expires())
    assert owner.path.read_bytes() == before
