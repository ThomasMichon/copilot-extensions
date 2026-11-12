"""Passive admission candidates over the actual owner store and launch journal."""

import asyncio
from dataclasses import fields, replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_remote_driver.headed.recovery import (
    RecoveryDescriptor, RecoveryStep, record_recovery_step,
)
from agent_worktrees import config as cfg, launch_execution, launch_recovery_store, tracking
from agent_worktrees import launch_recovery_admission as admission


class Lease:
    def __init__(self, remaining=30):
        self.remaining = remaining
        self.calls = 0

    def remaining_seconds(self):
        self.calls += 1
        return self.remaining


class NativeOwner:
    def __init__(self):
        self.native_scope = object()
        self.settled = None

    def scope_settled(self, native_scope):
        assert native_scope is self.native_scope
        return self.settled


@pytest.fixture
def owner(tmp_path, monkeypatch):
    directory = cfg.project_dir("demo")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "test",
        "repos": {"demo": {"anchor": str(tmp_path), "default_branch": "dev"}},
    }), encoding="utf-8")
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "checkout"), "demo", "test",
        "windows", directory / "worktrees",
    )
    monkeypatch.setattr(launch_recovery_store.locks, "read_lock", lambda *_: None)
    correlation = launch_execution.Correlation("a" * 32, "b" * 32)
    execution = launch_execution.Execution(record.yaml_path, correlation)
    execution.append("execution-requested")
    descriptor = RecoveryDescriptor(
        tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id),
        record.worktree_id, "descriptor-1", "source-owner", "source-1", "conversation",
        "environment", "boot", "scope", "incarnation", 1,
    )
    binding = admission.RecoveryBinding(
        descriptor, correlation, execution.subject["operation_id"],
        admission.SourceSelection("target", "account", "recipient", "route", "controller-1"),
        admission.OwnerIntent.capture(record.yaml_path), NativeOwner(),
    )
    store = launch_recovery_store.WorktreeRecoveryStore(record.yaml_path)
    step = RecoveryStep("retire_intent", "intent", ("unknown-external-work",))
    record_recovery_step(store, descriptor, "recovery-operation", 0, step)
    inspector = admission.RecoveryAdmissionInspector(
        record.yaml_path, binding, "recovery-operation",
    )
    observation = admission.RecoveryObservation(binding, "approved", 1, Lease())
    return inspector, observation, store, record.yaml_path


def inspect(owner, **changes):
    inspector, observation, _, _ = owner
    arguments = {
        "descriptor": observation.binding.descriptor,
        "operation_id": "recovery-operation", "phase": "retire_intent",
        "expected_revision": 1, "observation": observation, **changes,
    }
    return inspector.inspect(**arguments)


def test_matching_owned_input_is_only_a_passive_candidate(owner, monkeypatch):
    inspector, observation, store, path = owner
    before = {item: item.read_bytes() for item in path.parent.rglob("*") if item.is_file()}
    monkeypatch.setattr(
        launch_recovery_store.tracking_write, "dispatch",
        lambda *_a, **_k: pytest.fail("inspection dispatched a write"),
    )
    result = inspect(owner)
    assert result == {
        "classification": "candidate", "reason": "matching-owned-inputs",
        "effects_wired": False, "native_recovery": "unqualified",
        "reconciliation_required": True, "replay": "never",
        "unknown_effects": ["unknown-external-work"], "legacy_holds": [],
    }
    assert inspect(owner) == result
    reopened = launch_recovery_store.WorktreeRecoveryStore(path)
    assert reopened.load(observation.binding.descriptor) == store.load(observation.binding.descriptor)
    assert {item: item.read_bytes() for item in path.parent.rglob("*") if item.is_file()} == before
    assert observation.lease.calls == 2
    assert "native_owner" not in repr(observation)
    assert "lease=" not in repr(observation)


@pytest.mark.parametrize("name", [item.name for item in fields(RecoveryDescriptor)])
def test_every_request_descriptor_field_must_match_retained_input(owner, name):
    original = owner[1].binding.descriptor
    changed = replace(original, **{name: 2 if name == "lease_revision" else "different"})
    assert inspect(owner, descriptor=changed)["reason"] == "original-binding-mismatch"
    assert owner[1].lease.calls == 0


@pytest.mark.parametrize("name", [item.name for item in fields(admission.SourceSelection)])
def test_current_source_selection_change_is_not_original_binding(owner, name):
    observation = owner[1]
    changed = replace(
        observation.binding, source=replace(observation.binding.source, **{name: "changed"}),
    )
    assert inspect(owner, observation=replace(
        observation, binding=changed,
    ))["reason"] == "current-binding-mismatch"
    assert observation.lease.calls == 0


@pytest.mark.parametrize("change", ["native-owner", "descriptor", "correlation", "launch-operation"])
def test_current_owner_or_launch_cannot_be_substituted(owner, change):
    observation = owner[1]
    changes = {
        "native-owner": {"native_owner": object()},
        "descriptor": {"descriptor": replace(observation.binding.descriptor, scope_ref="another")},
        "correlation": {"correlation": launch_execution.Correlation("c" * 32, "b" * 32)},
        "launch-operation": {"launch_operation_id": "c" * 32},
    }
    changed = replace(observation.binding, **changes[change])
    assert inspect(owner, observation=replace(
        observation, binding=changed,
    ))["classification"] == "held"


@pytest.mark.parametrize("changes,classification", [
    ({"source_state": "revoked"}, "held"),
    ({"source_state": "unknown"}, "unknown"),
    ({"source_state": True}, "unknown"),
    ({"authority_revision": True}, "held"),
    ({"authority_revision": "a" * 32}, "held"),
    ({"authority_revision": 0}, "held"),
    ({"lease": None}, "unknown"),
])
def test_unapproved_or_opaque_authority_never_becomes_candidate(owner, changes, classification):
    assert inspect(owner, observation=replace(
        owner[1], **changes,
    ))["classification"] == classification


@pytest.mark.parametrize("remaining", [0, -1, True, float("inf"), float("nan"), 3601])
def test_lease_expiry_or_invalid_budget_refuses(owner, remaining):
    assert inspect(owner, observation=replace(
        owner[1], lease=Lease(remaining),
    ))["reason"] == "source-lease-unconfirmed"


def test_resume_requires_exact_new_authority_and_independent_owned_settlement(owner):
    _, observation, store, _ = owner
    descriptor = observation.binding.descriptor
    record_recovery_step(store, descriptor, "recovery-operation", 1, RecoveryStep("settled", "scope"))
    record_recovery_step(
        store, descriptor, "recovery-operation", 2,
        RecoveryStep("resume_intent", "resume", authority_revision=2),
    )
    assert inspect(owner)["classification"] == "reconcile"
    request = {"phase": "resume_intent", "expected_revision": 3}
    assert inspect(owner, **request)["reason"] == "authority-revision-mismatch"
    current = replace(observation, authority_revision=2)
    assert inspect(owner, observation=current, **request)["reason"] == "scope-settlement-unconfirmed"
    assert inspect(owner, observation=replace(
        current, scope_settled=True,
    ), **request)["reason"] == "scope-settlement-unconfirmed"
    current.binding.native_owner.settled = True
    assert inspect(owner, observation=replace(
        current, scope_settled=True,
    ), **request)["classification"] == "candidate"
    assert inspect(owner, observation=replace(
        current, authority_revision=3, scope_settled=True,
    ), **request)["reason"] == "authority-revision-mismatch"


@pytest.mark.parametrize("settled", [None, False, 1, "settled"])
def test_provider_status_must_confirm_the_original_scope(owner, settled):
    _, observation, store, _ = owner
    descriptor = observation.binding.descriptor
    record_recovery_step(store, descriptor, "recovery-operation", 1, RecoveryStep("settled", "scope"))
    record_recovery_step(
        store, descriptor, "recovery-operation", 2,
        RecoveryStep("resume_intent", "resume", authority_revision=2),
    )
    observation.binding.native_owner.settled = settled
    result = inspect(
        owner, phase="resume_intent", expected_revision=3,
        observation=replace(observation, authority_revision=2, scope_settled=True),
    )
    assert result["reason"] == "scope-settlement-unconfirmed"


@pytest.mark.parametrize("originally_missing", [False, True])
def test_native_scope_is_retained_before_inspector_construction_and_reopen(owner, originally_missing):
    _, observation, _, path = owner
    if originally_missing:
        observation.binding.native_owner.native_scope = None
        observation = replace(observation, binding=replace(observation.binding))
    original = observation.binding._native_scope
    observation.binding.native_owner.native_scope = object()
    assert observation.binding._native_scope is original
    for _ in range(2):
        reopened = admission.RecoveryAdmissionInspector(path, observation.binding, "recovery-operation")
        result = reopened.inspect(
            observation.binding.descriptor, "recovery-operation", "retire_intent", 1, observation,
        )
        assert result["reason"] == "native-scope-changed"
    current = replace(observation.binding)
    assert inspect(owner, observation=replace(
        observation, binding=current,
    ))["reason"] == "current-binding-mismatch"


def test_missing_provider_capability_preserves_inspection_but_not_settlement(owner):
    _, observation, store, path = owner
    binding = replace(observation.binding, native_owner=object())
    reopened = admission.RecoveryAdmissionInspector(path, binding, "recovery-operation")
    current = replace(observation, binding=binding)
    assert reopened.inspect(
        binding.descriptor, "recovery-operation", "retire_intent", 1, current,
    )["classification"] == "candidate"
    record_recovery_step(store, binding.descriptor, "recovery-operation", 1, RecoveryStep("settled", "scope"))
    record_recovery_step(
        store, binding.descriptor, "recovery-operation", 2,
        RecoveryStep("resume_intent", "resume", authority_revision=2),
    )
    result = reopened.inspect(
        binding.descriptor, "recovery-operation", "resume_intent", 3,
        replace(current, authority_revision=2, scope_settled=True),
    )
    assert result["reason"] == "scope-settlement-unconfirmed"


def test_scope_change_during_status_requires_reconciliation(owner):
    _, observation, store, _ = owner
    descriptor = observation.binding.descriptor
    record_recovery_step(store, descriptor, "recovery-operation", 1, RecoveryStep("settled", "scope"))
    record_recovery_step(
        store, descriptor, "recovery-operation", 2,
        RecoveryStep("resume_intent", "resume", authority_revision=2),
    )
    provider = observation.binding.native_owner

    def changed(_scope):
        provider.native_scope = object()
        return True

    provider.scope_settled = changed
    result = inspect(
        owner, phase="resume_intent", expected_revision=3,
        observation=replace(observation, authority_revision=2, scope_settled=True),
    )
    assert result["classification"] == "reconcile"
    assert result["reason"] == "native-scope-changed"


@pytest.mark.parametrize("cleanup", ["retained", "settled", "close-failed"])
def test_inspector_consumes_the_real_auth_child_passive_contract(owner, monkeypatch, cleanup):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_remote_driver.headed import auth_child

    async def inline(function, *args):
        return function(*args)

    monkeypatch.setattr(auth_child, "require_auth_custody", lambda: None)
    monkeypatch.setattr(auth_child.asyncio, "to_thread", inline)
    process = SimpleNamespace(pid=123, wait=Mock(), poll=Mock())
    job = SimpleNamespace(
        try_terminate_and_confirm_empty=Mock(return_value=True),
        terminate_and_wait=Mock(), close=Mock(),
    )
    monkeypatch.setattr(
        auth_child, "spawn_sync_in_kill_on_close_job", Mock(return_value=(process, job)),
    )
    provider = auth_child.AuthPaneChild()
    provider.launch(["fixture"])
    _, observation, store, path = owner
    binding = replace(observation.binding, native_owner=provider)
    reopened = admission.RecoveryAdmissionInspector(path, binding, "recovery-operation")
    record_recovery_step(store, binding.descriptor, "recovery-operation", 1, RecoveryStep("settled", "scope"))
    record_recovery_step(
        store, binding.descriptor, "recovery-operation", 2,
        RecoveryStep("resume_intent", "resume", authority_revision=2),
    )
    if cleanup == "settled":
        asyncio.run(provider.terminate())
    elif cleanup == "close-failed":
        job.close.side_effect = OSError("fixture cleanup failed")
        with pytest.raises(OwnedCleanupError):
            asyncio.run(provider.terminate())
    operations = (
        process.wait, process.poll, job.try_terminate_and_confirm_empty,
        job.terminate_and_wait, job.close,
    )
    for operation in operations:
        operation.reset_mock()
        operation.side_effect = AssertionError("inspection must remain passive")
    before = {item: item.read_bytes() for item in path.parent.rglob("*") if item.is_file()}
    for _ in range(2):
        result = reopened.inspect(
            binding.descriptor, "recovery-operation", "resume_intent", 3,
            replace(observation, binding=binding, authority_revision=2, scope_settled=True),
        )
        assert result["classification"] == ("candidate" if cleanup == "settled" else "unknown")
        assert result["effects_wired"] is False
        assert result["unknown_effects"] == ["unknown-external-work"]
    for operation in operations:
        operation.assert_not_called()
    assert {item: item.read_bytes() for item in path.parent.rglob("*") if item.is_file()} == before


def test_missing_or_different_intent_is_not_authority(owner):
    assert inspect(owner, operation_id="replacement")["classification"] == "held"
    assert inspect(owner, expected_revision=2)["classification"] == "reconcile"
    assert inspect(owner, observation=None)["classification"] == "unknown"
    _, _, _, path = owner
    (path.parent / path.stem / "launch-recovery.json").unlink()
    result = inspect(owner)
    assert result["reason"] == "intent-unavailable"
    assert result["unknown_effects"] is None


@pytest.mark.parametrize("changes", [
    {"expected_revision": True}, {"expected_revision": 0}, {"phase": []},
    {"phase": "observation_restored"},
])
def test_invalid_intent_request_is_passive_refusal(owner, changes):
    assert inspect(owner, **changes)["reason"] == "invalid-intent-request"
    assert owner[1].lease.calls == 0


@pytest.mark.parametrize("status", ["complete", "finalized", "orphaned"])
def test_new_inspector_cannot_reopen_closed_owner_intent(owner, status):
    _, observation, _, path = owner
    path.write_text(
        path.read_text(encoding="utf-8").replace("status: active", f"status: {status}"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="intent changed"):
        admission.RecoveryAdmissionInspector(path, observation.binding, "recovery-operation")
    with pytest.raises(ValueError, match="intent is closed"):
        admission.OwnerIntent.capture(path)


def test_same_operation_reopen_preserves_holds_without_dispatch(owner):
    _, observation, store, path = owner
    initial = store.load(observation.binding.descriptor)
    assert store.compare_and_swap(
        initial.descriptor, 1, replace(
            initial, steps=(*initial.steps, RecoveryStep("settled", "scope")),
            legacy_holds=("receiver-unconfirmed",),
        ),
    )
    record_recovery_step(
        store, initial.descriptor, initial.operation_id, 2,
        RecoveryStep("resume_intent", "resume", authority_revision=2),
        legacy_holds=("receiver-unconfirmed",),
    )
    reopened = admission.RecoveryAdmissionInspector(path, observation.binding, initial.operation_id)
    result = reopened.inspect(
        initial.descriptor, initial.operation_id, "resume_intent", 3,
        replace(observation, authority_revision=2, scope_settled=True),
    )
    assert result["classification"] == "held"
    assert result["legacy_holds"] == ["receiver-unconfirmed"]
    assert result["unknown_effects"] == ["unknown-external-work"]


@pytest.mark.parametrize("change", ["nonce", "status", "head-revision", "launch", "corrupt-store"])
def test_original_owner_state_or_launch_changes_remain_held_or_unknown(owner, change):
    _, observation, _, path = owner
    if change == "launch":
        binding = observation.binding
        target = launch_execution.launch_attempt._path(
            path.parent, binding.correlation.attempt_id, binding.launch_operation_id, execution=True,
        )
        value = json.loads(target.read_text(encoding="utf-8"))
        value["resolution_id"] = "c" * 32
        target.write_text(json.dumps(value), encoding="utf-8")
    elif change == "corrupt-store":
        (path.parent / path.stem / "launch-recovery.json").write_text("{", encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
        if change == "nonce":
            text = text.replace("creation_nonce:", "old_nonce:") + "\ncreation_nonce: changed\n"
        elif change == "status":
            text = text.replace("status: active", "status: complete")
        else:
            text += "\nhead_revision: 10\n"
        path.write_text(text, encoding="utf-8")
    assert inspect(owner)["classification"] in {"held", "unknown"}


def test_change_during_lease_observation_requires_reconciliation(owner):
    _, observation, store, _ = owner

    class ConcurrentLease:
        def remaining_seconds(self):
            record_recovery_step(
                store, observation.binding.descriptor, "recovery-operation", 1,
                RecoveryStep("settled", "concurrent-scope"),
            )
            return 30

    result = inspect(owner, observation=replace(observation, lease=ConcurrentLease()))
    assert result["classification"] == "reconcile"
    assert result["unknown_effects"] == ["unknown-external-work"]


def test_native_scope_change_during_lease_observation_requires_reconciliation(owner):
    class ChangedLease:
        def remaining_seconds(self):
            owner[1].binding.native_owner.native_scope = object()
            return 30

    result = inspect(owner, observation=replace(owner[1], lease=ChangedLease()))
    assert result["classification"] == "reconcile"


def test_unknown_source_response_is_static_and_does_not_retry(owner):
    class Unavailable:
        calls = 0

        def remaining_seconds(self):
            self.calls += 1
            raise RuntimeError("private-provider-response")

    lease = Unavailable()
    result = inspect(owner, observation=replace(owner[1], lease=lease))
    assert result["classification"] == "unknown"
    assert "private-provider-response" not in json.dumps(result)
    assert lease.calls == 1


@pytest.mark.parametrize("revision", ["head_revision", "lifecycle_revision"])
def test_original_intent_is_not_recaptured_before_construction_or_on_reopen(owner, revision):
    _, observation, _, path = owner
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n{revision}: 10\n", encoding="utf-8",
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="intent changed"):
            admission.RecoveryAdmissionInspector(path, observation.binding, "recovery-operation")
    assert inspect(owner)["reason"] == "owner-state-changed"


@pytest.mark.parametrize("name", ["execution_leg", "session_backend"])
@pytest.mark.parametrize("when", ["before-inspection", "during-lease"])
def test_opaque_provider_state_cannot_disappear_into_absence(owner, name, when):
    _, observation, _, path = owner

    def change():
        path.write_text(
            path.read_text(encoding="utf-8") + f"\n{name}:\n  version: 999\n",
            encoding="utf-8",
        )

    if when == "before-inspection":
        change()
        with pytest.raises(ValueError, match="Opaque provider state"):
            admission.OwnerIntent.capture(path)
        with pytest.raises(ValueError, match="Opaque provider state"):
            admission.RecoveryAdmissionInspector(path, observation.binding, "recovery-operation")
        result = inspect(owner)
    else:
        class ChangingLease:
            def remaining_seconds(self):
                change()
                return 30

        result = inspect(owner, observation=replace(observation, lease=ChangingLease()))
    assert result["classification"] == "unknown"
    assert result["reason"] == "owner-evidence-unavailable"
