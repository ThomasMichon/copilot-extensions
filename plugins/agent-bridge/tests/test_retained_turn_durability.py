"""Original DB admissions only; no native-send or power-loss qualification."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from agent_bridge.db import Database
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.native_keyed_admission import KeyedNativePrompt
from agent_bridge.runtime_custody import RuntimeAdmission
from test_runtime_custody import close, enrolled, session


def keyed_request() -> KeyedNativePrompt:
    return KeyedNativePrompt(
        invocation_key="original-invocation", expected_native_session_id="native-a",
        prompt="original prompt",
        binding={
            "task_id": "task-a", "admission_generation": 0,
            "original_reservation_key": "reservation-a", "creation_key": "creation-a",
            "allocation_revision": 1, "expected_turn_index": 0, "origin_kind": "created",
            "worktree_id": "worktree-a", "project": "fixture", "path": ".",
            "creation_nonce": "nonce-a",
        },
    )


@contextmanager
def observing_commits(db: Database) -> Iterator[list[int]]:
    conn = db._get_conn()
    observed: list[int] = []

    def trace(statement: str) -> None:
        if statement == "COMMIT":
            observed.append(conn.execute("PRAGMA synchronous").fetchone()[0])

    conn.set_trace_callback(trace)
    try:
        yield observed
    finally:
        conn.set_trace_callback(None)


def admit(
    db: Database, kind: str, admission: RuntimeAdmission | None, *,
    queue_id: int | None = None,
) -> dict[str, Any] | None:
    if kind == "session":
        session(db, "new-session", admission)
        return None
    request = keyed_request() if kind == "keyed" else None
    return db.create_turn(
        "s", 0, "original prompt", 2.0, runtime_admission=admission,
        pending_prompt_id=queue_id, native_admission=request,
        delivery_id=request.delivery_id() if request else "original-delivery",
    )


@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize("associated", [False, True])
@pytest.mark.parametrize("kind", ["session", "turn", "queued", "keyed"])
def test_retained_admission_commit_uses_full_sync_only_when_associated(
    tmp_db: Database, adopted: bool, associated: bool, kind: str,
) -> None:
    _, _, admission = enrolled(tmp_db)
    session(tmp_db, "s")
    queue_id = tmp_db.enqueue_prompt("s", "original prompt", 1.0) if kind == "queued" else None
    passive = Database(tmp_db.db_path, read_only=True) if adopted else None
    db = open_existing_writable(passive) if passive else tmp_db
    try:
        with observing_commits(db) as observed:
            admit(db, kind, admission if associated else None, queue_id=queue_id)
        assert observed == [2 if associated else 1]
        assert db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1
        if kind == "queued":
            assert db.peek_pending_prompt("s") is None
            assert db.get_turn("s", 0)["runtime_custody_json"] == (
                admission.document() if associated else None
            )
    finally:
        if passive:
            db.close()
            passive.close()


@pytest.mark.parametrize("kind", ["session", "turn", "queued", "keyed"])
@pytest.mark.parametrize("refusal", ["closed", "foreign"])
def test_refused_retained_admission_rolls_back_and_restores_sync(
    tmp_db: Database, kind: str, refusal: str,
) -> None:
    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "s")
    queue_id = tmp_db.enqueue_prompt("s", "original prompt", 1.0) if kind == "queued" else None
    if refusal == "closed":
        close(tmp_db, owner, producer)
    else:
        admission = replace(admission, producer="not-enrolled")
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        admit(tmp_db, kind, admission, queue_id=queue_id)
    assert not tmp_db._get_conn().in_transaction
    assert tmp_db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1
    assert tmp_db.get_session("new-session") is None
    assert tmp_db.get_turns("s") == []
    if kind == "queued":
        assert tmp_db.peek_pending_prompt("s")["id"] == queue_id


def test_keyed_retained_retry_recovers_original_receipt_after_closure(tmp_db: Database) -> None:
    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "s")
    original = admit(tmp_db, "keyed", admission)
    assert original is not None
    close(tmp_db, owner, producer)
    with observing_commits(tmp_db) as observed:
        recovered = admit(tmp_db, "keyed", admission)
    assert recovered is not None
    assert original["inserted"] is True and recovered["inserted"] is False
    assert original["receipt"] == recovered["receipt"]
    assert len(tmp_db.get_turns("s")) == 1
    assert tmp_db.get_turn("s", 0)["runtime_custody_json"] == admission.document()
    assert observed == []
    assert tmp_db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1


@pytest.mark.parametrize("policy", [0, 1, 2, 3])
def test_retained_admission_preserves_original_connection_policy(
    tmp_db: Database, policy: int,
) -> None:
    _, _, admission = enrolled(tmp_db)
    conn = tmp_db._get_conn()
    conn.execute(f"PRAGMA synchronous={policy}")
    with observing_commits(tmp_db) as observed:
        admit(tmp_db, "session", admission)
    assert observed == [max(2, policy)]
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == policy


@pytest.mark.parametrize("kind", ["session", "turn", "queued", "keyed"])
def test_failed_retained_commit_restores_policy_and_original_queue(
    tmp_db: Database, kind: str,
) -> None:
    _, _, admission = enrolled(tmp_db)
    session(tmp_db, "s")
    queue_id = tmp_db.enqueue_prompt("s", "original prompt", 1.0) if kind == "queued" else None
    conn = tmp_db._get_conn()

    def deny_commit(action: int, first: str | None, *_args: object) -> int:
        return sqlite3.SQLITE_DENY if (
            action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT"
        ) else sqlite3.SQLITE_OK

    conn.set_authorizer(deny_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            admit(tmp_db, kind, admission, queue_id=queue_id)
    finally:
        conn.set_authorizer(None)
    assert not conn.in_transaction
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert tmp_db.get_session("new-session") is None
    assert tmp_db.get_turns("s") == []
    if queue_id is not None:
        assert tmp_db.peek_pending_prompt("s")["id"] == queue_id


def test_real_process_lost_admission_replies_preserve_original_rows(tmp_path: Path) -> None:
    db = Database(tmp_path / "retained.db")
    owner, producer, admission = enrolled(db)
    for sid in ("direct", "queued", "keyed"):
        session(db, sid)
    queue_id = db.enqueue_prompt("queued", "original prompt", 1.0)
    child = """
import json, os, sys
from agent_bridge.db import Database
from agent_bridge.native_keyed_admission import KeyedNativePrompt
from agent_bridge.runtime_custody import RuntimeAdmission
db = Database(sys.argv[1])
admission = RuntimeAdmission(**json.loads(sys.argv[2]))
request = KeyedNativePrompt.model_validate_json(sys.argv[4])
db.create_session("new-session", "new", None, ".", "local", "idle", 1.0,
                  runtime_admission=admission)
db.create_turn("direct", 0, "original prompt", 2.0, runtime_admission=admission)
db.create_turn("queued", 0, "original prompt", 2.0, runtime_admission=admission,
               pending_prompt_id=int(sys.argv[3]))
db.create_turn("keyed", 0, request.prompt, 2.0, runtime_admission=admission,
               native_admission=request, delivery_id=request.delivery_id())
os._exit(0)
"""
    try:
        subprocess.run(
            [sys.executable, "-c", child, str(db.db_path), admission.document(),
             str(queue_id), keyed_request().model_dump_json()],
            check=True, capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        close(db, owner, producer)
        reopened = Database(db.db_path)
        try:
            assert reopened.peek_pending_prompt("queued") is None
            assert reopened.get_session("new-session")["runtime_custody_json"] == admission.document()
            for sid in ("direct", "queued", "keyed"):
                row, = reopened.get_turns(sid)
                assert row["runtime_custody_json"] == admission.document()
                assert row["prompt"] == "original prompt" and row["completed_at"] is None
            recovered = reopened.create_turn(
                "keyed", 0, "original prompt", 2.0, native_admission=keyed_request(),
                delivery_id=keyed_request().delivery_id(), runtime_admission=admission,
            )
            assert recovered["inserted"] is False and recovered["receipt"]["turn_index"] == 0
            report = reopened.runtime_custody_inventory(owner)
            assert len(report["rows"]["turns"]) == 3 and report["restore_qualified"] is False
        finally:
            reopened.close()
    finally:
        db.close()
