"""Owner-derived contributor provenance from a complete verified export."""

from __future__ import annotations

from contextlib import closing
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from test_agenda_export import (
    EXPORT, destination, rewrite_member_manifest, selection, source_bytes,
)
from test_agenda_v2_core import STORE, SKILL, current, lane as lane, proof, v2

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def wait(home: Path, actor: str | None, session: str | None) -> None:
    frame, item, _ = current(home)
    revision = STORE.read(home)["revision"]
    STORE.mutate(home, v2({
        "operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "fixture event",
        "next_action": "Recheck fixture event", "evidence": proof(),
    }, revision, f"wait-{revision}"), actor=actor, session=session)


def reader() -> object:
    return importlib.import_module("agenda_export_contributors")


def test_complete_contributors_preserve_nullable_and_noncanonical_identities(lane: Path) -> None:
    foreign = "\u03bb / external\n" + "s" * 1010
    assert len(foreign.encode()) == 1024
    wait(lane, None, None)
    wait(lane, "fixture author", None)
    wait(lane, "fixture author", foreign)
    wait(lane, "fixture author", foreign)
    selected, target = selection(lane), destination(lane)
    manifest = EXPORT.export_store(lane, target, **selected)
    before_source, before_export = source_bytes(lane), source_bytes(target)

    projection = reader().read_contributors(target, **selected, expected_revision=5)

    assert projection == {
        "schema": "efforts.agenda-export-contributors", "version": 1,
        "completeness": "complete",
        "manifest_sha256": hashlib.sha256((target / EXPORT.MANIFEST).read_bytes()).hexdigest(),
        "source": {"worktree": "fixture-lane", "store_id": selected["store_id"], "revision": 5},
        "contributors": [
            {"actor": None, "session": "fixture-head", "qualification": "caller-asserted",
             "first_revision": 1, "last_revision": 1, "operations": 1},
            {"actor": None, "session": None, "qualification": "unknown",
             "first_revision": 2, "last_revision": 2, "operations": 1},
            {"actor": "fixture author", "session": None, "qualification": "caller-asserted",
             "first_revision": 3, "last_revision": 3, "operations": 1},
            {"actor": "fixture author", "session": foreign, "qualification": "caller-asserted",
             "first_revision": 4, "last_revision": 5, "operations": 2},
        ],
    }
    assert EXPORT.verify_export(target, **selected) == manifest
    assert source_bytes(lane) == before_source and source_bytes(target) == before_export


@pytest.mark.parametrize("damage", ["request", "result", "bytes", "selection"])
def test_contributors_never_bypass_complete_export_verification(lane: Path, damage: str) -> None:
    selected, target = selection(lane), destination(lane)
    EXPORT.export_store(lane, target, **selected)
    if damage in ("request", "result"):
        with closing(sqlite3.connect(target / STORE.DATABASE, isolation_level=None)) as db:
            text = db.execute(f"SELECT {damage} FROM operations").fetchone()[0]
            value = json.loads(text)
            identity = value["identity"] if damage == "request" else value["actor"]
            identity["session"] = "rewritten-attribution"
            db.execute(f"UPDATE operations SET {damage}=?", (STORE.encode(value),))
        rewrite_member_manifest(target, tables=True)
    elif damage == "bytes":
        with (target / STORE.DATABASE).open("ab") as stream:
            stream.write(b"changed")
    else:
        selected["worktree"] = "different-lane"
    before = source_bytes(target)
    with pytest.raises(EXPORT.AgendaError):
        reader().read_contributors(target, **selected)
    assert source_bytes(target) == before


@pytest.mark.parametrize("limit", ["MAX_CONTRIBUTORS", "MAX_PROJECTION_BYTES"])
def test_projection_overflow_refuses_whole_result_without_breaking_old_verify(
    lane: Path, monkeypatch: pytest.MonkeyPatch, limit: str,
) -> None:
    wait(lane, None, None)
    selected, target = selection(lane), destination(lane)
    manifest = EXPORT.export_store(lane, target, **selected)
    monkeypatch.setattr(reader(), limit, 1)
    with pytest.raises(EXPORT.AgendaError, match="contributor.*budget"):
        reader().read_contributors(target, **selected)
    assert EXPORT.verify_export(target, **selected) == manifest


def test_projection_uses_the_verified_connection_and_checks_post_projection_bytes(
    lane: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected, target = selection(lane), destination(lane)
    EXPORT.export_store(lane, target, **selected)
    real_inspect = EXPORT._inspect
    real_project = reader()._project
    inspected = []

    def inspect(db: sqlite3.Connection, *args: object) -> dict:
        inspected.append(db)
        return real_inspect(db, *args)

    def project(db: sqlite3.Connection, *args: object) -> dict:
        assert inspected == [db] and db.in_transaction
        assert db.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE operations SET revision=revision")
        result = real_project(db, *args)
        with (target / STORE.DATABASE).open("ab") as stream:
            stream.write(b"changed-after-projection")
        return result

    monkeypatch.setattr(EXPORT, "_inspect", inspect)
    monkeypatch.setattr(reader(), "_project", project)
    with pytest.raises(EXPORT.AgendaError, match="changed during verification"):
        reader().read_contributors(target, **selected)


def test_projection_has_no_history_page_ceiling(lane: Path) -> None:
    for _ in range(100):
        wait(lane, None, "fixture-head")
    selected, target = selection(lane), destination(lane)
    EXPORT.export_store(lane, target, **selected)
    projection = reader().read_contributors(target, **selected)
    assert projection["source"]["revision"] == 101
    assert projection["contributors"] == [{
        "actor": None, "session": "fixture-head", "qualification": "caller-asserted",
        "first_revision": 1, "last_revision": 101, "operations": 101,
    }]


def test_projection_retains_the_original_verification_deadline(
    lane: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected, target = selection(lane), destination(lane)
    EXPORT.export_store(lane, target, **selected)
    real_inspect = EXPORT._inspect

    def inspect(*args: object) -> dict:
        result = real_inspect(*args)
        args[-1].deadline = 0
        return result

    monkeypatch.setattr(EXPORT, "_inspect", inspect)
    with pytest.raises(EXPORT.AgendaError, match="deadline exceeded"):
        reader().read_contributors(target, **selected)


def test_actual_cli_negotiates_projection_and_preserves_old_manifest(lane: Path) -> None:
    selected, target = selection(lane), destination(lane)
    manifest = EXPORT.export_store(lane, target, **selected)
    script = SKILL / "scripts" / "agenda_export_cli.py"

    def invoke(*args: str) -> subprocess.CompletedProcess[str]:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run([sys.executable, "-B", str(script), *args],
                              capture_output=True, text=True, timeout=10, creationflags=flags)

    advertised = json.loads(invoke("info").stdout)
    assert advertised["capabilities"] == ["agenda-complete-export-v1"]
    assert advertised["optional_capabilities"] == ["agenda-export-contributors-v1"]
    args = ["--destination", str(target), "--worktree", selected["worktree"],
            "--store-id", selected["store_id"], "--expected-revision", "1",
            "--contract-version", "1"]
    projected = invoke("contributors", *args)
    assert projected.returncode == 0, projected.stderr
    response = json.loads(projected.stdout)
    assert response["ok"] and response["contributors"] == reader().read_contributors(target, **selected)
    assert "manifest" not in response
    assert json.loads(invoke("verify", *args).stdout)["manifest"] == manifest
    assert invoke("contributors", *args, "--state-home", str(lane)).returncode == 2
    assert invoke("contributors", *args, "--contract-ver", "1").returncode == 2
    args[args.index("--expected-revision") + 1] = "2"
    refused = invoke("contributors", *args)
    assert refused.returncode == 2
    response = json.loads(refused.stdout)
    assert response["ok"] is False and "contributors" not in response and "manifest" not in response
