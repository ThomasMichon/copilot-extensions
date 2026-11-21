"""Database access preserves native path identity and SQLite URI options."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import sys

import pytest

from update_driver import RequestSpec, TrustedRunner, drain, enqueue, status
from update_driver._sqlite import database_uri


@pytest.mark.parametrize("spelling", [
    r"C:\private space\percent%question?hash#\queue.sqlite3",
    r"\\?\C:\private space\percent%question?hash#\queue.sqlite3",
])
def test_local_windows_uri_has_no_namespace_authority(spelling: str) -> None:
    assert database_uri(PureWindowsPath(spelling), mode="rw") == (
        "file:///C:/private%20space/percent%25question%3Fhash%23/queue.sqlite3?mode=rw"
    )


@pytest.mark.parametrize("spelling", [
    r"\\server\share\private space\queue.sqlite3",
    r"\\?\UNC\server\share\private space\queue.sqlite3",
    r"\\?\unc\server\share\private space\queue.sqlite3",
])
def test_unc_uri_preserves_the_server_and_share(spelling: str) -> None:
    assert database_uri(PureWindowsPath(spelling), mode="ro") == (
        "file://server/share/private%20space/queue.sqlite3?mode=ro"
    )


def test_sqlite_modes_keep_absent_reads_noncreating_and_existing_reads_readonly(area: Path) -> None:
    path = area / "private % # queue.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(database_uri(path, mode="ro"), uri=True)
    assert not path.exists()
    with closing(sqlite3.connect(database_uri(path, mode="rwc"), uri=True)) as db:
        db.execute("CREATE TABLE evidence (value INTEGER)")
        db.execute("INSERT INTO evidence VALUES (7)")
        db.commit()
    with closing(sqlite3.connect(database_uri(path, mode="ro"), uri=True)) as db:
        assert db.execute("SELECT value FROM evidence").fetchone() == (7,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("INSERT INTO evidence VALUES (8)")


@pytest.mark.parametrize("extended", [False, True])
def test_queue_and_private_runner_keep_selected_database(area: Path, extended: bool) -> None:
    from update_driver import _runner

    root = area.resolve()
    if extended:
        if os.name != "nt":
            pytest.skip("native Windows filesystem spelling")
        if not str(root).startswith("\\\\?\\"):
            root = Path("\\\\?\\" + str(root))
    root /= "queue"
    request = enqueue(
        RequestSpec("synthetic"), runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
        root=root,
    )
    with pytest.raises(RuntimeError, match="runner has no active request"):
        _runner.execute(str(root), request.id, "", "")
    assert drain(root=root, executor=lambda transaction: 0) == 0
    assert status(request.id, root=root).exit_code == 0
