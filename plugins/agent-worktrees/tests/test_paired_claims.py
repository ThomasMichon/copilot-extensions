"""A paired knowledge worktree remains an owned obligation until finalized."""

from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import obligations, tracking, worktree_creation


@pytest.fixture
def pair(tmp_path, monkeypatch):
    from agent_worktrees import config as cfg

    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / str(name))
    records = []
    for name in ("harness", "knowledge"):
        directory = tmp_path / name / "worktrees"
        directory.mkdir(parents=True)
        record = tracking.create_new_record(
            name, f"worktree/{name}", str(tmp_path / "checkouts" / name),
            name, "host", "windows", directory,
        )
        records.append(record)
    harness, knowledge = records
    for record, sibling, role in (
        (harness, knowledge, "harness"), (knowledge, harness, "knowledge"),
    ):
        record.pair_id = "pair"
        record.pair_role = role
        record.pair_kind = "worktree"
        record.pair_ref = f"host/{sibling.repo}/{sibling.worktree_id}"
        tracking.save_record(record)
    harness.resources.append(tracking.ResourceClaim(
        kind="worktree", ref=harness.pair_ref, state=obligations.ACTIVE,
    ))
    tracking.save_record(harness)
    return SimpleNamespace(harness=harness, knowledge=knowledge)


def test_pair_stamp_journals_the_knowledge_worktree(pair, monkeypatch):
    from agent_worktrees import knowledge_plugins

    harness = replace(pair.harness, resources=[])
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kwargs: {})
    stamp = {
        name: getattr(harness, name)
        for name in ("pair_id", "pair_role", "pair_ref", "pair_kind")
    }
    worktree_creation._stamp_and_compose_paired_knowledge(
        SimpleNamespace(), harness, harness.worktree_path, stamp,
    )
    saved = tracking.load_record(harness.yaml_path)
    claims = [claim for claim in saved.resources if claim.ref == harness.pair_ref]
    assert len(claims) == 1
    assert claims[0].kind == "worktree"
    assert claims[0].is_unsettled


@pytest.mark.parametrize("mutation", ["settle", "release", "overwrite", "bulk"])
def test_unfinished_pair_claim_cannot_be_cleared(pair, mutation):
    record = pair.harness
    before = record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="paired.*finaliz"):
        if mutation == "settle":
            tracking.settle_resource_claim(record, record.pair_ref)
        elif mutation == "release":
            tracking.release_resource_claim(record, record.pair_ref)
        elif mutation == "overwrite":
            tracking.add_resource_claim(record, tracking.ResourceClaim(
                kind="session", ref=record.pair_ref, state=obligations.AT_REST,
            ))
        else:
            tracking.release_all_resources(record)
    assert record.yaml_path.read_bytes() == before
    assert tracking.load_record(record.yaml_path).resources[0].is_unsettled


def test_gone_and_safe_sweep_does_not_abandon_an_unfinished_pair(pair):
    record = pair.harness
    assert tracking.sweep_abandoned_obligations(
        record, gone_of=lambda claim: True, safe_of=lambda claim: True,
    ) == []
    assert tracking.load_record(record.yaml_path).resources[0].is_unsettled


@pytest.mark.parametrize("mutation", ["remove", "settle", "unpair"])
def test_full_record_save_cannot_erase_unfinished_paired_responsibility(pair, mutation):
    record = pair.harness
    before = record.yaml_path.read_bytes()
    if mutation == "remove":
        record.resources = []
    elif mutation == "settle":
        record.resources[0].state = obligations.AT_REST
    else:
        record.pair_id = record.pair_role = record.pair_ref = record.pair_kind = None
    with pytest.raises(ValueError, match="paired.*finaliz"):
        tracking.save_record(record)
    assert record.yaml_path.read_bytes() == before


def test_finalized_reciprocal_knowledge_worktree_allows_settlement(pair):
    pair.knowledge.status = "finalized"
    tracking.save_record(pair.knowledge)
    settled = tracking.settle_resource_claim(pair.harness, pair.harness.pair_ref)
    assert settled is not None and settled.is_at_rest
    assert tracking.load_record(pair.harness.yaml_path).resources[0].is_at_rest


@pytest.mark.parametrize("state", ["active", "pushed", "finalizing", "complete", "orphaned"])
def test_only_a_finalized_sibling_can_settle_the_pair(pair, state):
    pair.knowledge.status = state
    tracking.save_record(pair.knowledge)
    with pytest.raises(ValueError, match="paired.*finaliz"):
        tracking.settle_resource_claim(pair.harness, pair.harness.pair_ref)


@pytest.mark.parametrize("field,value", [
    ("pair_id", "different"),
    ("pair_ref", "host/other/other"),
    ("pair_role", "harness"),
    ("pair_kind", "anchor"),
    ("repo", "other"),
    ("worktree_id", "other"),
    ("machine", "other"),
    ("platform", "linux"),
])
def test_a_foreign_or_nonreciprocal_finalized_record_is_not_completion(pair, field, value):
    sibling = pair.knowledge
    sibling.status = "finalized"
    setattr(sibling, field, value)
    tracking.save_record(sibling, pair.knowledge.yaml_path)
    with pytest.raises(ValueError, match="paired.*finaliz"):
        tracking.release_resource_claim(pair.harness, pair.harness.pair_ref)


@pytest.mark.parametrize("state", ["missing", "unreadable"])
def test_unknown_sibling_preserves_the_claim(pair, state):
    path = pair.knowledge.yaml_path
    if state == "missing":
        path.unlink()
    else:
        path.write_text("[invalid: yaml", encoding="utf-8")
    before = pair.harness.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="paired.*finaliz"):
        tracking.release_resource_claim(pair.harness, pair.harness.pair_ref)
    assert pair.harness.yaml_path.read_bytes() == before


@pytest.mark.parametrize("operation", ["release", "remove", "settle"])
def test_native_claim_mutators_return_a_structured_refusal(pair, monkeypatch, operation):
    from agent_worktrees import tracking_claim_write as writes

    monkeypatch.setattr(writes, "_authority_rejection", lambda record: None)
    before = pair.harness.yaml_path.read_bytes()
    arguments = {
        "worktree_id": pair.harness.worktree_id,
        "yaml_path": str(pair.harness.yaml_path),
        "ref": pair.harness.pair_ref,
        "remove": operation == "remove",
        "disposition": obligations.AT_REST,
    }
    response = (
        writes.apply_claim_settle(arguments) if operation == "settle"
        else writes.apply_claim_release(arguments)
    )
    assert response["error"] == "rejected"
    assert "must be finalized" in response["message"]
    assert pair.harness.yaml_path.read_bytes() == before


def test_abandon_is_not_a_bypass_for_paired_work(pair):
    from agent_worktrees import finalize

    assert not finalize._assert_obligations_settled(
        pair.harness, pair.harness.worktree_id,
        abandon=True, handoff_to="another-owner",
    )


def test_a_stale_full_record_writer_cannot_clear_after_sibling_reopens(pair, monkeypatch):
    pair.knowledge.status = "finalized"
    tracking.save_record(pair.knowledge)
    before = pair.harness.yaml_path.read_bytes()
    original_save = tracking.save_record

    def reopened_before_save(record, path=None, **kwargs):
        pair.knowledge.status = "active"
        original_save(pair.knowledge)
        original_save(record, path, **kwargs)

    monkeypatch.setattr(tracking, "save_record", reopened_before_save)
    with pytest.raises(ValueError, match="paired.*finaliz"):
        tracking.settle_resource_claim(pair.harness, pair.harness.pair_ref)
    assert pair.harness.yaml_path.read_bytes() == before


def test_legacy_missing_claim_reopens_a_false_finalized_harness(pair):
    from agent_worktrees import paired_claims

    pair.knowledge.status = "finalized"
    tracking.save_record(pair.knowledge)
    pair.harness.resources = []
    pair.harness.status = "finalized"
    tracking.save_record(pair.harness)
    pair.knowledge.status = "active"
    tracking.save_record(pair.knowledge)

    paired_claims.ensure_claim(pair.harness)
    saved = tracking.load_record(pair.harness.yaml_path)
    assert saved.status == "active"
    assert len(saved.resources) == 1
    assert saved.resources[0].ref == saved.pair_ref
    assert saved.resources[0].is_unsettled


def test_reconciliation_is_idempotent_and_preserves_other_claims(pair):
    from agent_worktrees import paired_claims

    tracking.add_resource_claim(pair.harness, tracking.ResourceClaim(
        kind="pr", ref="example/project#1", state=obligations.ACTIVE,
    ))
    paired_claims.ensure_claim(pair.harness)
    paired_claims.ensure_claim(pair.harness)
    assert len(pair.harness.resources) == 2
    assert tracking.release_resource_claim(pair.harness, "example/project#1") is not None
    assert pair.harness.resources[0].is_unsettled


def test_sibling_lifecycle_contention_preserves_claim_and_allows_retry(pair):
    pair.knowledge.status = "finalized"
    tracking.save_record(pair.knowledge)
    ready, release = threading.Event(), threading.Event()

    def hold_sibling():
        with tracking._RecordLock(pair.knowledge.yaml_path, require_sidecar=True):
            ready.set()
            assert release.wait(5)

    worker = threading.Thread(target=hold_sibling)
    worker.start()
    try:
        assert ready.wait(5)
        before = pair.harness.yaml_path.read_bytes()
        with pytest.raises(ValueError, match="paired.*finaliz"):
            tracking.settle_resource_claim(pair.harness, pair.harness.pair_ref)
        assert pair.harness.yaml_path.read_bytes() == before
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    record = tracking.load_record(pair.harness.yaml_path)
    assert tracking.settle_resource_claim(record, record.pair_ref).is_at_rest


@pytest.mark.parametrize("settled", [False, True])
def test_unchanged_claim_updates_do_not_probe_the_sibling(pair, monkeypatch, settled):
    from agent_worktrees import paired_claims

    if settled:
        pair.knowledge.status = "finalized"
        tracking.save_record(pair.knowledge)
        tracking.settle_resource_claim(pair.harness, pair.harness.pair_ref)
    monkeypatch.setattr(
        paired_claims, "sibling_finalized",
        lambda record: pytest.fail("ordinary ledger updates must not read the sibling"),
    )
    pair.harness.title = "An ordinary status update"
    tracking.save_record(pair.harness)


def test_reconciliation_preserves_the_existing_claim_note(pair):
    from agent_worktrees import paired_claims

    pair.harness.resources[0].note = "Retained ownership context"
    tracking.save_record(pair.harness)
    pair.knowledge.status = "finalized"
    tracking.save_record(pair.knowledge)
    paired_claims.ensure_claim(pair.harness)
    assert tracking.load_record(pair.harness.yaml_path).resources[0].note == (
        "Retained ownership context"
    )


def test_unowned_harness_has_no_parent_settlement_work(monkeypatch):
    from agent_worktrees import execution_spaces

    monkeypatch.setattr(
        execution_spaces, "require_record_mutation",
        lambda *args: pytest.fail("no owner means no parent mutation or authority discovery"),
    )
    execution_spaces.settle_parent_obligation(
        SimpleNamespace(owner_claim_ref=None, pair_role="harness", pair_kind="worktree"),
        SimpleNamespace(), "harness", output=SimpleNamespace(),
    )


@pytest.mark.parametrize("mutation", ["unpair", "finalizing", "finalized"])
def test_legacy_pair_without_claim_cannot_drop_responsibility(pair, mutation):
    path = pair.harness.yaml_path
    legacy = yaml.safe_load(path.read_text(encoding="utf-8"))
    legacy["resources"] = []
    path.write_text(yaml.safe_dump(legacy), encoding="utf-8")
    record = tracking.load_record(path)
    before = path.read_bytes()
    if mutation == "unpair":
        record.pair_id = record.pair_role = record.pair_ref = record.pair_kind = None
    else:
        record.status = mutation
    with pytest.raises(ValueError, match="paired.*finaliz"):
        tracking.save_record(record)
    assert path.read_bytes() == before


@pytest.mark.parametrize("role", ["harness", "knowledge"])
@pytest.mark.parametrize("field,value", [
    ("pair_id", "foreign"), ("pair_ref", "host/other/other"),
    ("pair_role", "other"), ("pair_kind", "anchor"), ("repo", "other"),
    ("worktree_id", "other"), ("machine", "other"), ("platform", "linux"),
])
def test_prune_requires_reciprocal_finalized_identity_in_both_directions(
    pair, role, field, value,
):
    from agent_worktrees import prune

    record = getattr(pair, role)
    sibling = pair.knowledge if role == "harness" else pair.harness
    document = yaml.safe_load(sibling.yaml_path.read_text(encoding="utf-8"))
    document["status"] = "finalized"
    sibling.yaml_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert prune.default_paired_sibling_final(record) is True
    document[field] = value
    sibling.yaml_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert prune.default_paired_sibling_final(record) is None


@pytest.mark.parametrize("state", ["active", "complete", "finalizing"])
def test_clean_legacy_dispatch_pair_requires_explicit_sibling_finalization(pair, state):
    from agent_worktrees import terminal_conclusion

    pair.knowledge.status = state
    assert terminal_conclusion._sibling_preservation_reason(
        pair.knowledge, pair.harness,
    ) == "pair-sibling-not-finalized"


def test_missing_claim_cannot_be_reconciled_settled_after_sibling_reopens(pair, monkeypatch):
    from agent_worktrees import paired_claims

    pair.knowledge.status = "finalized"
    tracking.save_record(pair.knowledge)
    pair.harness.resources = []
    tracking.save_record(pair.harness)
    before = pair.harness.yaml_path.read_bytes()
    save = tracking.save_record

    def reopen_before_save(record, path=None, **kwargs):
        if record.repo == pair.harness.repo:
            pair.knowledge.status = "active"
            save(pair.knowledge)
        return save(record, path, **kwargs)

    monkeypatch.setattr(tracking, "save_record", reopen_before_save)
    with pytest.raises(ValueError, match="paired.*finaliz"):
        paired_claims.ensure_claim(pair.harness)
    assert pair.harness.yaml_path.read_bytes() == before


@pytest.mark.parametrize("state", [None, obligations.ACTIVE])
def test_missing_or_new_active_claim_preserves_no_probe_fast_path(pair, monkeypatch, state):
    from agent_worktrees import paired_claims

    document = yaml.safe_load(pair.harness.yaml_path.read_text(encoding="utf-8"))
    document["resources"] = []
    pair.harness.yaml_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    record = tracking.load_record(pair.harness.yaml_path)
    if state:
        record.resources = [tracking.ResourceClaim(kind="worktree", ref=record.pair_ref, state=state)]
    monkeypatch.setattr(
        paired_claims, "sibling_finalized",
        lambda record: pytest.fail("unchanged missing and new active claims need no finalized probe"),
    )
    tracking.save_record(record)


@pytest.mark.parametrize("status", ["active", "finalized"])
def test_missing_checkout_legacy_pair_retirement_preserves_refused_ledger(pair, status):
    from pathlib import Path

    path = pair.harness.yaml_path
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["resources"] = []
    document["status"] = status
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    record = tracking.load_record(path)
    assert not Path(record.worktree_path).exists()
    before = path.read_bytes(), pair.knowledge.yaml_path.read_bytes()
    assert tracking.retire_record(record, path.parent) is False
    assert (path.read_bytes(), pair.knowledge.yaml_path.read_bytes()) == before


@pytest.mark.parametrize("unrelated,state", [
    (False, obligations.AT_REST), (False, obligations.ACTIVE), (True, obligations.AT_REST),
])
def test_dispatch_admission_exempts_only_exact_verified_settled_pair(pair, unrelated, state):
    from agent_worktrees import terminal_conclusion

    record = pair.harness
    record.resources[0].state = state
    if unrelated:
        record.resources.append(tracking.ResourceClaim(
            kind="worktree", ref="host/other/other", state=obligations.AT_REST,
        ))
    reason = terminal_conclusion._preservation_reason(
        record, policy=terminal_conclusion.DISPATCH_ATTEMPT_POLICY, pair_clear=True,
    )
    assert reason == ("outstanding-obligations" if unrelated or state == obligations.ACTIVE else None)


@pytest.mark.parametrize("kind", ["bridge", "system"])
@pytest.mark.parametrize("status", ["complete", "completed"])
@pytest.mark.parametrize("previous", ["missing", obligations.RELEASED])
@pytest.mark.parametrize("sibling_final", [False, True])
def test_managed_completion_reconciles_only_the_durable_pair(
    pair, kind, status, previous, sibling_final,
):
    from agent_worktrees import paired_claims

    if sibling_final:
        tracking.update_status(pair.knowledge, "finalized")
    path = pair.harness.yaml_path
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(kind=kind, status=status)
    if previous == "missing":
        document["resources"] = []
    else:
        document["resources"][0]["state"] = previous
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    record = tracking.load_record(path)
    unrelated = tracking.ResourceClaim(kind="worktree", ref="host/other/other")
    with pytest.raises(ValueError, match="frozen"):
        tracking.add_resource_claim(record, unrelated)
    paired_claims.ensure_claim(record)
    saved = tracking.load_record(path)
    claim, = saved.resources
    assert claim.ref == saved.pair_ref
    if sibling_final:
        assert saved.status == status
        assert claim.state == (obligations.RELEASED if previous == obligations.RELEASED else obligations.AT_REST)
        with pytest.raises(ValueError, match="frozen"):
            tracking.add_resource_claim(saved, unrelated)
    else:
        assert saved.status == "active"
        assert claim.state == obligations.ACTIVE


@pytest.mark.parametrize("protected", ["handoff", "producer", "opaque"])
def test_dedicated_pair_reconciliation_preserves_existing_protections(pair, protected):
    from agent_worktrees import paired_claims

    tracking.update_status(pair.knowledge, "finalized")
    record = pair.harness
    record.kind, record.status = "bridge", "complete"
    claim, = record.resources
    if protected == "handoff":
        claim.handoff_bundle = "reserved-bundle"
    elif protected == "producer":
        claim.producer_present = True
    else:
        claim.metadata_opaque = True
    before = record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="handoff|producer"):
        paired_claims.ensure_claim(record)
    assert record.yaml_path.read_bytes() == before
    assert claim.state == obligations.ACTIVE


def test_first_pair_stamp_rechecks_a_sibling_that_reopens_before_save(pair, monkeypatch):
    from agent_worktrees import knowledge_plugins

    stamp = {name: getattr(pair.harness, name) for name in (
        "pair_id", "pair_role", "pair_ref", "pair_kind",
    )}
    path = pair.harness.yaml_path
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name in stamp:
        document.pop(name, None)
    document["resources"] = []
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    harness = tracking.load_record(path)
    tracking.update_status(pair.knowledge, "finalized")
    before = path.read_bytes()
    save = tracking.save_record

    def reopen_before_first_stamp(record, path=None, **kwargs):
        if record.repo == harness.repo:
            pair.knowledge.status = "active"
            save(pair.knowledge)
        return save(record, path, **kwargs)

    monkeypatch.setattr(tracking, "save_record", reopen_before_first_stamp)
    monkeypatch.setattr(
        knowledge_plugins, "compose_from_pair",
        lambda **kwargs: pytest.fail("an uncommitted pair must not reach composition"),
    )
    with pytest.raises(ValueError, match="paired.*finaliz"):
        worktree_creation._stamp_and_compose_paired_knowledge(
            SimpleNamespace(), harness, harness.worktree_path, stamp,
        )
    assert path.read_bytes() == before
    assert not tracking.load_record(path).is_paired


def test_first_settled_pair_save_holds_the_sibling_lock(pair):
    stamp = {name: getattr(pair.harness, name) for name in (
        "pair_id", "pair_role", "pair_ref", "pair_kind",
    )}
    path = pair.harness.yaml_path
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name in stamp:
        document.pop(name, None)
    document["resources"] = []
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    proposed = tracking.load_record(path)
    for name, value in stamp.items():
        setattr(proposed, name, value)
    proposed.resources = [tracking.ResourceClaim(
        kind="worktree", ref=proposed.pair_ref, state=obligations.AT_REST,
    )]
    tracking.update_status(pair.knowledge, "finalized")
    ready, release = threading.Event(), threading.Event()

    def hold_sibling():
        with tracking._RecordLock(pair.knowledge.yaml_path, require_sidecar=True):
            ready.set()
            assert release.wait(5)

    worker = threading.Thread(target=hold_sibling)
    worker.start()
    before = path.read_bytes()
    try:
        assert ready.wait(5)
        with pytest.raises(ValueError, match="paired.*finaliz"):
            tracking.save_record(proposed)
        assert path.read_bytes() == before
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    tracking.save_record(proposed)
    claim, = tracking.load_record(path).resources
    assert claim.is_at_rest


@pytest.mark.parametrize("reopen", [False, True])
def test_retargeted_settled_pair_requires_the_new_sibling_to_stay_finalized(pair, monkeypatch, reopen):
    from agent_worktrees import knowledge_plugins

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kwargs: {})
    tracking.update_status(pair.knowledge, "finalized")
    tracking.settle_resource_claim(pair.harness, pair.harness.pair_ref)
    replacement = tracking.create_new_record(
        "replacement", "worktree/replacement", pair.knowledge.worktree_path + "-replacement",
        pair.knowledge.repo, "host", "windows", pair.knowledge.yaml_path.parent,
    )
    replacement.pair_id, replacement.pair_role, replacement.pair_kind = (
        "replacement-pair", "knowledge", "worktree",
    )
    replacement.pair_ref = pair.knowledge.pair_ref
    tracking.update_status(replacement, "finalized")
    harness = tracking.load_record(pair.harness.yaml_path)
    before = harness.yaml_path.read_bytes()
    stamp = {
        "pair_id": replacement.pair_id, "pair_role": "harness", "pair_kind": "worktree",
        "pair_ref": f"host/{replacement.repo}/{replacement.worktree_id}",
    }
    save = tracking.save_record

    def reopen_replacement_before_save(record, path=None, **kwargs):
        if record.repo == harness.repo and reopen:
            replacement.status = "active"
            save(replacement)
        return save(record, path, **kwargs)

    monkeypatch.setattr(tracking, "save_record", reopen_replacement_before_save)
    if reopen:
        with pytest.raises(ValueError, match="paired.*finaliz"):
            worktree_creation._stamp_and_compose_paired_knowledge(
                SimpleNamespace(), harness, harness.worktree_path, stamp,
            )
        assert harness.yaml_path.read_bytes() == before
    else:
        worktree_creation._stamp_and_compose_paired_knowledge(
            SimpleNamespace(), harness, harness.worktree_path, stamp,
        )
        saved = tracking.load_record(harness.yaml_path)
        assert saved.pair_ref == stamp["pair_ref"]
        claim, = [claim for claim in saved.resources if claim.ref == saved.pair_ref]
        assert claim.is_at_rest


def test_rollback_of_invalidated_finalized_pair_restores_active_responsibility(pair):
    from agent_worktrees import finalize, paired_claims

    tracking.update_status(pair.knowledge, "finalized")
    tracking.release_resource_claim(pair.harness, pair.harness.pair_ref)
    tracking.update_status(pair.harness, "finalized")
    tracking.update_status(pair.harness, "finalizing")
    tracking.update_status(pair.knowledge, "active")
    finalize._rollback_finalizing_freeze(pair.harness.yaml_path, "finalized", pair.harness.worktree_id)
    saved = tracking.load_record(pair.harness.yaml_path)
    assert saved.status == "active"
    claim, = saved.resources
    assert claim.state == obligations.ACTIVE
    paired_claims.ensure_claim(saved)
    assert tracking.load_record(saved.yaml_path).status == "active"


def test_rollback_unfreezes_before_deferred_pair_custody_reconciliation(pair, monkeypatch):
    from agent_worktrees import finalize, paired_claims, tracking_claims

    tracking.update_status(pair.knowledge, "finalized")
    tracking.release_resource_claim(pair.harness, pair.harness.pair_ref)
    tracking.update_status(pair.harness, "finalized")
    tracking.update_status(pair.harness, "finalizing")
    tracking.update_status(pair.knowledge, "active")
    reservation = tracking_claims.claim_handoff_reservation
    monkeypatch.setattr(
        tracking_claims, "claim_handoff_reservation", lambda *args: "reserved-bundle",
    )
    finalize._rollback_finalizing_freeze(pair.harness.yaml_path, "finalized", pair.harness.worktree_id)
    saved = tracking.load_record(pair.harness.yaml_path)
    assert saved.status == "active"
    claim, = saved.resources
    assert claim.state == obligations.RELEASED
    monkeypatch.setattr(tracking_claims, "claim_handoff_reservation", reservation)
    paired_claims.ensure_claim(saved)
    claim, = tracking.load_record(saved.yaml_path).resources
    assert claim.state == obligations.ACTIVE
