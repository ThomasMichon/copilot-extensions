"""Commit on a separate WAL connection between the observation read and first write."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_dispatch import bridge
from agent_dispatch import ci_launch_observation as observations
from agent_dispatch import queue as queue_module
from agent_dispatch import queue_execution_custody as custody
from agent_dispatch.queue import TaskQueue
from tests.test_unqualified_launch_observation import admitted, make_repair, post


@pytest.mark.parametrize("race", ["once", "exhausted", "staging", "competing-attempt"])
def test_stale_admission_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str,
) -> None:
    queue = TaskQueue(tmp_path / "wal.db")
    task = make_repair(queue)
    with queue._connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.execute("CREATE TABLE unrelated(n INTEGER)")
    reads = sends = 0
    observe = observations.observe

    def raced(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
        nonlocal reads
        row = observe(conn, key)
        reads += 1
        if reads <= 2 and (race != "once" or reads == 1):
            with queue._connect() as writer:
                writer.execute("INSERT INTO unrelated VALUES (1)")
                if reads == 2 and race == "competing-attempt":
                    writer.execute(
                        "INSERT INTO ci_launch_observations VALUES"
                        "(?,'other',?,?, 'container:other','/workspace','pending',NULL)",
                        (key, task.id, task.generation),
                    )
                if reads == 2 and race == "staging":
                    queue.record_staging_spawn(key, "local-body:other")
        return row

    def launch(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal sends
        sends += 1
        return SimpleNamespace(returncode=0, stdout='{"session_id":"fixture"}', stderr="")

    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admitted)
    monkeypatch.setattr(observations, "observe", raced)
    monkeypatch.setattr(bridge, "spawn_worker", launch)
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **k: True)
    body = post(queue, task)
    key = body["reservation"]["key"]
    with queue._connect() as conn:
        row = observe(conn, key)
    if race == "once":
        assert body["coordinator_launch"]["ok"], body
        assert sends == 1 and row and row["state"] == "accepted"
    else:
        assert not sends and not body["coordinator_launch"]["ok"]
        if race == "exhausted":
            assert row is None and body["reservation"]["state"] == "deferred", body
            assert body["coordinator_launch"]["outcome"] == "deferred"
            assert body["coordinator_launch"]["retryable"] is True
            fresh, acquired = queue.reserve_spawn(task.id, reserved_by=task.origin_ref)
            assert acquired and fresh.key != key
        else:
            assert body["reservation"]["state"] == "reserving"
            assert body["coordinator_launch"]["retryable"] is False
            if race == "staging":
                assert row is None and body["reservation"]["session_handle"] == "local-body:other"
            else:
                assert row and row["attempt_id"] == "other" and row["state"] == "pending"


def test_version_is_bound_to_the_original_classification_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = TaskQueue(tmp_path / "classification.db")
    task = make_repair(queue)
    original = custody.execution_scope_qualified
    reads = 0

    def raced(conn: sqlite3.Connection, repo: str, source: str) -> bool:
        nonlocal reads
        qualified = original(conn, repo, source)
        reads += 1
        if reads == 1:
            with queue._connect() as writer:
                writer.execute("UPDATE tasks SET prompt='{}' WHERE id=?", (task.id,))
        return qualified

    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admitted)
    monkeypatch.setattr(custody, "execution_scope_qualified", raced)
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("stale task"))
    body = post(queue, task)
    assert reads == 2 and body["coordinator_launch"]["retryable"] is False
    assert body["reservation"]["state"] == "reserving"
    with queue._connect() as conn:
        assert observations.observe(conn, body["reservation"]["key"]) is None


def test_unknown_commit_is_not_stale_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = TaskQueue(tmp_path / "commit.db")
    task = make_repair(queue)
    receipts: list[str] = []

    class LostCommitReply(queue_module._ClosingConnection):
        writing = False

        def watch(self, sql: str) -> None:
            if sql.startswith("INSERT INTO ci_launch_observations"):
                self.writing = True

        def commit(self) -> None:
            super().commit()
            if self.writing:
                receipts.append("lost")
                raise sqlite3.OperationalError("ambiguous commit")

    def observe(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
        assert isinstance(conn, LostCommitReply)
        conn.set_trace_callback(conn.watch)
        return original(conn, key)

    original = observations.observe
    with monkeypatch.context() as patch:
        patch.setattr("agent_dispatch.ci_repair_spawn._invoke", admitted)
        patch.setattr(queue_module, "_ClosingConnection", LostCommitReply)
        patch.setattr(observations, "observe", observe)
        patch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("unknown commit"))
        body = post(queue, task)
    assert receipts == ["lost"] and body["coordinator_launch"]["retryable"] is False
    assert body["reservation"]["state"] == "reserving"
    with queue._connect() as conn:
        row = original(conn, body["reservation"]["key"])
        assert row and row["state"] == "pending"
