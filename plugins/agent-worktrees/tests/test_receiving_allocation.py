"""First-save foreign ownership is local-journal-backed, not acknowledgement-gated."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent_worktrees import creation_requests, execution_spaces, tracking
from agent_worktrees import receiving_allocation as receiving, worktree_creation
from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals


@pytest.fixture
def receiver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = _create_config(tmp_path)
    records = creation_requests.cfg.project_dir(config.repo_name) / "worktrees"
    monkeypatch.setattr(creation_requests.cfg, "tracking_dir", lambda name=None: records)
    monkeypatch.setattr(creation_requests.cfg, "load_project_config", lambda *a, **k: config)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    context = execution_spaces.current_execution_context(config)
    admission = receiving.ReceivingAllocation.from_fields({
        "origin": "remote/parent/origin", "request_id": "original", "token": "a" * 32,
        "target": {
            "machine": context.execution_machine, "project": config.repo_name,
            "options": {"no_pair": True},
        },
    })
    return config, admission, records


def create(receiver: tuple) -> tuple[dict, tracking.WorktreeRecord]:
    config, admission, records = receiver
    result = worktree_creation._create_worktree_core(
        config, owner_ref=admission.origin, receiving_allocation=admission,
        creation_key=admission.key, no_pair=True, build_launch_plan=False, identity_mode="tuple-v1",
    )
    return result, tracking.load_record(records / f"{result['worktree']['id']}.yaml")


def test_real_core_journals_nonce_before_git_and_creator_on_first_record_save(receiver, monkeypatch):
    config, admission, records = receiver
    original = worktree_creation.git_ops.create_worktree
    seen = []

    def effect(anchor: str, path: str, *args: object, **kwargs: object) -> None:
        row = creation_requests.inspect(config, admission.key)
        assert row["status"] == "pending"
        assert row["worktree"]["creation_nonce"]
        assert not list(records.glob("*.yaml"))
        seen.append(row["worktree"]["creation_nonce"])
        original(anchor, path, *args, **kwargs)

    monkeypatch.setattr(worktree_creation.git_ops, "create_worktree", effect)
    result, record = create(receiver)
    assert "launch" not in result and record.sessions == []
    assert record.owner_ref == admission.origin
    assert record.receiving_allocation["phase"] == "created"
    assert record.creation_nonce == seen[0]
    assert record.receiving_allocation["identity"] == receiving.record_identity(record).to_dict()
    assert creation_requests.inspect(config, admission.key)["status"] == "complete"
    execution_spaces.require_record_mutation(record, config)


def test_unlinked_created_child_allows_normal_local_mutation_and_completed_replay(receiver):
    config, _, records = receiver
    first, record = create(receiver)
    record.title = "ordinary local update before origin claim acknowledgement"
    tracking.save_record(record, records / f"{record.worktree_id}.yaml")
    execution_spaces.require_record_mutation(record, config)
    replayed, same = create(receiver)
    assert replayed["creation"]["replayed"] is True
    assert replayed["worktree"] == first["worktree"]
    assert same.receiving_allocation["phase"] == "created"
    assert len(list(records.glob("*.yaml"))) == 1


@pytest.mark.parametrize("change", [
    {"origin": "remote/parent/other"}, {"token": "b" * 32},
    {"target": {"machine": "different", "project": "demo-repo"}},
])
def test_changed_receiving_facts_never_allocate_replacement(receiver, change):
    config, admission, records = receiver
    create(receiver)
    changed = receiving.ReceivingAllocation.from_fields({**admission.to_fields(), **change})
    if "origin" in change:
        # A distinct creator is a distinct operation; test substitution under
        # the already-journaled original key rather than asking for a new one.
        with pytest.raises(ValueError, match="journaled owner"):
            worktree_creation._create_worktree_core(
                config, owner_ref=changed.origin, receiving_allocation=changed,
                creation_key=admission.key, no_pair=True, build_launch_plan=False,
            )
    else:
        with pytest.raises((ValueError, creation_requests.CreationRequestFailure)):
            worktree_creation._create_worktree_core(
                config, owner_ref=changed.origin, receiving_allocation=changed,
                creation_key=changed.key, no_pair=True, build_launch_plan=False,
            )
    assert len(list(records.glob("*.yaml"))) == 1


def test_generic_stale_writes_preserve_receipt_creator_nonce_and_acknowledgement(receiver):
    _, _, records = receiver
    _, record = create(receiver)
    path = records / f"{record.worktree_id}.yaml"
    stale = copy.deepcopy(record)
    record.receiving_allocation["phase"] = "linked"
    with receiving.transaction(path):
        tracking.save_record(record, path)
    stale.receiving_allocation = None
    stale.receiving_allocation_present = False
    stale.owner_ref = None
    stale.creation_nonce = "replacement"
    tracking.save_record(stale, path)
    current = tracking.load_record(path)
    assert current.receiving_allocation["phase"] == "linked"
    assert current.receiving_allocation["identity"]["creation_nonce"] == current.creation_nonce
    assert current.owner_ref == record.owner_ref
    with receiving.transaction(path), pytest.raises(ValueError, match="immutable"):
        record.receiving_allocation["phase"] = "created"
        tracking.save_record(record, path)


@pytest.mark.parametrize("damage", ["nonce", "origin", "request", "unknown", "null"])
def test_forged_or_damaged_receipt_never_grants_foreign_owner_mutation(receiver, damage):
    config, _, records = receiver
    _, record = create(receiver)
    path = records / f"{record.worktree_id}.yaml"
    raw = yaml.safe_load(path.read_text())
    if damage == "nonce":
        raw["creation_nonce"] = "replacement"
    elif damage == "origin":
        raw["owner_ref"] = "remote/parent/other"
        raw["receiving_allocation"]["origin"] = raw["owner_ref"]
    elif damage == "request":
        raw["receiving_allocation"]["request_id"] = "not-in-journal"
    elif damage == "unknown":
        raw["receiving_allocation"]["version"] = 99
    else:
        raw["receiving_allocation"] = None
    path.write_text(yaml.safe_dump(raw))
    changed = tracking.load_record(path)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="receiving allocation"):
        execution_spaces.require_record_mutation(changed, config)
    tracking.save_record(changed, path)
    assert tracking.load_record(path).receiving_allocation_present


def test_legacy_foreign_owner_guard_still_refuses_before_effects(receiver):
    config, admission, records = receiver
    with pytest.raises(RuntimeError, match="cross-space owner"):
        worktree_creation._create_worktree_core(
            config, owner_ref=admission.origin, no_pair=True,
            build_launch_plan=False, identity_mode="tuple-v1",
        )
    assert not list(records.glob("*.yaml"))


def test_receiving_admission_cannot_skip_original_journal_or_launch_during_create(receiver):
    config, admission, records = receiver
    for fields in ({}, {"creation_key": admission.key, "build_launch_plan": True}):
        with pytest.raises(ValueError, match="journaled owner"):
            worktree_creation._create_worktree_core(
                config, owner_ref=admission.origin, receiving_allocation=admission,
                no_pair=True, **fields,
            )
    assert not list(records.glob("*.yaml"))


def test_receiving_fields_reserve_closeout_capacity_before_accepting_request(receiver):
    _, admission, records = receiver
    fields = admission.to_fields()
    fields["target"]["options"]["name"] = "x" * 8000
    with pytest.raises(ValueError, match="lifecycle"):
        receiving.ReceivingAllocation.from_fields(fields)
    assert not list(records.glob("*.yaml"))


def test_oversized_escaped_receiving_identity_is_refused_before_journal_reservation_or_git(receiver, monkeypatch):
    config, admission, records = receiver
    root = Path(config.default_repo.worktree_root).joinpath(*(["\U0010ffff" * 40] * 9))
    monkeypatch.setitem(config.repos, config.repo_name, replace(config.default_repo, worktree_root=str(root)))
    monkeypatch.setattr(
        worktree_creation.git_ops, "create_worktree",
        lambda *args, **kwargs: pytest.fail("oversized child identity reached Git creation"),
    )
    with pytest.raises(ValueError, match="identity exceeds lifecycle"):
        create(receiver)
    row = creation_requests.inspect(config, admission.key)
    assert row is None or row["worktree"] is None
    assert not list(records.glob("*.yaml"))


def test_journal_backed_foreign_creator_is_not_resolved_in_receiving_registry(receiver, monkeypatch):
    config, admission, _ = receiver
    _, record = create(receiver)
    original = execution_spaces.require_owner_identity
    foreign, _, _ = receiving.allocation.qualified_ref(admission.origin)

    def local_identity(machine, config, **kwargs):
        if machine == foreign:
            raise execution_spaces.ExecutionSpaceError("foreign creator is not a receiving registry entry")
        return original(machine, config, **kwargs)

    monkeypatch.setattr(execution_spaces, "require_owner_identity", local_identity)
    execution_spaces.require_record_mutation(record, config)


def test_explicit_receiving_registry_does_not_need_foreign_creator_entry(receiver):
    config, admission, _ = receiver
    _, record = create(receiver)
    context = execution_spaces.current_execution_context(config)
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(yaml.safe_dump({
        "machines": {record.execution_machine: {
            "hostname": context.hostname, "physical_host": record.machine,
            "execution_platform": record.platform,
        }},
    }))
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        execution_spaces.require_owner_identity(admission.origin.split("/")[0], config)
    execution_spaces.require_record_mutation(record, config)
    record.receiving_allocation = None
    record.receiving_allocation_present = False
    # Missing receipt is still recoverable only from the same original journal.
    execution_spaces.require_record_mutation(record, config)
