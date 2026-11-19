from pathlib import Path
import sqlite3
from contextlib import closing

import pytest

from agent_dispatch.proposal_recovery import ProposalIntake


def test_rebuild_from_proposal_records_without_queue_or_payloads(tmp_path: Path) -> None:
    payload = "portable fixture payload\n" * 100
    a = ProposalIntake.create(tmp_path / "intake", repo="https://example.invalid/org/repo",
                              title="original proposal", payload=payload)
    b = ProposalIntake.recover(a.records, tmp_path / "B")
    fields = {"decision": "accept revised input"}
    receipt = b.steer("answer", fields)
    assert receipt["acceptance"]["task_id"] == a.identity
    assert receipt["wake_status"] == "not_requested"
    # Neither queue DB, WAL, nor either generation's payload blobs follow the records.
    (tmp_path / "intake" / "generation").rename(tmp_path / "unavailable-A")
    (tmp_path / "B").rename(tmp_path / "unavailable-B")
    back = ProposalIntake.recover(a.records, tmp_path / "A-return")
    assert back.identity == a.identity
    task = back.queue.get(a.identity)
    assert task is not None and task.status == "proposed" and task.title == "original proposal"
    assert back.queue.read_payload(task) == payload
    assert back.queue.steer_receipt(a.identity, "answer") == receipt
    assert back.steer("answer", fields) == receipt
    assert len(back.queue.steer_log(a.identity)) == 1
    with pytest.raises(ValueError, match="different fields"):
        back.steer("answer", {"decision": "different"})
    with pytest.raises(ValueError, match="superseded"):
        b.steer("late", fields)
    assert back.steer("second", fields)["acceptance"]["steer_id"] != receipt["acceptance"]["steer_id"]
    with closing(sqlite3.connect(back.queue.db_path)) as db:
        assert db.execute("SELECT count(*) FROM wake_outbox").fetchone() == (0,)


def test_failed_journal_commit_is_not_acknowledged(tmp_path: Path) -> None:
    a = ProposalIntake.create(tmp_path / "intake", repo="https://example.invalid/org/repo",
                              title="original proposal", payload="data")
    with closing(sqlite3.connect(a.records)) as db:
        db.execute("CREATE TRIGGER refused BEFORE INSERT ON steer_v1 BEGIN SELECT RAISE(FAIL,'full'); END")
    with pytest.raises(sqlite3.IntegrityError, match="full"):
        a.steer("never-accepted", {"answer": "no"})
    with pytest.raises(ValueError, match="unavailable"):
        a.steer("next", {"answer": "no"})
    fresh = ProposalIntake.recover(a.records, tmp_path / "recovered")
    assert fresh.queue.steer_log(a.identity) == []


def test_relative_paths_survive_caller_directory_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    intake = ProposalIntake.create(Path("lane"), title="proposal", repo="https://example.invalid/org/repo",
                                   payload="fixture")
    receipt = intake.steer("first", {"decision": "yes"})
    after = ProposalIntake.recover(Path("lane/proposal.records"), Path("after"))
    monkeypatch.chdir(tmp_path.parent)
    assert after.steer("first", {"decision": "yes"}) == receipt


@pytest.mark.parametrize("reason", ["compatible-import", "unavailable-cache", "incompatible-format"])
def test_offered_generation_is_used_only_by_matching_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          reason: str) -> None:
    from agent_dispatch import proposal_transfer as transfer

    a = ProposalIntake.create(tmp_path / "lane", title="task", repo="https://example.invalid/o/r", payload="data")
    receipt = a.steer("first", {"extension": {"unknown": [1, 2, 3]}})
    previous = Path(a.queue.db_path).parent
    transfer.offer(a)
    if reason != "compatible-import":
        previous.rename(tmp_path / "withheld")
    if reason == "incompatible-format":
        monkeypatch.setattr(transfer, "CACHE_FORMAT", 2)
    target = tmp_path / "incoming"
    with pytest.raises(ValueError, match="current offer"):
        transfer.receive(a.records, target, outgoing=tmp_path / "unrelated")
    b, selected = transfer.receive(a.records, target, outgoing=previous)
    assert selected == reason and b.queue.steer_receipt(a.identity, "first") == receipt
    with pytest.raises(ValueError):
        a.steer("late", {"decision": "no"})
    assert b.steer("first", {"extension": {"unknown": [1, 2, 3]}}) == receipt
    with pytest.raises(ValueError, match="current offer"):
        transfer.receive(a.records, tmp_path / "duplicate", outgoing=previous)
