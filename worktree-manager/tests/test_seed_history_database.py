"""Read-only initialization classification and serialized history deduplication."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from worktree_manager import seed_history


@pytest.mark.parametrize("operation", ["entries", "remember"])
def test_history_tolerates_real_journal_commit_during_sidecar_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("previous")
    connection = sqlite3.connect(history.path)
    journal = history.path.with_name(history.path.name + "-journal")
    protect = seed_history.protect
    observed: list[Path] = []
    try:
        connection.execute(
            "INSERT INTO seeds(project, prompt, submitted_at) VALUES (?, ?, ?)",
            ("demo", "concurrent", 0),
        )
        assert journal.exists()

        def commit_before_protection(path: Path, **kwargs) -> None:
            if path == journal and not observed:
                observed.append(path)
                connection.commit()
                assert not path.exists()
            protect(path, **kwargs)

        monkeypatch.setattr(seed_history, "protect", commit_before_protection)
        if operation == "remember":
            history.remember("next")
        else:
            assert history.entries() == ["concurrent", "previous"]
        assert observed == [journal]
    finally:
        connection.close()
    assert history.entries() == (
        ["next", "concurrent", "previous"] if operation == "remember"
        else ["concurrent", "previous"]
    )


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("unsafe link")])
def test_sidecar_protection_failures_other_than_disappearance_still_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError,
) -> None:
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("previous")
    protect = seed_history.protect

    def fail_sidecar(path: Path, **kwargs) -> None:
        if path.name.endswith("-journal"):
            raise error
        protect(path, **kwargs)

    monkeypatch.setattr(seed_history, "protect", fail_sidecar)
    with pytest.raises(seed_history.SeedHistoryError):
        history.entries()


def test_concurrent_identical_submissions_deduplicate_under_early_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("previous")
    connect = sqlite3.connect
    reads: list[bool] = []
    barrier = Barrier(4)

    class LockedReadConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=(), /) -> sqlite3.Cursor:
            if sql.startswith("SELECT prompt") and "LIMIT 1" in sql:
                reads.append(self.in_transaction)
                assert self.in_transaction, "deduplication must read under the explicit transaction"
            return super().execute(sql, parameters)

    def observed_connect(*args, **kwargs) -> sqlite3.Connection:
        return connect(*args, **kwargs, factory=LockedReadConnection)

    monkeypatch.setattr(seed_history.sqlite3, "connect", observed_connect)
    prompt = "  identical 'quoted' draft\nsecond line\n"
    def save(_index: int) -> None:
        barrier.wait(timeout=5)
        seed_history.SeedHistory("demo", tmp_path).remember(prompt)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(4)))
    assert reads == [True] * 4
    assert history.entries() == [prompt, "previous"]


@pytest.mark.parametrize("kind", ["zero-file", "connection-only", "empty-sqlite"])
def test_uninitialized_history_reads_are_read_only_and_leave_schema_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    history = seed_history.SeedHistory("demo", tmp_path)
    history.path.parent.mkdir()
    if kind == "zero-file":
        history.path.touch()
    else:
        with sqlite3.connect(history.path) as conn:
            if kind == "empty-sqlite":
                conn.execute("VACUUM")
        conn.close()
    before = history.path.read_bytes()
    connect = sqlite3.connect
    opened: list[str] = []
    def read_only_connect(*args, **kwargs) -> sqlite3.Connection:
        assert kwargs.get("uri") is True and str(args[0]).endswith("?mode=ro")
        connection = connect(*args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE must_not_be_created(value TEXT)")
        opened.append(str(args[0]))
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(seed_history.sqlite3, "connect", read_only_connect)
        assert history.entries() == []
    assert len(opened) == 1 and history.path.read_bytes() == before
    with connect(history.path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
    conn.close()


def test_interrupted_before_history_ddl_leaves_readable_uninitialized_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = seed_history.SeedHistory("demo", tmp_path)
    connect = sqlite3.connect
    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=(), /) -> sqlite3.Cursor:
            if sql.startswith("CREATE TABLE"):
                raise OSError("interrupted before schema initialization")
            return super().execute(sql, parameters)
    def interrupted_connect(*args, **kwargs) -> sqlite3.Connection:
        return connect(*args, **kwargs, factory=InterruptedConnection)
    with monkeypatch.context() as patch:
        patch.setattr(seed_history.sqlite3, "connect", interrupted_connect)
        with pytest.raises(seed_history.SeedHistoryError, match="not been submitted"):
            history.remember("retained draft")
    assert history.path.exists()
    before = history.path.read_bytes()
    assert history.entries() == [] and history.path.read_bytes() == before
    history.remember("retry")
    assert history.entries() == ["retry"]


@pytest.mark.parametrize("kind", ["corrupt", "foreign", "incompatible", "view", "marked-empty"])
def test_unknown_history_database_is_refused_without_schema_changes(
    tmp_path: Path, kind: str,
) -> None:
    history = seed_history.SeedHistory("demo", tmp_path)
    history.path.parent.mkdir()
    if kind == "corrupt":
        history.path.write_bytes(b"not a SQLite database")
    else:
        with sqlite3.connect(history.path) as conn:
            if kind == "foreign":
                conn.execute("CREATE TABLE foreign_data(value TEXT)")
                conn.execute("INSERT INTO foreign_data VALUES ('unrelated content')")
            elif kind == "incompatible":
                conn.execute("CREATE TABLE seeds(id INTEGER, project TEXT, prompt TEXT)")
            elif kind == "view":
                conn.execute("CREATE VIEW seeds AS SELECT 1 AS id, 'demo' AS project")
            else:
                conn.execute("PRAGMA user_version = 19")
        conn.close()
    before = history.path.read_bytes()
    with pytest.raises(seed_history.SeedHistoryError):
        history.entries()
    assert history.path.read_bytes() == before
    with pytest.raises(seed_history.SeedHistoryError):
        history.remember("must not adopt this database")
    assert history.path.read_bytes() == before


@pytest.mark.parametrize("kind", [
    "discard-trigger", "extra-table", "extra-index", "version-marker", "application-marker",
])
def test_initialized_history_refuses_extra_schema_and_markers_without_mutation(
    tmp_path: Path, kind: str,
) -> None:
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("previous")
    with sqlite3.connect(history.path) as conn:
        if kind == "discard-trigger":
            conn.execute(
                "CREATE TRIGGER discard_submitted AFTER INSERT ON seeds "
                "BEGIN DELETE FROM seeds WHERE id = NEW.id; END"
            )
        elif kind == "extra-table":
            conn.execute("CREATE TABLE foreign_data(value TEXT)")
        elif kind == "extra-index":
            conn.execute("CREATE INDEX foreign_index ON seeds(project)")
        elif kind == "version-marker":
            conn.execute("PRAGMA user_version = 19")
        else:
            conn.execute("PRAGMA application_id = 19")
    conn.close()
    before = history.path.read_bytes()
    with pytest.raises(seed_history.SeedHistoryError):
        history.entries()
    assert history.path.read_bytes() == before
    with pytest.raises(seed_history.SeedHistoryError):
        history.remember("must remain recoverable")
    assert history.path.read_bytes() == before
