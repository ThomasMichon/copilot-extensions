"""Synthetic rescue through the actual SQLite/export and normal writer owners."""

from __future__ import annotations

from contextlib import closing
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, cast

import pytest

FIXTURES = importlib.import_module("test_agenda_export")
EXPORT, destination, rows = FIXTURES.EXPORT, FIXTURES.destination, FIXTURES.rows
selection, source_bytes = FIXTURES.selection, FIXTURES.source_bytes
CORE = importlib.import_module("test_agenda_v2_core")
SKILL, STORE, current, lane = CORE.SKILL, CORE.STORE, CORE.current, CORE.lane
outcome, proof, revisit, v2 = CORE.outcome, CORE.proof, CORE.revisit, CORE.v2
RESCUE = importlib.import_module("agenda_rescue")

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


@pytest.fixture
def packet(lane: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    outcome(lane)
    revisit(lane, {"content": "new accepted input"})
    target = destination(lane, "managed")
    saved = destination(lane)
    selected = selection(lane)
    manifest = EXPORT.export_store(lane, saved, **selected, expected_revision=3)
    placement = {
        "scope": {"project": "fixture-project", "repository": "fixture-project"},
        "domain": "agendas", "lane": "main",
        "state_home": str(target), "store_id": selected["store_id"],
        "worktree_id": selected["worktree"], "creation_nonce": "fixture-incarnation",
        "placement_generation": 1,
    }
    monkeypatch.setattr(RESCUE.managed, "allocate", lambda *args: placement)
    return {"source": lane, "target": target, "export": saved, "manifest": manifest, "placement": placement,
            "arguments": {"provider_python": sys.executable, "project": "fixture-project",
                          "creation_nonce": "fixture-incarnation", "lane": "main",
                          "expected_revision": 3, "operation_id": "fixture-rescue", **selected}}


def rescue(packet: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return cast(dict[str, Any], RESCUE.relocate(
        packet["source"], packet["export"], **{**packet["arguments"], **overrides},
    ))


def test_complete_history_cutover_preserves_source_and_advancing_retry(packet: dict[str, Any]) -> None:
    source, target, saved = (packet[key] for key in ("source", "target", "export"))
    original_rows = rows(source)
    original_export = source_bytes(saved)
    ignored = source / "unknown-evidence.bin"
    ignored.write_bytes(b"unknown evidence is not moved, deleted or adopted")
    reply = rescue(packet)
    assert reply["source_fenced"] and reply["activated"] and reply["revision"] == 3
    assert rows(source) == rows(target) == original_rows
    assert source_bytes(saved) == original_export
    assert ignored.read_bytes() == b"unknown evidence is not moved, deleted or adopted"
    with pytest.raises(RESCUE.AgendaError, match="unsupported"):
        STORE.read(source)
    with pytest.raises(RESCUE.AgendaError, match="unsupported"):
        STORE.record_delivery(source, 3, {"state": "not-configured", "error": None})
    outcome(target)
    advanced = STORE.read(target)
    retry = rescue(packet)
    assert not retry["activated"] and retry["revision"] == 4
    assert STORE.read(target) == advanced and rows(source) == original_rows
    assert EXPORT.verify_export(saved, **selection(target), expected_revision=3) == packet["manifest"]


def test_additive_provider_metadata_does_not_invalidate_cutover_recovery(packet: dict[str, Any]) -> None:
    rescue(packet)
    packet["placement"]["observed_at"] = "later provider observation"
    packet["placement"]["scope"]["display_name"] = "new provider presentation"
    assert rescue(packet)["activated"] is False
    assert STORE.read(packet["target"])["revision"] == 3


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_orphan_sidecars_are_preserved_before_first_database_creation(
    packet: dict[str, Any], suffix: str,
) -> None:
    (packet["target"] / (STORE.DATABASE + suffix)).write_bytes(b"unrecognized SQLite evidence")
    before = {key: source_bytes(packet[key]) for key in ("source", "target", "export")}
    with pytest.raises(RESCUE.AgendaError, match="genuinely empty"):
        rescue(packet)
    assert {key: source_bytes(packet[key]) for key in before} == before


def test_unrecognized_database_hot_journal_is_never_recovered_or_deleted(packet: dict[str, Any]) -> None:
    target = packet["target"]
    database = target / STORE.DATABASE
    database.write_bytes((packet["source"] / STORE.DATABASE).read_bytes())
    script = r'''
import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute("PRAGMA cache_size=1")
db.execute("BEGIN IMMEDIATE")
db.execute("UPDATE state SET metadata=?",("uncommitted"*20000,))
os._exit(0)
'''
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
    seeded = subprocess.run(
        [sys.executable, "-B", "-c", script, str(database)],
        capture_output=True, text=True, timeout=10, creationflags=flags,
    )
    assert seeded.returncode == 0, seeded.stderr
    assert (target / (STORE.DATABASE + "-journal")).is_file()
    before = {key: source_bytes(packet[key]) for key in ("source", "target", "export")}
    with pytest.raises(RESCUE.AgendaError):
        rescue(packet)
    assert {key: source_bytes(packet[key]) for key in before} == before


def test_destination_directory_barrier_precedes_source_writer_fence(
    packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = source_bytes(packet["source"])
    with monkeypatch.context() as patch:
        def unavailable(*args: Any) -> None:
            raise OSError("fixture destination directory barrier failed")
        patch.setattr(EXPORT, "_sync_directory", unavailable)
        with pytest.raises(RESCUE.AgendaError, match="destination directory barrier"):
            rescue(packet)
    assert source_bytes(packet["source"]) == before
    assert STORE.read(packet["source"])["revision"] == 3
    with pytest.raises(RESCUE.AgendaError, match="unsupported"):
        STORE.read(packet["target"])
    assert rescue(packet)["activated"] is True


@pytest.mark.parametrize("stage", ["source-fence", "reservation", "activation"])
@pytest.mark.parametrize("when", ["before-open", "after-open"])
def test_database_replacement_before_sqlite_recovery_preserves_all_files(
    packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch, stage: str, when: str,
) -> None:
    foreign = destination(packet["source"], "replacement-foreign")
    foreign_database = foreign / STORE.DATABASE
    script = r'''
import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute("CREATE TABLE sentinel(value TEXT)")
db.execute("INSERT INTO sentinel VALUES (?)",("retained"*30000,))
db.execute("PRAGMA cache_size=1")
db.execute("BEGIN IMMEDIATE")
db.execute("UPDATE sentinel SET value=?",("uncommitted"*30000,))
os._exit(0)
'''
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
    seeded = subprocess.run(
        [sys.executable, "-B", "-c", script, str(foreign_database)],
        capture_output=True, text=True, timeout=10, creationflags=flags,
    )
    assert seeded.returncode == 0, seeded.stderr
    foreign_journal = foreign / (STORE.DATABASE + "-journal")
    assert foreign_journal.is_file()
    foreign_bytes, journal_bytes = foreign_database.read_bytes(), foreign_journal.read_bytes()
    home = packet["source"] if stage == "source-fence" else packet["target"]
    selected = home / STORE.DATABASE
    retained = home / "retained-original.sqlite3"
    selected_journal = home / (STORE.DATABASE + "-journal")
    mode = "rwc" if stage == "reservation" else "rw"
    uri = selected.as_uri() + "?mode=" + mode
    native = sqlite3.connect
    triggered, moved = False, False
    original: bytes | None = None

    def replace() -> None:
        nonlocal moved
        selected.rename(retained)
        foreign_database.rename(selected)
        foreign_journal.rename(selected_journal)
        moved = True

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal triggered, original
        inject = bool(args) and args[0] == uri and not triggered
        if inject:
            triggered = True
            original = selected.read_bytes()
            if when == "before-open":
                replace()
        connection: sqlite3.Connection = native(*args, **kwargs)
        if inject and when == "after-open":
            try:
                replace()
            except BaseException:
                connection.close()
                raise
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        with pytest.raises(RESCUE.AgendaError):
            rescue(packet)
    assert triggered
    actual_database = selected if moved else foreign_database
    actual_journal = selected_journal if moved else foreign_journal
    assert actual_database.read_bytes() == foreign_bytes
    assert actual_journal.read_bytes() == journal_bytes
    assert (retained if moved else selected).read_bytes() == original


def test_export_longer_than_a_history_page_moves_every_accepted_operation(
    packet: dict[str, Any],
) -> None:
    source = packet["source"]
    for index in range(50):
        outcome(source)
        revisit(source, {"content": f"accepted-{index}"})
    saved = destination(source, "long-export")
    packet["export"] = saved
    packet["arguments"]["expected_revision"] = 103
    EXPORT.export_store(source, saved, **selection(source), expected_revision=103)
    original = rows(source)
    assert rescue(packet)["source_revision"] == 103
    assert rows(packet["target"]) == original
    assert len(STORE.history(packet["target"])) == 100
    assert len(original["operations"]) == 103


@pytest.mark.parametrize("stage", ["activation", "copy"])
def test_interrupted_activation_keeps_both_writers_fenced_and_exact_retry_recovers(
    packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    original = rows(packet["source"])
    with monkeypatch.context() as patch:
        if stage == "activation":
            def interrupted(*args: Any) -> None:
                raise OSError("fixture stopped after source commit")
            patch.setattr(RESCUE, "_activate", interrupted)
        else:
            native = EXPORT._table_hashes

            def interrupted_copy(db: sqlite3.Connection, budget: Any) -> dict[str, Any]:
                result = native(db, budget)
                if db.execute("PRAGMA user_version").fetchone()[0] == 2 and (
                        db.execute("SELECT count(*) FROM operations").fetchone()[0] == 3):
                    # The target has its copied rows while the write transaction
                    # is still uncommitted; both source passes precede this one.
                    paths = db.execute("PRAGMA database_list").fetchall()
                    if Path(paths[0][2]).parent == packet["target"]:
                        raise OSError("fixture copy verification interrupted")
                return cast(dict[str, Any], result)
            patch.setattr(EXPORT, "_table_hashes", interrupted_copy)
        with pytest.raises(RESCUE.AgendaError, match="fixture"):
            rescue(packet)
    for home in (packet["source"], packet["target"]):
        with pytest.raises(RESCUE.AgendaError, match="unsupported"):
            STORE.read(home)
    assert rows(packet["source"]) == original
    assert rescue(packet)["activated"] is True
    assert rows(packet["target"]) == original


@pytest.mark.parametrize("changed", ["source-revision", "export", "destination", "operation"])
def test_changed_inputs_refuse_without_replacing_accepted_data(
    packet: dict[str, Any], changed: str,
) -> None:
    if changed == "source-revision":
        outcome(packet["source"])
    elif changed == "export":
        with (packet["export"] / STORE.DATABASE).open("ab") as stream:
            stream.write(b"changed")
    elif changed == "destination":
        (packet["target"] / "retained.bin").write_bytes(b"another owner's allocation")
    else:
        rescue(packet)
        packet["arguments"]["operation_id"] = "another-operation"
    before = {key: source_bytes(packet[key]) for key in ("source", "target", "export")}
    with pytest.raises(RESCUE.AgendaError):
        rescue(packet)
    assert {key: source_bytes(packet[key]) for key in before} == before


def test_replaced_destination_after_fence_is_not_adopted(
    packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(RESCUE, "_activate", lambda *args: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(RESCUE.AgendaError):
            rescue(packet)
    target = packet["target"]
    original = target / STORE.DATABASE
    original.rename(target / "retained-original.sqlite3")
    original.write_bytes((target / "retained-original.sqlite3").read_bytes())
    before = source_bytes(target)
    with pytest.raises(RESCUE.AgendaError):
        rescue(packet)
    assert source_bytes(target) == before


@pytest.mark.parametrize("version", [None, 1])
def test_manual_and_unsupported_stores_remain_byte_preserved(
    packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch, version: int | None,
) -> None:
    if version is None:
        (packet["source"] / "cursor.json").write_text('{"version":1,"retained":"manual"}')
    else:
        with closing(sqlite3.connect(packet["source"] / STORE.DATABASE)) as db:
            db.execute("PRAGMA user_version=1")
    monkeypatch.setattr(RESCUE.managed, "allocate", lambda *args: pytest.fail("unsupported source allocated"))
    before = source_bytes(packet["source"])
    with pytest.raises(RESCUE.AgendaError):
        rescue(packet)
    assert source_bytes(packet["source"]) == before
    assert not list(packet["target"].iterdir())


def test_already_loaded_writer_rechecks_version_after_the_source_write_lock(
    packet: dict[str, Any], tmp_path: Path,
) -> None:
    frame, item, _attempt = current(packet["source"])
    request = v2({
        "operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "fixture", "next_action": "Wait",
        "evidence": proof("waiting evidence"),
    }, 3, "preloaded-writer")
    request_path = tmp_path / "preloaded-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    ready, release = tmp_path / "ready", tmp_path / "release"
    script = r'''
import json,sqlite3,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import agenda_store
ready,release=map(Path,sys.argv[4:6])
native=sqlite3.connect
class Gated(sqlite3.Connection):
    def execute(self,sql,*args,**kwargs):
        if sql=="BEGIN IMMEDIATE":
            ready.write_text("loaded")
            deadline=time.monotonic()+15
            while not release.exists():
                if time.monotonic()>=deadline:
                    raise TimeoutError("fixture writer deadline")
                time.sleep(0.01)
        return super().execute(sql,*args,**kwargs)
sqlite3.connect=lambda *args,**kwargs:native(*args,**kwargs,factory=Gated)
try:
    agenda_store.mutate(Path(sys.argv[2]),json.loads(Path(sys.argv[3]).read_text()))
except agenda_store.AgendaError as exc:
    assert "unsupported" in str(exc),str(exc)
    print("preloaded writer refused")
else:
    raise AssertionError("preloaded writer changed the fenced source")
'''
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(SKILL / "scripts"), str(packet["source"]),
         str(request_path), str(ready), str(release)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=flags,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline, "writer did not reach its admission gate"
            time.sleep(0.01)
        rescue(packet)
        before_source, before_target = rows(packet["source"]), rows(packet["target"])
        release.touch()
        output, error = process.communicate(timeout=20)
        assert process.returncode == 0, error
        assert output.strip() == "preloaded writer refused"
        assert rows(packet["source"]) == before_source and rows(packet["target"]) == before_target
    finally:
        release.touch(exist_ok=True)
        process.communicate(timeout=25)
