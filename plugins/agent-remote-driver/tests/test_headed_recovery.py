"""Actor-free recovery bookkeeping with a fixture-owned durable CAS adapter."""

from dataclasses import asdict, replace
import json
import sqlite3
import time

import pytest

from agent_remote_driver.headed.auth_wait import AuthWaitReceiver
from agent_remote_driver.headed.recovery import (
    RecoveryConflict, RecoveryDescriptor, RecoveryRecord, RecoveryStep,
    crash_candidate, record_recovery_step, reduce_recovery,
)


@pytest.fixture
def descriptor():
    return RecoveryDescriptor(
        owner_ref="original-owner", worktree_ref="original-worktree",
        descriptor_revision="descriptor-1", source_owner="source-owner",
        source_revision="source-1", session_id="original-conversation",
        environment_ref="fixture-environment", boot_ref="boot-1", scope_ref="scope-1",
        incarnation="incarnation-1", lease_revision=1,
    )


class OwnerStore:
    def __init__(self, path):
        self.path = path
        self.lose_ack = False
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records "
                "(descriptor TEXT PRIMARY KEY, operation TEXT UNIQUE, body TEXT)",
            )

    def load(self, descriptor):
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT body FROM records WHERE descriptor = ?",
                (json.dumps(asdict(descriptor), sort_keys=True),),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        return RecoveryRecord(
            value["operation_id"], RecoveryDescriptor(**value["descriptor"]),
            tuple(RecoveryStep(**{**step, "unknown_effects": tuple(step["unknown_effects"])})
                  for step in value["steps"]),
            tuple(value["legacy_holds"]),
        )

    def compare_and_swap(self, descriptor, expected_revision, record):
        key = json.dumps(asdict(descriptor), sort_keys=True)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM records WHERE descriptor = ?", (key,)).fetchone()
            revision = len(json.loads(row[0])["steps"]) if row else 0
            if revision != expected_revision:
                return False
            conflict = db.execute(
                "SELECT descriptor FROM records WHERE operation = ?", (record.operation_id,),
            ).fetchone()
            if conflict and conflict[0] != key:
                return False
            db.execute(
                "INSERT OR REPLACE INTO records VALUES (?, ?, ?)",
                (key, record.operation_id, json.dumps(asdict(record))),
            )
        if self.lose_ack:
            raise ConnectionError("Fixture lost the durable commit acknowledgement")
        return True


@pytest.fixture
def owner_store(tmp_path):
    return OwnerStore(tmp_path / "owner.sqlite")


def steps():
    return (
        RecoveryStep("retire_intent", "owner-admission", ("external-action-unknown",)),
        RecoveryStep("settled", "independent-whole-scope-settlement"),
        RecoveryStep("resume_intent", "current-source-admission", authority_revision=2),
        RecoveryStep("observation_restored", "same-conversation-observed",
                     restored_incarnation="incarnation-2"),
    )


@pytest.mark.parametrize("count", [1, 3])
def test_reopen_after_intent_keeps_original_transition_and_unknown_effects(
    owner_store, descriptor, count,
):
    for index, step in enumerate(steps()[:count]):
        record_recovery_step(
            owner_store, descriptor, "recovery-1", index, step,
            legacy_holds=("native-connection-lost",),
        )
    reopened = OwnerStore(owner_store.path)
    record = reopened.load(descriptor)
    assert record.revision == count
    assert record.steps[-1] == steps()[count - 1]
    assert record.unknown_effects == ("external-action-unknown",)
    assert record.legacy_holds == ("native-connection-lost",)
    assert record_recovery_step(reopened, descriptor, "recovery-1", 0, steps()[0]) == record
    with pytest.raises(RecoveryConflict):
        record_recovery_step(
            reopened, descriptor, "recovery-1", count,
            replace(steps()[0], evidence_ref="different-retirement"),
        )


@pytest.mark.parametrize("field,value", [
    ("owner_ref", "another-owner"), ("worktree_ref", "another-worktree"),
    ("descriptor_revision", "descriptor-2"), ("source_owner", "another-source"),
    ("source_revision", "source-2"), ("session_id", "another-conversation"),
    ("environment_ref", "another-environment"), ("boot_ref", "boot-2"),
    ("scope_ref", "scope-2"), ("incarnation", "incarnation-2"), ("lease_revision", 2),
])
def test_changed_binding_cannot_advance_or_reinterpret_old_transition(descriptor, field, value):
    original = reduce_recovery(None, descriptor, "recovery-1", steps()[0])
    with pytest.raises(RecoveryConflict):
        reduce_recovery(original, replace(descriptor, **{field: value}), "recovery-1", steps()[1])
    assert original.steps == (steps()[0],)


def test_resume_requires_settlement_new_authority_and_new_incarnation(descriptor):
    record = reduce_recovery(None, descriptor, "recovery-1", steps()[0])
    with pytest.raises(RecoveryConflict):
        reduce_recovery(record, descriptor, "recovery-1", steps()[2])
    record = reduce_recovery(record, descriptor, "recovery-1", steps()[1])
    with pytest.raises(RecoveryConflict):
        reduce_recovery(
            record, descriptor, "recovery-1", replace(steps()[2], authority_revision=1),
        )
    record = reduce_recovery(record, descriptor, "recovery-1", steps()[2])
    with pytest.raises(RecoveryConflict):
        reduce_recovery(
            record, descriptor, "recovery-1",
            replace(steps()[3], restored_incarnation=descriptor.incarnation),
        )


def test_lost_resume_commit_ack_is_reconciled_without_another_intent(owner_store, descriptor):
    for index, step in enumerate(steps()[:2]):
        record_recovery_step(owner_store, descriptor, "recovery-1", index, step)
    owner_store.lose_ack = True
    with pytest.raises(ConnectionError):
        record_recovery_step(owner_store, descriptor, "recovery-1", 2, steps()[2])
    reopened = OwnerStore(owner_store.path)
    record = record_recovery_step(reopened, descriptor, "recovery-1", 2, steps()[2])
    assert record.revision == 3
    assert record.steps.count(steps()[2]) == 1
    assert record.unknown_effects == ("external-action-unknown",)


def test_stale_cas_refuses_without_overwriting_the_original_owner_record(owner_store, descriptor):
    record = record_recovery_step(owner_store, descriptor, "recovery-1", 0, steps()[0])
    with pytest.raises(RecoveryConflict):
        record_recovery_step(owner_store, descriptor, "recovery-1", 0, steps()[1])
    assert not owner_store.compare_and_swap(descriptor, 0, record)
    assert owner_store.load(descriptor) == record


def test_lost_native_resume_ack_keeps_uncertainty_after_observation_returns(owner_store, descriptor):
    for index, step in enumerate(steps()[:2]):
        record_recovery_step(owner_store, descriptor, "recovery-1", index, step)
    resume = replace(steps()[2], unknown_effects=("resume-acceptance-unknown",))
    record_recovery_step(owner_store, descriptor, "recovery-1", 2, resume)
    reopened = OwnerStore(owner_store.path)
    pending = reopened.load(descriptor)
    assert pending.steps[-1] == resume
    with pytest.raises(RecoveryConflict):
        record_recovery_step(
            reopened, descriptor, "recovery-1", 3,
            replace(resume, evidence_ref="replacement-launch"),
        )
    restored = record_recovery_step(reopened, descriptor, "recovery-1", 3, steps()[3])
    assert restored.unknown_effects == ("external-action-unknown", "resume-acceptance-unknown")
    assert restored.status()["reconciliation_required"]


def test_new_operation_cannot_replace_the_original_scope_intent(owner_store, descriptor):
    original = record_recovery_step(owner_store, descriptor, "recovery-1", 0, steps()[0])
    with pytest.raises(RecoveryConflict):
        record_recovery_step(owner_store, descriptor, "new-request", 0, steps()[0])
    with pytest.raises(RecoveryConflict):
        record_recovery_step(
            owner_store, replace(descriptor, boot_ref="boot-2"), "recovery-1", 0, steps()[0],
        )
    assert owner_store.load(descriptor) == original


def test_restored_observation_never_clears_legacy_holds_or_claims_completed_work(
    owner_store, descriptor,
):
    for index, step in enumerate(steps()):
        record = record_recovery_step(
            owner_store, descriptor, "recovery-1", index, step,
            legacy_holds=("native-connection-lost",),
        )
    status = record.status()
    assert status["phase"] == "observation_restored"
    assert status["unknown_effects"] == ["external-action-unknown"]
    assert status["legacy_holds"] == ["native-connection-lost"]
    assert status["reconciliation_required"] and not status["effects_wired"]
    assert record_recovery_step(owner_store, descriptor, "recovery-1", 0, steps()[2]) == record


def test_receiver_export_is_diagnostic_and_does_not_thaw_legacy_auth_holds(descriptor):
    receiver = AuthWaitReceiver(descriptor.session_id, {
        "source_owner": descriptor.source_owner, "source_revision": descriptor.source_revision,
        "lease_revision": 1, "budget_seconds": 10, "expires_at": time.time() + 10,
    })
    descriptor = replace(descriptor, incarnation=receiver.incarnation)
    receiver.preserve_hold("native-connection-lost")
    receiver.stopped(None)
    before = receiver.status()
    exported = receiver.crash_candidate(descriptor, unknown_effects=("external-action-unknown",))
    assert exported["classification"] == "investigate"
    assert not exported["retirement_authorized"] and not exported["effects_wired"]
    assert exported["legacy_hold"] == "native-connection-lost"
    assert exported["unknown_effects"] == ["external-action-unknown"]
    assert receiver.status() == before and not receiver._grant.is_set()
    with pytest.raises(RecoveryConflict):
        receiver.crash_candidate(replace(descriptor, boot_ref="boot-2", session_id="foreign"))


def test_missing_observation_is_unknown_not_an_absent_or_retirable_process(descriptor):
    exported = crash_candidate(descriptor, None)
    assert exported["classification"] == "unknown"
    assert not exported["retirement_authorized"]
    with pytest.raises(ValueError):
        RecoveryStep("settled", "")
    with pytest.raises(ValueError):
        RecoveryStep("resume_intent", "source", authority_revision=True)
