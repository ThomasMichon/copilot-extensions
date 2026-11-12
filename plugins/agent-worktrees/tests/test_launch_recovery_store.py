"""Actor-free original-owner persistence; no native recovery effects."""

from dataclasses import fields, replace
import json
import os

import pytest

from agent_remote_driver.headed.recovery import (
    RecoveryConflict, RecoveryDescriptor, RecoveryRecord, RecoveryStep, record_recovery_step,
)
from agent_remote_driver.headed.recovery_codec import load_recovery_status
from agent_worktrees import config as cfg, launch_recovery_store as state, tracking, tracking_write


@pytest.fixture
def journal(tmp_path, monkeypatch):
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
    monkeypatch.setattr(state.locks, "read_lock", lambda *_: None)
    store = state.WorktreeRecoveryStore(record.yaml_path)
    descriptor = RecoveryDescriptor(
        tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id),
        record.worktree_id, "descriptor-1", "source-owner", "source-1", "conversation",
        "environment", "boot", "scope", "incarnation", 1,
    )
    return store, descriptor, record.yaml_path


def _initial(descriptor, operation="operation"):
    return RecoveryRecord(
        operation, descriptor, (RecoveryStep("retire_intent", "original-intent", ("pending-work",)),),
        ("legacy-hold",),
    )


def _target(path):
    return path.parent / path.stem / "launch-recovery.json"


def test_missing_status_is_read_only_and_unknown(journal):
    store, descriptor, path = journal
    before = set(path.parent.rglob("*"))
    assert store.load(descriptor) is None
    status = load_recovery_status(store, descriptor)
    assert status["phase"] == "unknown" and status["effects_wired"] is False
    assert set(path.parent.rglob("*")) == before


def test_reopen_all_phases_keeps_unknown_effects_and_holds(journal):
    store, descriptor, path = journal
    steps = [
        RecoveryStep("retire_intent", "original-intent", ("pending-work",)),
        RecoveryStep("settled", "whole-scope-settlement"),
        RecoveryStep("resume_intent", "owner-resume", authority_revision=2),
        RecoveryStep("observation_restored", "same-conversation", restored_incarnation="new-native"),
    ]
    for revision, step in enumerate(steps):
        record = record_recovery_step(
            store, descriptor, "operation", revision, step, legacy_holds=("legacy-hold",),
        )
        store = state.WorktreeRecoveryStore(path)
        assert store.load(descriptor) == record
    assert record_recovery_step(store, descriptor, "operation", 0, steps[0]) == record
    status = load_recovery_status(store, descriptor)
    assert status["unknown_effects"] == ["pending-work"]
    assert status["legacy_holds"] == ["legacy-hold"]
    assert status["native_recovery"] == "unqualified" and status["effects_wired"] is False
    assert status["reconciliation_required"] is True and status["replay"] == "never"


@pytest.mark.parametrize("field", [field.name for field in fields(RecoveryDescriptor)])
def test_every_descriptor_field_is_exact(journal, field):
    store, descriptor, path = journal
    original = _initial(descriptor)
    assert store.compare_and_swap(descriptor, 0, original)
    changed = replace(descriptor, **{field: 2 if field == "lease_revision" else "different"})
    before = _target(path).read_bytes()
    if field in {"owner_ref", "worktree_ref"}:
        with pytest.raises(ValueError, match="worktree owner"):
            store.load(changed)
        with pytest.raises(ValueError, match="worktree owner"):
            store.compare_and_swap(changed, 0, _initial(changed, "another"))
    else:
        assert store.load(changed) is None
        assert not store.compare_and_swap(changed, 0, _initial(changed))
        assert _target(path).read_bytes() == before
        assert store.compare_and_swap(changed, 0, _initial(changed, "another"))
        assert store.load(descriptor) == original


@pytest.mark.parametrize("conflict", [
    "stale", "operation", "history", "hold-clear", "hold-reorder", "skip",
])
def test_cas_cannot_rewrite_or_skip_original_transition(journal, conflict):
    store, descriptor, path = journal
    original = replace(_initial(descriptor), legacy_holds=("first", "second"))
    assert store.compare_and_swap(descriptor, 0, original)
    proposed = replace(
        original, steps=(*original.steps, RecoveryStep("settled", "whole-scope")),
    )
    expected = 1
    if conflict == "stale":
        expected = 0
    elif conflict == "operation":
        proposed = replace(proposed, operation_id="replacement")
    elif conflict == "history":
        proposed = replace(proposed, steps=(
            RecoveryStep("retire_intent", "rewritten"), proposed.steps[-1],
        ))
    elif conflict == "hold-clear":
        proposed = replace(proposed, legacy_holds=())
    elif conflict == "hold-reorder":
        proposed = replace(proposed, legacy_holds=("second", "first"))
    elif conflict == "skip":
        proposed = replace(proposed, steps=(
            *proposed.steps, RecoveryStep("resume_intent", "resume", authority_revision=2),
        ))
    before = _target(path).read_bytes()
    assert not store.compare_and_swap(descriptor, expected, proposed)
    assert _target(path).read_bytes() == before


def test_store_must_begin_at_revision_zero_with_only_intent(journal):
    store, descriptor, _ = journal
    initial = _initial(descriptor)
    assert not store.compare_and_swap(descriptor, 1, initial)
    assert not store.compare_and_swap(descriptor, 0, replace(
        initial, steps=(*initial.steps, RecoveryStep("settled", "scope")),
    ))
    assert store.load(descriptor) is None


@pytest.mark.parametrize("revision", [True, -1, 0.0, "0"])
def test_invalid_revision_never_dispatches(journal, monkeypatch, revision):
    store, descriptor, _ = journal
    monkeypatch.setattr(
        tracking_write, "dispatch", lambda *_a, **_k: pytest.fail("invalid CAS was dispatched"),
    )
    with pytest.raises(ValueError, match="exact store revision"):
        store.compare_and_swap(descriptor, revision, _initial(descriptor))


def test_full_store_preserves_all_original_operations(journal, monkeypatch):
    store, descriptor, path = journal
    monkeypatch.setattr(state, "_MAX_RECORDS", 1)
    assert store.compare_and_swap(descriptor, 0, _initial(descriptor))
    changed = replace(descriptor, boot_ref="new-boot")
    before = _target(path).read_bytes()
    with pytest.raises(ValueError, match="store is full"):
        store.compare_and_swap(changed, 0, _initial(changed, "another-operation"))
    assert _target(path).read_bytes() == before


def test_unaware_resident_is_never_sent_the_new_verb(journal, monkeypatch):
    store, descriptor, _ = journal
    monkeypatch.setattr(state.locks, "read_lock", lambda *_: {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": ["launch_seed_stage"],
    })
    monkeypatch.setattr(
        tracking_write, "_send_tracking_write_request",
        lambda *_a, **_k: pytest.fail("unaware resident received recovery CAS"),
    )
    assert store.compare_and_swap(descriptor, 0, _initial(descriptor))


def test_additive_holds_are_retained(journal):
    store, descriptor, _ = journal
    initial = _initial(descriptor)
    assert store.compare_and_swap(descriptor, 0, initial)
    next_record = replace(
        initial, steps=(*initial.steps, RecoveryStep("settled", "scope", ("other-unknown",))),
        legacy_holds=(*initial.legacy_holds, "another-hold"),
    )
    assert store.compare_and_swap(descriptor, 1, next_record)
    assert store.load(descriptor) == next_record
    assert next_record.unknown_effects == ("pending-work", "other-unknown")


def test_lost_ack_requires_reread_not_direct_fallback(journal, monkeypatch):
    store, descriptor, _ = journal
    original = _initial(descriptor)
    endpoint = {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": [state._VERB], "tracking_write_verb_versions": {state._VERB: 1},
    }
    monkeypatch.setattr(state.locks, "read_lock", lambda *_: endpoint)
    calls = []

    def sent(*_args, **kwargs):
        calls.append(True)
        state._apply(kwargs["payload"]["args"])
        raise tracking_write.AmbiguousWriteOutcome("response lost after commit")

    monkeypatch.setattr(tracking_write, "_send_tracking_write_request", sent)
    monkeypatch.setattr(tracking_write.wcs_client, "release", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tracking_write, "run_direct", lambda *_a, **_k: pytest.fail("replayed ambiguous write"),
    )
    with pytest.raises(tracking_write.AmbiguousWriteOutcome):
        store.compare_and_swap(descriptor, 0, original)
    assert calls == [True] and store.load(descriptor) == original
    assert record_recovery_step(
        store, descriptor, "operation", 0, original.steps[0],
        legacy_holds=original.legacy_holds,
    ) == original
    assert calls == [True]


@pytest.mark.parametrize("point", ["flush", "publish", "after-publish"])
def test_commit_failure_never_acknowledges_success(journal, monkeypatch, point):
    store, descriptor, path = journal
    original = _initial(descriptor)
    publish = state.checkout_durability.replace

    def fail(*args, **kwargs):
        if point == "after-publish":
            publish(*args, **kwargs)
        raise OSError("injected durable commit failure")

    monkeypatch.setattr(
        state.os if point == "flush" else state.checkout_durability,
        "fsync" if point == "flush" else "replace", fail,
    )
    with pytest.raises(OSError):
        store.compare_and_swap(descriptor, 0, original)
    assert store.load(descriptor) == (original if point == "after-publish" else None)
    assert not list(_target(path).parent.glob("*.tmp"))


@pytest.mark.parametrize("corruption", ["duplicate", "version", "nonce", "oversize", "record", "op-id"])
def test_corrupt_store_is_not_repaired_or_truncated(journal, corruption):
    store, descriptor, path = journal
    assert store.compare_and_swap(descriptor, 0, _initial(descriptor))
    target = _target(path)
    data = json.loads(target.read_bytes())
    if corruption == "duplicate":
        raw = target.read_text(encoding="utf-8").replace('"version":1', '"version":1,"version":1')
    elif corruption == "oversize":
        raw = " " * (state._MAX_BYTES + 1)
    else:
        if corruption == "version":
            data["version"] = True
        elif corruption == "nonce":
            data["creation_nonce"] = "replacement"
        elif corruption == "record":
            data["records"][0] = "{}"
        else:
            data["records"].append(data["records"][0])
        raw = json.dumps(data)
    target.write_text(raw, encoding="utf-8")
    before = target.read_bytes()
    with pytest.raises(ValueError):
        store.load(descriptor)
    with pytest.raises(ValueError):
        store.compare_and_swap(descriptor, 1, _initial(descriptor))
    assert target.read_bytes() == before


def test_recreated_record_cannot_adopt_original_journal(journal):
    store, descriptor, path = journal
    assert store.compare_and_swap(descriptor, 0, _initial(descriptor))
    record = tracking.load_record(path)
    path.unlink()
    tracking.create_new_record(
        record.worktree_id, record.branch, record.worktree_path, record.repo,
        record.machine, record.platform, path.parent,
    )
    with pytest.raises(ValueError, match="incarnation changed"):
        store.load(descriptor)
    with pytest.raises(ValueError, match="incarnation changed"):
        store.compare_and_swap(descriptor, 0, _initial(descriptor))
    with pytest.raises(ValueError, match="binding or format"):
        state.WorktreeRecoveryStore(path).load(descriptor)


def test_noncanonical_record_copy_is_not_a_writer(journal, tmp_path):
    _store, _descriptor, path = journal
    copied = tmp_path / path.name
    copied.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="canonical worktree"):
        state.WorktreeRecoveryStore(copied)
    assert not (tmp_path / path.stem).exists()


def test_receiving_owner_refusal_preserves_absence(journal, monkeypatch):
    store, descriptor, path = journal

    def refuse(_record):
        raise ValueError("foreign execution space")

    monkeypatch.setattr(state, "require_project_record_mutation", refuse)
    with pytest.raises(ValueError, match="foreign execution space"):
        store.compare_and_swap(descriptor, 0, _initial(descriptor))
    assert not _target(path).exists()


def test_hardlinked_journal_is_refused_without_write(journal, tmp_path):
    store, descriptor, path = journal
    assert store.compare_and_swap(descriptor, 0, _initial(descriptor))
    os.link(_target(path), tmp_path / "retained-link")
    with pytest.raises(ValueError, match="unshared file"):
        store.load(descriptor)


def test_missing_settlement_remains_rejected_by_real_reducer(journal):
    store, descriptor, _ = journal
    assert store.compare_and_swap(descriptor, 0, _initial(descriptor))
    with pytest.raises(RecoveryConflict):
        record_recovery_step(
            store, descriptor, "operation", 1,
            RecoveryStep("resume_intent", "premature", authority_revision=2),
        )
    assert store.load(descriptor).revision == 1
