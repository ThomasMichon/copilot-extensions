"""Foundation state shares execution custody and never turns interruption into success."""

import json
import os
import sqlite3
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from single_instance_lease.operation import OperationLock, OperationOwnershipError
from update_driver import (
    InterruptedError, RecoveryError, RequestSpec, StateError, TrustedRunner,
    drain, enqueue, ensure_worker, foundation_status, recover_after_restart,
    recover_foundation_after_restart, recovery_status, status, wait,
)
from update_driver import foundation_state as fs
from update_driver._directory import create_queue_directory

BOOT = "linux:11111111-1111-4111-8111-111111111111"
NEXT_BOOT = "linux:22222222-2222-4222-8222-222222222222"
DIGEST = "a" * 64


@pytest.fixture
def space(area, monkeypatch):
    root = area / "state"
    create_queue_directory(root)
    monkeypatch.setattr(fs.boot, "kernel_boot_identity", lambda: BOOT)
    return root


def custody(root):
    return OperationLock(root / "execution.lock")


def request(root):
    return enqueue(
        RequestSpec("foundation-fixture"),
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(root)),
        root=root,
    )


def document(operation):
    return {"schema": fs.SCHEMA, "version": fs.VERSION, **asdict(operation)}


def test_absent_native_status_and_recovery_never_create_state(area):
    root = area / "missing"
    assert foundation_status(root=root) is None
    assert recover_foundation_after_restart(root=root) is None
    assert status(root=root) == ()
    assert not root.exists()


@pytest.mark.parametrize("code,reason,state", [
    (0, None, "completed"), (7, "builder_failed", "failed"),
    (None, "builder_settlement_unknown", "interrupted"),
])
def test_native_outcomes_preserve_canonical_status_and_uncertainty(space, code, reason, state):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
        assert running.fenced and foundation_status(root=space) == running
        result = fs.finish(space, running, owned, exit_code=code, error=reason)
        assert result.state == state and result.exit_code == code and result.error == reason
        assert result.fenced is (state == "interrupted")
        assert foundation_status(root=space) == result
    assert not list(space.glob(".foundation-operation-*.tmp"))


def test_begin_requires_exact_live_execution_custody(space):
    with OperationLock(space / "other.lock") as other:
        with pytest.raises(fs.FoundationStateError, match="execution lock"):
            fs.begin(space, DIGEST, other)
    with pytest.raises(OperationOwnershipError):
        fs.begin(space, DIGEST, custody(space))
    assert foundation_status(root=space) is None


def test_duplicate_begin_does_not_interrupt_or_overwrite_its_live_operation(space):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
        before = (space / fs.FILENAME).read_bytes()
        with pytest.raises(fs.FoundationStateError, match="fenced"):
            fs.begin(space, "b" * 64, owned)
        assert foundation_status(root=space) == running
        assert (space / fs.FILENAME).read_bytes() == before
        with pytest.raises(fs.FoundationStateError, match="digest"):
            fs.begin(space, "invalid", owned)
        assert (space / fs.FILENAME).read_bytes() == before
        with pytest.raises(fs.FoundationStateError, match="active operation"):
            fs.finish(space, replace(running, source_hash="b" * 64), owned, exit_code=0)


def test_pending_work_waits_behind_live_native_custody_then_executes_once(space):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
        queued = request(space)
        assert queued.state == "pending"
        assert foundation_status(root=space).state == "running"
        fs.finish(space, running, owned, exit_code=0)
    executed = []
    assert drain(root=space, executor=lambda tx: executed.append(tx.request.id) or 7) == 0
    result = wait(queued.id, root=space, timeout=1)
    assert result.state == "completed" and result.exit_code == 7
    assert executed == [queued.id]


def test_native_owner_loss_fences_cold_acceptance_without_creating_sqlite(space):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
    with pytest.raises(InterruptedError, match="foundation"):
        request(space)
    interrupted = foundation_status(root=space)
    assert interrupted.operation_id == running.operation_id
    assert interrupted.state == "interrupted" and interrupted.exit_code is None
    assert interrupted.error == "owner_lost_custody" and interrupted.fenced
    assert not (space / "queue.sqlite3").exists()


def test_native_loss_remains_visible_to_waiters_and_never_spawns_another_owner(space, monkeypatch):
    queued = request(space)
    with custody(space) as owned:
        fs.begin(space, DIGEST, owned)
    with pytest.raises(InterruptedError, match="foundation"):
        wait(queued.id, root=space, timeout=1)
    assert status(queued.id, root=space).state == "pending"

    def forbidden(*args, **kwargs):
        pytest.fail("a fenced foundation must not spawn or execute installation")

    monkeypatch.setattr("agent_procutil.spawn_daemon", forbidden)
    with pytest.raises(InterruptedError, match="foundation"):
        ensure_worker([sys.executable, "-m", "update_driver", "worker"], root=space)
    with pytest.raises(InterruptedError, match="foundation"):
        drain(root=space, executor=forbidden)


def test_executor_cannot_report_success_after_leaving_uncertain_native_construction(space):
    queued = request(space)

    def executor(transaction):
        fs.begin(space, DIGEST, transaction._lock)
        return 0

    with pytest.raises(InterruptedError, match="foundation"):
        drain(root=space, executor=executor)
    assert status(queued.id, root=space).state == "interrupted"
    assert foundation_status(root=space).state == "interrupted"


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-sharing publication")
def test_status_reader_overlap_retries_terminal_publication_under_custody(space, monkeypatch):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
        with fs._open_read(space / fs.FILENAME) as reader:
            assert json.load(reader)["state"] == "running"
            original_move = fs._move_windows
            conflicts = []

            def overlapping_move(temporary, path):
                owned.assert_owned()
                try:
                    original_move(temporary, path)
                except OSError as error:
                    conflicts.append(error.winerror)
                    reader.close()
                    raise

            monkeypatch.setattr(fs, "_move_windows", overlapping_move)
            completed = fs.finish(space, running, owned, exit_code=0)
            assert foundation_status(root=space) == completed
            assert conflicts and set(conflicts) <= {5, 32, 33}
        assert completed.state == "completed" and completed.exit_code == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing conflict deadline")
def test_persistent_reader_conflict_remains_explicit_and_bounded(space, monkeypatch):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
        with fs._open_read(space / fs.FILENAME):
            ticks = iter([0, 3])
            monkeypatch.setattr(fs.time, "monotonic", lambda: next(ticks))
            with pytest.raises(fs.FoundationStateError, match="persistence"):
                fs.finish(space, running, owned, exit_code=0)
            assert foundation_status(root=space) == running


@pytest.mark.parametrize("state", ["running", "interrupted", "blocked"])
def test_foundation_admission_refuses_unsettled_queue_owner_loss(space, state):
    queued = request(space)
    with sqlite3.connect(space / "queue.sqlite3") as db:
        db.execute(
            "UPDATE requests SET state=?,started_at=1,boot_id=? WHERE id=?",
            (state, BOOT, queued.id),
        )
    with custody(space) as owned:
        with pytest.raises(fs.FoundationStateError, match="unsettled queue"):
            fs.begin(space, DIGEST, owned)
    assert foundation_status(root=space) is None
    assert status(queued.id, root=space).state == state


def test_only_executing_transaction_can_enclose_foundation_construction(space):
    queued = request(space)

    def executor(transaction):
        running = fs.begin(space, DIGEST, transaction._lock)
        fs.finish(space, running, transaction._lock, exit_code=0)
        return 0

    drain(root=space, executor=executor)
    assert status(queued.id, root=space).state == "completed"
    assert foundation_status(root=space).state == "completed"
    with sqlite3.connect(space / "queue.sqlite3") as db:
        db.execute(
            "UPDATE requests SET state='running',exit_code=NULL WHERE id=?", (queued.id,),
        )
    with custody(space) as owned:
        with pytest.raises(fs.FoundationStateError, match="unsettled queue"):
            fs.begin(space, DIGEST, owned)


def test_foundation_admission_allows_positively_settled_queue_failure(space):
    queued = request(space)
    with sqlite3.connect(space / "queue.sqlite3") as db:
        db.execute(
            """UPDATE requests SET state='interrupted',started_at=1,finished_at=2,
            boot_id=?,settled_boot_id=?,settled_at=3 WHERE id=?""",
            (BOOT, NEXT_BOOT, queued.id),
        )
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
        assert running.state == "running"


def test_foundation_admission_refuses_existing_unsupported_queue(space):
    with sqlite3.connect(space / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=999")
    with custody(space) as owned:
        with pytest.raises(StateError, match="unsupported"):
            fs.begin(space, DIGEST, owned)
    assert foundation_status(root=space) is None


def test_explicit_recovery_requires_new_native_boot_and_never_replays(space, monkeypatch):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
    before = (space / fs.FILENAME).read_bytes()
    assert recovery_status(root=space).error == "native_foundation_recovery_required"
    with pytest.raises(RecoveryError, match="foundation"):
        recover_after_restart(root=space)
    with pytest.raises(RecoveryError, match="different native kernel boot"):
        recover_foundation_after_restart(root=space)
    assert (space / fs.FILENAME).read_bytes() == before
    monkeypatch.setattr(fs.boot, "kernel_boot_identity", lambda: NEXT_BOOT)
    settled = recover_foundation_after_restart(root=space)
    assert settled.state == "interrupted" and settled.exit_code is None
    assert settled.operation_id == running.operation_id
    assert settled.boot_id == BOOT and settled.settled_boot_id == NEXT_BOOT
    assert not settled.fenced and not (space / "queue.sqlite3").exists()
    assert recover_foundation_after_restart(root=space) == settled
    with custody(space) as owned:
        newer = fs.begin(space, "b" * 64, owned)
        assert newer.operation_id != running.operation_id and newer.boot_id == NEXT_BOOT


def test_active_or_cross_platform_native_recovery_is_refused(space, monkeypatch):
    with custody(space) as owned:
        fs.begin(space, DIGEST, owned)
        before = (space / fs.FILENAME).read_bytes()
        monkeypatch.setattr(fs.boot, "kernel_boot_identity", lambda: NEXT_BOOT)
        with pytest.raises(RecoveryError, match="active"):
            recover_foundation_after_restart(root=space)
        assert (space / fs.FILENAME).read_bytes() == before
    monkeypatch.setattr(
        fs.boot, "kernel_boot_identity", lambda: NEXT_BOOT.replace("linux:", "windows:"),
    )
    with pytest.raises(RecoveryError, match="different native kernel boot"):
        recover_foundation_after_restart(root=space)
    assert (space / fs.FILENAME).read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("operation_id", "invalid"),
    ("source_hash", "a" * 63), ("boot_id", "linux:invalid"),
    ("state", []), ("state", "pending"), ("started_at", True),
    ("started_at", float("nan")), ("started_at", 10**500),
    ("exit_code", True), ("exit_code", 1 << 64),
    ("error", "unbounded or credential-shaped text"),
    ("settled_boot_id", NEXT_BOOT), ("settled_at", 1),
])
def test_malformed_native_state_blocks_reading_and_queue_acceptance(space, field, value):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
    data = document(running)
    data[field] = value
    (space / fs.FILENAME).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateError):
        foundation_status(root=space)
    with pytest.raises(StateError):
        request(space)
    with pytest.raises(RecoveryError):
        recover_foundation_after_restart(root=space)
    assert not (space / "queue.sqlite3").exists()


def test_existing_artifacts_never_turn_missing_progress_into_a_legacy_fallback(space):
    (space / fs.ARTIFACT_DIRECTORY).mkdir()
    with pytest.raises(StateError, match="progress missing"):
        foundation_status(root=space)
    with pytest.raises(StateError, match="progress missing"):
        request(space)
    with pytest.raises(RecoveryError, match="progress missing"):
        recover_foundation_after_restart(root=space)
    assert not (space / "queue.sqlite3").exists()


@pytest.mark.parametrize("raw", [
    b"{", b'{"version":1,"version":1}', b"\xff", b"[" * 2000,
    b"x" * (fs.MAX_BYTES + 1),
], ids=["broken-json", "duplicate-field", "invalid-utf8", "excess-nesting", "oversized"])
def test_native_encoding_and_size_failures_are_explicit_state_errors(space, raw):
    (space / fs.FILENAME).write_bytes(raw)
    with pytest.raises(StateError):
        foundation_status(root=space)
    with pytest.raises(StateError):
        request(space)
    assert not (space / "queue.sqlite3").exists()


def test_failed_publication_cannot_report_acceptance_or_delete_an_unowned_temporary(space, monkeypatch):
    def refuse(*args):
        raise OSError("synthetic publication failure")

    with custody(space) as owned:
        monkeypatch.setattr(fs, "_publish", refuse)
        with pytest.raises(fs.FoundationStateError, match="persistence"):
            fs.begin(space, DIGEST, owned)
        assert not (space / fs.FILENAME).exists()
        assert not list(space.glob(".foundation-operation-*.tmp"))
        collision = space / (".foundation-operation-" + "a" * 32 + ".tmp")
        collision.write_text("unowned", encoding="ascii")
        monkeypatch.setattr(fs.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
        with pytest.raises(fs.FoundationStateError, match="persistence"):
            fs.begin(space, DIGEST, owned)
        assert collision.read_text() == "unowned"


def test_native_recovery_refuses_substitution_during_boot_observation(space, monkeypatch):
    with custody(space) as owned:
        running = fs.begin(space, DIGEST, owned)
    changed = document(replace(running, operation_id="f" * 32))

    def substituted():
        (space / fs.FILENAME).write_text(json.dumps(changed), encoding="utf-8")
        return NEXT_BOOT

    monkeypatch.setattr(fs.boot, "kernel_boot_identity", substituted)
    with pytest.raises(RecoveryError, match="state changed"):
        recover_foundation_after_restart(root=space)
    assert foundation_status(root=space).settled_at is None
