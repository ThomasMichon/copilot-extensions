"""Independent content readback, not equal-cursor or transport-ACK inference."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from zdd.private_protocol import Deadline, PrivateIPCError

from agent_bridge.acp_continuation import capture_idle_continuation
from agent_bridge.acp_durability import (
    capture_idle_durability, decode_idle_durability, verify_idle_durability,
)
from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.private_commit import verify_host_commit
from agent_bridge.session_host.private_receipt import decode_host_receipt
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from test_acp_idle_continuation import connected, settle
from test_predecessor_runtime import NativeChannel


@asynccontextmanager
async def durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Session, Database, Any, dict[str, object]]]:
    async with connected(monkeypatch) as (client, events, receipt, _send, _reader):
        db = Database(tmp_path / "synthetic.db")
        try:
            db.create_session(
                "surviving-session", "synthetic", None, None, "local", "idle", 1.0,
            )
            db.update_session_acp_id("surviving-session", "surviving-acp")
            events._db = db
            for event in events.get_events():
                db.append_event("surviving-session", event.id, event.event, event.data, event.timestamp)
            db.create_turn("surviving-session", 0, "synthetic prompt", 1.0)
            result = await client.send_prompt("synthetic prompt")
            await settle(client)
            db.update_turn(
                "surviving-session", 0, response_text=result["response_text"],
                thought_text=result["thought_text"], stop_reason=result["stop_reason"],
                tool_calls_json=json.dumps(result["tool_calls"]), completed_at=2.0,
            )
            db.flush()
            session = Session(
                "surviving-session", "synthetic", SpawnTarget(cwd="/synthetic"),
                preserve_target=True,
            )
            session.status, session.client = SessionStatus.IDLE, client
            session.event_log, session.acp_session_id = events, "surviving-acp"
            session.turn_count = 1
            native = receipt()
            payload = capture_idle_continuation(client, native, events, Deadline(5))
            yield session, db, native, payload
        finally:
            events._db = None
            db.close()


def receiver(db: Database) -> tuple[Database, EventLog]:
    passive = Database(db.db_path, read_only=True)
    # The receiver reads actual committed bytes, not the source's live log.
    return passive, EventLog.from_db(
        passive, "surviving-session", acp_session_id="surviving-acp",
    )


def test_real_sdk_event_and_turn_contents_have_independent_committed_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with durable(tmp_path, monkeypatch) as (session, db, native, continuation):
            proof = await capture_idle_durability(session, continuation, native, Deadline(5))
            state = decode_idle_durability(proof)
            assert state.turns[0].response_text == "actual-reducer-text"
            assert state.turns[0].thought_text == "actual-thought"
            assert state.turns[0].tool_calls[0]["content"] == ["tool-result"]
            assert "actual-reducer-text" not in repr(state)
            assert state.next_event_id == session.event_log._next_id
            passive, log = receiver(db)
            try:
                observed = await verify_idle_durability(proof, continuation, log, passive, Deadline(5))
                assert observed == state and passive.read_only
                assert passive._writer_thread is None
                assert session.client.is_running
            finally:
                passive.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["receiver-event", "database-event", "database-turn", "new-turn", "queue"])
def test_equal_ids_do_not_hide_content_or_completed_turn_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    async def scenario() -> None:
        async with durable(tmp_path, monkeypatch) as (session, db, native, continuation):
            proof = await capture_idle_durability(session, continuation, native, Deadline(5))
            passive, log = receiver(db)
            try:
                cursor = log._next_id
                if mutation == "receiver-event":
                    log._events[-1].data = {"content": "different"}
                elif mutation == "database-event":
                    db.execute_write(
                        "UPDATE events SET data_json=? WHERE session_id=? AND event_id=?",
                        ('{"content":"different"}', session.session_id, cursor - 1),
                    )
                elif mutation == "database-turn":
                    db.update_turn(session.session_id, 0, response_text="different")
                elif mutation == "new-turn":
                    db.create_turn(session.session_id, 1, "pending", 3.0)
                else:
                    db.execute_write(
                        "INSERT INTO pending_prompts(session_id,prompt,created_at) VALUES(?,?,?)",
                        (session.session_id, "queued", 3.0),
                    )
                with pytest.raises(PrivateIPCError):
                    await verify_idle_durability(proof, continuation, log, passive, Deadline(5))
                assert log._next_id == cursor and passive._writer_thread is None
            finally:
                passive.close()
    asyncio.run(scenario())


def test_uncommitted_event_refuses_without_flushing_or_blocking_source_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with durable(tmp_path, monkeypatch) as (session, db, native, _continuation):
            db._write_lock.acquire()
            try:
                session.event_log.append("synthetic-extra", {"content": "not yet committed"})
                continuation = capture_idle_continuation(
                    session.client, native, session.event_log, Deadline(5),
                )
                with pytest.raises(PrivateIPCError):
                    await asyncio.wait_for(
                        capture_idle_durability(session, continuation, native, Deadline(2)), 3,
                    )
                assert db._event_write_q.unfinished_tasks > 0
            finally:
                db._write_lock.release()
    asyncio.run(scenario())


def test_equal_contents_in_another_database_do_not_establish_file_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with durable(tmp_path, monkeypatch) as (session, db, native, continuation):
            proof = await capture_idle_durability(session, continuation, native, Deadline(5))
            duplicate = tmp_path / "different-file.db"
            with sqlite3.connect(duplicate) as copy_conn:
                db._get_conn().backup(copy_conn)
            passive = Database(duplicate, read_only=True)
            try:
                log = EventLog.from_db(passive, session.session_id, acp_session_id=session.acp_session_id)
                with pytest.raises(PrivateIPCError):
                    await verify_idle_durability(proof, continuation, log, passive, Deadline(5))
            finally:
                passive.close()
    asyncio.run(scenario())


def test_capture_refuses_oversize_database_contents_before_materializing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zdd.private_protocol import MAX_FRAME_BYTES, Refusal

    async def scenario() -> None:
        async with durable(tmp_path, monkeypatch) as (session, db, native, continuation):
            db.update_turn(session.session_id, 0, response_text="x" * MAX_FRAME_BYTES)
            with pytest.raises(PrivateIPCError) as caught:
                await capture_idle_durability(session, continuation, native, Deadline(5))
            assert caught.value.reason is Refusal.SIZE
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["incomplete", "reducer", "count", "task", "gap", "no-db"])
def test_capture_refuses_incomplete_manager_or_content_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    async def scenario() -> None:
        async with durable(tmp_path, monkeypatch) as (session, db, native, continuation):
            task = None
            try:
                if mutation == "incomplete":
                    db.execute_write(
                        "UPDATE turns SET completed_at=NULL WHERE session_id=?", (session.session_id,),
                    )
                elif mutation == "reducer":
                    db.update_turn(session.session_id, 0, thought_text="not the captured thought")
                elif mutation == "count":
                    session.turn_count = 2
                elif mutation == "task":
                    task = session._prompt_task = asyncio.create_task(asyncio.sleep(0.01))
                elif mutation == "gap":
                    db.execute_write(
                        "DELETE FROM events WHERE session_id=? AND event_id=1", (session.session_id,),
                    )
                    session.event_log._events.pop(0)
                else:
                    session.event_log._db = None
                with pytest.raises(PrivateIPCError):
                    await capture_idle_durability(session, continuation, native, Deadline(5))
            finally:
                if task is not None:
                    await task
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["unknown", "coercion", "digest", "cursor", "turn-gap", "event-gap"])
def test_typed_durability_refuses_mutated_private_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    async def scenario() -> None:
        async with durable(tmp_path, monkeypatch) as (session, _db, native, continuation):
            payload = await capture_idle_durability(session, continuation, native, Deadline(5))
            changed = copy.deepcopy(payload)
            if mutation == "unknown":
                changed["extra"] = "fixture-sensitive"
            elif mutation == "coercion":
                changed["next_event_id"] = str(changed["next_event_id"])
            elif mutation == "digest":
                changed["continuation_sha256"] = "not-a-digest"
            elif mutation == "cursor":
                changed["next_event_id"] += 1
            elif mutation == "turn-gap":
                changed["turns"][0]["turn_index"] = 1
            else:
                changed["events"][0]["event_id"] = 0
            with pytest.raises(PrivateIPCError) as caught:
                decode_idle_durability(changed)
            assert "fixture-sensitive" not in str(caught.value)
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", [None, "host", "generation", "cursor", "frontend", "wire", "token"])
def test_native_commit_custody_requires_same_retained_host_and_reservation(
    monkeypatch: pytest.MonkeyPatch, mutation: str | None,
) -> None:
    async def scenario() -> None:
        async with connected(monkeypatch) as (client, events, receipt, _send, _reader):
            await settle(client)
            original = receipt()
            before = decode_host_receipt(original.private_payload(Deadline(5)))
            frontend = replace(before.frontend, pid=before.frontend.pid + 1, generation=5)
            payload = asdict(replace(
                before, frontend=frontend, attachment_generation=3, snapshot="d" * 64,
            ))
            if mutation == "host":
                payload["host"]["pid"] += 1
            elif mutation == "generation":
                payload["attachment_generation"] = 4
            elif mutation == "cursor":
                payload["ack_cursor"] -= 1
            elif mutation == "frontend":
                payload["frontend"]["generation"] += 1
            elif mutation == "wire":
                payload["acp_wire"]["next_client_request_id"] += 1
            elif mutation == "token":
                payload["snapshot"] = "e" * 64
            if mutation is None:
                committed = verify_host_commit(
                    payload, NativeChannel(before.host), original, frontend, "d" * 64, 3, Deadline(5),
                )
                try:
                    assert decode_host_receipt(committed.private_payload(Deadline(5))).frontend == frontend
                    assert original.private_payload(Deadline(5))["snapshot"] == "c" * 64
                finally:
                    committed.close()
            else:
                with pytest.raises(PrivateIPCError):
                    verify_host_commit(
                        payload, NativeChannel(before.host), original, frontend, "d" * 64, 3, Deadline(5),
                    )
            assert client.is_running and events._next_id > 0
    asyncio.run(scenario())
