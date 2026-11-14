"""Actor-free intent persistence, not current authorization or native recovery."""

from dataclasses import asdict, fields, replace
import json

import pytest

from agent_remote_driver.headed.recovery import (
    PermitRecoveryIntent, RecoveryDescriptor, RecoveryRecord, RecoveryStep,
)
from agent_worktrees import config as cfg, launch_execution, launch_execution_permit as permits
from agent_worktrees import launch_permit_recovery_intent as state, launch_recovery_store as store
from agent_worktrees import tracking, tracking_write
from agent_worktrees.launch_recovery_admission import OwnerIntent, SourceSelection


@pytest.fixture
def owner(tmp_path, monkeypatch):
    directory = cfg.project_dir("demo")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "test",
        "repos": {"demo": {
            "anchor": str(tmp_path), "worktree_root": str(tmp_path), "default_branch": "dev",
        }},
    }), encoding="utf-8")
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo", "test",
        cfg.detect_platform(), directory / "worktrees",
    )
    monkeypatch.setattr(state.locks, "read_lock", lambda *_: None)
    correlation = launch_execution.Correlation("a" * 32, "b" * 32)
    execution = launch_execution.Execution(record.yaml_path, correlation)
    execution.append("execution-requested")
    binding = permits.ExecutionBinding(
        "c" * 32, correlation, execution.subject["operation_id"],
        OwnerIntent.capture(record.yaml_path),
        SourceSelection("target", "account", "recipient", "route", "controller-1"),
        "conversation", "environment", "boot", "scope", "incarnation",
    )
    descriptor = RecoveryDescriptor(
        tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id),
        record.worktree_id, "descriptor-1", "source-owner", "source-revision",
        "conversation", "environment", "boot", "scope", "incarnation", 101,
    )
    recovery = RecoveryRecord(
        "recovery-operation", descriptor,
        (RecoveryStep("retire_intent", "original-intent", ("unknown-work",)),),
        ("retained-hold",),
    )
    recovery_store = store.WorktreeRecoveryStore(record.yaml_path)
    assert recovery_store.compare_and_swap(descriptor, 0, recovery)
    permit = permits.WorktreeExecutionPermit(record.yaml_path, binding)
    permit.record_decision(0, permits.ExecutionDecision("owned-start", "start", "approved", 200))
    permit.record_decision(1, permits.ExecutionDecision("owned-retire", "retire", "approved", 300))
    intent = PermitRecoveryIntent(
        descriptor, recovery.operation_id, "retire_intent", 1, "opaque-lease",
        "receiver", 7, binding.permit_id, 1, 2, "owned-retire",
    )
    return state.WorktreePermitRecoveryIntents(record.yaml_path, binding), binding, intent, record.yaml_path


def _snapshots(path):
    return {item: item.read_bytes() for item in path.parent.rglob("*") if item.is_file()}


def _resume(owner):
    _journal, binding, intent, path = owner
    recovery_store = store.WorktreeRecoveryStore(path)
    original = recovery_store.load(intent.descriptor)
    settled = replace(original, steps=(*original.steps, RecoveryStep("settled", "scope")))
    assert recovery_store.compare_and_swap(intent.descriptor, 1, settled)
    resumed = replace(settled, steps=(
        *settled.steps, RecoveryStep("resume_intent", "resume", authority_revision=102),
    ))
    assert recovery_store.compare_and_swap(intent.descriptor, 2, resumed)
    permits.WorktreeExecutionPermit(path, binding).record_decision(
        2, permits.ExecutionDecision("owned-restore", "restore", "approved", 400),
    )
    return replace(
        intent, phase="resume_intent", record_revision=3,
        permit_revision=3, decision_ref="owned-restore", receiver_revision=8,
    )


def test_missing_and_reopened_reads_never_write_or_upgrade_v1(owner, monkeypatch):
    journal, binding, intent, path = owner
    before = _snapshots(path)
    assert journal.load(intent.descriptor) == ()
    assert _snapshots(path) == before
    assert journal.record_intent(intent) == intent
    assert all(item.read_bytes() == raw for item, raw in before.items())
    reopened = state.WorktreePermitRecoveryIntents(path, binding)
    monkeypatch.setattr(
        tracking_write, "dispatch", lambda *_a, **_k: pytest.fail("read/reconciliation dispatched"),
    )
    before = _snapshots(path)
    assert reopened.load(intent.descriptor) == (intent,)
    assert reopened.record_intent(intent) == intent
    assert _snapshots(path) == before
    original = store.WorktreeRecoveryStore(path).load(intent.descriptor)
    assert original.unknown_effects == ("unknown-work",)
    assert original.legacy_holds == ("retained-hold",)


def test_two_phases_keep_independent_revision_domains_and_original_history(owner, monkeypatch):
    journal, binding, intent, path = owner
    journal.record_intent(intent)
    resume = _resume(owner)
    before = _snapshots(path)
    assert journal.record_intent(resume) == resume
    assert all(item.read_bytes() == raw for item, raw in before.items()
               if item != state._target(path))
    assert state.WorktreePermitRecoveryIntents(path, binding).load(intent.descriptor) == (intent, resume)
    permits.WorktreeExecutionPermit(path, binding).record_decision(
        3, permits.ExecutionDecision("later-revocation", "restore", "revoked", 500),
    )
    monkeypatch.setattr(
        tracking_write, "dispatch", lambda *_a, **_k: pytest.fail("historical data was replayed"),
    )
    assert journal.record_intent(intent) == intent
    assert journal.record_intent(resume) == resume
    assert store.WorktreeRecoveryStore(path).load(intent.descriptor).status()["effects_wired"] is False


@pytest.mark.parametrize("field", [item.name for item in fields(RecoveryDescriptor)])
def test_every_original_descriptor_field_is_exact(owner, field):
    journal, _binding, intent, path = owner
    changed = replace(intent, descriptor=replace(
        intent.descriptor, **{field: 102 if field == "lease_revision" else "different"},
    ))
    before = _snapshots(path)
    with pytest.raises(ValueError):
        journal.record_intent(changed)
    assert _snapshots(path) == before


@pytest.mark.parametrize("changes", [
    {"operation_id": "other-operation"}, {"permit_id": "d" * 32},
    {"permit_revision": 3}, {"decision_ref": "other-decision"},
])
def test_operation_permit_and_decision_must_match_retained_owners(owner, changes):
    journal, _binding, intent, path = owner
    before = _snapshots(path)
    with pytest.raises(ValueError):
        journal.record_intent(replace(intent, **changes))
    assert _snapshots(path) == before


@pytest.mark.parametrize("action,state_value", [
    ("start", "approved"), ("restore", "approved"), ("retire", "revoked"),
])
def test_start_wrong_action_and_revocation_cannot_supply_a_new_retire_intent(owner, action, state_value):
    journal, binding, intent, path = owner
    decision = permits.ExecutionDecision("latest-decision", action, state_value, 400)
    permits.WorktreeExecutionPermit(path, binding).record_decision(2, decision)
    before = _snapshots(path)
    with pytest.raises(ValueError, match="action-specific"):
        journal.record_intent(replace(intent, permit_revision=3, decision_ref=decision.decision_ref))
    assert _snapshots(path) == before


def test_original_permit_revision_cannot_relabel_a_recovery_decision_as_start(owner):
    journal, binding, intent, path = owner
    permits.WorktreeExecutionPermit(path, binding).record_decision(
        2, permits.ExecutionDecision("another-retire", "retire", "approved", 400),
    )
    with pytest.raises(ValueError, match="action-specific"):
        journal.record_intent(replace(
            intent, original_permit_revision=2, permit_revision=3, decision_ref="another-retire",
        ))
    assert not state._target(path).exists()


def test_new_store_does_not_convert_a_v1_resume_without_original_retire_intent(owner):
    journal, _binding, intent, path = owner
    resume = _resume(owner)
    before = _snapshots(path)
    with pytest.raises(ValueError, match="next original phase"):
        journal.record_intent(resume)
    with pytest.raises(ValueError, match="revision changed"):
        journal.record_intent(intent)
    assert _snapshots(path) == before


@pytest.mark.parametrize("field,value", [
    ("operation_id", "other"), ("source_lease_ref", "other"), ("receiver_ref", "other"),
    ("receiver_revision", 8), ("decision_ref", "other"), ("permit_revision", 3),
])
def test_committed_phase_cannot_be_replaced(owner, field, value):
    journal, _binding, intent, path = owner
    journal.record_intent(intent)
    before = _snapshots(path)
    with pytest.raises(ValueError, match="different data"):
        journal.record_intent(replace(intent, **{field: value}))
    assert _snapshots(path) == before


def test_changed_owner_or_launch_cannot_publish(owner):
    journal, binding, intent, path = owner
    wrong = state.WorktreePermitRecoveryIntents(
        path, replace(binding, correlation=replace(binding.correlation, resolution_id="d" * 32)),
    )
    with pytest.raises(ValueError, match="correlation changed"):
        wrong.record_intent(intent)
    path.write_text(path.read_text(encoding="utf-8") + "\nhead_revision: 10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="owner intent changed"):
        journal.record_intent(intent)
    assert not state._target(path).exists()


def test_unaware_resident_never_receives_optional_intent_semantics(owner, monkeypatch):
    journal, _binding, intent, _path = owner
    monkeypatch.setattr(state.locks, "read_lock", lambda *_: {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": [store._VERB],
    })
    monkeypatch.setattr(
        tracking_write, "_send_tracking_write_request",
        lambda *_a, **_k: pytest.fail("unaware resident received new semantics"),
    )
    assert journal.record_intent(intent) == intent


def test_lost_ack_reopens_exact_commit_without_a_second_dispatch(owner, monkeypatch):
    journal, binding, intent, path = owner
    monkeypatch.setattr(state.locks, "read_lock", lambda *_: {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": [state._VERB],
    })
    calls = []

    def sent(*_args, **kwargs):
        calls.append(True)
        state._apply(kwargs["payload"]["args"])
        raise tracking_write.AmbiguousWriteOutcome("response lost after commit")

    monkeypatch.setattr(tracking_write, "_send_tracking_write_request", sent)
    monkeypatch.setattr(tracking_write.wcs_client, "release", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tracking_write, "run_direct", lambda *_a, **_k: pytest.fail("ambiguous request replayed"),
    )
    with pytest.raises(tracking_write.AmbiguousWriteOutcome):
        journal.record_intent(intent)
    reopened = state.WorktreePermitRecoveryIntents(path, binding)
    assert reopened.load(intent.descriptor) == (intent,)
    assert reopened.record_intent(intent) == intent
    assert calls == [True]


@pytest.mark.parametrize("point", ["flush", "publish", "after-publish"])
def test_failed_durable_publication_is_not_acknowledged(owner, monkeypatch, point):
    journal, _binding, intent, path = owner
    publish = store.checkout_durability.replace

    def fail(*args, **kwargs):
        if point == "after-publish":
            publish(*args, **kwargs)
        raise OSError("injected durable publication failure")

    monkeypatch.setattr(
        store.os if point == "flush" else store.checkout_durability,
        "fsync" if point == "flush" else "replace", fail,
    )
    with pytest.raises(OSError):
        journal.record_intent(intent)
    assert journal.load(intent.descriptor) == ((intent,) if point == "after-publish" else ())
    assert not list(state._target(path).parent.glob("*.tmp"))


@pytest.mark.parametrize("corruption", ["version", "nonce", "duplicate", "intent", "binding", "oversize"])
def test_invalid_or_future_store_is_not_rewritten(owner, corruption):
    journal, _binding, intent, path = owner
    journal.record_intent(intent)
    target = state._target(path)
    data = json.loads(target.read_bytes())
    if corruption == "version":
        data["version"] = 2
    elif corruption == "nonce":
        data["creation_nonce"] = "other-incarnation"
    elif corruption == "duplicate":
        data["intents"].append(data["intents"][0])
    elif corruption == "intent":
        data["intents"][0]["intent"] = "{}"
    elif corruption == "binding":
        data["intents"][0]["binding"]["source"]["controller_generation"] = "other-controller"
        data["intents"][0]["binding"]["owner_intent"]["creation_nonce"] = "other-incarnation"
    target.write_text(
        " " * (store._MAX_BYTES + 1) if corruption == "oversize" else json.dumps(data),
        encoding="utf-8",
    )
    before = target.read_bytes()
    with pytest.raises(ValueError):
        journal.load(intent.descriptor)
    with pytest.raises(ValueError):
        journal.record_intent(intent)
    assert target.read_bytes() == before


def test_full_store_does_not_evict_original_intent(owner, monkeypatch):
    journal, _binding, intent, path = owner
    journal.record_intent(intent)
    resume = _resume(owner)
    monkeypatch.setattr(store, "_MAX_RECORDS", 1)
    before = state._target(path).read_bytes()
    with pytest.raises(ValueError, match="store is full"):
        journal.record_intent(resume)
    assert state._target(path).read_bytes() == before


def test_receiver_strictly_rejects_noncanonical_request(owner):
    _journal, binding, intent, path = owner
    with pytest.raises(ValueError, match="request"):
        state._apply({"yaml_path": str(path), "binding": asdict(binding), "intent": intent})
