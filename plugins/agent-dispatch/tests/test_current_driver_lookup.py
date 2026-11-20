"""Driver lookup remains an indexed observation under ordinary and older queue writers."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_dispatch.current_driver_lookup import DriverMatch, current_driver_match
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from tests.test_pool_placement import place, pool, publish


@pytest.fixture
def q(tmp_path: Path) -> TaskQueue:
    return TaskQueue(tmp_path / "tasks.db")


def read(conn: sqlite3.Connection, machine: str = "münchen", tree: str = "tree",
         native: str | None = "native", task: str = "reader") -> DriverMatch:
    return current_driver_match(
        conn, task_id=task, machine=machine, worktree=tree, native_id=native,
    )


@pytest.mark.parametrize(("owner", "pin_host", "pin_tree", "expected"), [
    ("MÜNCHEN/tree", None, None, DriverMatch.MATCH),
    ("foreign/tree", "MÜNCHEN", "tree", DriverMatch.CLEAR),
    ("tree", None, None, DriverMatch.UNKNOWN),
    ("legacy", "\u00a0MÜNCHEN", "tree", DriverMatch.CLEAR),
    ("tree", "foreign", "tree", DriverMatch.CLEAR),
    ("other", None, None, DriverMatch.CLEAR),
])
def test_assignment_mutations_are_visible_without_custom_writer(
    q: TaskQueue, owner: str, pin_host: str | None, pin_tree: str | None, expected: DriverMatch,
) -> None:
    with sqlite3.connect(q.db_path) as old, q._connect() as reader:
        old.execute("INSERT INTO tasks(id,status,owner,target_machine,target_worktree)"
                    " VALUES ('task','claimed',?,?,?)", (owner, pin_host, pin_tree))
        old.commit()
        reader.execute("BEGIN")
        assert read(reader) is expected
        old.execute("UPDATE tasks SET status='completed' WHERE id='task'")
        old.commit()
        assert read(reader) is expected
        reader.rollback()
        reader.execute("BEGIN")
        assert read(reader) is DriverMatch.CLEAR


def test_retained_submission_and_waiter_use_independent_lifetimes(q: TaskQueue) -> None:
    with q._connect() as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO tasks(id,status,owner,owner_session_id) "
                     "VALUES ('task','suspended','MÜNCHEN/elsewhere','native')")
        assert read(conn) is DriverMatch.MATCH
        conn.execute("UPDATE tasks SET status='submitted',completed_by=owner,owner=NULL")
        assert read(conn) is DriverMatch.MATCH
        assert read(conn, "other") is DriverMatch.CLEAR
        assert read(conn, task="task") is DriverMatch.CLEAR
        conn.execute("INSERT INTO run_waiters(task_id,generation,pid,owner,owner_session_id,"
                     "resume_worktree,state,created_at,updated_at) VALUES ('task',1,0,"
                     "'MÜNCHEN/tree','native','tree','preparing',1,1)")
        conn.execute("UPDATE tasks SET status='completed'")
        assert read(conn) is DriverMatch.MATCH
        assert read(conn, task="task") is DriverMatch.MATCH
        conn.execute("UPDATE run_waiters SET owner='bare',state='active'")
        assert read(conn) is DriverMatch.UNKNOWN
        conn.execute("UPDATE run_waiters SET state='retired'")
        assert read(conn) is DriverMatch.CLEAR


def test_plain_writer_pool_rebinding_and_rollback(q: TaskQueue) -> None:
    with sqlite3.connect(q.db_path) as old:
        old.execute("INSERT INTO tasks(id,status,generation,target_worktree)"
                    " VALUES ('task','claimed',1,'tree')")
        assert read(old) is DriverMatch.UNKNOWN
        old.execute("INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,"
                    "updated_at,worktree) VALUES ('key','task',1,'reserving',1,1,'tree')")
        old.execute("INSERT INTO task_pool_assignments(task_id,registration_id,machine,env,"
                    "reservation_key,task_generation,catalog_revision)"
                    " VALUES ('task','pool','MÜNCHEN','linux','key',1,1)")
        assert read(old) is DriverMatch.MATCH
        assert read(old, "foreign") is DriverMatch.CLEAR
        old.commit()
        old.execute("UPDATE spawn_reservations SET worktree='second',state='settled'")
        assert read(old) is DriverMatch.CLEAR
        assert read(old, tree="second") is DriverMatch.MATCH
        old.rollback()
        old.execute("BEGIN")
        assert read(old) is DriverMatch.MATCH
        old.execute("UPDATE task_pool_assignments SET assignment_released=1")
        assert read(old) is DriverMatch.UNKNOWN  # retained pin, not the released machine
        old.execute("UPDATE tasks SET status='queued',generation=2")
        assert read(old) is DriverMatch.CLEAR


def test_real_placement_publishes_target_before_native_start(q: TaskQueue) -> None:
    publish(q, pool(machine="MÜNCHEN"))
    task = q.create("targeted work", repo="example", target_worktree="tree")
    assert isinstance(task, Task)
    reservation, created = place(q, task)
    assert created and reservation.worktree == "tree"
    claimed = q.get(task.id)
    assert claimed and claimed.owner is None and claimed.owner_session_id is None
    with q._connect() as conn:
        conn.execute("BEGIN")
        assert read(conn) is DriverMatch.MATCH
        assert read(conn, "different") is DriverMatch.CLEAR
        assert read(conn, task=task.id) is DriverMatch.CLEAR


def test_replace_on_secondary_key_preserves_displaced_pin(q: TaskQueue) -> None:
    with sqlite3.connect(q.db_path) as old:
        old.executemany("INSERT INTO tasks(id,status,generation,target_worktree)"
                        " VALUES (?,'claimed',1,'tree')", [("one",), ("two",)])
        old.execute("INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,"
                    "updated_at,worktree) VALUES ('key','one',1,'reserving',1,1,'selected')")
        statement = (
            "INSERT OR REPLACE INTO task_pool_assignments(task_id,registration_id,machine,env,"
            "reservation_key,task_generation,catalog_revision)"
            " VALUES (?,'pool','MÜNCHEN','linux','key',1,1)"
        )
        old.execute(statement, ("one",))
        assert read(old, tree="selected") is DriverMatch.MATCH
        old.execute(statement, ("two",))
        assert read(old, tree="selected") is DriverMatch.CLEAR
        assert read(old, task="two") is DriverMatch.UNKNOWN


def test_excessive_unusual_spelling_fanout_is_not_absence(q: TaskQueue) -> None:
    with q._connect() as conn:
        conn.execute("BEGIN")
        conn.executemany("INSERT INTO tasks(id,status,owner) VALUES (?,'claimed',?)",
                         [(str(i), f"MÁLAGA-{i}/tree") for i in range(50)])
        assert read(conn) is DriverMatch.UNKNOWN


def test_unicode_pin_matching_is_not_route_matching(q: TaskQueue) -> None:
    with q._connect() as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO tasks(id,status,owner,target_machine,target_worktree)"
                     " VALUES ('task','claimed','legacy','Straße','tree')")
        assert read(conn, "STRASSE") is DriverMatch.MATCH
        conn.execute("UPDATE tasks SET owner='Straße/tree'")
        assert read(conn, "STRASSE") is DriverMatch.CLEAR


@pytest.mark.parametrize(("owner", "pin_tree"), [
    ("legacy", "tree"), ("tree", None), (None, "tree"),
])
@pytest.mark.parametrize(("stored", "caller", "expected"), [
    (" HOST ", "host", DriverMatch.CLEAR), ("HOST", " host ", DriverMatch.CLEAR),
    (" HOST ", " host ", DriverMatch.MATCH),
    ("\u00a0Straße", "STRASSE", DriverMatch.CLEAR),
    ("Straße", "\u00a0STRASSE", DriverMatch.CLEAR),
    ("\u00a0Straße", "\u00a0STRASSE", DriverMatch.MATCH),
])
def test_pin_preserves_whitespace(
    q: TaskQueue, owner: str | None, pin_tree: str | None,
    stored: str, caller: str, expected: DriverMatch,
) -> None:
    with sqlite3.connect(q.db_path) as conn:
        conn.execute("INSERT INTO tasks(id,status,owner,target_machine,target_worktree)"
                     " VALUES ('task','claimed',?,?,?)", (owner, stored, pin_tree))
        assert read(conn, caller) is expected


@pytest.mark.parametrize(("owner", "machine"), [
    (" HOST /tree", "host"), ("MÜNCHEN/tree", "\u00a0münchen"),
    ("\u00a0MÜNCHEN/tree", "münchen"),
])
def test_routed_owner_still_normalizes_whitespace(q: TaskQueue, owner: str, machine: str) -> None:
    with sqlite3.connect(q.db_path) as conn:
        conn.execute("INSERT INTO tasks(id,status,owner) VALUES ('task','started',?)", (owner,))
        assert read(conn, machine) is DriverMatch.MATCH


def test_v1_projection_upgrade_preserves_concurrent_pinned_reader_and_writer(q: TaskQueue) -> None:
    from agent_dispatch.current_driver_schema import migrate

    names = ("pin", "tasks_bare", "prestart_pin")
    legacy_sql = (
        "SELECT task_id FROM current_driver_pool_v1 INDEXED BY idx_current_driver_v1_prestart_pin"
        " WHERE placed=0 AND tree='tree' AND (CASE WHEN machine GLOB '*[^ -~]*' THEN 'u'||machine"
        " ELSE 'a'||lower(trim(COALESCE(machine,''))) END)='ahost'"
    )
    with q._connect() as new, sqlite3.connect(q.db_path) as old:
        for name in names:
            new.execute(f"DROP INDEX idx_current_driver_v2_{name}")
        old.execute("INSERT INTO tasks(id,status,target_machine,target_worktree)"
                    " VALUES ('legacy','claimed',' host ','tree')")
        old.commit()
        old.execute("BEGIN")
        assert old.execute(legacy_sql).fetchone() == ("legacy",)
        migrate(new)
        migrate(new)
        assert old.execute(legacy_sql).fetchone() == ("legacy",)
        old.rollback()
        old.execute("UPDATE tasks SET target_machine='host' WHERE id='legacy'")
        old.commit()
        assert old.execute(legacy_sql).fetchone() == ("legacy",)
        new.execute("BEGIN")
        assert read(new, " host ") is DriverMatch.CLEAR
        assert read(new, "HOST") is DriverMatch.MATCH
        indexed = {r[0] for r in new.execute("SELECT name FROM sqlite_schema WHERE type='index'")}
        assert all(f"idx_current_driver_{version}_{name}" in indexed
                   for version in ("v1", "v2") for name in names)


def test_migration_seeds_preexisting_bindings(tmp_path: Path) -> None:
    from agent_dispatch.current_driver_schema import migrate

    q = TaskQueue(tmp_path / "old.db")
    with q._connect() as conn:
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger' "
            "AND name LIKE 'current_driver_pool_v1_%'"
        ).fetchall():
            conn.execute(f"DROP TRIGGER {row[0]}")
        conn.execute("DROP VIEW current_driver_pool_source_v1")
        conn.execute("DROP TABLE current_driver_pool_v1")
        conn.execute("INSERT INTO tasks(id,status,generation) VALUES ('old','claimed',1)")
        conn.execute("INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,"
                     "updated_at,worktree) VALUES ('key','old',1,'reserving',1,1,'tree')")
        conn.execute("INSERT INTO task_pool_assignments(task_id,registration_id,machine,env,"
                     "reservation_key,task_generation,catalog_revision)"
                     " VALUES ('old','pool','MÜNCHEN','linux','key',1,1)")
        migrate(conn)
        migrate(conn)
        conn.execute("BEGIN")
        assert read(conn) is DriverMatch.MATCH


def test_index_cost_is_bounded_across_same_tree_history_and_other_live_work(
    q: TaskQueue, record_property: Callable[[str, object], None],
) -> None:
    with q._connect() as conn:
        costs: list[int] = []
        for start, end in ((0, 20), (20, 2000)):
            conn.execute("BEGIN")
            rows = [{"i": str(i)} for i in range(start, end)]
            for state, prefix, tree, machine, released in (
                ("completed", "old", "tree", "MÜNCHEN", 1),
                ("claimed", "live", "another-tree", "MÜNCHEN", 0),
                ("claimed", "foreign", "tree", "MÁLAGA", 0),
            ):
                params = [{**r, "task": prefix + r["i"], "state": state, "tree": tree,
                           "machine": machine, "released": released} for r in rows]
                conn.executemany("INSERT INTO tasks(id,status,generation) VALUES (:task,:state,1)",
                                 params)
                conn.executemany(
                    "INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,"
                    "updated_at,worktree) VALUES (:task,:task,1,"
                    "CASE WHEN :released THEN 'settled' ELSE 'reserving' END,1,1,:tree)", params,
                )
                conn.executemany(
                    "INSERT INTO task_pool_assignments(task_id,registration_id,machine,env,"
                    "reservation_key,task_generation,catalog_revision,assignment_released)"
                    " VALUES (:task,'pool',:machine,'linux',:task,1,1,:released)", params,
                )
            conn.executemany(
                "INSERT INTO run_waiters(task_id,generation,pid,owner,owner_session_id,"
                "resume_worktree,state,created_at,updated_at) VALUES (:i,1,0,"
                "'MÁLAGA/tree','native','tree','active',1,1)", rows,
            )
            conn.commit()
            conn.execute("BEGIN")
            steps = 0

            def tick() -> int:
                nonlocal steps
                steps += 1
                return 0

            conn.set_progress_handler(tick, 1)
            try:
                assert read(conn) is DriverMatch.CLEAR
            finally:
                conn.set_progress_handler(None, 0)
            costs.append(steps)
            conn.rollback()
    record_property("vm_steps_20_then_2000", costs)
    assert costs[1] <= costs[0] * 2, costs
