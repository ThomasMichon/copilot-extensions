"""Queue connections must close independently of cyclic garbage collection."""

from __future__ import annotations

import gc
import sqlite3
from contextlib import nullcontext

import pytest

from agent_dispatch.queue import TaskQueue, TaskError, _ClosingConnection
from tests._helpers import TEST_REPO


@pytest.mark.parametrize("failure", [False, True])
def test_queue_operations_close_connections_without_garbage_collection(
    tmp_path, monkeypatch, failure,
):
    queue = TaskQueue(tmp_path / "queue.db")
    connect = sqlite3.connect
    connections = []

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(32):
            if failure:
                with pytest.raises(TaskError, match="no such reservation"):
                    queue.record_spawn("missing")
            else:
                assert queue.list(repo=TEST_REPO) == []
        assert len(connections) == 32
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()
        if enabled:
            gc.enable()


@pytest.mark.parametrize("failure", [False, True])
def test_queue_connection_context_preserves_transaction_outcome(tmp_path, failure):
    queue = TaskQueue(tmp_path / "queue.db")
    with queue._connect() as connection:
        connection.execute("CREATE TABLE lifetime (value TEXT)")
    with pytest.raises(ValueError) if failure else nullcontext():
        with queue._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO lifetime VALUES ('kept')")
            if failure:
                raise ValueError("rollback")
    with queue._connect() as connection:
        assert [row["value"] for row in connection.execute("SELECT value FROM lifetime")] == (
            [] if failure else ["kept"]
        )


@pytest.mark.parametrize("statement", [
    "PRAGMA busy_timeout=", "PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON",
])
@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, KeyboardInterrupt])
def test_queue_connection_closes_when_setup_fails(
    tmp_path, monkeypatch, statement, error_type,
):
    queue = TaskQueue(tmp_path / "queue.db")
    connect = sqlite3.connect
    connections = []

    class SetupFailureConnection(_ClosingConnection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith(statement):
                raise error_type("setup failed")
            return super().execute(sql, *args, **kwargs)

    def failing_connect(*args, **kwargs):
        assert kwargs["factory"] is _ClosingConnection
        connection = connect(*args, **{**kwargs, "factory": SetupFailureConnection})
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", failing_connect)
    try:
        with pytest.raises(error_type, match="setup failed"):
            queue._connect()
        assert len(connections) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connections[0].execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


def test_queue_connection_closes_and_rolls_back_when_commit_fails(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    with queue._connect() as connection:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id)"
            " DEFERRABLE INITIALLY DEFERRED)"
        )
    connection = queue._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO child VALUES (1)")
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
        with queue._connect() as other:
            other.execute("BEGIN IMMEDIATE")
            assert other.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0
            other.execute("INSERT INTO parent VALUES (1)")
            other.execute("INSERT INTO child VALUES (1)")
    finally:
        connection.close()


def test_queue_connection_supports_explicit_lifetime_without_context(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    connection = queue._connect()
    try:
        assert isinstance(connection, sqlite3.Connection)
        connection.execute("BEGIN")
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        connection.rollback()
        assert connection.execute("SELECT 2").fetchone()[0] == 2
    finally:
        connection.close()
