"""Measure migration separately from per-write maintenance on synthetic queue history."""

import sqlite3
from collections.abc import Callable
from functools import partial
from pathlib import Path

from agent_dispatch.queue import TaskQueue
from agent_dispatch.unresolved_lineage import migrate


def _steps(conn: sqlite3.Connection, action: Callable[[], object]) -> int:
    steps = 0

    def count() -> int:
        nonlocal steps
        steps += 1
        return 0

    conn.set_progress_handler(count, 1)
    try:
        action()
    finally:
        conn.set_progress_handler(None, 0)
    return steps


def test_lineage_write_cost(
    tmp_path: Path, record_property: Callable[[str, object], None],
) -> None:
    send = ("INSERT INTO execution_send_custody VALUES ('s',?,'r','src',1,?,?,1,"
            "'launch','{}',?,'',1,1)")
    samples: list[list[int]] = []
    for history in (20, 2000):
        queue = TaskQueue(tmp_path / f"{history}.db")
        with sqlite3.connect(queue.db_path) as conn:
            conn.execute("BEGIN")
            for kind in ("trigger", "table"):
                names = conn.execute("SELECT name FROM sqlite_schema WHERE name LIKE "
                                     "'lineage_v1_%' AND type=?", (kind,)).fetchall()
                for name, in names:
                    conn.execute(f"DROP {kind} {name}")
            conn.executemany(send, [(str(i), "old", str(i), "sent") for i in range(history)])
            conn.execute(send, ("pending", "task", "res", "unknown"))
            conn.execute("INSERT INTO tasks(id,status) VALUES ('task','completed')")
            conn.execute("INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,"
                         "updated_at) VALUES ('res','task',1,'settled',1,1)")
            writes = (
                "UPDATE tasks SET exclusive_key='new' WHERE id='task'",
                "UPDATE spawn_reservations SET exclusive_key='new' WHERE key='res'",
                "UPDATE execution_send_custody SET state='sent' WHERE scope_key='s' "
                "AND send_id='pending'",
            )

            def measure(statements: tuple[str, ...]) -> list[int]:
                conn.execute("SAVEPOINT measurement")
                costs = [_steps(conn, partial(conn.execute, sql)) for sql in statements]
                conn.execute("ROLLBACK TO measurement")
                conn.execute("RELEASE measurement")
                return costs

            baseline = measure(writes)
            installation = _steps(conn, lambda: migrate(conn))
            assert conn.execute(
                "SELECT count(*) FROM lineage_v1_execution_send_custody"
            ).fetchone() == (1,)
            enabled = measure(writes)
            record_property(f"history_{history}", (installation, baseline, enabled))
            samples.append(enabled)
    assert all(big <= small * 2 for small, big in zip(*samples, strict=True)), samples
