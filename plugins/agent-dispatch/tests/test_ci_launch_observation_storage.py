"""Verify additive migration and the cost of the transport-owner writes."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_dispatch import ci_launch_observation as observations
from agent_dispatch import queue_execution_custody as custody
from agent_dispatch.coordinator_execution_sender import CoordinatorExecutionSender
from agent_dispatch.queue import TaskQueue
from tests.test_ci_repair import EXECUTION
from tests.test_unqualified_launch_observation import admitted, make_repair


def test_upgrade_does_not_invent_an_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "historical.db"
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admitted)
    with monkeypatch.context() as old:
        old.setattr(observations, "migrate", lambda _: None)
        queue = TaskQueue(path)
        task = make_repair(queue)
        reservation, acquired = queue.reserve_spawn(task.id, reserved_by=task.origin_ref)
        assert acquired
        queue.fail_spawn(reservation.key, detail="missing reply")
    with sqlite3.connect(path) as old_writer:
        assert not old_writer.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='ci_launch_observations'"
        ).fetchone()
        restarted = TaskQueue(path)
        with restarted._connect() as conn:
            observations.migrate(conn)
            assert observations.observe(conn, reservation.key) is None
        old_writer.execute("UPDATE spawn_reservations SET detail='historic update' WHERE key=?",
                           (reservation.key,))
        old_writer.commit()
        retained = restarted.get_reservation(reservation.key)
        assert retained and retained.state == "failed" and retained.detail == "historic update"
        with restarted._connect() as conn:
            assert observations.observe(conn, reservation.key) is None


@pytest.mark.parametrize("mixed", [False, True])
def test_write_cost_is_independent_of_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None], mixed: bool,
) -> None:
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admitted)
    def measure(count: int) -> tuple[int, int, int, int]:
        queue = TaskQueue(tmp_path / f"{count}.db")
        task = make_repair(queue)
        res, acquired = queue.reserve_spawn(task.id, reserved_by=task.origin_ref)
        assert acquired and task.origin_ref
        sender = CoordinatorExecutionSender(queue, task, res, "fixture",
                                            queue.get_registration(task.origin_ref))
        with queue._connect() as conn:
            conn.execute("BEGIN")
            rows = [{"id": str(i)} for i in range(count)]
            conn.executemany("INSERT INTO tasks(id,status) VALUES (:id,'completed')", rows)
            conn.executemany("INSERT INTO spawn_reservations(key,task_id,attempt,state,"
                             "reserved_at,updated_at) VALUES (:id,:id,1,'failed',1,1)", rows)
            conn.executemany("INSERT INTO ci_launch_observations VALUES"
                             "(:id,:id,:id,1,'container:other','/workspace','unknown',NULL)", rows)
            if mixed:
                conn.executemany("INSERT INTO execution_send_custody VALUES"
                                 "('scope',:id,'other','ci-repair',1,:id,:id,1,'launch','{}',"
                                 "'sent','',1,1)", rows)
        steps = qualification = 0
        original, qualified = queue._connect, custody.execution_scope_qualified

        def progress() -> int:
            nonlocal steps
            steps += 1
            return 0

        def measured() -> sqlite3.Connection:
            conn = original()
            conn.set_progress_handler(progress, 1)
            return conn

        def baseline(conn: sqlite3.Connection, repo: str, source: str) -> bool:
            nonlocal qualification
            before = steps
            result = qualified(conn, repo, source)
            qualification += steps - before
            return result

        with monkeypatch.context() as patch:
            patch.setattr(queue, "_connect", measured)
            patch.setattr(custody, "execution_scope_qualified", baseline)
            with sender.ci_launch(EXECUTION["venue"], EXECUTION["workspace"]) as attempt:
                at_transport = steps
                attempt.sent(staging_handle="local-body:fixture")
        return at_transport, qualification, at_transport - qualification, steps - at_transport

    costs = [measure(count) for count in (20, 2000)]
    record_property("admission_total_existing_classification_added_settlement", costs)
    # The deferred classification is the pre-existing read, not an extra scan under a writer lock.
    for index in (2, 3) if mixed else (0, 1, 2, 3):
        assert costs[1][index] <= costs[0][index] + 20, costs
