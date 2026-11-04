"""Queue status wire and persistence parity at the supported Python minimum."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_index.indexing.task_store import TERMINAL, TaskStatus, TaskStore


@pytest.mark.parametrize("status", list(TaskStatus))
def test_status_keeps_string_rendering_and_json_wire(status: TaskStatus) -> None:
    value = status.value
    assert status == value
    assert hash(status) == hash(value)
    assert TaskStatus(value) is status
    assert str(status) == value
    assert f"{status}" == value
    assert format(status, ">18") == format(value, ">18")
    assert json.loads(json.dumps({"status": status})) == {"status": value}
    if sys.version_info >= (3, 11):
        from enum import StrEnum

        assert isinstance(status, StrEnum)


@pytest.mark.parametrize("status", sorted(TERMINAL, key=lambda value: value.value))
def test_terminal_status_keeps_sqlite_and_record_wire(
    tmp_path: Path, status: TaskStatus,
) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    task = store.enqueue(source="git:fixture")
    claimed = store.dequeue_next()
    assert claimed is not None and claimed.id == task.id
    assert claimed.status == TaskStatus.PROCESSING.value
    assert store.update_status(task.id, status)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, typeof(status), finished_at FROM tasks WHERE id = ?", (task.id,),
        ).fetchone()
    assert row is not None
    assert row[0:2] == (status.value, "text")
    assert row[2] is not None
    record = store.get_task(task.id)
    assert record is not None
    assert type(record.status) is str
    assert json.loads(json.dumps(record.to_dict()))["status"] == status.value
