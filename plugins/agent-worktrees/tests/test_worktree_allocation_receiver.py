"""Named receiving creation, cancellation and exact retained lifecycle evidence."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from agent_worktrees import creation_requests, tracking
from agent_worktrees import owned_worktree as allocation, receiving_creation_journal as journal
from agent_worktrees.worktree_allocation_receiver import receive
from test_receiving_allocation import receiver as receiver


def request(receiver: tuple, identity: dict | None = None) -> dict:
    _, admission, _ = receiver
    return {
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        **admission.to_fields(), "identity": copy.deepcopy(identity),
    }


def origin_metadata(receiver: tuple, result: dict | None = None) -> dict:
    _, admission, _ = receiver
    return {
        "version": allocation.METADATA_VERSION, "lifetime": allocation.LIFETIME,
        "namespace": allocation.NAMESPACE, "creator": admission.origin,
        "request_id": admission.request_id, "token": admission.token,
        "recovery": admission.target, "phase": "bound" if result else "pending",
        **({"identity": result["identity"]} if result else {}),
    }


def test_named_create_replay_and_inspect_retain_one_sessionless_child(receiver):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    assert created["status"] == "allocated" and created["allocation_finished"] is True
    assert allocation.verified_observation(created, origin_metadata(receiver))
    assert receive("create", request(receiver), config) == created
    assert receive("inspect", request(receiver), config) == created
    assert len(list(records.glob("*.yaml"))) == 1
    record = tracking.load_record(next(records.glob("*.yaml")))
    assert record.sessions == [] and record.receiving_allocation["phase"] == "created"


def test_origin_ack_is_optional_local_metadata_and_never_a_creation_or_launch_gate(receiver):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    assert created["status"] == "allocated"
    with pytest.raises(ValueError, match="exact bound"):
        receive("confirm", request(receiver), config)
    confirmed = receive("confirm", request(receiver, created["identity"]), config)
    assert confirmed["status"] == "confirmed"
    assert receive("confirm", request(receiver, created["identity"]), config) == confirmed
    assert confirmed["identity"] == created["identity"]
    record = tracking.load_record(next(records.glob("*.yaml")))
    assert record.sessions == []


@pytest.mark.parametrize("operation", ["create", "inspect", "confirm", "cancel"])
def test_changed_token_cannot_read_cancel_or_allocate_under_original_request(receiver, operation):
    config, _, records = receiver
    receive("create", request(receiver), config)
    payload = {**request(receiver), "token": "b" * 32}
    with pytest.raises((ValueError, creation_requests.CreationRequestFailure)):
        receive(operation, payload, config)
    assert len(list(records.glob("*.yaml"))) == 1


def test_sealed_precreation_cancellation_survives_replay_and_never_creates(receiver):
    config, _, records = receiver
    cancelled = receive("cancel", request(receiver), config)
    assert cancelled["status"] == "cancelled"
    assert allocation.verified_conclusion(cancelled, origin_metadata(receiver))
    assert receive("cancel", request(receiver), config) == cancelled
    assert receive("inspect", request(receiver), config) == cancelled
    assert receive("create", request(receiver), config) == cancelled
    assert not list(records.glob("*.yaml"))


def test_cancel_before_reserve_fences_already_running_creation_callback(receiver):
    config, admission, records = receiver
    entered, finish = Event(), Event()
    effects = []

    def allocate(reserve) -> dict:
        entered.set()
        assert finish.wait(3)
        reserve({"id": "never-created"})
        effects.append("forbidden effect")
        return {}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            creation_requests.create, config, admission.key,
            {"receiving_allocation": admission.to_fields()}, allocate,
        )
        assert entered.wait(3)
        try:
            cancelled = receive("cancel", request(receiver), config)
            assert cancelled["status"] == "cancelled"
        finally:
            finish.set()
        with pytest.raises(creation_requests.CreationRequestFailure, match="already has"):
            future.result(timeout=3)
    assert effects == [] and not list(records.glob("*.yaml"))


def test_allocated_identity_can_never_be_precreation_cancelled(receiver):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    before = next(records.glob("*.yaml")).read_bytes()
    with pytest.raises(ValueError, match="reserved effects"):
        receive("cancel", request(receiver), config)
    assert receive("inspect", request(receiver), config) == created
    assert next(records.glob("*.yaml")).read_bytes() == before


@pytest.mark.parametrize("status", ["finalized", "archived"])
def test_actual_saved_conclusion_outlives_child_record_without_reverse_connection(receiver, status):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    record = tracking.load_record(path)
    record.status = status
    tracking.save_record(record, path)
    concluded = receive("inspect", request(receiver, created["identity"]), config)
    assert allocation.verified_conclusion(concluded, origin_metadata(receiver, created))
    path.unlink()
    assert receive("inspect", request(receiver, created["identity"]), config) == concluded


def test_missing_record_is_unknown_and_never_a_conclusion_or_cancellation(receiver):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    next(records.glob("*.yaml")).unlink()
    result = receive("inspect", request(receiver, created["identity"]), config)
    assert result["status"] == "unknown" and result["allocation_finished"] is False
    assert not allocation.verified_conclusion(result, origin_metadata(receiver, created))
    with pytest.raises(ValueError, match="reserved effects"):
        receive("cancel", request(receiver), config)


def test_unsaved_finality_is_not_receiving_conclusion(receiver):
    config, _, records = receiver
    receive("create", request(receiver), config)
    record = tracking.load_record(next(records.glob("*.yaml")))
    record.status = "finalized"
    with pytest.raises(ValueError, match="actual saved"):
        journal.note_conclusion(record, config)
    assert receive("inspect", request(receiver), config)["status"] == "allocated"


def test_concluded_receiving_lifetime_cannot_be_reopened_by_stale_or_generic_save(receiver):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    record = tracking.load_record(path)
    stale = copy.deepcopy(record)
    record.status = "finalized"
    tracking.save_record(record, path)
    with pytest.raises(ValueError, match="concluded"):
        tracking.save_record(stale, path)
    result = receive("create", request(receiver), config)
    assert result["status"] == "finalized" and result["identity"] == created["identity"]
    assert len(list(records.glob("*.yaml"))) == 1


@pytest.mark.parametrize("changed", [
    {"version": True}, {"extra": True}, {"schema": "unknown"},
    {"target": {"machine": "different", "project": "demo-repo"}},
    {"identity": {"ref": "incomplete"}},
])
def test_strict_receiving_request_refuses_before_new_state(receiver, changed):
    config, _, records = receiver
    with pytest.raises(ValueError):
        receive("create", {**request(receiver), **changed}, config)
    assert not list(records.glob("*.yaml"))


def test_read_only_unknown_inspection_does_not_create_journal(receiver):
    config, _, records = receiver
    result = receive("inspect", request(receiver), config)
    assert result["status"] == "unknown" and result["allocation_finished"] is False
    assert not (Path(records).parent / "workspace-creation.sqlite3").exists()


def test_create_with_expected_identity_never_allocates_without_original_history(receiver, monkeypatch):
    from agent_worktrees import worktree_creation

    config, admission, records = receiver
    identity = {
        "ref": f"{admission.target['machine']}/{config.repo_name}/original-child",
        "creation_nonce": "a1b2c3", "path": str(Path(config.srcroot) / "original-child"),
        "branch": "worktree/original-child",
    }
    monkeypatch.setattr(
        worktree_creation, "_create_worktree_core",
        lambda *a, **k: pytest.fail("an expected original identity must never allocate"),
    )
    result = receive("create", request(receiver, identity), config)
    assert result["status"] == "unknown" and not result["allocation_finished"]
    assert not list(records.glob("*.yaml"))
    assert not (Path(records).parent / "workspace-creation.sqlite3").exists()


def test_create_with_expected_identity_only_inspects_original_even_with_invalid_creation_options(receiver, monkeypatch):
    from agent_worktrees import worktree_creation

    config, admission, _ = receiver
    created = receive("create", request(receiver), config)
    monkeypatch.setattr(
        worktree_creation, "_create_worktree_core",
        lambda *a, **k: pytest.fail("expected-identity replay must not enter allocation"),
    )
    assert receive("create", request(receiver, created["identity"]), config) == created
    admission.target["options"] = {"unknown": True}
    with pytest.raises(ValueError, match="original allocation facts"):
        receive("create", request(receiver, created["identity"]), config)


def test_delayed_finalized_inspector_cannot_downgrade_concurrent_archived_journal(receiver, monkeypatch):
    config, admission, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    finalized = tracking.load_record(path)
    finalized.status = "finalized"
    tracking.save_record(finalized, path)
    original = creation_requests._connection
    interleaved = []

    def concurrent_archive(journal_path):
        if not interleaved:
            interleaved.append(True)
            archived = tracking.load_record(path)
            archived.status = "archived"
            tracking.save_record(archived, path)
        return original(journal_path)

    # Interleave after the inspector validated finalized YAML but before its
    # journal transaction; the concurrent writer commits archived first.
    monkeypatch.setattr(creation_requests, "_connection", concurrent_archive)
    with pytest.raises(ValueError, match="downgrade retained archived"):
        journal.note_conclusion(finalized, config)
    row = creation_requests.inspect(config, admission.key)
    assert journal.concluded_identity(row, admission)[1] == "archived"
    result = receive("inspect", request(receiver, created["identity"]), config)
    assert result["status"] == "archived" and result["identity"] == created["identity"]


@pytest.mark.parametrize("options", [{"no_pair": 1}, {"unknown": True}, {"kind": "unsupported"}])
def test_invalid_creation_options_do_not_trap_pre_effect_responsibility(receiver, options):
    config, admission, records = receiver
    admission.target["options"] = options
    with pytest.raises(ValueError):
        receive("create", request(receiver), config)
    assert receive("inspect", request(receiver), config)["status"] == "unknown"
    cancelled = receive("cancel", request(receiver), config)
    assert cancelled["status"] == "cancelled" and cancelled["allocation_finished"]
    assert receive("inspect", request(receiver), config) == cancelled
    assert not list(records.glob("*.yaml"))
