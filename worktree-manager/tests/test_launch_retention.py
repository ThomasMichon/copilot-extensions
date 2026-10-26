"""Physical input expiry and timestamp-preserving legacy history migration."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from worktree_manager import launch_attempts, launch_retention, seed_history

NOW = 2_000_000_000.0
OLD = NOW - launch_attempts.RETENTION_SECONDS
SECRET = "expired-private-marker-" * 300
LIVE = "  retained exact input\nsecond line\n"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(seed_history.time, "time", lambda: NOW)


def legacy(root: Path, *, mode: str = "delete") -> seed_history.SeedHistory:
    store = seed_history.SeedHistory("example", root)
    store.path.parent.mkdir()
    with closing(sqlite3.connect(store.path)) as conn, conn:
        assert conn.execute(f"PRAGMA journal_mode={mode}").fetchone()[0] == mode
        conn.execute("PRAGMA secure_delete=OFF")
        conn.execute(
            "CREATE TABLE seeds (id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
            "prompt TEXT NOT NULL, submitted_at REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO seeds VALUES (?, ?, ?, ?)",
            [(1, "example", SECRET, OLD), (2, "example", LIVE, OLD + 1)],
        )
        conn.execute("INSERT INTO seeds VALUES (3, 'other', ?, ?)", (SECRET, OLD - 1))
        conn.execute("INSERT INTO seeds VALUES (4, 'deleted', ?, ?)", (SECRET, OLD - 2))
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("PRAGMA secure_delete=OFF")
        conn.execute("DELETE FROM seeds WHERE id=4")
    return store


@pytest.mark.parametrize("mode", ["delete", "wal"])
def test_legacy_migration_erases_expired_and_freelist_copies_preserving_original_rows(
    tmp_path, mode,
):
    store = legacy(tmp_path, mode=mode)
    assert b"expired-private-marker-" in store.path.read_bytes()
    # Input hiding already applies before a physical maintenance opportunity.
    if mode == "wal":
        before = {path.name: path.read_bytes() for path in store.path.parent.iterdir()}
        with pytest.raises(seed_history.SeedHistoryError, match="maintenance"):
            store.entries()
        assert {path.name: path.read_bytes() for path in store.path.parent.iterdir()} == before
    else:
        assert store.entries() == [LIVE]
    assert store.expire() == 2
    assert store.expire() == 0
    assert store.entries() == [LIVE]
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("SELECT * FROM seeds").fetchall() == [(2, "example", LIVE, OLD + 1)]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA application_id").fetchone()[0] == seed_history._APPLICATION_ID
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert b"expired-private-marker-" not in store.path.read_bytes()
    assert list(store.path.parent.iterdir()) == [store.path]


def test_normal_remember_migrates_and_does_not_extend_an_existing_inputs_deadline(tmp_path):
    store = legacy(tmp_path)
    store.remember(LIVE)
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("SELECT id, submitted_at FROM seeds").fetchall() == [(2, OLD + 1)]
    assert b"expired-private-marker-" not in store.path.read_bytes()
    store.remember("new explicit submission")
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute(
            "SELECT prompt, submitted_at FROM seeds ORDER BY id"
        ).fetchall() == [(LIVE, OLD + 1), ("new explicit submission", NOW)]


def test_new_history_expiry_hides_then_securely_erases_at_exact_boundary(tmp_path, monkeypatch):
    store = seed_history.SeedHistory("example", tmp_path)
    store.remember(SECRET)
    monkeypatch.setattr(seed_history.time, "time", lambda: NOW + launch_attempts.RETENTION_SECONDS - 1)
    assert store.entries() == [SECRET]
    monkeypatch.setattr(seed_history.time, "time", lambda: NOW + launch_attempts.RETENTION_SECONDS)
    assert store.entries() == []
    assert b"expired-private-marker-" in store.path.read_bytes()
    assert store.expire() == 1 and store.expire() == 0
    assert b"expired-private-marker-" not in store.path.read_bytes()


def test_interrupted_legacy_scrub_retries_without_changing_original_timestamp(tmp_path, monkeypatch):
    store = legacy(tmp_path)
    connect = sqlite3.connect

    class Interrupted(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql == "VACUUM":
                raise sqlite3.OperationalError("simulated interruption")
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(seed_history.sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=Interrupted))
        with pytest.raises(seed_history.SeedHistoryError, match="erase"):
            store.expire()
    with closing(connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert store.expire() == 0
    with closing(connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT submitted_at FROM seeds").fetchall() == [(OLD + 1,)]
    assert b"expired-private-marker-" not in store.path.read_bytes()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_orphan_history_sidecar_is_not_empty_or_removed(tmp_path, suffix):
    store = seed_history.SeedHistory("example", tmp_path)
    store.path.parent.mkdir()
    sidecar = store.path.with_name(store.path.name + suffix)
    sidecar.write_bytes(b"owned-state-must-not-be-guessed")
    for action in (store.entries, store.expire, lambda: store.remember("new")):
        with pytest.raises(seed_history.SeedHistoryError, match="sidecar"):
            action()
    assert sidecar.read_bytes() == b"owned-state-must-not-be-guessed"
    assert not store.path.exists()


def test_foreign_legacy_schema_is_not_migrated_or_scrubbed(tmp_path):
    store = legacy(tmp_path)
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    before = store.path.read_bytes()
    with pytest.raises(seed_history.SeedHistoryError, match="schema"):
        store.expire()
    assert store.path.read_bytes() == before


def test_busy_legacy_wal_is_not_removed_or_claimed_as_success(tmp_path):
    store = legacy(tmp_path, mode="wal")
    with closing(sqlite3.connect(store.path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM seeds").fetchone()
        wal = store.path.with_name(store.path.name + "-wal")
        before = wal.read_bytes()
        with pytest.raises(seed_history.SeedHistoryError, match="erase"):
            store.expire()
        assert wal.exists() and wal.read_bytes() == before
        assert reader.execute("PRAGMA user_version").fetchone()[0] == 0
    assert store.expire() == 2


def test_maintenance_erases_both_copies_preserves_metadata_and_pending_seed(tmp_path, monkeypatch):
    history = legacy(tmp_path)
    journal = launch_attempts.LaunchAttempts(tmp_path)
    attempt = uuid4().hex
    monkeypatch.setattr(launch_attempts.time, "time", lambda: OLD)
    journal.record_intent(
        attempt, action="new", project="example", surface="test", is_local=True, prompt=SECRET,
    )
    monkeypatch.setattr(launch_attempts.time, "time", lambda: NOW)
    pending = tmp_path / "pending-daemon-seed.json"
    pending.write_text(SECRET, encoding="utf-8")
    assert launch_retention.maintain(tmp_path) is None
    assert journal.detail(attempt)["inputs_available"] is False
    assert len(journal.detail(attempt)["events"]) == 1
    assert b"expired-private-marker-" not in journal.path.read_bytes()
    assert history.entries() == [LIVE]
    assert b"expired-private-marker-" not in history.path.read_bytes()
    assert pending.read_text(encoding="utf-8") == SECRET


@pytest.mark.parametrize("broken", ["journal", "history"])
def test_maintenance_reports_failure_and_still_expires_the_other_store(
    tmp_path, monkeypatch, broken,
):
    calls = []
    def journal_expire(_self):
        calls.append("journal")
        if broken == "journal":
            raise launch_attempts.LaunchAttemptError(SECRET)
    def history_expire(_self):
        calls.append("history")
        if broken == "history":
            raise seed_history.SeedHistoryError(SECRET)
    monkeypatch.setattr(launch_attempts.LaunchAttempts, "expire", journal_expire)
    monkeypatch.setattr(seed_history.SeedHistory, "expire", history_expire)
    message = launch_retention.maintain(tmp_path)
    assert calls == ["journal", "history"]
    assert SECRET not in message
    assert ("launch journal" if broken == "journal" else "prompt history") in message


def test_missing_maintenance_stores_stay_missing(tmp_path):
    assert launch_retention.maintain(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_real_picker_callback_expires_both_stores_without_remote_or_launch_work(tmp_path, monkeypatch):
    from worktree_manager import resident_picker
    from worktree_manager.production_picker import runner

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    history = legacy(tmp_path)
    journal = launch_attempts.LaunchAttempts(tmp_path)
    monkeypatch.setattr(launch_attempts.time, "time", lambda: OLD)
    journal.record_intent(
        uuid4().hex, action="open", project="example", surface="test", is_local=True, prompt=SECRET,
    )
    monkeypatch.setattr(launch_attempts.time, "time", lambda: NOW)
    monkeypatch.setattr(resident_picker, "wait_for_local", lambda *_a: False)
    monkeypatch.setattr(
        runner, "_start_housekeeping", lambda: pytest.fail("no remote work during local expiry"),
    )
    def picker(*, after_first_refresh, **kwargs):
        assert after_first_refresh() is None
        assert history.entries() == [LIVE]
        assert b"expired-private-marker-" not in history.path.read_bytes()
        assert b"expired-private-marker-" not in journal.path.read_bytes()
        return {"action": "cancel"}
    monkeypatch.setattr(runner, "run_tui_picker", picker)
    try:
        assert runner.run("example", mock_mode=False, local=True, bootstrap_payload={
            "version": 1, "project": "example", "default_live": False,
        }) == {"action": "cancel"}
    finally:
        runner.context.reset()


@pytest.mark.parametrize("timestamp", ["invalid", -1, float("inf")])
def test_invalid_legacy_timestamps_are_not_reinterpreted_or_given_a_new_deadline(tmp_path, timestamp):
    store = legacy(tmp_path)
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("UPDATE seeds SET submitted_at=? WHERE id=2", (timestamp,))
    before = store.path.read_bytes()
    for read in (store.entries, store.cursor):
        with pytest.raises(seed_history.SeedHistoryError, match="timestamp"):
            read()
        assert store.path.read_bytes() == before
    with pytest.raises(seed_history.SeedHistoryError, match="timestamp"):
        store.expire()
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("kind", ["history", "journal"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_delete_mode_with_unowned_wal_sidecars_refuses_without_erasing_evidence(tmp_path, kind, suffix):
    if kind == "history":
        store = legacy(tmp_path)
        error = seed_history.SeedHistoryError
    else:
        store = launch_attempts.LaunchAttempts(tmp_path)
        store.record_intent(
            uuid4().hex, action="open", project="example", surface="test", is_local=True,
        )
        error = launch_attempts.LaunchAttemptError
    sidecar = store.path.with_name(store.path.name + suffix)
    sidecar.write_bytes(b"unknown-retained-evidence")
    before = store.path.read_bytes()
    with pytest.raises(error, match="sidecar"):
        store.expire()
    assert store.path.read_bytes() == before
    assert sidecar.read_bytes() == b"unknown-retained-evidence"


@pytest.mark.parametrize("kind", ["zero-file", "empty-sqlite", "corrupt", "false-wal-header"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_incomplete_non_wal_history_never_initializes_over_leftover_evidence(tmp_path, kind, suffix):
    store = seed_history.SeedHistory("example", tmp_path)
    store.path.parent.mkdir()
    if kind == "zero-file":
        store.path.touch()
    elif kind == "empty-sqlite":
        with closing(sqlite3.connect(store.path)) as conn:
            conn.execute("VACUUM")
    elif kind == "corrupt":
        store.path.write_bytes(b"not a SQLite header")
    else:
        store.path.write_bytes(b"not SQLite".ljust(18, b"!") + b"\x02\x02")
    sidecar = store.path.with_name(store.path.name + suffix)
    sidecar.write_bytes(b"leftover-evidence-must-survive")
    before = {path.name: path.read_bytes() for path in store.path.parent.iterdir()}
    for operation in (store.entries, store.cursor, store.expire, lambda: store.remember("new input")):
        with pytest.raises(seed_history.SeedHistoryError, match="sidecar"):
            operation()
        assert {path.name: path.read_bytes() for path in store.path.parent.iterdir()} == before
