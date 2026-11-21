"""Empty custody at one owner cannot suppress another owner's unresolved lineage."""

import sqlite3
from pathlib import Path

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_execution_custody import (
    ExecutionSendIdentity,
    ExecutionSendUnresolved,
    require_execution_send_reconciliation,
)
from agent_dispatch.unresolved_lineage import migrate, require_clear
from tests import test_execution_send_custody as sends

setup = sends.setup


def compare(conn: sqlite3.Connection, task: Task, blocked: bool) -> None:
    if blocked:
        with pytest.raises(ExecutionSendUnresolved):
            require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
        with pytest.raises(ExecutionSendUnresolved):
            require_clear(conn, task)
    else:
        require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
        require_clear(conn, task)


@pytest.mark.parametrize(("table", "key"), [
    ("wake_outbox", "logical"), ("run_waiter_wakes", "exclusive"),
    ("execution_send_custody", "logical"), ("execution_send_custody", "reservation"),
])
def test_only_empty_owner_skips_its_lineage_lookup(tmp_path: Path, table: str, key: str) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("Observer", repo="example", dedup_key="logical", exclusive_key="exclusive")
    assert isinstance(task, Task)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO tasks(id,status,repo,dedup_key,exclusive_key)"
            " VALUES ('other','completed',?,?,?)",
            (task.repo, task.dedup_key if key == "logical" else None,
             task.exclusive_key if key == "exclusive" else None),
        )
        if table == "wake_outbox":
            conn.execute(
                "INSERT INTO wake_outbox(id,task_id,owner,generation,wake_seq,status,"
                "created_at,updated_at,execution_send_state)"
                " VALUES ('w','other','foreign/worker',1,1,'failed',1,1,'unknown')"
            )
        elif table == "run_waiter_wakes":
            conn.execute(
                "INSERT INTO run_waiter_wakes(id,task_id,waiter_generation,task_generation,owner,"
                "resume_worktree,sender,message,status,created_at,updated_at,execution_send_state)"
                " VALUES ('w','other',1,1,'foreign/worker','worker','f','m',"
                "'failed',1,1,'pending')"
            )
        else:
            conn.execute(
                "INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,updated_at,"
                "exclusive_key) VALUES ('r','other',1,'settled',1,1,?)",
                (task.exclusive_key if key == "reservation" else None,),
            )
            conn.execute(
                "INSERT INTO execution_send_custody VALUES "
                "('s','id','r','src',1,'other','r',1,'launch','{}','unknown','',1,1)"
            )
        compare(conn, task, True)


def test_current_keys_move_while_admitted_identity_does_not(
    setup: tuple[TaskQueue, ExecutionSendIdentity, Task],
) -> None:
    queue, identity, other = setup
    observer = queue.create("Observer", repo="example", dedup_key="logical", exclusive_key="key")
    assert isinstance(observer, Task)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        compare(conn, observer, False)
        queue.admit_execution_send(identity, source="scheduled", expected_producer_generation=1)
        conn.rollback()
        conn.execute("BEGIN")
        compare(conn, observer, False)
        conn.execute("UPDATE tasks SET exclusive_key='key' WHERE id=?", (other.id,))
        compare(conn, observer, True)
        conn.rollback()
        conn.execute("BEGIN")
        compare(conn, observer, False)
        conn.execute("UPDATE tasks SET status='completed',repo=?,dedup_key='logical' WHERE id=?",
                     (observer.repo, other.id))
        compare(conn, observer, True)
        conn.execute("UPDATE tasks SET repo='foreign' WHERE id=?", (other.id,))
        compare(conn, observer, False)
        conn.execute("UPDATE spawn_reservations SET exclusive_key='key',state='settled'"
                     " WHERE key=?", (identity.reservation_key,))
        compare(conn, observer, True)
        conn.commit()
        queue.settle_execution_send(identity, outcome="unknown", detail="uncertain")
        conn.execute("BEGIN")
        compare(conn, observer, True)
        conn.rollback()
        queue.settle_execution_send(identity, outcome="not_sent", detail="owner confirmed")
        conn.execute("BEGIN")
        compare(conn, observer, False)


def test_upgrade_indexes_preexisting_unknown_without_editing_old_rows(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "old.db")
    task = queue.create("Observer", repo="example", dedup_key="logical")
    assert isinstance(task, Task)
    with sqlite3.connect(queue.db_path) as writer:
        for kind in ("trigger", "table"):
            names = writer.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'lineage_v1_%'"
                                   " AND type=?", (kind,)).fetchall()
            for name, in names:
                writer.execute(f"DROP {kind} {name}")
        writer.execute("INSERT INTO tasks(id,repo,dedup_key,status)"
                       " VALUES ('old',?,'logical','completed')", (task.repo,))
        writer.execute("INSERT INTO wake_outbox(id,task_id,owner,generation,wake_seq,status,"
                       "created_at,updated_at,execution_send_state) "
                       "VALUES ('old-wake','old','legacy',1,1,'failed',1,1,'unknown')")
        writer.commit()
        with queue._connect() as reader:
            reader.execute("BEGIN IMMEDIATE")
            migrate(reader)
            migrate(reader)
            reader.commit()
            reader.execute("BEGIN")
            compare(reader, task, True)
            writer.execute("UPDATE wake_outbox SET execution_send_state='sent'"
                           " WHERE id='old-wake'")
            writer.commit()
            compare(reader, task, True)  # Original snapshot
            reader.rollback()
            reader.execute("BEGIN")
            compare(reader, task, False)
        assert writer.execute("SELECT execution_send FROM wake_outbox").fetchone() == (None,)


def test_replace_moves_custody_between_lineages_without_phantom_block(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "replace.db")
    task = queue.create("Observer", repo="example", exclusive_key="key")
    assert isinstance(task, Task)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO tasks(id,status,exclusive_key) VALUES ('old','completed','key')")
        conn.execute("INSERT INTO tasks(id,status) VALUES ('other','completed')")
        conn.execute("INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,"
                     "updated_at) VALUES ('r','other',1,'settled',1,1)")
        statement = (
            "INSERT OR REPLACE INTO execution_send_custody VALUES ('s',?,'r','src',1,?,'r',1,"
            "'launch','{}',?,'',1,1)"
        )
        conn.execute(statement, ("old-send", "old", "pending"))
        compare(conn, task, True)
        conn.execute(statement, ("new-send", "other", "unknown"))
        compare(conn, task, False)
        conn.execute("UPDATE tasks SET exclusive_key='key' WHERE id='other'")
        compare(conn, task, True)
        conn.execute(statement, ("last-send", "other", "sent"))
        compare(conn, task, False)


def test_legacy_waiters_with_missing_ids_keep_distinct_task_custody(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "legacy-waiters.db")
    task = queue.create("Observer", repo="example", exclusive_key="shared")
    assert isinstance(task, Task)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        for owner in ("one", "two"):
            conn.execute(
                "INSERT INTO run_waiter_wakes(task_id,waiter_generation,task_generation,owner,"
                "resume_worktree,sender,message,status,created_at,updated_at,execution_send_state)"
                " VALUES (?,1,1,'legacy','tree','fixture','m','stale',1,1,'unknown')", (owner,),
            )
        conn.execute("INSERT INTO tasks(id,status,exclusive_key)"
                     " VALUES ('one','completed','shared')")
        compare(conn, task, True)
        conn.execute("DELETE FROM run_waiter_wakes WHERE task_id='two'")
        compare(conn, task, True)
        conn.execute("UPDATE tasks SET exclusive_key='different' WHERE id='one'")
        compare(conn, task, False)
        conn.execute("UPDATE tasks SET exclusive_key='shared' WHERE id='one'")
        conn.execute("UPDATE run_waiter_wakes SET execution_send_state='not_sent'")
        compare(conn, task, False)
