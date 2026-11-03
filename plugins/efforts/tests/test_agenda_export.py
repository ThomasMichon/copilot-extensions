"""Complete owner snapshots, including the immutable ledger, without replay."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from test_agenda_store import AGENDA, STORE, documents as documents, proof, request
from test_agenda_v2_core import context, current, lane as lane, outcome, revisit, update

EXPORT = importlib.import_module("agenda_export")
POSIX = pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory-fsync qualification")


def selection(home: Path) -> dict:
    state = STORE.read(home)
    return {"worktree": state["snapshot"]["worktree"], "store_id": state["store_id"]}


def destination(home: Path, name: str = "export") -> Path:
    target = home.parent / f"{home.name}-{name}"
    target.mkdir(mode=0o700)
    return target


def rows(home: Path) -> dict:
    with closing(sqlite3.connect((home / STORE.DATABASE).as_uri() + "?mode=ro", uri=True)) as db:
        return {name: db.execute(f"SELECT * FROM {name} ORDER BY {EXPORT.ORDER[name]}").fetchall()
                for name in EXPORT.TABLES}


def source_bytes(home: Path) -> dict:
    return {item.name: item.read_bytes() for item in home.iterdir() if item.is_file()}


def rewrite_member_manifest(target: Path) -> None:
    """Keep the byte checksum honest so semantic corruption must be detected."""
    manifest_path = target / EXPORT.MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    raw = (target / STORE.DATABASE).read_bytes()
    manifest["members"][STORE.DATABASE] = {
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@POSIX
def test_full_ledger_original_bytes_and_delivery_are_preserved(lane: Path) -> None:
    # Cross the ordinary history API's 100-entry ceiling.
    for value in range(52):
        outcome(lane)
        revisit(lane, {"content": f"changed-{value}"})
    STORE.record_delivery(lane, 105, {
        "state": "delivered", "error": None, "reply": {"ok": True, "detail": "fixture receipt"},
    })
    selected = selection(lane)
    original_rows, original_bytes = rows(lane), source_bytes(lane)
    before = STORE.read(lane)
    target = destination(lane)
    manifest = EXPORT.export_store(lane, target, **selected, expected_revision=105)
    assert manifest["completeness"] == "complete" and manifest["revision"] == 105
    assert manifest["tables"]["operations"]["rows"] == 105
    assert manifest["tables"]["agenda_records"]["rows"] == len(before["records"])
    assert rows(target) == original_rows
    assert EXPORT.verify_export(target, **selected, expected_revision=105) == manifest
    transported = lane.parent / f"{lane.name}-transported"
    shutil.copytree(target, transported)
    assert (transported / EXPORT.MANIFEST).stat().st_nlink == 1
    assert EXPORT.verify_export(transported, **selected, expected_revision=105) == manifest
    assert STORE.read(lane) == before and source_bytes(lane) == original_bytes
    # Unreferenced adopter files are not export members and are never crawled.
    (lane / "unrelated-private-data").write_bytes(b"not agenda state")
    assert EXPORT.export_store(lane, destination(lane, "second"), **selected)["revision"] == 105
    assert not (target / "unrelated-private-data").exists()


@POSIX
def test_deferred_acceptance_observation_survives_revisit_deadline(lane: Path) -> None:
    revisit_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    frame, _, _ = current(lane)
    result = update(lane, {
        "operation": "defer", "frame_id": frame, "decision_ref": "fixture pending decision",
        "decision_packet_ref": "fixture packet", "revisit_at": revisit_at.isoformat(),
        "evidence": proof(),
    })
    deferred_id = result["transition"]["deferred_id"]
    selected = selection(lane)
    accepted = STORE.delivery_record(lane)[1]
    assert accepted["snapshot"]["due_rechecks"] == []
    assert accepted["snapshot"]["next_revisit_at"] == revisit_at.isoformat()
    before_bytes, before_rows = source_bytes(lane), rows(lane)
    target = destination(lane)

    manifest = EXPORT.export_store(lane, target, **selected, expected_revision=2)
    assert EXPORT.verify_export(target, **selected, expected_revision=2) == manifest
    assert STORE.read(lane)["snapshot"]["due_rechecks"] == []
    # Cross the real deadline without replacing the clock or projection. This
    # integration fixture waits at most its five-second declared revisit delay.
    remaining = (revisit_at - datetime.now(timezone.utc)).total_seconds()
    assert 0 < remaining <= 5
    threading.Event().wait(remaining + 0.01)
    live = STORE.read(lane)["snapshot"]
    assert live["due_rechecks"] == [deferred_id] and live["next_revisit_at"] is None
    assert live["next_action"] != accepted["snapshot"]["next_action"]
    assert live["deferred"][0]["due"] and live["deferred"][0]["trigger"] == "time"
    assert EXPORT.verify_export(target, **selected, expected_revision=2) == manifest
    after_target = destination(lane, "after-revisit")
    after_manifest = EXPORT.export_store(lane, after_target, **selected, expected_revision=2)
    assert after_manifest["tables"] == manifest["tables"]
    assert EXPORT.verify_export(after_target, **selected, expected_revision=2) == after_manifest
    assert rows(after_target) == before_rows
    assert source_bytes(lane) == before_bytes and rows(target) == before_rows
    assert STORE.delivery_record(target)[1] == accepted

    # A legacy producer could have projected after the deadline, but no real
    # owner projection combines due rechecks with the pre-deadline action/flags.
    changed_observation = {
        **accepted, "snapshot": {**accepted["snapshot"], "due_rechecks": live["due_rechecks"]},
    }
    with closing(sqlite3.connect(target / STORE.DATABASE, isolation_level=None)) as db:
        db.execute("UPDATE delivery SET observation=?", (STORE.encode(changed_observation),))
    rewrite_member_manifest(target)
    with pytest.raises(AGENDA.AgendaError, match="delivery observation"):
        EXPORT.verify_export(target, **selected, expected_revision=2)
    assert source_bytes(lane) == before_bytes


@POSIX
@pytest.mark.parametrize("legacy_projection", [False, True])
def test_actual_producer_crosses_revisit_deadline_during_mutation(
    lane: Path, monkeypatch: pytest.MonkeyPatch, legacy_projection: bool,
) -> None:
    frame, _, _ = current(lane)
    real_apply, real_project = EXPORT.owner.apply, EXPORT.owner.project_at
    first_revisit = datetime.now(timezone.utc) + timedelta(seconds=3)
    last_revisit = first_revisit + timedelta(seconds=2)
    first = update(lane, {
        "operation": "defer", "frame_id": frame, "decision_ref": "first pending decision",
        "decision_packet_ref": "first packet", "revisit_at": first_revisit.isoformat(),
        "evidence": proof(),
    })["transition"]["deferred_id"]
    update(lane, {
        "operation": "assign-root", "items": [{"definition_ref": "nested"}],
        "assignment_ref": "fixture independent work",
        "baseline_context": context({"content": "independent fixture input"}), "evidence": proof(),
    })
    second_frame, _, _ = current(lane)

    def delayed_apply(queue: object, change: dict) -> dict:
        transition = real_apply(queue, change)
        remaining = (first_revisit - datetime.now(timezone.utc)).total_seconds()
        assert 0 < remaining <= 3
        threading.Event().wait(remaining + 0.01)
        return transition

    def old_producer_projection(documents: dict, records: dict, **kwargs: object) -> object:
        # Exercise the historical producer's actual live-clock projection,
        # ignoring only the new producer's explicit acceptance-clock argument.
        return real_project(documents, records)

    monkeypatch.setattr(EXPORT.owner, "apply", delayed_apply)
    if legacy_projection:
        monkeypatch.setattr(EXPORT.owner, "project_at", old_producer_projection)
    result = update(lane, {
        "operation": "defer", "frame_id": second_frame, "decision_ref": "second pending decision",
        "decision_packet_ref": "second packet", "revisit_at": last_revisit.isoformat(),
        "evidence": proof(),
    })
    assert datetime.fromisoformat(result["committed_at"].replace("Z", "+00:00")) < first_revisit
    second = result["transition"]["deferred_id"]
    accepted = STORE.delivery_record(lane)[1]
    assert STORE.read(lane)["snapshot"]["due_rechecks"] == [first]
    assert accepted["snapshot"]["due_rechecks"] == ([first] if legacy_projection else [])
    original_rows, original_bytes = rows(lane), source_bytes(lane)
    # Read back after a second boundary: neither today's projection nor the
    # acceptance-time projection reproduces the older intermediate observation.
    remaining = (last_revisit - datetime.now(timezone.utc)).total_seconds()
    assert 0 < remaining <= 2
    threading.Event().wait(remaining + 0.01)
    assert STORE.read(lane)["snapshot"]["due_rechecks"] == [first, second]
    selected, target = selection(lane), destination(lane)
    manifest = EXPORT.export_store(lane, target, **selected, expected_revision=4)
    assert EXPORT.verify_export(target, **selected, expected_revision=4) == manifest
    assert rows(target) == original_rows and source_bytes(lane) == original_bytes
    assert STORE.delivery_record(target)[1] == accepted
    assert "projected_at" not in accepted and "projected_at" not in manifest


@POSIX
def test_actual_concurrent_writer_cannot_split_snapshot(lane: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = selection(lane)
    target = destination(lane)
    started, committed = threading.Event(), threading.Event()
    real_backup, real_inspect = EXPORT._backup, EXPORT._inspect
    futures = []

    def writer() -> None:
        started.set()
        outcome(lane)
        committed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        def capture(source: sqlite3.Connection, copied: sqlite3.Connection, budget: object) -> None:
            real_backup(source, copied, budget)
            futures.append(pool.submit(writer))
            assert started.wait(5)
            # The real writer has a separate connection/transaction; it can
            # commit only after the source's pinned read transaction closes.
            assert not committed.wait(0.05)

        def inspect(*args: object, **kwargs: object) -> dict:
            assert committed.wait(5)
            return real_inspect(*args, **kwargs)

        monkeypatch.setattr(EXPORT, "_backup", capture)
        monkeypatch.setattr(EXPORT, "_inspect", inspect)
        manifest = EXPORT.export_store(lane, target, **selected, expected_revision=1)
        futures[0].result(timeout=5)
    assert STORE.read(lane)["revision"] == 2
    assert manifest["revision"] == 1
    assert manifest["tables"]["operations"]["rows"] == 1
    assert EXPORT.verify_export(target, **selected, expected_revision=1) == manifest


@POSIX
@pytest.mark.parametrize("damage", [
    "unknown-table", "missing-delivery", "missing-operation", "missing-record",
    "changed-record", "changed-request", "changed-metadata", "changed-observation",
])
def test_verifier_rejects_rehashed_semantic_or_schema_corruption(lane: Path, damage: str) -> None:
    outcome(lane)
    selected = selection(lane)
    target = destination(lane)
    EXPORT.export_store(lane, target, **selected)
    with closing(sqlite3.connect(target / STORE.DATABASE, isolation_level=None)) as db:
        if damage == "unknown-table":
            db.execute("CREATE TABLE unknown (authority TEXT)")
        elif damage == "missing-delivery":
            db.execute("DELETE FROM delivery")
        elif damage == "missing-operation":
            db.execute("DELETE FROM operations WHERE revision=1")
        elif damage == "missing-record":
            db.execute("DELETE FROM agenda_records WHERE id=(SELECT id FROM agenda_records LIMIT 1)")
        elif damage == "changed-record":
            identity, text = db.execute("SELECT id,body FROM agenda_records LIMIT 1").fetchone()
            body = json.loads(text)
            body["actor"]["actor"] = "rewritten"
            db.execute("UPDATE agenda_records SET body=? WHERE id=?", (json.dumps(body), identity))
        elif damage == "changed-request":
            text = db.execute("SELECT request FROM operations WHERE revision=1").fetchone()[0]
            body = json.loads(text)
            body["request"]["expected_revision"] = 500
            db.execute("UPDATE operations SET request=? WHERE revision=1", (json.dumps(body),))
        elif damage == "changed-metadata":
            db.execute("UPDATE state SET metadata='{}'")
        else:
            db.execute("UPDATE delivery SET observation='{}'")
    rewrite_member_manifest(target)
    with pytest.raises(AGENDA.AgendaError):
        EXPORT.verify_export(target, **selected)
    corrupt_bytes = source_bytes(target)
    refused = destination(lane, f"refused-{damage}")
    with pytest.raises(AGENDA.AgendaError):
        EXPORT.export_store(target, refused, **selected)
    assert not (refused / EXPORT.MANIFEST).exists()
    assert source_bytes(target) == corrupt_bytes


@POSIX
def test_identity_revision_member_and_manifest_checks(lane: Path) -> None:
    selected = selection(lane)
    target = destination(lane)
    manifest = EXPORT.export_store(lane, target, **selected)
    with pytest.raises(AGENDA.AgendaError, match="another worktree"):
        EXPORT.verify_export(target, worktree="different", store_id=selected["store_id"])
    with pytest.raises(AGENDA.AgendaError, match="replaced"):
        EXPORT.verify_export(target, worktree=selected["worktree"], store_id="0" * 32)
    with pytest.raises(AGENDA.AgendaError, match="revision"):
        EXPORT.verify_export(target, **selected, expected_revision=2)
    with (target / STORE.DATABASE).open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(AGENDA.AgendaError, match="hash mismatch"):
        EXPORT.verify_export(target, **selected)
    # No absence or empty-directory case can produce an empty success.
    empty = destination(lane, "empty")
    with pytest.raises(AGENDA.AgendaError):
        EXPORT.verify_export(empty, **selected)
    target2 = destination(lane, "manifest-tamper")
    EXPORT.export_store(lane, target2, **selected)
    manifest["revision"] = 42
    (target2 / EXPORT.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AGENDA.AgendaError, match="disagree"):
        EXPORT.verify_export(target2, **selected)
    target3 = destination(lane, "missing-member")
    EXPORT.export_store(lane, target3, **selected)
    (target3 / STORE.DATABASE).rename(target3 / "retained-member")
    with pytest.raises(AGENDA.AgendaError, match="missing or undeclared"):
        EXPORT.verify_export(target3, **selected)


@POSIX
def test_no_initialization_adoption_or_unsafe_paths(lane: Path) -> None:
    selected = selection(lane)
    before = source_bytes(lane)
    missing = lane / "missing"
    empty_source = lane / "empty-source"
    empty_source.mkdir()
    for index, source in enumerate((missing, empty_source)):
        target = destination(lane, f"missing-{index}")
        with pytest.raises(AGENDA.AgendaError, match="exist"):
            EXPORT.export_store(source, target, **selected)
        assert not list(target.iterdir())
    nonempty = destination(lane, "nonempty")
    (nonempty / "owned").write_text("do not adopt", encoding="utf-8")
    with pytest.raises(AGENDA.AgendaError, match="empty"):
        EXPORT.export_store(lane, nonempty, **selected)
    assert (nonempty / "owned").read_text() == "do not adopt"
    link = lane / "link"
    link.symlink_to(lane, target_is_directory=True)
    with pytest.raises(AGENDA.AgendaError, match="symlink"):
        EXPORT.export_store(link, destination(lane, "link-target"), **selected)
    unsafe_destination = lane.parent / f"{lane.name}-unsafe-destination"
    unsafe_destination.symlink_to(nonempty, target_is_directory=True)
    with pytest.raises(AGENDA.AgendaError, match="symlink"):
        EXPORT.export_store(lane, unsafe_destination, **selected)
    with pytest.raises(AGENDA.AgendaError, match="disjoint|empty"):
        EXPORT.export_store(lane, lane, **selected)
    for index, arguments in enumerate((
        {"worktree": "wrong", "store_id": selected["store_id"]},
        {**selected, "expected_revision": 9},
        {"worktree": selected["worktree"], "store_id": "0" * 32},
    )):
        target = destination(lane, f"wrong-selection-{index}")
        with pytest.raises(AGENDA.AgendaError):
            EXPORT.export_store(lane, target, **arguments)
        assert not list(target.iterdir())
    assert all((lane / name).read_bytes() == raw for name, raw in before.items())
    assert not (missing / STORE.DATABASE).exists() and not list(empty_source.iterdir())


@POSIX
def test_readonly_source_and_explicit_legacy_refusal(
    lane: Path, documents: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = selection(lane)
    database = lane / STORE.DATABASE
    before = source_bytes(lane)
    database.chmod(0o400)
    target = destination(lane, "readonly")
    real_backup = EXPORT._backup

    def assert_readonly(source: sqlite3.Connection, copied: sqlite3.Connection, budget: object) -> None:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            source.execute("CREATE TABLE forbidden_write (value TEXT)")
        real_backup(source, copied, budget)

    monkeypatch.setattr(EXPORT, "_backup", assert_readonly)
    EXPORT.export_store(lane, target, **selected)
    assert source_bytes(lane) == before
    home = lane.parent / f"{lane.name}-legacy"
    home.mkdir(mode=0o700)
    STORE.mutate(home, request({"operation": "init", "documents": documents}, 0, "init"))
    legacy = STORE.read(home)
    with pytest.raises(AGENDA.AgendaError, match="versions disagree"):
        EXPORT.export_store(home, destination(lane, "legacy-export"),
                            worktree="fixture-lane", store_id=legacy["store_id"])
    assert STORE.read(home) == legacy


@POSIX
@pytest.mark.parametrize("failure", ["backup", "fsync", "size", "deadline"])
def test_interruption_never_publishes_success_or_changes_source(
    lane: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    selected = selection(lane)
    before = source_bytes(lane)
    target = destination(lane)

    def interrupted(*args: object, **kwargs: object) -> None:
        raise OSError("injected interruption")

    if failure == "backup":
        monkeypatch.setattr(EXPORT, "_backup", interrupted)
    elif failure == "fsync":
        monkeypatch.setattr(EXPORT.os, "fsync", interrupted)
    elif failure == "size":
        monkeypatch.setattr(EXPORT, "MAX_BYTES", 1)
    else:
        monkeypatch.setattr(EXPORT, "DEADLINE_SECONDS", 0)
    with pytest.raises(AGENDA.AgendaError):
        EXPORT.export_store(lane, target, **selected)
    assert not (target / EXPORT.MANIFEST).exists()
    assert source_bytes(lane) == before
    with pytest.raises(AGENDA.AgendaError):
        EXPORT.verify_export(target, **selected)


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory/publication acceptance")
def test_native_windows_complete_export_and_readback(lane: Path) -> None:
    target = destination(lane)
    before = source_bytes(lane)
    manifest = EXPORT.export_store(lane, target, **selection(lane))
    assert EXPORT.verify_export(target, **selection(lane)) == manifest
    assert source_bytes(lane) == before and not (target / EXPORT.PENDING).exists()


class NativeCall:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.result


@pytest.mark.parametrize("failure", [None, "open", "flush", "close"])
def test_windows_owned_directory_barrier_access_and_handle_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    from ctypes import wintypes

    kernel = SimpleNamespace(
        CreateFileW=NativeCall(wintypes.HANDLE(-1).value if failure == "open" else 42),
        FlushFileBuffers=NativeCall(failure != "flush"),
        CloseHandle=NativeCall(failure != "close"),
    )
    monkeypatch.setattr(EXPORT, "_windows_kernel", lambda: kernel)
    monkeypatch.setattr(EXPORT, "_windows_error", lambda: OSError("native directory barrier failed"))
    if failure:
        with pytest.raises(OSError, match="native directory barrier"):
            EXPORT._windows_flush_directory(tmp_path)
    else:
        EXPORT._windows_flush_directory(tmp_path)
    assert kernel.CreateFileW.calls == [
        (str(tmp_path), 0x40000000, 0x7, None, 3, 0x02000000, None),
    ]
    assert kernel.FlushFileBuffers.calls == ([] if failure == "open" else [(42,)])
    assert kernel.CloseHandle.calls == ([] if failure == "open" else [(42,)])


def test_windows_unsupported_directory_refuses_before_writing(
    lane: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = destination(lane)
    before = source_bytes(lane)

    def unsupported(path: Path) -> None:
        raise OSError("unsupported owned-directory barrier")

    monkeypatch.setattr(EXPORT, "_WINDOWS", True)
    monkeypatch.setattr(EXPORT, "_windows_flush_directory", unsupported)
    with pytest.raises(AGENDA.AgendaError, match="unsupported owned-directory"):
        EXPORT.export_store(lane, target, **selection(lane))
    assert not list(target.iterdir()) and source_bytes(lane) == before


@pytest.mark.parametrize("success", [True, False])
def test_windows_publication_is_write_through_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, success: bool,
) -> None:
    move = NativeCall(success)
    monkeypatch.setattr(EXPORT, "_windows_kernel", lambda: SimpleNamespace(MoveFileExW=move))
    monkeypatch.setattr(EXPORT, "_windows_error", lambda: OSError("publication refused"))
    source, target = tmp_path / "prepared", tmp_path / "manifest"
    if success:
        EXPORT._windows_publish(source, target)
    else:
        with pytest.raises(OSError, match="publication refused"):
            EXPORT._windows_publish(source, target)
    assert move.calls == [(str(source), str(target), 0x8)]
