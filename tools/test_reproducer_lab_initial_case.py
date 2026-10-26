"""Synthetic gate composition and local SQLite/JSONL controls; no guest launch."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_reproducer_lab_monitoring import LAB

sys.path.insert(0, str(LAB))
import initial_case


class Connection(sqlite3.Connection):
    def __exit__(self, *args: Any) -> bool:
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Queue:
    def __init__(self, path: Path) -> None:
        self.db_path = str(path)
        self.creates: list[dict[str, object]] = []
        self.disposition = "created"
        self.fail_create = False
        with self._connect() as connection:
            for table in initial_case.DISPATCH_HISTORY:
                connection.execute(f"CREATE TABLE {table} (id TEXT, state TEXT)")

    def _connect(self) -> Connection:
        return sqlite3.connect(self.db_path, factory=Connection)

    def create_outcome(self, title: str, **kwargs: object) -> SimpleNamespace:
        self.creates.append({"title": title, **kwargs})
        with self._connect() as connection:
            connection.execute("INSERT INTO tasks VALUES ('synthetic-task', 'queued')")
        if self.fail_create:
            raise OSError("synthetic lost reply after create")
        return SimpleNamespace(disposition=self.disposition, task=SimpleNamespace(id="synthetic-task"))


class BridgeReader:
    def __init__(self, path: Path) -> None:
        self.db_path = path
        self.read_only = True
        with sqlite3.connect(path) as connection:
            for table in initial_case.BRIDGE_HISTORY:
                connection.execute(f"CREATE TABLE {table} (id TEXT, state TEXT)")
        info = path.stat()
        self._read_only_file_id = info.st_dev, info.st_ino

    def execute_read(self, sql: str) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            return connection.execute(sql).fetchall()
        finally:
            connection.close()


@pytest.fixture
def seams(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    queue = Queue(tmp_path / "dispatch.db")
    db = BridgeReader(tmp_path / "bridge.db")
    journal = SimpleNamespace(db=db)
    assertions: list[bool] = []
    custody = SimpleNamespace(assert_owned=lambda: assertions.append(True))
    entries: list[dict[str, object]] = []
    diagnostics = SimpleNamespace(
        directory=initial_case.DIAGNOSTICS, component="reproducer", service="reproducer-lab",
        write_event_strict=lambda event: entries.append(event),
    )
    identity = initial_case.FileIdentity(10, 20)
    monkeypatch.setattr(initial_case, "_journal_paths", lambda *_args: (identity,))
    return SimpleNamespace(
        queue=queue, journal=journal, custody=custody, diagnostics=diagnostics,
        entries=entries, assertions=assertions,
    )


def submit(seams: SimpleNamespace) -> Any:
    return initial_case.submit_initial_task(
        seams.custody, seams.queue, seams.journal, seams.diagnostics,
    )


def test_initial_create_uses_existing_queue_and_strict_entry(seams: SimpleNamespace) -> None:
    outcome = submit(seams)
    assert outcome.task.id == "synthetic-task"
    assert seams.assertions == [True]
    assert len(seams.entries) == len(seams.queue.creates) == 1
    entry = seams.entries[0]
    assert set(entry) == {"ts", "service", "component", "event", "case"}
    assert entry["event"] == "initial_task_create_entry"
    assert "prompt" not in entry and "ready" not in entry
    create = seams.queue.creates[0]
    assert create["dedup_key"] == initial_case.CASE
    assert create["repo"] == initial_case.LANE
    assert create["requires"] == ["agent:reproducer-lab"]
    assert create["prompt"] == "Return the literal synthetic result: offline-case-complete."


@pytest.mark.parametrize("owner,tables", [
    ("dispatch", initial_case.DISPATCH_HISTORY),
    ("bridge", initial_case.BRIDGE_HISTORY),
])
@pytest.mark.parametrize("state", ["pending", "unknown", "accepted", "completed", "refused", "failed"])
def test_any_retained_history_refuses_even_terminal_rows(
    seams: SimpleNamespace, owner: str, tables: tuple[str, ...], state: str,
) -> None:
    path = seams.queue.db_path if owner == "dispatch" else seams.journal.db.db_path
    for table in tables:
        with sqlite3.connect(path) as connection:
            connection.execute(f"INSERT INTO {table} VALUES ('retained', ?)", (state,))
        with pytest.raises(ValueError, match="original-owner reconciliation"):
            submit(seams)
        assert not seams.entries and not seams.queue.creates
        with sqlite3.connect(path) as connection:
            assert connection.execute(f"SELECT state FROM {table}").fetchone() == (state,)
            connection.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize("owner", ["dispatch", "bridge"])
def test_missing_journal_table_is_not_an_empty_history(
    seams: SimpleNamespace, owner: str,
) -> None:
    path = seams.queue.db_path if owner == "dispatch" else seams.journal.db.db_path
    table = initial_case.DISPATCH_HISTORY[0] if owner == "dispatch" else initial_case.BRIDGE_HISTORY[0]
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP TABLE {table}")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        submit(seams)
    assert not seams.entries and not seams.queue.creates


def test_strict_failure_prevents_actual_create(seams: SimpleNamespace) -> None:
    def fail(_event: dict[str, object]) -> None:
        raise OSError("synthetic sync failure")

    seams.diagnostics.write_event_strict = fail
    with pytest.raises(OSError, match="sync failure"):
        submit(seams)
    assert not seams.queue.creates


def test_history_appearing_during_sync_refuses_new_work(seams: SimpleNamespace) -> None:
    def write(event: dict[str, object]) -> None:
        seams.entries.append(event)
        with sqlite3.connect(seams.journal.db.db_path) as connection:
            connection.execute("INSERT INTO acquisition_attempts VALUES ('old', 'unknown')")

    seams.diagnostics.write_event_strict = write
    with pytest.raises(ValueError, match="Bridge work"):
        submit(seams)
    assert len(seams.entries) == 1 and not seams.queue.creates


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_journal_replacement_never_reaches_create(
    seams: SimpleNamespace, monkeypatch: Any, stage: int,
) -> None:
    calls = 0

    def paths(*_args: object) -> tuple[initial_case.FileIdentity, ...]:
        nonlocal calls
        calls += 1
        if calls == stage:
            raise ValueError("synthetic journal replacement")
        return (initial_case.FileIdentity(10, 20),)

    monkeypatch.setattr(initial_case, "_journal_paths", paths)
    with pytest.raises(ValueError, match="replacement"):
        submit(seams)
    assert not seams.queue.creates
    assert len(seams.entries) == (1 if stage == 3 else 0)


def test_lost_create_reply_preserves_task_and_refuses_replay(seams: SimpleNamespace) -> None:
    seams.queue.fail_create = True
    with pytest.raises(OSError, match="lost reply"):
        submit(seams)
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(seams)
    assert len(seams.entries) == len(seams.queue.creates) == 1


def test_deduplicated_create_is_not_reported_as_new(seams: SimpleNamespace) -> None:
    seams.queue.disposition = "deduplicated"
    with pytest.raises(ValueError, match="did not create a new task"):
        submit(seams)
    assert len(seams.queue.creates) == 1


def test_event_loop_call_refuses_without_io(seams: SimpleNamespace) -> None:
    async def call() -> None:
        with pytest.raises(RuntimeError, match="outside an event loop"):
            submit(seams)

    asyncio.run(call())
    assert not seams.entries and not seams.queue.creates


@pytest.mark.parametrize("field,value", [
    ("directory", Path("/tmp/foreign")), ("component", "bridge"), ("service", "foreign"),
])
def test_wrong_diagnostic_owner_refuses(seams: SimpleNamespace, field: str, value: object) -> None:
    setattr(seams.diagnostics, field, value)
    with pytest.raises(ValueError, match="diagnostic owner"):
        submit(seams)
    assert not seams.entries and not seams.queue.creates


@pytest.mark.parametrize("times", [(0, 5), (0, 0, 5)])
def test_exact_journal_deadline_refuses(
    seams: SimpleNamespace, monkeypatch: Any, times: tuple[int, ...],
) -> None:
    clock = iter(times)
    monkeypatch.setattr(initial_case.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="expired"):
        submit(seams)
    assert not seams.entries and not seams.queue.creates


def test_private_path_requires_regular_single_link_and_fixed_device(tmp_path: Path) -> None:
    path = tmp_path / "db"
    path.write_bytes(b"synthetic")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="private guest journal"):
        initial_case._private_path(path, directory=False, device=path.stat().st_dev + 1)


@pytest.mark.parametrize("directory", [False, True])
def test_private_path_accepts_only_exact_guest_posture(directory: bool) -> None:
    fields = dict(
        st_mode=0o40700 if directory else 0o100600, st_uid=1000, st_gid=1000,
        st_dev=10, st_ino=20, st_nlink=1,
    )
    path = SimpleNamespace(lstat=lambda: SimpleNamespace(**fields))
    path.resolve = lambda **_kwargs: path
    assert initial_case._private_path(path, directory=directory, device=10) == (
        initial_case.FileIdentity(10, 20)
    )


@pytest.mark.parametrize("changes", [
    {"st_mode": 0o100644}, {"st_mode": 0o10600}, {"st_mode": 0o120600},
    {"st_mode": 0o40600}, {"st_uid": 0}, {"st_gid": 0}, {"st_nlink": 2},
])
def test_private_db_path_rejects_wrong_type_owner_mode_or_links(changes: dict[str, int]) -> None:
    fields = dict(st_mode=0o100600, st_uid=1000, st_gid=1000, st_dev=10, st_ino=20, st_nlink=1)
    fields.update(changes)
    path = SimpleNamespace(lstat=lambda: SimpleNamespace(**fields))
    path.resolve = lambda **_kwargs: path
    with pytest.raises(ValueError, match="private guest journal"):
        initial_case._private_path(path, directory=False, device=10)


def test_redirected_parent_refuses_even_matching_file_posture() -> None:
    fields = dict(st_mode=0o100600, st_uid=1000, st_gid=1000, st_dev=10, st_ino=20, st_nlink=1)
    path = SimpleNamespace(lstat=lambda: SimpleNamespace(**fields), resolve=lambda **_kwargs: object())
    with pytest.raises(ValueError, match="private guest journal"):
        initial_case._private_path(path, directory=False, device=10)


@pytest.mark.parametrize("failure", [None, "queue", "bridge", "writable", "opened", "custody"])
def test_journal_paths_bind_existing_reader_and_execution_owner(
    monkeypatch: Any, failure: str | None,
) -> None:
    assertions: list[bool] = []

    def owned() -> None:
        if failure == "custody":
            raise RuntimeError("synthetic lost custody")
        assertions.append(True)

    custody = SimpleNamespace(assert_owned=owned)
    queue = SimpleNamespace(db_path="/foreign" if failure == "queue" else initial_case.DISPATCH)
    database = SimpleNamespace(
        db_path=Path("/foreign") if failure == "bridge" else initial_case.BRIDGE,
        read_only=failure != "writable",
        _read_only_file_id=(10, 99 if failure == "opened" else 20),
    )
    monkeypatch.setattr(initial_case, "STATE", SimpleNamespace(lstat=lambda: SimpleNamespace(st_dev=10)))
    checked: list[tuple[Path, bool, int]] = []

    def observe(path: Path, *, directory: bool, device: int) -> initial_case.FileIdentity:
        checked.append((path, directory, device))
        return initial_case.FileIdentity(10, 20)

    monkeypatch.setattr(initial_case, "_private_path", observe)
    if failure is not None:
        with pytest.raises((ValueError, RuntimeError)):
            initial_case._journal_paths(custody, queue, SimpleNamespace(db=database))
    else:
        assert initial_case._journal_paths(custody, queue, SimpleNamespace(db=database)) == (
            initial_case.FileIdentity(10, 20),
        ) * 4
        assert checked == [
            (initial_case.DISPATCH.parent, True, 10), (initial_case.DISPATCH, False, 10),
            (initial_case.BRIDGE.parent, True, 10), (initial_case.BRIDGE, False, 10),
        ]
        assert assertions == [True]


@pytest.mark.parametrize("stage", [2, 3])
def test_changed_observed_file_identity_refuses(
    seams: SimpleNamespace, monkeypatch: Any, stage: int,
) -> None:
    calls = 0

    def paths(*_args: object) -> tuple[initial_case.FileIdentity, ...]:
        nonlocal calls
        calls += 1
        return (initial_case.FileIdentity(10, 99 if calls == stage else 20),)

    monkeypatch.setattr(initial_case, "_journal_paths", paths)
    with pytest.raises(ValueError, match="journals changed"):
        submit(seams)
    assert not seams.queue.creates and len(seams.entries) == (1 if stage == 3 else 0)


def test_lost_custody_after_create_preserves_unknown_outcome(seams: SimpleNamespace) -> None:
    def lost() -> None:
        raise RuntimeError("synthetic custody lost after create")

    seams.custody.assert_owned = lost
    with pytest.raises(RuntimeError, match="custody lost"):
        submit(seams)
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(seams)
    assert len(seams.queue.creates) == 1


@pytest.mark.parametrize("retained", [False, True])
def test_optional_cleanup_history_is_checked_when_present(
    seams: SimpleNamespace, retained: bool,
) -> None:
    with sqlite3.connect(seams.queue.db_path) as connection:
        connection.execute("CREATE TABLE cleanup_recoveries (request_id TEXT)")
        if retained:
            connection.execute("INSERT INTO cleanup_recoveries VALUES ('orphan')")
    if retained:
        with pytest.raises(ValueError, match="Dispatch work"):
            submit(seams)
        assert not seams.entries and not seams.queue.creates
    else:
        assert submit(seams).disposition == "created"


def test_optional_cleanup_view_is_not_treated_as_missing(seams: SimpleNamespace) -> None:
    with sqlite3.connect(seams.queue.db_path) as connection:
        connection.execute("CREATE VIEW cleanup_recoveries AS SELECT 'orphan' AS request_id")
    with pytest.raises(ValueError, match="not a supported table"):
        submit(seams)
    assert not seams.entries and not seams.queue.creates
