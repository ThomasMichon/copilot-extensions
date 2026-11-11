"""Installation-local indexed cleanup work; no directory census on allocation."""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path


@contextlib.contextmanager
def connection(installation: Path, *, private_owner):
    """Open only this cache layout's private catalog, refusing unknown schemas."""
    path = installation / "catalog.sqlite"
    created = False
    try:
        descriptor = private_owner.open_private(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        info = path.lstat()
        if not path.is_file() or path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Python cache catalog is not ordinary")
    else:
        os.close(descriptor)
        created = True
    database = None
    try:
        database = sqlite3.connect(path, timeout=0.1)
        database.execute("BEGIN IMMEDIATE")
        version = database.execute("PRAGMA user_version").fetchone()[0]
        if created:
            database.execute("CREATE TABLE entries (key TEXT PRIMARY KEY, owner BLOB NOT NULL)")
            database.execute("CREATE TABLE progress (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            database.execute(
                "CREATE TABLE work (id INTEGER PRIMARY KEY, key TEXT NOT NULL, "
                "directory TEXT NOT NULL, after TEXT NOT NULL DEFAULT '', "
                "UNIQUE(key, directory))",
            )
            database.execute("PRAGMA user_version = 1")
        elif version != 1:
            raise ValueError("Python cache catalog schema is unsupported")
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
