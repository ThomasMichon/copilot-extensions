"""Owned decision persistence only; no native recovery or credential effects."""

from dataclasses import asdict, fields, replace
import json

import pytest

from agent_remote_driver.headed.recovery import RecoveryDescriptor, RecoveryRecord, RecoveryStep
from agent_worktrees import config as cfg, launch_execution, launch_recovery_store, tracking
from agent_worktrees import launch_execution_permit as permits, tracking_write
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
    monkeypatch.setattr(permits.locks, "read_lock", lambda *_: None)
    monkeypatch.setattr(permits.time, "time", lambda: 100)
    correlation = launch_execution.Correlation("a" * 32, "b" * 32)
    execution = launch_execution.Execution(record.yaml_path, correlation)
    execution.append("execution-requested")
    binding = permits.ExecutionBinding(
        "c" * 32, correlation, execution.subject["operation_id"],
        OwnerIntent.capture(record.yaml_path),
        SourceSelection("target", "account", "recipient", "route", "controller-1"),
        "conversation", "environment", "boot", "scope", "incarnation",
    )
    decision = permits.ExecutionDecision("owned-start", "start", "approved", 200)
    return permits.WorktreeExecutionPermit(record.yaml_path, binding), binding, decision, record.yaml_path


def _inspect(owner, decision=None, **changes):
    journal, binding, original, _path = owner
    arguments = {
        "action": "start", "expected_revision": 1,
        "owned_decision": original if decision is None else decision,
        "current_source": binding.source, "source_state": "approved", **changes,
    }
    return journal.inspect(**arguments)


def test_missing_record_and_private_owned_decision_are_not_inferred(owner, monkeypatch):
    journal, _binding, decision, path = owner
    before = set(path.parent.rglob("*"))
    assert journal.load() is None
    assert _inspect(owner)["reason"] == "decision-unavailable"
    assert set(path.parent.rglob("*")) == before
    journal.record_decision(0, decision)
    monkeypatch.setattr(
        tracking_write, "dispatch", lambda *_a, **_k: pytest.fail("passive read dispatched"),
    )
    before = {item: item.read_bytes() for item in path.parent.rglob("*") if item.is_file()}
    assert _inspect(owner, owned_decision=None)["reason"] == "owned-decision-unavailable"
    assert _inspect(owner, owned_decision=asdict(decision))["reason"] == "owned-decision-unavailable"
    assert _inspect(owner)["classification"] == "candidate"
    assert {item: item.read_bytes() for item in path.parent.rglob("*") if item.is_file()} == before


def test_start_is_not_retirement_or_restore_and_restore_is_a_new_exact_decision(owner):
    journal, binding, start, path = owner
    first = journal.record_decision(0, start)
    for action in ("retire", "restore"):
        assert _inspect(owner, action=action)["reason"] == "action-not-authorized"
    restore = permits.ExecutionDecision("owned-restore", "restore", "approved", 300)
    second = journal.record_decision(1, restore)
    assert first.revision == 1 and second.revision == 2
    assert _inspect(owner, restore, action="restore")["reason"] == "permit-revision-changed"
    assert _inspect(owner, action="restore", expected_revision=2)["reason"] == "owned-decision-mismatch"
    assert _inspect(owner, restore, action="restore", expected_revision=3)["classification"] == "reconcile"
    result = _inspect(owner, restore, action="restore", expected_revision=2)
    assert result == {
        "classification": "candidate", "reason": "matching-owned-decision",
        "effects_wired": False, "native_recovery": "unqualified",
        "intent_contract": "unavailable", "reconciliation_required": True, "replay": "never",
    }
    reopened = permits.WorktreeExecutionPermit(path, binding)
    assert reopened.load() == second
    assert reopened.record_decision(1, restore) == second
    with pytest.raises(ValueError, match="revision changed"):
        reopened.record_decision(0, start)
    assert reopened.load() == second


@pytest.mark.parametrize("name", [item.name for item in fields(SourceSelection)])
def test_every_current_source_selection_is_exact(owner, name):
    journal, binding, start, _path = owner
    journal.record_decision(0, start)
    assert _inspect(owner, current_source=replace(
        binding.source, **{name: "changed"},
    ))["reason"] == "current-source-mismatch"


@pytest.mark.parametrize("changes,reason", [
    ({"source_state": "revoked"}, "current-source-unapproved"),
    ({"source_state": "unknown"}, "current-source-unapproved"),
    ({"source_state": True}, "current-source-unapproved"),
    ({"current_source": None}, "current-source-unavailable"),
    ({"expected_revision": True}, "invalid-permit-revision"),
    ({"action": "resume_intent"}, "unsupported-action"),
])
def test_missing_source_approval_or_invalid_request_is_not_a_candidate(owner, changes, reason):
    owner[0].record_decision(0, owner[2])
    assert _inspect(owner, **changes)["reason"] == reason


def test_expiry_and_revocation_order_real_decisions_not_credentials(owner):
    journal, _binding, start, _path = owner
    journal.record_decision(0, start)
    expired = permits.ExecutionDecision("expired-restore", "restore", "approved", 100)
    journal.record_decision(1, expired)
    assert _inspect(owner, expired, action="restore", expected_revision=2)["reason"] == "decision-expired"
    revoked = permits.ExecutionDecision("revoked-restore", "restore", "revoked", 200)
    journal.record_decision(2, revoked)
    assert _inspect(owner, expired, action="restore", expected_revision=2)["classification"] == "reconcile"
    assert _inspect(owner, expired, action="restore", expected_revision=3)["reason"] == "owned-decision-mismatch"
    assert _inspect(owner, revoked, action="restore", expected_revision=3)["reason"] == "decision-revoked"


@pytest.mark.parametrize("field,value", [
    ("permit_id", "d" * 32), ("conversation_ref", "changed"),
    ("environment_ref", "changed"), ("boot_ref", "changed"),
    ("scope_ref", "changed"), ("incarnation_ref", "changed"),
    ("source", SourceSelection("target", "account", "recipient", "route", "controller-2")),
    ("correlation", launch_execution.Correlation("a" * 32, "d" * 32)),
])
def test_same_original_launch_cannot_get_replacement_binding_or_permit(owner, field, value):
    journal, binding, start, path = owner
    journal.record_decision(0, start)
    changed = permits.WorktreeExecutionPermit(path, replace(binding, **{field: value}))
    before = permits._target(path).read_bytes()
    with pytest.raises(ValueError, match="binding changed"):
        changed.record_decision(0, start)
    assert permits._target(path).read_bytes() == before


def test_missing_or_changed_launch_correlation_cannot_publish(owner):
    journal, binding, start, path = owner
    wrong = replace(binding, launch_operation_id="d" * 32)
    with pytest.raises(FileNotFoundError):
        permits.WorktreeExecutionPermit(path, wrong).record_decision(0, start)
    wrong = replace(binding, correlation=replace(binding.correlation, resolution_id="d" * 32))
    with pytest.raises(ValueError, match="correlation changed"):
        permits.WorktreeExecutionPermit(path, wrong).record_decision(0, start)
    assert journal.load() is None


def test_competing_revision_cannot_rewrite_or_reuse_decision_reference(owner):
    journal, _binding, start, path = owner
    journal.record_decision(0, start)
    restore = permits.ExecutionDecision("owned-restore", "restore", "approved", 200)
    retired = permits.ExecutionDecision("owned-retire", "retire", "approved", 200)
    journal.record_decision(1, restore)
    before = permits._target(path).read_bytes()
    with pytest.raises(ValueError, match="revision changed"):
        journal.record_decision(1, retired)
    with pytest.raises(ValueError, match="append-only"):
        journal.record_decision(2, restore)
    assert permits._target(path).read_bytes() == before


@pytest.mark.parametrize("decision", [
    None, {"decision_ref": "credential-only"}, "source-lease", True,
])
def test_missing_or_untyped_authority_cannot_dispatch(owner, monkeypatch, decision):
    monkeypatch.setattr(
        tracking_write, "dispatch", lambda *_a, **_k: pytest.fail("invalid decision dispatched"),
    )
    with pytest.raises(ValueError, match="independently retained execution decision"):
        owner[0].record_decision(0, decision)


@pytest.mark.parametrize("revision", [True, -1, 0.0, "0"])
def test_invalid_revision_cannot_dispatch(owner, monkeypatch, revision):
    monkeypatch.setattr(
        tracking_write, "dispatch", lambda *_a, **_k: pytest.fail("invalid revision dispatched"),
    )
    with pytest.raises(ValueError, match="exact execution decision revision"):
        owner[0].record_decision(revision, owner[2])


def test_decision_budget_never_discards_history_or_starts_a_replacement(owner, monkeypatch):
    journal, binding, start, path = owner
    monkeypatch.setattr(permits, "_MAX_DECISIONS", 1)
    journal.record_decision(0, start)
    before = permits._target(path).read_bytes()
    with pytest.raises(ValueError, match="append-only"):
        journal.record_decision(1, permits.ExecutionDecision("next", "retire", "approved", 200))
    with pytest.raises(ValueError, match="binding changed"):
        permits.WorktreeExecutionPermit(path, replace(
            binding, permit_id="d" * 32,
        )).record_decision(0, start)
    assert permits._target(path).read_bytes() == before


def test_lost_commit_ack_is_reread_not_replayed_or_reset(owner, monkeypatch):
    journal, binding, start, path = owner
    endpoint = {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": [permits._VERB],
        "tracking_write_verb_versions": {permits._VERB: 1},
    }
    monkeypatch.setattr(permits.locks, "read_lock", lambda *_: endpoint)
    calls = []

    def lost(*_args, **kwargs):
        calls.append(True)
        assert permits._apply(kwargs["payload"]["args"]) == {"committed": True}
        raise tracking_write.AmbiguousWriteOutcome("response lost after durable commit")

    monkeypatch.setattr(tracking_write, "_send_tracking_write_request", lost)
    monkeypatch.setattr(tracking_write.wcs_client, "release", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tracking_write, "run_direct", lambda *_a, **_k: pytest.fail("ambiguous append replayed"),
    )
    with pytest.raises(tracking_write.AmbiguousWriteOutcome):
        journal.record_decision(0, start)
    reopened = permits.WorktreeExecutionPermit(path, binding)
    assert reopened.record_decision(0, start) == journal.load()
    assert journal.load().revision == 1 and calls == [True]


@pytest.mark.parametrize("mutation", ["owner", "closed", "nonce", "receiving"])
def test_owner_change_or_foreign_receiving_refusal_preserves_absence(owner, monkeypatch, mutation):
    journal, _binding, start, path = owner
    if mutation == "receiving":
        def refuse(_record):
            raise ValueError("foreign execution space")
        monkeypatch.setattr(permits, "require_project_record_mutation", refuse)
    else:
        record = tracking.load_record(path)
        if mutation == "owner":
            record.head_revision += 1
        elif mutation == "nonce":
            path.unlink()
            tracking.create_new_record(
                record.worktree_id, record.branch, record.worktree_path, record.repo,
                record.machine, record.platform, path.parent,
            )
        else:
            record.status = "finalizing"
        if mutation != "nonce":
            tracking.save_record(record)
    with pytest.raises(ValueError):
        journal.record_decision(0, start)
    assert not permits._target(path).exists()


@pytest.mark.parametrize("corruption", ["version", "duplicate", "binding", "history", "null"])
def test_corrupt_or_newer_contract_is_not_upgraded_or_repaired(owner, corruption):
    journal, _binding, start, path = owner
    journal.record_decision(0, start)
    target = permits._target(path)
    data = json.loads(target.read_bytes())
    if corruption == "version":
        data["version"] = 2
    elif corruption == "binding":
        data["permits"][0]["binding"]["owner_intent"]["creation_nonce"] = "changed"
    elif corruption == "history":
        data["permits"][0]["decisions"].append(data["permits"][0]["decisions"][0])
    elif corruption == "null":
        data = None
    raw = json.dumps(data)
    if corruption == "duplicate":
        raw = raw.replace('"version": 1', '"version": 1, "version": 1')
    target.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        journal.load()
    with pytest.raises(ValueError):
        journal.record_decision(1, permits.ExecutionDecision("restore", "restore", "approved", 200))
    assert target.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("point", ["flush", "publish", "after-publish"])
def test_failed_durable_publication_is_not_acknowledged(owner, monkeypatch, point):
    journal, _binding, start, path = owner
    publish = launch_recovery_store.checkout_durability.replace

    def fail(*args):
        if point == "after-publish":
            publish(*args)
        raise OSError("injected durable decision failure")

    monkeypatch.setattr(
        launch_recovery_store.os if point == "flush" else launch_recovery_store.checkout_durability,
        "fsync" if point == "flush" else "replace", fail,
    )
    with pytest.raises(OSError):
        journal.record_decision(0, start)
    current = journal.load()
    assert (current is not None) == (point == "after-publish")
    assert not list(permits._target(path).parent.glob("*.tmp"))


def test_prior_recovery_journal_unknown_effects_holds_and_revision_meanings_are_untouched(owner):
    journal, binding, start, path = owner
    intent = binding.owner_intent
    descriptor = RecoveryDescriptor(
        tracking.format_claim_ref(intent.execution_machine, intent.repo, intent.worktree_id),
        intent.worktree_id, "descriptor", "source", "lease", "conversation",
        "environment", "boot", "scope", "incarnation", 9,
    )
    old = launch_recovery_store.WorktreeRecoveryStore(path)
    retained = RecoveryRecord(
        "original-recovery", descriptor,
        (RecoveryStep("retire_intent", "original-intent", ("unknown-work",)),), ("legacy-hold",),
    )
    assert old.compare_and_swap(descriptor, 0, retained)
    before = (path.parent / path.stem / "launch-recovery.json").read_bytes()
    journal.record_decision(0, start)
    assert journal.load().revision == 1 and descriptor.lease_revision == 9
    assert old.load(descriptor) == retained
    assert (path.parent / path.stem / "launch-recovery.json").read_bytes() == before
