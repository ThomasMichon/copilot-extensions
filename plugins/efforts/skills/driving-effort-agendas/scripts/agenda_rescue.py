#!/usr/bin/env python3
"""Explicit, history-preserving relocation of an exported format-2 agenda."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from agenda import AgendaError, _text, decode
import agenda_export as export
import agenda_history as history
import agenda_managed as managed
import agenda_store as store

CAPABILITY = "agenda-managed-rescue-v1"
SCHEMA = "efforts.agenda-managed-rescue"
TABLE = "agenda_relocation"


def _identity(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_dev, info.st_ino]


def _record(db: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = db.execute(f"SELECT body FROM {TABLE} WHERE singleton=1").fetchall()
        if len(rows) != 1:
            raise AgendaError("relocation has no unique recovery record")
        return decode(rows[0][0].encode(), "relocation recovery", limit=131072)
    except sqlite3.Error as exc:
        raise AgendaError("fenced store has no supported relocation recovery record") from exc


def _fence(db: sqlite3.Connection, record: dict[str, Any]) -> None:
    raw = store.encode(record)
    decode(raw.encode(), "relocation recovery", limit=131072)
    db.execute(f"CREATE TABLE {TABLE} (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
               "body TEXT NOT NULL)")
    db.execute(f"INSERT INTO {TABLE} VALUES (1,?)", (raw,))
    db.execute(f"PRAGMA user_version={store.RELOCATED_VERSION}")


def _placement_identity(placement: dict[str, Any]) -> dict[str, Any]:
    """Bind stable identity, not additive provider presentation/observation fields."""
    result = {key: placement[key] for key in (
        "domain", "lane", "store_id", "creation_nonce", "worktree_id",
        "placement_generation", "state_home",
    )}
    result["scope"] = {key: placement["scope"].get(key) for key in (
        "project", "repository", "repository_id", "marketplace_id",
        "project_root_device", "project_root_inode",
    )}
    return result


def _members(home: Path) -> None:
    allowed = {store.DATABASE + suffix for suffix in ("", "-journal", "-wal", "-shm")}
    if any(path.name not in allowed for path in home.iterdir()):
        raise AgendaError("relocation destination contains unrelated files; preserve and reconcile")


def _reserve(home: Path, request: dict[str, Any]) -> list[int]:
    if _identity(home) != request["destination_directory"]:
        raise AgendaError("allocated destination directory changed")
    _members(home)
    path = store.safe_path(home / store.DATABASE)
    if path.exists():
        with store.transaction(home, readonly=True, relocation=True) as db:
            identity = _identity(path)
            if db.execute("PRAGMA user_version").fetchone()[0] != store.RELOCATED_VERSION or (
                    _record(db) != {"request": request, "database_identity": identity}):
                raise AgendaError("relocation destination is not this attempt's reserved store")
            return identity
    if any(home.iterdir()):
        raise AgendaError("first relocation reservation requires a genuinely empty destination")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        info = os.fstat(fd)
        identity = [info.st_dev, info.st_ino]
    finally:
        os.close(fd)
    with store.transaction(home, create=True, write=True, store_version=2) as db:
        if _identity(path) != identity or _identity(home) != request["destination_directory"]:
            raise AgendaError("destination changed during reservation")
        if db.execute("SELECT 1 FROM state").fetchone():
            raise AgendaError("relocation destination was initialized by another writer")
        _fence(db, {"request": request, "database_identity": identity})
        return identity


def _prefix(
    target: sqlite3.Connection, snapshot: sqlite3.Connection, home: Path,
    request: dict[str, Any], budget: export._Budget,
) -> int:
    """An exact retry may observe later accepted work, never replace it."""
    export._schema(target)
    export._bounded_rows(target)
    current = store._state(target, home)
    if current["store_id"] != request["store_id"] or (
            current["snapshot"]["worktree"] != request["worktree"]) or (
            current["revision"] < request["revision"]):
        raise AgendaError("relocated store no longer has the selected identity/history")
    source, _revision, _committed = history._header(target, request["worktree"], request["store_id"])
    export._ledger(target, current, source, budget)
    for table, key in (("operations", "operation_id"), ("agenda_records", "id")):
        columns = ",".join(export.TABLES[table])
        for row in snapshot.execute(f"SELECT {columns} FROM {table}"):
            budget.check()
            index = export.TABLES[table].index(key)
            if target.execute(f"SELECT {columns} FROM {table} WHERE {key}=?", (row[index],)).fetchone() != row:
                raise AgendaError("relocated store changed accepted source history")
    if target.execute("SELECT count(*) FROM operations WHERE revision<=?",
                      (request["revision"],)).fetchone()[0] != request["revision"]:
        raise AgendaError("relocated store has an incomplete accepted history prefix")
    return store._revision(current["revision"], "relocated revision")


def _activate(
    home: Path, snapshot: sqlite3.Connection, record: dict[str, Any],
    manifest: dict[str, Any], budget: export._Budget,
) -> tuple[int, bool]:
    request = record["request"]
    if _identity(home) != request["destination_directory"]:
        raise AgendaError("reserved destination directory identity changed")
    _members(home)
    if _identity(store.safe_path(home / store.DATABASE)) != record["destination_database"]:
        raise AgendaError("reserved destination database identity changed; preserve and reconcile")
    with store.transaction(home, write=True, relocation=True) as db:
        db.set_progress_handler(budget.progress, 1000)
        if _identity(store.safe_path(home / store.DATABASE)) != record["destination_database"] or (
                _identity(home) != request["destination_directory"]):
            raise AgendaError("destination database changed during open")
        if db.execute("PRAGMA user_version").fetchone()[0] == 2:
            return _prefix(db, snapshot, home, request, budget), False
        if _record(db) != {"request": request, "database_identity": record["destination_database"]}:
            raise AgendaError("reserved destination belongs to another relocation")
        for table, columns in export.TABLES.items():
            if db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise AgendaError("reserved destination contains unexpected accepted data")
            query = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
            for row in snapshot.execute(f"SELECT {','.join(columns)} FROM {table}"):
                budget.check()
                db.execute(query, row)
        db.execute(f"DROP TABLE {TABLE}")
        db.execute("PRAGMA user_version=2")
        export._schema(db)
        if export._table_hashes(db, budget) != manifest["tables"]:
            raise AgendaError("destination does not preserve the complete exported store")
        return request["revision"], True


def relocate(
    source_home: Path, export_home: Path, *, provider_python: str, project: str,
    worktree: str, creation_nonce: str, lane: str, store_id: str,
    expected_revision: int, operation_id: str,
) -> dict[str, Any]:
    """Fence the source before activating one exact owner-allocated destination.

    The caller must quiesce writers/publishers and retain both homes and the
    export after an unknown result. An exact retry resumes this operation; it
    never rolls the destination back or replays accepted agenda operations.
    """
    _text(operation_id, "relocation operation identity")
    if store._revision(expected_revision, "expected revision") < 1:
        raise AgendaError("relocation requires an accepted expected revision")
    if len(operation_id.encode()) > 1024:
        raise AgendaError("relocation operation identity exceeds 1 KiB")
    source_home = store.safe_path(Path(source_home), directory=True)
    export_home = store.safe_path(Path(export_home), directory=True)
    if any(store.safe_path(source_home / name).exists()
           for name in ("cursor.json", "agenda.init-recovery.json")):
        raise AgendaError("manual or legacy JSON lanes are not supported relocation sources")
    with export._verified_export(
        export_home, worktree=worktree, store_id=store_id, expected_revision=expected_revision,
    ) as (manifest, raw, snapshot, budget):
        with store.transaction(source_home, readonly=True, relocation=True) as source:
            if source.execute("PRAGMA user_version").fetchone()[0] == 2:
                _identity_header, revision, _committed = history._header(source, worktree, store_id)
                if revision != manifest["revision"]:
                    raise AgendaError("source changed after export; no writer cutover")
        placement = managed.allocate(provider_python, project, worktree, creation_nonce, lane, store_id)
        destination = store.safe_path(Path(placement["state_home"]), directory=True)
        homes = (source_home, export_home, destination)
        if any(a == b or a in b.parents or b in a.parents
               for index, a in enumerate(homes) for b in homes[index + 1:]):
            raise AgendaError("relocation source, export and destination must be disjoint")
        request = {
            "schema": SCHEMA, "version": 1, "operation_id": operation_id,
            "source_home": str(source_home), "source_directory": _identity(source_home),
            "source_database": _identity(store.safe_path(source_home / store.DATABASE)),
            "export_home": str(export_home), "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "placement": _placement_identity(placement), "destination_directory": _identity(destination),
            "worktree": worktree, "store_id": store_id, "revision": manifest["revision"],
        }
        # Both homes must support the same durability contract as complete export.
        with export._pinned_directory(source_home), export._pinned_directory(destination) as directory_fd:
            with store.transaction(source_home, write=True, relocation=True) as source:
                if _identity(store.safe_path(source_home / store.DATABASE)) != request["source_database"] or (
                        _identity(source_home) != request["source_directory"]):
                    raise AgendaError("source database changed during open")
                version = source.execute("PRAGMA user_version").fetchone()[0]
                if version == 2:
                    export._schema(source)
                    if export._table_hashes(source, budget) != manifest["tables"]:
                        raise AgendaError("source changed after export; no writer cutover")
                    destination_id = _reserve(destination, request)
                    export._sync_directory(destination, directory_fd)
                    record = {"request": request, "destination_database": destination_id}
                    _fence(source, record)
                else:
                    record = _record(source)
                    if record.get("request") != request:
                        raise AgendaError("source is fenced for a different relocation; preserve and reconcile")
                    if export._table_hashes(source, budget) != manifest["tables"]:
                        raise AgendaError("fenced source history differs from its complete export")
            # The source commit is durable before destination activation. A
            # previously loaded old writer rechecks user_version after its lock.
            revision, activated = _activate(destination, snapshot, record, manifest, budget)
            return {"schema": SCHEMA, "version": 1, "persisted": True,
                    "operation_id": operation_id, "source_fenced": True,
                    "state_home": str(destination), "placement": placement,
                    "store_id": store_id, "source_revision": manifest["revision"],
                    "revision": revision, "activated": activated,
                    "source_retained": True, "export_retained": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", allow_abbrev=False)
    move = commands.add_parser("relocate", allow_abbrev=False)
    move.add_argument("--source-home", type=Path, required=True)
    move.add_argument("--export-home", type=Path, required=True)
    for name in ("provider-python", "project", "worktree", "creation-nonce", "lane",
                 "store-id", "operation-id"):
        move.add_argument(f"--{name}", required=True)
    move.add_argument("--expected-revision", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            result = {"schema": SCHEMA, "version": 1, "capabilities": [CAPABILITY]}
        else:
            values = vars(args)
            values.pop("command")
            result = relocate(**values)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))
        return 0
    except (AgendaError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"agenda rescue: {exc}; retain source, export and destination", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
