"""Installation-local indexed cleanup work; no directory census on allocation."""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path

from . import python_cache_permissions as permissions


@contextlib.contextmanager
def connection(installation: Path, *, private_owner, check_budget=None):
    """Open only this cache layout's private catalog, refusing unknown schemas."""
    check_budget = check_budget or (lambda: None)
    check_budget()
    path = installation / "catalog.sqlite"
    try:
        descriptor = private_owner.open_private(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    for candidate in (path, *(Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
        check_budget()
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        check_budget()
        permissions.check_file(candidate)
    database = None
    try:
        check_budget()
        database = sqlite3.connect(path, timeout=0.1)
        check_budget()
        database.execute("BEGIN IMMEDIATE")
        check_budget()
        version = database.execute("PRAGMA user_version").fetchone()[0]
        check_budget()
        if version == 0 and database.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is None:
            check_budget()
            database.execute("CREATE TABLE entries (key TEXT PRIMARY KEY, owner BLOB NOT NULL)")
            check_budget()
            database.execute("CREATE TABLE progress (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            check_budget()
            database.execute(
                "CREATE TABLE work (id INTEGER PRIMARY KEY, key TEXT NOT NULL, "
                "directory TEXT NOT NULL, after TEXT NOT NULL DEFAULT '', "
                "UNIQUE(key, directory))",
            )
            check_budget()
            database.execute("PRAGMA user_version = 1")
        elif version != 1:
            raise ValueError("Python cache catalog schema is unsupported")
        check_budget()
        yield database
        database.commit()
    except sqlite3.Error as error:
        if database is not None:
            database.rollback()
        raise ValueError("Python cache catalog is unavailable or invalid") from error
    except BaseException:
        if database is not None:
            database.rollback()
        raise
    finally:
        if database is not None:
            database.close()


def register(installation: Path, key: str, owner: bytes, *, private_owner) -> None:
    """Index an allocation before its completion record can enable bytecode."""
    with connection(installation, private_owner=private_owner) as database:
        database.execute("INSERT OR IGNORE INTO entries(key, owner) VALUES (?, ?)", (key, owner))
        existing = database.execute("SELECT owner FROM entries WHERE key = ?", (key,)).fetchone()
        if existing is None or existing[0] != owner:
            raise ValueError("Python cache catalog ownership differs")
