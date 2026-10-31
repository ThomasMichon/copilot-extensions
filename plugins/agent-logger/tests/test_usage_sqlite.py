"""Synthetic stores only: bounded, scoped SQLite accounting ingestion."""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pytest

from agent_logger import usage_sqlite
from agent_logger.sync.publication_directory import PublicationDirectory
from agent_logger.usage_sqlite import UsageReadError, UsageStoreMissingError, read_sqlite_usage

_CREATE = (
    "CREATE TABLE assistant_usage_events "
    "(session_id TEXT, created_at TEXT, model TEXT, input_tokens, duration_ms)"
)
_INSERT = "INSERT INTO assistant_usage_events VALUES (?, ?, ?, ?, ?)"


def _store(path: Path, rows: list[tuple[Any, ...]] | None = None) -> Path:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(_CREATE)
        connection.executemany(_INSERT, rows or [])
        connection.commit()
    return path


def _row(session_id: str = "chosen", **changes: Any) -> tuple[Any, ...]:
    values = {
        "session_id": session_id,
        "created_at": "2026-01-01T00:00:00Z",
        "model": "example-model",
        "input_tokens": 12,
        "duration_ms": 0.5,
    }
    values.update(changes)
    return tuple(values.values())


def test_scope_shape_and_nulls(tmp_path: Path) -> None:
    path = _store(
        tmp_path / "usage.db", [_row(), _row("other", model=b"invalid"), _row(model=None)]
    )
    result = read_sqlite_usage(path, {"chosen", "not-present"})
    assert set(result) == {"chosen"}
    assert len(result["chosen"]) == 2
    assert result["chosen"][0] == {
        "source": "session-store",
        "session_id": "chosen",
        "values": {
            "created_at": "2026-01-01T00:00:00Z",
            "model": "example-model",
            "input_tokens": 12,
            "duration_ms": 0.5,
        },
    }
    assert result["chosen"][1]["values"]["model"] is None
    assert "output_tokens" not in result["chosen"][0]["values"]


def test_committed_wal_without_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "usage.db"
    with closing(sqlite3.connect(path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(_CREATE)
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute(_INSERT, _row())
        writer.commit()
        wal = Path(f"{path}-wal")
        assert wal.stat().st_size > 0
        assert read_sqlite_usage(path, {"chosen"})["chosen"][0]["values"]["input_tokens"] == 12


@pytest.mark.parametrize("missing_parent", [False, True])
def test_missing_does_not_create_file(tmp_path: Path, missing_parent: bool) -> None:
    parent = tmp_path / "missing" if missing_parent else tmp_path
    path = parent / "usage.db"
    with pytest.raises(UsageStoreMissingError, match="^accounting store is missing$"):
        read_sqlite_usage(path, {"chosen"})
    assert not path.exists()
    assert parent.exists() is not missing_parent


def test_empty_scope_never_opens_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("empty scope opened the accounting store")

    monkeypatch.setattr(usage_sqlite.sqlite3, "connect", forbidden)
    assert read_sqlite_usage(tmp_path / "absent.db", set()) == {}


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE unrelated (session_id TEXT)",
        "CREATE TABLE assistant_usage_events (session_id TEXT, model TEXT)",
        "CREATE VIEW assistant_usage_events AS "
        "SELECT 'chosen' AS session_id, 'time' AS created_at, 'model' AS model",
    ],
)
def test_schema_gaps_are_errors(tmp_path: Path, sql: str) -> None:
    path = tmp_path / "usage.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(sql)
        connection.commit()
    with pytest.raises(UsageReadError, match="accounting store"):
        read_sqlite_usage(path, {"chosen"})


def test_corrupt_store_has_safe_error(tmp_path: Path) -> None:
    path = tmp_path / "usage.db"
    path.write_bytes(b"synthetic invalid database containing confidential-example")
    with pytest.raises(UsageReadError) as error:
        read_sqlite_usage(path, {"chosen"})
    assert str(error.value) == "accounting store could not be read safely"
    assert error.value.__suppress_context__


def test_exact_and_exceeded_row_budgets(tmp_path: Path) -> None:
    path = _store(tmp_path / "usage.db", [_row(), _row()])
    assert len(read_sqlite_usage(path, {"chosen"}, max_rows=2)["chosen"]) == 2
    with pytest.raises(UsageReadError, match="row budget"):
        read_sqlite_usage(path, {"chosen"}, max_rows=1)


def test_row_budget_is_aggregate_across_batches(tmp_path: Path) -> None:
    ids = {f"session-{index:03d}" for index in range(201)}
    path = _store(tmp_path / "usage.db", [_row(session_id) for session_id in sorted(ids)])
    assert sum(map(len, read_sqlite_usage(path, ids, max_rows=201).values())) == 201
    with pytest.raises(UsageReadError, match="row budget"):
        read_sqlite_usage(path, ids, max_rows=200)


@pytest.mark.parametrize("max_rows", [0, -1, True, 1.5, "2", 2**63])
def test_invalid_row_bounds(tmp_path: Path, max_rows: Any) -> None:
    with pytest.raises(UsageReadError, match="max_rows"):
        read_sqlite_usage(tmp_path / "absent.db", {"chosen"}, max_rows=max_rows)


@pytest.mark.parametrize("timeout", [0, -1, True, math.inf, math.nan, "1", 10**1000])
def test_invalid_time_bounds(tmp_path: Path, timeout: Any) -> None:
    with pytest.raises(UsageReadError, match="timeout"):
        read_sqlite_usage(tmp_path / "absent.db", {"chosen"}, timeout=timeout)


@pytest.mark.parametrize("session_ids", [{"a" * 2049}, {1}, ["chosen"]])
def test_invalid_scope(tmp_path: Path, session_ids: Any) -> None:
    with pytest.raises(UsageReadError, match="session_ids"):
        read_sqlite_usage(tmp_path / "absent.db", session_ids)


@pytest.mark.parametrize(
    "changes",
    [
        {"input_tokens": -1},
        {"input_tokens": 1.25},
        {"input_tokens": "12"},
        {"input_tokens": b"bytes"},
        {"duration_ms": -0.1},
        {"duration_ms": math.inf},
        {"duration_ms": "0.5"},
        {"model": "m" * 2049},
        {"created_at": b"time"},
    ],
)
def test_invalid_stored_scalars(tmp_path: Path, changes: dict[str, Any]) -> None:
    path = _store(tmp_path / "usage.db", [_row(**changes)])
    with pytest.raises(UsageReadError, match="invalid scalar"):
        read_sqlite_usage(path, {"chosen"})


@pytest.mark.parametrize("model", ["\u00e9" * 2048, "\U0001f680" * 2048])
def test_bounded_projection_preserves_multibyte_text(tmp_path: Path, model: str) -> None:
    path = _store(tmp_path / "usage.db", [_row(model=model)])
    assert read_sqlite_usage(path, {"chosen"})["chosen"][0]["values"]["model"] == model


@pytest.mark.parametrize("column", ["input_tokens", "turn_index", "duration_ms", "request_multiplier"])
def test_boolean_is_not_a_numeric_scalar(column: str) -> None:
    # SQLite stores SQL booleans as integers; guard the Python validation boundary too.
    with pytest.raises(UsageReadError, match="invalid scalar"):
        usage_sqlite._validate_scalar(column, True)


def test_optional_allowlist_and_privacy(tmp_path: Path) -> None:
    path = tmp_path / "usage.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE assistant_usage_events "
            "(session_id TEXT, created_at TEXT, model TEXT, "
            "api_call_id TEXT, turn_index INTEGER, agent_id TEXT, parent_tool_call_id TEXT, "
            "input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
            "cache_write_tokens INTEGER, reasoning_tokens INTEGER, total_nano_aiu INTEGER, "
            "request_multiplier REAL, duration_ms REAL, "
            "rawtoken_details_json TEXT, prompt TEXT, account TEXT, auth TEXT)"
        )
        connection.execute(
            "INSERT INTO assistant_usage_events VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("chosen", "2026-01-01T00:00:00Z", None, "call", 0, "agent", "parent", 1, 2, 3, 4, 5, 6,
             0.0, 0.5, "private-raw", "private-prompt", "private-account", "private-auth"),
        )
        connection.commit()
    records = read_sqlite_usage(path, {"chosen"})["chosen"]
    assert set(records[0]) == {"source", "session_id", "values"}
    values = records[0]["values"]
    assert set(values) == set(usage_sqlite._COLUMNS) - {"session_id"}
    assert "session_id" not in values
    assert values["created_at"] == "2026-01-01T00:00:00Z"
    assert values["api_call_id"] == "call"
    assert values["request_multiplier"] == 0.0
    assert "private-" not in repr(records)
    assert "rowid" not in values


@pytest.mark.parametrize("created_at", [None, ""])
def test_store_observation_requires_original_timestamp(tmp_path: Path, created_at: str | None) -> None:
    path = _store(tmp_path / "usage.db", [_row(created_at=created_at)])
    with pytest.raises(UsageReadError, match="timestamp is unavailable"):
        read_sqlite_usage(path, {"chosen"})


def test_bound_session_values_are_not_sql(tmp_path: Path) -> None:
    session_id = "' OR 1=1 -- ?"
    path = _store(tmp_path / "usage.db", [_row(session_id), _row("other")])
    assert set(read_sqlite_usage(path, {session_id})) == {session_id}


@pytest.mark.parametrize("target", ["database", "ancestor", "-wal", "-shm", "-journal"])
def test_symlink_refusal(tmp_path: Path, target: str) -> None:
    real = tmp_path / "real"
    real.mkdir()
    path = _store(real / "usage.db", [_row()])
    link = tmp_path / "link"
    try:
        if target == "database":
            link.symlink_to(path)
            candidate = link
        elif target == "ancestor":
            link.symlink_to(real, target_is_directory=True)
            candidate = link / "usage.db"
        else:
            Path(f"{path}{target}").symlink_to(path)
            candidate = path
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(UsageReadError, match="unsafe"):
        read_sqlite_usage(candidate, {"chosen"})


def test_directory_is_not_a_database(tmp_path: Path) -> None:
    with pytest.raises(UsageReadError, match="unsafe"):
        read_sqlite_usage(tmp_path, {"chosen"})


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are POSIX-only")
def test_special_file_refusal(tmp_path: Path) -> None:
    path = tmp_path / "usage.db"
    os.mkfifo(path)
    with pytest.raises(UsageReadError, match="unsafe"):
        read_sqlite_usage(path, {"chosen"})


@pytest.mark.parametrize("failure", ["schema", "rows", "scalar", "success"])
def test_connection_always_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    rows = [_row(input_tokens=-1)] if failure == "scalar" else [_row(), _row()]
    path = _store(tmp_path / "usage.db", rows)
    if failure == "schema":
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("DROP TABLE assistant_usage_events")
            connection.commit()
    connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    def tracked(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(usage_sqlite.sqlite3, "connect", tracked)
    if failure == "success":
        read_sqlite_usage(path, {"chosen"})
    else:
        with pytest.raises(UsageReadError):
            read_sqlite_usage(path, {"chosen"}, max_rows=1 if failure == "rows" else 10)
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_progress_callback_interrupts_expensive_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path / "usage.db", [_row("other")] * 2000)
    connect = sqlite3.connect
    scanning = False

    class TimedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            nonlocal scanning
            if sql.startswith("SELECT CASE WHEN"):
                scanning = True
            return super().execute(sql, parameters)

    def timed_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return connect(*args, **kwargs, factory=TimedConnection)

    monkeypatch.setattr(usage_sqlite.sqlite3, "connect", timed_connect)
    monkeypatch.setattr(usage_sqlite.time, "monotonic", lambda: 10.0 if scanning else 0.0)
    with pytest.raises(usage_sqlite.UsageTimeLimitError, match="time budget"):
        read_sqlite_usage(path, {"chosen"}, timeout=1.0)
    assert scanning


def test_snapshot_covers_multiple_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "usage.db"
    ids = {f"session-{index:03d}" for index in range(201)}
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(_CREATE)
        writer.execute(_INSERT, _row("session-000"))
        writer.commit()
        connect = sqlite3.connect
        batches = 0

        class SnapshotConnection(sqlite3.Connection):
            def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
                nonlocal batches
                if sql.startswith("SELECT CASE WHEN"):
                    batches += 1
                    assert len(parameters) <= 201
                    if batches == 2:
                        writer.execute(_INSERT, _row("session-200"))
                        writer.commit()
                return super().execute(sql, parameters)

        def snapshot_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            return connect(*args, **kwargs, factory=SnapshotConnection)

        monkeypatch.setattr(usage_sqlite.sqlite3, "connect", snapshot_connect)
        assert set(read_sqlite_usage(path, ids)) == {"session-000"}
        assert batches == 2


def test_default_row_budget_is_twenty_thousand(tmp_path: Path) -> None:
    path = _store(tmp_path / "usage.db", [_row()] * 20001)
    with pytest.raises(UsageReadError, match="row budget"):
        read_sqlite_usage(path, {"chosen"})


def test_lexical_ancestors_are_pinned_without_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path / "usage.db", [_row()])
    publication = usage_sqlite.publication_directory
    admitted: list[tuple[Path, bool]] = []

    @contextmanager
    def pinned(parent: Path, *, create: bool = True) -> Iterator[PublicationDirectory]:
        admitted.append((parent, create))
        with publication(parent, create=create) as directory:
            yield directory

    monkeypatch.setattr(usage_sqlite, "publication_directory", pinned)
    assert read_sqlite_usage(path, {"chosen"})
    assert admitted == [(path.parent, False)]


def test_replaced_filename_identity_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path / "usage.db", [_row()])
    real_stat = os.stat
    connect = sqlite3.connect
    opened = False
    filename = usage_sqlite.windows_extended_path(path)

    class ReplacedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            nonlocal opened
            cursor = super().execute(sql, parameters)
            if sql.startswith("SELECT type FROM sqlite_schema"):
                opened = True
            return cursor

    def changed_stat(name: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        info = real_stat(name, *args, **kwargs)
        if opened and isinstance(name, str) and name == filename:
            fields = list(info)
            fields[1] += 1
            return os.stat_result(fields)
        return info

    def changed_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return connect(*args, **kwargs, factory=ReplacedConnection)

    monkeypatch.setattr(usage_sqlite.os, "stat", changed_stat)
    monkeypatch.setattr(usage_sqlite.sqlite3, "connect", changed_connect)
    with pytest.raises(UsageReadError, match="identity|changed"):
        read_sqlite_usage(path, {"chosen"})


def test_unprovable_file_identity_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path / "usage.db", [_row()])
    real_fstat = os.fstat

    def unprovable(fd: int) -> os.stat_result:
        fields = list(real_fstat(fd))
        fields[1] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(usage_sqlite.os, "fstat", unprovable)
    with pytest.raises(UsageReadError, match="identity"):
        read_sqlite_usage(path, {"chosen"})


def test_ancestry_exit_failure_cannot_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path / "usage.db", [_row()])
    publication = usage_sqlite.publication_directory

    @contextmanager
    def changed(parent: Path, *, create: bool = True) -> Iterator[PublicationDirectory]:
        with publication(parent, create=create) as directory:
            yield directory
            raise ValueError("synthetic lexical ancestry change")

    monkeypatch.setattr(usage_sqlite, "publication_directory", changed)
    with pytest.raises(UsageReadError, match="could not be read safely"):
        read_sqlite_usage(path, {"chosen"})
