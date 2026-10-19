from __future__ import annotations

import json

import pytest

from agent_machines.installation_repair_admission import RepairAdmissionError
from agent_machines.installation_repair_restarts import RestartJournal

pytestmark = pytest.mark.guard


def test_restart_observation_never_creates_state(tmp_path):
    journal = RestartJournal(tmp_path)
    journal.load()
    assert journal.evidence()["pending"] == []
    assert not journal.root.exists()


def test_restart_confirmation_cannot_clear_newer_batch(tmp_path):
    journal = RestartJournal(tmp_path)
    with journal.locked():
        journal.require("fixture::example/base", "session", "a" * 64)
        old_digest = journal.pending_digest
        journal.require("fixture::example/other", "shell", "b" * 64)
        with pytest.raises(RepairAdmissionError, match="batch changed"):
            journal.confirm(old_digest)
    fresh = RestartJournal(tmp_path)
    fresh.load()
    assert len(fresh.pending) == 2
    with fresh.locked():
        fresh.confirm(fresh.pending_digest)
    observed = RestartJournal(tmp_path)
    observed.load()
    assert observed.pending == {}


def test_restart_mutations_require_exclusive_lock(tmp_path):
    journal = RestartJournal(tmp_path)
    with journal.locked():
        contender = RestartJournal(tmp_path)
        with pytest.raises(RepairAdmissionError, match="already active"):
            with contender.locked():
                pytest.fail("concurrent apply acquired the same lock")
        journal.require("fixture::example/base", "machine", "a" * 64)
    with RestartJournal(tmp_path).locked() as later:
        assert len(later.pending) == 1


@pytest.mark.parametrize("module,restart,plan", [
    ("x" * 257, "session", "a" * 64),
    ("valid::module", "none", "a" * 64),
    ("valid::module", "session", "not-a-digest"),
])
def test_restart_writer_refuses_unreadable_rows_without_poisoning_batch(
    tmp_path, module, restart, plan,
):
    journal = RestartJournal(tmp_path)
    with journal.locked():
        journal.require("fixture::valid/base", "shell", "b" * 64)
        previous = journal.path.read_bytes()
        with pytest.raises(RepairAdmissionError, match="invalid restart obligation"):
            journal.require(module, restart, plan)
        assert journal.path.read_bytes() == previous
        assert list(journal.pending) == ["fixture::valid/base"]
    reloaded = RestartJournal(tmp_path)
    reloaded.load()
    assert reloaded.pending == journal.pending


def test_restart_publication_failure_is_explicit_and_cleans_temporary_file(tmp_path, monkeypatch):
    journal = RestartJournal(tmp_path)
    with journal.locked():
        def fail_replace(*args):
            raise OSError("synthetic publication failure")
        monkeypatch.setattr("agent_machines.installation_repair_restarts.os.replace", fail_replace)
        with pytest.raises(OSError, match="publication failure"):
            journal.require("fixture::example/base", "session", "a" * 64)
    assert not list(journal.root.glob(".restarts-*"))
    assert not journal.path.exists()


@pytest.mark.parametrize(
    "damage", ["truncated", "wrong-version", "oversized", "bad-row", "duplicate"],
)
def test_restart_journal_corruption_is_never_an_empty_success(tmp_path, damage):
    journal = RestartJournal(tmp_path)
    with journal.locked():
        journal.require("fixture::example/base", "session", "a" * 64)
    value = json.loads(journal.path.read_text(encoding="utf-8"))
    if damage == "truncated":
        content = "{"
    elif damage == "oversized":
        content = " " * (2 * 1024 * 1024 + 1)
    elif damage == "duplicate":
        content = json.dumps(value)[:-1] + ', "pending": {}}'
    else:
        if damage == "wrong-version":
            value["version"] = True
        else:
            value["pending"]["fixture::example/base"]["restart"] = "none"
        content = json.dumps(value)
    journal.path.write_text(content, encoding="utf-8")
    with pytest.raises(RepairAdmissionError, match="cannot be read or validated"):
        RestartJournal(tmp_path).load()


def test_restart_journal_rejects_linked_state(tmp_path):
    journal = RestartJournal(tmp_path)
    journal.root.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    try:
        journal.path.symlink_to(target)
    except OSError:
        pytest.skip("native symlink privilege is unavailable")
    with pytest.raises(RepairAdmissionError, match="linked or reparse"):
        journal.load()
    assert target.read_text(encoding="utf-8") == "{}"


def test_restart_journal_rejects_dangling_state_root_before_creation(tmp_path):
    journal = RestartJournal(tmp_path)
    journal.root.parent.mkdir()
    target = tmp_path / "absent"
    try:
        journal.root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("native symlink privilege is unavailable")
    with pytest.raises(RepairAdmissionError, match="linked or reparse"):
        journal.load()
    assert not target.exists()
