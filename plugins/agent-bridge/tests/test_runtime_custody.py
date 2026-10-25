"""DB admission foundation, not native/SDK closure or restore qualification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import threading

import pytest

from agent_bridge.db import Database
from agent_bridge.runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer,
)


def installation(db: Database) -> RuntimeInstallation:
    return RuntimeInstallation("installation-a", "source-a", str(db.db_path.parent),
                               str(db.db_path.absolute()))


def session(db: Database, sid: str, admission: RuntimeAdmission | None = None) -> None:
    db.create_session(sid, sid, None, ".", "local", "idle", 1.0, runtime_admission=admission)


def enrolled(db: Database):
    owner = installation(db)
    epoch = db.initialize_runtime_epoch(owner)
    producer = RuntimeProducer("frontend-a", 101, "native-birth-a")
    admission = db.enroll_runtime_producer(owner, epoch.epoch, producer)
    return owner, producer, admission


def close(db: Database, owner: RuntimeInstallation, producer: RuntimeProducer, *, expected=None):
    return db.close_runtime_epoch(
        owner, expected or db.runtime_epoch(owner), transition_id="transition-a",
        target_assembly="assembly-a", predecessor=producer,
    )


def test_unqualified_ordinary_writes_do_not_enroll_or_close(tmp_db):
    session(tmp_db, "ordinary")
    tmp_db.create_turn("ordinary", 0, "original prompt", 1.0)
    report = tmp_db.runtime_custody_inventory(installation(tmp_db))
    assert report["epoch"] is None
    assert report["producers"] == []
    assert "installation-not-bound" in report["blockers"]
    assert report["restore_qualified"] is False and report["acknowledged"] is False
    tmp_db.delete_session("ordinary")
    assert tmp_db.get_session("ordinary") is None


def test_epoch_and_producer_persist_with_original_session_and_turn(tmp_db):
    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "original", admission)
    tmp_db.create_turn("original", 3, "original prompt", 1.0,
                       delivery_id="original-delivery", runtime_admission=admission)
    before = tmp_db.runtime_epoch(owner)
    report = tmp_db.runtime_custody_inventory(owner, expected=before)
    assert json.loads(report["rows"]["sessions"][0]["runtime_custody_json"]) == {
        "installation": owner.document(), "epoch": 1, "producer": admission.producer,
    }
    assert report["rows"]["turns"][0]["acquisition_delivery_id"] == "original-delivery"
    assert report["producers"][0]["producer_json"] == producer.document()
    state = close(tmp_db, owner, producer, expected=before)
    assert state.epoch == 2 and state.state == "closed"
    reopened = Database(tmp_db.db_path)
    try:
        assert reopened.runtime_epoch(owner) == state
        assert close(reopened, owner, producer, expected=before) == state
        with pytest.raises(RuntimeCustodyError, match="reinitialized"):
            reopened.initialize_runtime_epoch(owner)
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            reopened.enroll_runtime_producer(owner, 1, producer)
    finally:
        reopened.close()


def test_closure_refuses_another_identity_target_or_transition(tmp_db):
    owner, producer, _ = enrolled(tmp_db)
    observed = tmp_db.runtime_epoch(owner)
    close(tmp_db, owner, producer, expected=observed)
    with pytest.raises(RuntimeCustodyError, match="Another transition"):
        tmp_db.close_runtime_epoch(
            owner, observed, transition_id="transition-a",
            target_assembly="different-assembly", predecessor=producer,
        )
    with pytest.raises(RuntimeCustodyError, match="foreign"):
        tmp_db.runtime_epoch(replace(owner, source_fingerprint="foreign-source"))
    with pytest.raises(RuntimeCustodyError, match="another database"):
        tmp_db.runtime_epoch(replace(owner, database=str(tmp_db.db_path.parent / "other.db")))


def test_closure_requires_the_enrolled_original_native_observation(tmp_db):
    owner, producer, _ = enrolled(tmp_db)
    with pytest.raises(RuntimeCustodyError, match="enrolled producer"):
        close(tmp_db, owner, replace(producer, process_start="reused-pid"))
    assert tmp_db.runtime_epoch(owner).state == "open"


@pytest.mark.parametrize("associated", [False, True])
def test_closed_epoch_rejects_actual_session_and_turn_insertions(tmp_db, associated):
    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "existing")
    close(tmp_db, owner, producer)
    selected = admission if associated else None
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        session(tmp_db, "late", selected)
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        tmp_db.create_turn("existing", 0, "late", 2.0, runtime_admission=selected)
    assert tmp_db.get_session("late") is None
    assert tmp_db.get_turns("existing") == []
    assert tmp_db.runtime_custody_inventory(owner)["epoch"]["state"] == "closed"


def test_foreign_or_unenrolled_association_cannot_insert(tmp_db):
    owner, _, admission = enrolled(tmp_db)
    for bad in (replace(admission, producer="not-enrolled"),
                replace(admission, installation="foreign"), replace(admission, epoch=2)):
        with pytest.raises(sqlite3.IntegrityError, match="association_invalid"):
            session(tmp_db, "not-created", bad)
    assert tmp_db.get_session("not-created") is None
    assert tmp_db.runtime_epoch(owner).state == "open"


def test_turn_admission_refusal_does_not_consume_pending_queue(tmp_db):
    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "s")
    queue_id = tmp_db.enqueue_prompt("s", "queued", 1.0)
    close(tmp_db, owner, producer)
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        tmp_db.create_turn("s", 0, "queued", 2.0,
                           pending_prompt_id=queue_id, runtime_admission=admission)
    assert tmp_db.peek_pending_prompt("s")["id"] == queue_id
    assert tmp_db.get_turns("s") == []


def test_successful_queue_admission_keeps_association_in_same_transaction(tmp_db):
    owner, _, admission = enrolled(tmp_db)
    session(tmp_db, "s")
    queue_id = tmp_db.enqueue_prompt("s", "queued", 1.0)
    tmp_db.create_turn("s", 0, "queued", 2.0,
                       pending_prompt_id=queue_id, runtime_admission=admission)
    assert tmp_db.peek_pending_prompt("s") is None
    assert tmp_db.get_turn("s", 0)["runtime_custody_json"] == admission.document()
    assert tmp_db.runtime_custody_inventory(owner)["restore_qualified"] is False


def test_terminal_history_and_original_association_are_not_discarded(tmp_db):
    owner, _, admission = enrolled(tmp_db)
    session(tmp_db, "s", admission)
    tmp_db.create_turn("s", 0, "prompt", 1.0, runtime_admission=admission)
    tmp_db.update_turn("s", 0, completed_at=2.0)
    tmp_db.update_session_status("s", "ended", 2.0)
    report = tmp_db.runtime_custody_inventory(owner)
    assert len(report["rows"]["sessions"]) == len(report["rows"]["turns"]) == 1
    assert any("native-outcome-unqualified" in value for value in report["blockers"])
    assert tmp_db.gc_eligible_session_ids(["ended"], 3.0) == []
    with pytest.raises(RuntimeCustodyError, match="outcome reconciliation"):
        tmp_db.delete_session("s")
    with pytest.raises(sqlite3.IntegrityError, match="custody_retained"):
        tmp_db.execute_write("DELETE FROM turns WHERE session_id='s'")
    with pytest.raises(sqlite3.IntegrityError, match="association_immutable"):
        tmp_db.execute_write("UPDATE sessions SET runtime_custody_json=NULL WHERE id='s'")
    assert tmp_db.get_turn("s", 0) is not None


def test_empty_legacy_missing_and_unknown_cohorts_never_qualify(tmp_db):
    tmp_db.execute_write("DROP TABLE acquisition_attempts")
    report = tmp_db.runtime_custody_inventory(installation(tmp_db))
    assert report["rows"]["acquisition_attempts"] is None
    assert report["storage_inventory_complete"] is False
    assert report["unmediated_cohorts"]
    assert "missing-or-legacy-table:acquisition_attempts" in report["blockers"]
    tmp_db.execute_write("CREATE TABLE unrecognized_producer (id TEXT)")
    report = tmp_db.runtime_custody_inventory(installation(tmp_db))
    assert "unknown-table:unrecognized_producer" in report["blockers"]
    assert report["restore_qualified"] is False


def test_legacy_fields_are_unknown_not_empty(tmp_db):
    triggers = tmp_db.execute_read(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='sessions' "
        "AND name LIKE 'sessions_runtime_%'"
    )
    for row in triggers:
        tmp_db.execute_write(f"DROP TRIGGER {row['name']}")
    tmp_db.execute_write("ALTER TABLE sessions DROP COLUMN runtime_custody_json")
    report = tmp_db.runtime_custody_inventory(installation(tmp_db))
    assert report["rows"]["sessions"] is None
    assert "missing-or-legacy-table:sessions" in report["blockers"]


def test_inventory_revision_rejects_intervening_owner_write(tmp_db):
    owner, producer, admission = enrolled(tmp_db)
    observed = tmp_db.runtime_epoch(owner)
    session(tmp_db, "s", admission)
    with pytest.raises(RuntimeCustodyError, match="revision or epoch changed"):
        tmp_db.runtime_custody_inventory(owner, expected=observed)
    with pytest.raises(RuntimeCustodyError, match="changed before closure"):
        close(tmp_db, owner, producer, expected=observed)
    refreshed = tmp_db.runtime_epoch(owner)
    close(tmp_db, owner, producer, expected=refreshed)


@pytest.mark.parametrize("winner", ["admission", "closure"])
def test_real_sqlite_admission_and_close_have_one_serial_order(tmp_db, winner):
    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "s")
    other = Database(tmp_db.db_path)
    entered = threading.Event()
    release = threading.Event()

    def first():
        conn = tmp_db._get_conn()
        start = conn.total_changes
        timed_out = []

        def before_commit(statement):
            if statement == "COMMIT" and conn.total_changes > start and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    timed_out.append(True)

        conn.set_trace_callback(before_commit)
        try:
            if winner == "admission":
                tmp_db.create_turn("s", 0, "original", 1.0, runtime_admission=admission)
            else:
                close(tmp_db, owner, producer)
            assert not timed_out
        finally:
            conn.set_trace_callback(None)
            conn.close()
            tmp_db._local.conn = None

    def second():
        try:
            if winner == "admission":
                return close(other, owner, producer)
            return other.create_turn("s", 0, "late", 2.0, runtime_admission=admission)
        finally:
            conn = getattr(other._local, "conn", None)
            if conn is not None:
                conn.close()
                other._local.conn = None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(first)
            assert entered.wait(5)
            waiting = pool.submit(second)
            release.set()
            held.result(timeout=5)
            if winner == "admission":
                # A pre-lock snapshot may become stale; retry is an observation,
                # not another operation or an alternative transition identity.
                try:
                    waiting.result(timeout=5)
                except RuntimeCustodyError as exc:
                    assert "changed before closure" in str(exc)
                    close(other, owner, producer)
                assert len(other.get_turns("s")) == 1
            else:
                with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
                    waiting.result(timeout=5)
                assert other.get_turns("s") == []
        assert other.runtime_epoch(owner).state == "closed"
    finally:
        release.set()
        other.close()


def test_read_only_inspection_does_not_initialize_legacy_custody(tmp_db):
    owner = installation(tmp_db)
    tmp_db.execute_write("DROP TABLE bridge_runtime_producers")
    tmp_db.execute_write("DROP TABLE bridge_runtime_epoch")
    report = tmp_db.runtime_custody_inventory(owner)
    assert report["epoch"] is None
    assert "missing-or-legacy-table:bridge_runtime_epoch" in report["blockers"]
    with pytest.raises(RuntimeCustodyError, match="preparation"):
        Database(tmp_db.db_path, read_only=True)
    assert tmp_db.execute_read(
        "SELECT 1 FROM sqlite_master WHERE name='bridge_runtime_epoch'"
    ) == []
