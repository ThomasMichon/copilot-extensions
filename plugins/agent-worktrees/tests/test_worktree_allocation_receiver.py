"""Named receiving creation, cancellation and exact retained lifecycle evidence."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
import yaml

from agent_worktrees import creation_requests, dispatch_cohort, tracking
from agent_worktrees import owned_worktree as allocation, receiving_creation_journal as journal
from agent_worktrees.worktree_allocation_receiver import receive
from test_receiving_allocation import receiver as receiver


def cohort_attempt() -> dict:
    return {
        "task_id": "original-task", "reservation_key": "original-reservation",
        "attempt": 1, "driver": "cli", "supervisor": "original-supervisor",
        "cohort": {
            "schema": "dispatch-assignment-cohort", "version": 1,
            "emitter": {
                "repo": "source-repo", "source": "emitter", "producer_id": "source-reader",
                "generation": 2, "request_id": "original-request", "task_id": "original-task",
            },
            "pool": {
                "task_id": "original-task", "registration_id": "registered-workers",
                "machine": "execution-space", "env": "linux",
                "reservation_key": "original-reservation", "task_generation": 3,
                "catalog_revision": 7,
            },
        },
    }


def with_attempt(receiver: tuple, attempt: dict) -> tuple:
    config, admission, records = receiver
    fields = copy.deepcopy(admission.to_fields())
    fields["target"]["options"]["dispatch_attempt"] = attempt
    return config, type(admission).from_fields(fields), records


def test_original_cohort_survives_real_creation_replay_stale_save_and_reuse(receiver):
    receiver = with_attempt(receiver, cohort_attempt())
    config, admission, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    record = tracking.load_record(path)
    original = record.dispatch_attempt.to_dict()
    assert original["cohort"] == cohort_attempt()["cohort"]
    assert creation_requests.inspect(config, admission.key)["facts"]["dispatch_cohort"] == original["cohort"]
    # A later assignment or an older full-record writer cannot rename the allocation.
    record.dispatch_attempt = replace(record.dispatch_attempt, task_id="later-task", cohort=None)
    record.summary = "later assignment"
    tracking.save_record(record, path)
    current = tracking.load_record(path)
    assert current.summary == "later assignment" and current.dispatch_attempt.to_dict() == original
    detached = current.dispatch_attempt.cohort.to_dict()
    detached["pool"]["registration_id"] = "mutated-return"
    assert current.dispatch_attempt.to_dict() == original
    assert receive("create", request(receiver), config) == created
    result = receive("result", request(receiver), config)
    assert result["result"]["worktree"]["dispatch_attempt"] == original
    assert len(list(records.glob("*.yaml"))) == 1


def test_changed_cohort_cannot_replay_or_repair_original_journal(receiver):
    receiver = with_attempt(receiver, cohort_attempt())
    config, _, records = receiver
    receive("create", request(receiver), config)
    changed = cohort_attempt()
    changed["cohort"]["pool"]["catalog_revision"] += 1
    with pytest.raises((ValueError, creation_requests.CreationRequestFailure)):
        receive("create", request(with_attempt(receiver, changed)), config)
    path = next(records.glob("*.yaml"))
    # Corrupted on-disk evidence must not be repaired by replaying the old request.
    fields = yaml.safe_load(path.read_text())
    fields["dispatch_attempt"]["cohort"]["pool"]["catalog_revision"] += 1
    path.write_text(yaml.safe_dump(fields))
    with pytest.raises(ValueError, match="original dispatch cohort"):
        receive("result", request(receiver), config)
    assert len(list(records.glob("*.yaml"))) == 1


def test_opaque_future_cohort_survives_an_older_full_record_snapshot(receiver):
    receiver = with_attempt(receiver, cohort_attempt())
    config, _, records = receiver
    receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    stale = tracking.load_record(path)
    fields = yaml.safe_load(path.read_text())
    fields["dispatch_attempt"]["cohort"]["version"] = 2
    path.write_text(yaml.safe_dump(fields))
    stale.summary = "unrelated update"
    tracking.save_record(stale, path)
    current = tracking.load_record(path)
    assert current.summary == "unrelated update" and current.dispatch_attempt_opaque
    assert current.dispatch_attempt_raw == fields["dispatch_attempt"]


@pytest.mark.parametrize("damage", ["task", "reservation", "secret", "version", "counter"])
def test_invalid_or_mismatched_cohort_is_refused_before_allocation(receiver, damage):
    attempt = cohort_attempt()
    if damage == "task":
        attempt["cohort"]["emitter"]["task_id"] = "other-task"
    elif damage == "reservation":
        attempt["cohort"]["pool"]["reservation_key"] = "other-reservation"
    elif damage == "secret":
        attempt["cohort"]["emitter"]["producer_capability"] = "must-not-be-carried"
    elif damage == "version":
        attempt["cohort"]["version"] = True
    else:
        attempt["cohort"]["pool"]["catalog_revision"] = False
    receiver = with_attempt(receiver, attempt)
    config, _, records = receiver
    with pytest.raises(ValueError, match="cohort"):
        receive("create", request(receiver), config)
    assert not list(records.glob("*.yaml"))
    assert not (records.parent / "workspace-creation.sqlite3").exists()


@pytest.mark.parametrize("field,reference", [("task_id", "emitter"), ("reservation_key", "pool")])
def test_padded_attempt_ids_with_matching_cohort_refuse_before_receiving_effects(
    receiver, field, reference,
):
    attempt = cohort_attempt()
    attempt[field] = " " + attempt[field] + " "
    attempt["cohort"][reference][field] = attempt[field]
    if field == "task_id":
        attempt["cohort"]["pool"][field] = attempt[field]
    receiver = with_attempt(receiver, attempt)
    config, _, records = receiver
    with pytest.raises(ValueError, match="canonical task and reservation"):
        receive("create", request(receiver), config)
    assert not list(records.glob("*.yaml"))
    assert not (records.parent / "workspace-creation.sqlite3").exists()


def test_legacy_or_unattributed_cohort_is_never_backfilled(receiver):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    record = tracking.load_record(path)
    record.dispatch_attempt = tracking._dispatch_attempt_from_mapping({
        **cohort_attempt(), "creator_machine": "execution-space", "ownership": "created",
    })
    with pytest.raises(ValueError, match="cannot be backfilled"):
        tracking.save_record(record, path)
    assert tracking.load_record(path).dispatch_attempt is None
    assert receive("create", request(receiver), config) == created
    fields = cohort_attempt()
    fields["cohort"]["emitter"] = fields["cohort"]["pool"] = None
    parsed = dispatch_cohort.DispatchCohort.from_fields(
        fields["cohort"], task_id=fields["task_id"], reservation_key=fields["reservation_key"],
    )
    assert parsed.to_dict()["emitter"] is None and parsed.to_dict()["pool"] is None


@pytest.mark.parametrize("damage", [
    {"driver": " \t "}, {"supervisor": " \t "},
    {"driver": " " + "x" * 513 + " "}, {"supervisor": " " + "x" * 513 + " "},
    {"attempt": 2**63},
])
def test_unreloadable_attempt_refuses_before_receiving_or_direct_allocation(
    receiver, monkeypatch, damage,
):
    from agent_worktrees import execution_spaces, worktree_creation

    attempt = {**cohort_attempt(), **damage}
    receiver = with_attempt(receiver, attempt)
    config, _, records = receiver
    with pytest.raises(ValueError, match="cohort attempt"):
        receive("create", request(receiver), config)
    monkeypatch.setattr(
        execution_spaces, "require_current_execution_space",
        lambda *a: pytest.fail("invalid provenance reached allocation preflight"),
    )
    with pytest.raises(ValueError, match="cohort attempt"):
        worktree_creation._create_worktree_core(
            config, dispatch_attempt=attempt, build_launch_plan=False,
        )
    assert not list(records.glob("*.yaml"))
    assert not (records.parent / "workspace-creation.sqlite3").exists()


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
