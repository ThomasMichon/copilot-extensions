"""Known-source observations use real retained logs, not activated SDK actors."""

from __future__ import annotations

import asyncio
import copy
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge.events import EventLog
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.routes.session_observations import observations
from agent_bridge.session_observation_cli import ObservationStream
from agent_bridge.session_observation_contract import (
    HISTORY,
    MAX_BODY,
    MAX_FRAME,
    ObservationError,
    ReadRequest,
    Selection,
    decode,
    encode,
    position,
    read_position,
    sse,
    validate_frame,
)
from agent_bridge.session_observations import admit, frame


def request(*, history=False, after=None, before=None, limit=64, project="demo", follow=False):
    return ReadRequest(
        Selection("session-1", project, "worktree-1", None),
        history,
        follow,
        before if history else after,
        limit,
    )


@pytest.fixture
def source(tmp_db, session_manager):
    tmp_db.create_session("session-1", "fixture", None, "/fixture", "local", "idle", 1.0)
    log = EventLog(db=tmp_db, session_id="session-1", worktree_id="worktree-1")
    session = SimpleNamespace(
        session_id="session-1",
        target=SimpleNamespace(
            project="demo",
            worktree_id="worktree-1",
            type="local",
        ),
        event_log=log,
        acp_session_id="durable-1",
        subscriber_count=0,
        touch=lambda: None,
    )
    session_manager._sessions["session-1"] = session
    session_manager._generation_id = "fixture-host-generation"
    session_manager.start_session = AsyncMock(side_effect=AssertionError("must not start"))
    return SimpleNamespace(
        session_manager=session_manager,
        db=tmp_db,
        live_event_store=LiveEventStore(),
    )


def append(source, count):
    log = source.session_manager.get_session("session-1").event_log
    for index in range(count):
        log.append("tool_call_start", {"tool_call_id": f"tool-{index}", "title": "Work"})
    return log


def test_snapshot_delta_and_backward_page_do_not_move_delivery_cursor(source):
    log = append(source, 5)
    selected = request(limit=2)
    first = frame(admit(source, selected), selected)
    assert first["kind"] == "snapshot"
    assert len(first["entries"]) == 2
    assert first["entries"][0]["native_event_id"] == 4
    validate_frame(first, selected)
    page_request = request(history=True, before=first["next_before"])
    page = frame(admit(source, page_request), page_request)
    assert [item["native_event_id"] for item in page["entries"]] == [1, 2, 3]
    assert page["schema"] == HISTORY and page["has_more"] is False
    log.append("permission_request", {"request_id": "permission-1"})
    delta_request = request(after=first["position"])
    delta = frame(admit(source, delta_request), delta_request)
    assert delta["kind"] == "delta" and delta["base_position"] == first["position"]
    assert delta["entries"][0]["native_event_id"] == 6
    assert source.db.get_cursor("observer", "session-1") == 0
    assert delta["current_requests"] == "unknown"
    assert delta["entries"][0]["answerability"] == "unknown"


@pytest.mark.parametrize("change", ["unknown", "alias", "worktree", "project", "head"])
def test_exact_admission_never_starts_an_actor(source, change):
    selected = request()
    if change == "unknown":
        selected = ReadRequest(
            Selection("missing", "demo", "worktree-1", None), False, False, None, 64
        )
    elif change == "alias":
        source.session_manager.get_session("session-1").acp_session_id = "alias"
        selected = ReadRequest(
            Selection("alias", "demo", "worktree-1", None), False, False, None, 64
        )
    elif change == "worktree":
        selected = ReadRequest(
            Selection("session-1", "demo", "suffix-only", None), False, False, None, 64
        )
    elif change == "project":
        selected = ReadRequest(
            Selection("session-1", "foreign", "worktree-1", None), False, False, None, 64
        )
    else:
        selected = ReadRequest(
            Selection("session-1", "demo", "worktree-1", "head"), False, False, None, 64
        )
    with pytest.raises(ObservationError):
        admit(source, selected)
    source.session_manager.start_session.assert_not_called()


def test_observe_empty_source_then_first_event_does_not_invent_an_incarnation(source):
    known = admit(source, request())
    body = frame(known, request())
    assert body["position"] is None and body["source"]["journal_incarnation"] is None
    assert body["availability"] == "unknown"
    append(source, 1)
    assert frame(known, request())["source"]["journal_incarnation"] == known.log.continuity_id


def test_rebuild_invalidates_positions_and_retains_native_source_identity(source):
    log = append(source, 1)
    old = frame(admit(source, request()), request())
    log.rebuild([("turn_complete", {})])
    with pytest.raises(ObservationError, match="different source incarnation"):
        frame(admit(source, request()), request(after=old["position"]))
    fresh = frame(admit(source, request()), request())
    assert old["entries"][0]["id"] != fresh["entries"][0]["id"]


def test_hosting_replacement_is_not_an_identical_event_log(source):
    append(source, 1)
    old = frame(admit(source, request()), request())["position"]
    source.session_manager._generation_id = "replacement-generation"
    with pytest.raises(ObservationError, match="different source incarnation"):
        frame(admit(source, request()), request(after=old))


def test_reloaded_durable_log_preserves_journal_identity(source):
    log = append(source, 3)
    source.db.flush()
    original = frame(admit(source, request()), request())
    restored = EventLog.from_db(source.db, "session-1", worktree_id="worktree-1")
    assert restored.continuity_id == log.continuity_id
    source.session_manager.get_session("session-1").event_log = restored
    assert frame(admit(source, request()), request())["entries"] == original["entries"]


def test_private_retained_floor_and_hole_are_not_complete_history(source):
    log = append(source, 5)
    with log._lock:
        log._events = log._events[2:]
    known = admit(source, request())
    body = frame(known, request())
    assert body["retained"] == {"floor": 3, "head": 5, "complete": False}
    for offset in (0, 6):
        with pytest.raises(ObservationError, match="retained event range"):
            frame(known, request(after=position(body["source"], offset)))
    with log._lock:
        log._events.pop(1)
    with pytest.raises(ObservationError, match="native identity gap"):
        frame(known, request())


def test_foreign_and_boolean_positions_refuse(source):
    append(source, 1)
    identity = frame(admit(source, request()), request())["source"]
    foreign = {**identity, "session_id": "foreign"}
    with pytest.raises(ObservationError):
        read_position(position(foreign, 1), identity)
    with pytest.raises(ObservationError):
        read_position(position(identity, True), identity)


def test_large_page_is_byte_bounded_and_can_continue_without_skipping(source):
    log = append(source, 1)
    first = frame(admit(source, request()), request())
    for index in range(90):
        log.append(
            "tool_call_update",
            {
                "tool_call_id": f"tool-{index}",
                "title": "x" * 512,
                "status": "completed",
            },
        )
    after = first["position"]
    seen = []
    while True:
        selected = request(after=after)
        body = frame(admit(source, selected), selected)
        assert len(encode(body)) <= MAX_BODY and len(sse(body)) <= MAX_FRAME
        seen.extend(item["native_event_id"] for item in body["entries"])
        after = body["position"]
        if not body["has_more"]:
            break
    assert seen == list(range(2, 92))


def test_conflicting_duplicates_are_visible_to_the_owning_reader(source):
    append(source, 1)
    body = frame(admit(source, request()), request())
    mutated = copy.deepcopy(body)
    mutated["entries"][0]["details"]["title"] = "changed"
    with pytest.raises(ObservationError, match="conflicting"):
        validate_frame(mutated, request())
    duplicated = copy.deepcopy(body)
    duplicated["entries"].append(duplicated["entries"][0])
    with pytest.raises(ObservationError, match="duplicate"):
        validate_frame(duplicated, request())


def test_represented_actual_sdk_compaction_has_target_only_capacity(source):
    source.session_manager._sessions.clear()
    source.db.register_live_session(
        "session-1",
        machine="fixture-host",
        cwd="/fixture",
        worktree_id="worktree-1",
        repo="example/repo",
        branch=None,
        pid=None,
        role=None,
        now=1.0,
    )
    source.live_event_store.ingest(
        "session-1",
        [
            {
                "type": "session.compaction_start",
                "id": "sdk-event-1",
                "data": {
                    "currentTokens": 280,
                    "tokenLimit": 1000,
                    "compactionAttemptId": "attempt-1",
                    "model": "model-a",
                    "switchTargetModel": "model-b",
                    "targetPromptTokenLimit": 900,
                    "execution": "background",
                    "trigger": "automatic",
                },
            }
        ],
        worktree_id="worktree-1",
    )
    selected = request(project=None)
    body = frame(admit(source, selected), selected)
    item = body["entries"][0]
    evidence = item["details"]["compaction_evidence"]
    assert evidence["pre_usage"]["used_tokens"] == 280
    assert evidence["pre_usage"]["target_capacity_tokens"] == 1000
    assert evidence["pre_usage"]["active_capacity_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"
    assert evidence["compaction"]["compaction_attempt_id"] == "attempt-1"
    assert body["source"]["registry"] == "represented"
    assert body["durability"] == "unknown" and body["coverage"] == "partial"
    assert "represented-history-memory-only" in body["limitations"]
    source.live_event_store = LiveEventStore()
    with pytest.raises(ObservationError, match="no retained"):
        admit(source, selected)


def test_remote_hosted_connection_uses_only_its_existing_local_log(source):
    session = source.session_manager.get_session("session-1")
    session.target.type = "remote-acp"
    append(source, 1)
    body = frame(admit(source, request()), request())
    assert body["source"]["transport"] == "remote-acp"
    assert body["coverage"] == "partial" and body["durability"] == "unknown"
    session.client = SimpleNamespace(start=AsyncMock(), load_session=AsyncMock())
    frame(admit(source, request()), request())
    session.client.start.assert_not_called()
    session.client.load_session.assert_not_called()


def _http_request(source):
    return SimpleNamespace(
        app=SimpleNamespace(state=source),
        is_disconnected=AsyncMock(return_value=False),
    )


@pytest.mark.asyncio
async def test_follow_cancellation_releases_only_observer_and_idle_watch(source):
    log = append(source, 1)
    session = source.session_manager.get_session("session-1")
    reader = observations(_http_request(source), request(follow=True))
    assert decode((await anext(reader)).split(b"data: ", 1)[1].strip())["kind"] == "snapshot"
    assert session.subscriber_count == 1
    waiting = asyncio.create_task(anext(reader))
    for _ in range(20):
        if log._waiters:
            break
        await asyncio.sleep(0)
    assert log._waiters
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await reader.aclose()
    assert session.subscriber_count == 0 and log._waiters == []
    source.session_manager.start_session.assert_not_called()


@pytest.mark.asyncio
async def test_live_append_and_source_retirement_are_explicit(source):
    log = append(source, 1)
    reader = observations(_http_request(source), request(follow=True))
    await anext(reader)
    log.append("ask_user_resolved", {"tool_call_id": "question-1"})
    delta = decode((await anext(reader)).split(b"data: ", 1)[1].strip())
    assert delta["kind"] == "delta"
    assert delta["entries"][0]["kind"] == "input-cleared"
    del source.session_manager._sessions["session-1"]
    stopped = decode((await anext(reader)).split(b"data: ", 1)[1].strip())
    assert stopped["kind"] == "error" and stopped["code"] == "source-unavailable"
    await reader.aclose()
    assert log._waiters == []


@pytest.mark.asyncio
async def test_replaced_handle_does_not_transfer_an_idle_watch(source):
    append(source, 1)
    original = source.session_manager.get_session("session-1")
    reader = observations(_http_request(source), request(follow=True))
    await anext(reader)
    replacement = SimpleNamespace(
        session_id="session-1",
        acp_session_id="replacement",
        target=original.target,
        event_log=EventLog(),
        subscriber_count=5,
    )
    source.session_manager._sessions["session-1"] = replacement
    result = decode((await anext(reader)).split(b"data: ", 1)[1].strip())
    assert result["kind"] == "gap" and result["code"] == "source-changed"
    await reader.aclose()
    assert original.subscriber_count == 0
    assert replacement.subscriber_count == 5


@pytest.mark.asyncio
async def test_database_failure_is_bounded_source_error(source, monkeypatch):
    import sqlite3

    source.session_manager._sessions.clear()

    def failed(_session_id):
        raise sqlite3.OperationalError("private source detail must not escape")

    monkeypatch.setattr(source.db, "get_live_session", failed)
    reader = observations(_http_request(source), request())
    result = decode((await anext(reader)).split(b"data: ", 1)[1].strip())
    assert result["kind"] == "error" and result["code"] == "source-unavailable"
    assert "private source detail" not in result["message"]
    await reader.aclose()


@pytest.mark.asyncio
async def test_history_shutdown_never_changes_the_action_contract(source):
    source.uvicorn_server = SimpleNamespace(should_exit=True)
    reader = observations(_http_request(source), request(history=True))
    result = decode((await anext(reader)).split(b"data: ", 1)[1].strip())
    assert result["schema"] == HISTORY and result["kind"] == "error"
    await reader.aclose()


@pytest.mark.parametrize(
    "raw",
    [
        b"event: snapshot\ndata: {}\n\n",
        b'event: snapshot\ndata: {"schema":"bridge-session-observations-v1",'
        b'"version":true,"kind":"snapshot"}\n\n',
        b'event: snapshot\ndata: {"x":1,"x":2}\n\n',
        b'event: snapshot\ndata: {"x":NaN}\n\n',
        b"x" * (MAX_FRAME + 1),
        b"event: snapshot\n",
    ],
)
def test_bounded_stream_rejects_malformed_frames_and_closes(raw):
    response = io.BytesIO(raw)
    reader = ObservationStream(response)
    with pytest.raises(ObservationError):
        next(reader)
    assert response.closed


def test_valid_bounded_stream_preserves_semantics(source):
    append(source, 1)
    body = frame(admit(source, request()), request())
    response = io.BytesIO(sse(body))
    with ObservationStream(response) as reader:
        assert next(reader) == body
        with pytest.raises(StopIteration):
            next(reader)
        with pytest.raises(StopIteration):
            next(reader)
    assert response.closed


def test_unknown_native_event_is_not_inferred_from_its_prose(source):
    log = append(source, 1)
    log.append("unknown.event", {"message": "permission compaction subagent started"})
    body = frame(admit(source, request()), request())
    assert body["entries"][-1]["kind"] == "source-event"


@pytest.mark.parametrize("fault", [None, TimeoutError, KeyboardInterrupt])
def test_observation_http_resource_boundary(monkeypatch, fault):
    """Actual HTTPResponse must not materialize an unbounded unterminated line."""
    from http.client import HTTPResponse

    from agent_bridge.client import BridgeClient
    from agent_bridge.session_observation_cli import read
    from agent_bridge.session_observation_contract import OBSERVATIONS

    assert MAX_FRAME == 35 * 1024 and MAX_BODY == 32 * 1024
    headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"

    class Source(io.RawIOBase):
        prefix = headers + b'event: snapshot\ndata: {"unterminated":"'
        offset = 0
        generated = 0

        def readable(self):
            return True

        def readinto(self, buffer):
            assert 0 < len(buffer) <= 1024
            if self.offset < len(self.prefix):
                part = self.prefix[self.offset:self.offset + len(buffer)]
                buffer[:len(part)] = part
                self.offset += len(part)
                return len(part)
            if fault is not None:
                raise fault("source silence or caller cancellation")
            # A broken unlimited readline fails this guard, rather than growing
            # test memory indefinitely or waiting for the outer runner timeout.
            assert self.generated < 2 * MAX_FRAME, "unbounded HTTP line read"
            buffer[:] = b"x" * len(buffer)
            self.generated += len(buffer)
            return len(buffer)

    source = Source()
    buffered = io.BufferedReader(source, buffer_size=1024)
    socket = SimpleNamespace(makefile=lambda *_args: buffered)

    class Response(HTTPResponse):
        def __next__(self):
            raise AssertionError("raw HTTP/SSE iterator used instead of bounded reads")

    response = Response(socket)
    response.begin()
    opened = []

    def open_response(_request, *, timeout):
        opened.append(timeout)
        return response

    client = BridgeClient("http://fixture.invalid", "synthetic-token", connect_grace=0)
    monkeypatch.setattr(client, "health", lambda: {"capabilities": {OBSERVATIONS: 1}})
    monkeypatch.setattr("urllib.request.urlopen", open_response)
    unrelated = io.BytesIO(b"independent subscription")
    try:
        reader = read(client, request())
        with pytest.raises(ObservationError if fault is None else fault):
            next(reader)
        assert opened == [15]
        assert response.isclosed() and source.closed
        assert not unrelated.closed
        assert source.offset + source.generated <= len(headers) + MAX_FRAME + 1 + 1024
    finally:
        response.close()
        unrelated.close()


@pytest.mark.asyncio
async def test_observation_cancellation_preserves_other_subscription(source):
    """Cancel one captured waiter, retaining the actor and independent observer."""
    log = append(source, 1)
    session = source.session_manager.get_session("session-1")
    original_rows = source.db.get_events("session-1")
    first = observations(_http_request(source), request(follow=True))
    second = observations(_http_request(source), request(follow=True))
    await anext(first)
    await anext(second)
    assert session.subscriber_count == 2
    pending = asyncio.create_task(anext(first))
    other = asyncio.create_task(anext(second))
    try:
        for _ in range(50):
            if len(log._waiters) == 2:
                break
            await asyncio.sleep(0)
        assert len(log._waiters) == 2
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1)
        await first.aclose()
        assert session.subscriber_count == 1 and len(log._waiters) == 1
        assert not other.done()
        assert source.session_manager.get_session("session-1") is session
        assert source.db.get_events("session-1") == original_rows
        log.append("permission_resolved", {"request_id": "still-observed"})
        delivered = decode(
            (await asyncio.wait_for(other, timeout=1)).split(b"data: ", 1)[1].strip()
        )
        assert delivered["entries"][0]["kind"] == "permission-cleared"
    finally:
        for task in (pending, other):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await first.aclose()
        await second.aclose()
    assert session.subscriber_count == 0 and log._waiters == []
    source.session_manager.start_session.assert_not_called()
