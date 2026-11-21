"""Empty durable drains must remain readers beside an unrelated queue writer."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pytest

from agent_dispatch import queue as queue_module
from agent_dispatch.queue import TaskQueue

OPERATIONS = (
    "recover_inflight_wakes", "claim_due_wake",
    "recover_inflight_verification_requests", "claim_due_verification_request",
)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_empty_outbox_does_not_contend_with_another_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    monkeypatch.setattr(queue_module, "_BUSY_TIMEOUT_MS", 25)
    drain: Callable[[], object] = getattr(queue, operation)
    with queue._connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        result = drain()
        assert result == (0 if operation.startswith("recover_") else None)
        assert writer.in_transaction


@pytest.mark.parametrize("operation", OPERATIONS)
def test_failed_idle_probe_preserves_database_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    connect = queue._connect
    error = sqlite3.OperationalError("fixture candidate-read failure")

    class ProbeConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(
            self, sql: str, parameters: tuple[object, ...] = (),
        ) -> sqlite3.Cursor:
            if sql.startswith("SELECT 1"):
                raise error
            return self.connection.execute(sql, parameters)

    @contextmanager
    def failing_probe() -> Iterator[ProbeConnection]:
        with connect() as connection:
            yield ProbeConnection(connection)

    monkeypatch.setattr(queue, "_connect", failing_probe)
    drain: Callable[[], object] = getattr(queue, operation)
    with pytest.raises(sqlite3.OperationalError) as caught:
        drain()
    assert caught.value is error
