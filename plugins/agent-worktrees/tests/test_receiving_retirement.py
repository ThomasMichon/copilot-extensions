"""Retirement never discards the last durable receiving conclusion evidence."""

from __future__ import annotations

import sqlite3

import pytest
import yaml

from agent_worktrees import creation_requests, paired_claims, tracking
from agent_worktrees import receiving_allocation as receiving, receiving_creation_journal as journal
from agent_worktrees.worktree_allocation_receiver import receive
from test_receiving_allocation import receiver as receiver
from test_worktree_allocation_receiver import request
from _cleanup_revalidation_helpers import make_record


@pytest.mark.parametrize("old_writer", [False, True])
def test_unpaired_retirement_defers_deletion_until_original_conclusion_is_durable(receiver, monkeypatch, old_writer):
    config, admission, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    if old_writer:
        raw = yaml.safe_load(path.read_text())
        del raw["receiving_allocation"]
        path.write_text(yaml.safe_dump(raw))
    record = tracking.load_record(path)
    original = journal.note_conclusion

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(journal, "note_conclusion", locked)
    assert tracking.retire_record(record, records) is False
    assert path.exists()
    terminal = tracking.load_record(path)
    assert terminal.status == "archived" and terminal.receiving_allocation_present
    assert creation_requests.inspect(config, admission.key)["status"] == "complete"
    monkeypatch.setattr(journal, "note_conclusion", original)
    assert tracking.retire_record(terminal, records) is True
    path.unlink()
    result = receive("inspect", request(receiver, created["identity"]), config)
    assert result["status"] == "archived" and result["identity"] == created["identity"]


@pytest.mark.parametrize("initiator", ["receiving", "companion"])
def test_paired_hard_delete_waits_for_both_receiving_conclusions(receiver, monkeypatch, tmp_path, initiator):
    config, admission, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    record = tracking.load_record(path)
    sibling = make_record(tmp_path, wt_id="companion", status="finalized")
    sibling.repo = config.repo_name
    sibling.reaped_at = "2026-01-01T00:00:00"
    record.pair_id = sibling.pair_id = "original-pair"
    record.pair_role, sibling.pair_role = "harness", "knowledge"
    record.pair_kind = sibling.pair_kind = "worktree"
    record.pair_ref = f"{record.execution_machine}/{config.repo_name}/{sibling.worktree_id}"
    sibling.pair_ref = receiving.record_identity(record).ref
    sibling_path = records / "companion.yaml"
    tracking.save_record(record, path)
    tracking.save_record(sibling, sibling_path)
    monkeypatch.setattr(tracking.cfg, "project_dir", lambda *a, **k: records.parent)
    monkeypatch.setattr(paired_claims, "require_clearable", lambda *a, **k: None)
    monkeypatch.setattr(
        paired_claims, "reciprocal_sibling",
        lambda candidate: tracking.load_record(sibling_path if candidate.worktree_id == record.worktree_id else path),
    )
    if initiator == "companion":
        # Model interruption after terminal YAML persistence but before its
        # conclusion hook, then retirement initiated from the other half.
        raw = yaml.safe_load(path.read_text())
        raw["status"], raw["reaped_at"] = "finalized", "2026-01-01T00:00:00"
        path.write_text(yaml.safe_dump(raw))
    original = journal.note_conclusion

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(journal, "note_conclusion", locked)
    retiring_path = path if initiator == "receiving" else sibling_path
    assert tracking.retire_record(tracking.load_record(retiring_path), records) is False
    assert path.exists() and sibling_path.exists()
    assert creation_requests.inspect(config, admission.key)["status"] == "complete"
    monkeypatch.setattr(journal, "note_conclusion", original)
    assert tracking.retire_record(tracking.load_record(retiring_path), records) is True
    assert not path.exists() and not sibling_path.exists()
    result = receive("inspect", request(receiver, created["identity"]), config)
    assert result["status"] == "finalized" and result["identity"] == created["identity"]
