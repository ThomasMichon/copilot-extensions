"""First-touch HTTP/inbox flow; replay, acknowledgement and notice races."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.requests import Request
from fastapi.testclient import TestClient
import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.live_questions import LiveQuestions
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.live_message_receipts import record_consumption
from agent_bridge.routes import live_sessions, status
from agent_bridge.status_notices import deliver_notice
from agent_bridge.status_runtime import StatusRuntime
from agent_bridge.status_store import StatusGap, StatusStore


@pytest.fixture
def api(tmp_db):
    app = FastAPI()
    app.state.db = tmp_db
    app.state.ready = True
    app.state.live_event_store = LiveEventStore()
    app.state.live_questions = LiveQuestions()
    app.state.session_manager = SimpleNamespace(is_draining=False)
    app.state.status_runtime = StatusRuntime(app)
    app.include_router(status.router)
    app.include_router(live_sessions.router)
    for session in ("watcher", "agent-a", "agent-b"):
        tmp_db.register_live_session(
            session, machine=None, cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=time.time(),
        )
    return TestClient(app)


def selection(**extra):
    return {
        "watcher": "watcher", "agents": [{"session_id": "agent-a"}],
        "events": ["question_pending", "permission_pending", "idle", "message_consumed", "stalled", "error"],
        **extra,
    }


def subscribe(api, name="watch", **extra):
    response = api.put(f"/api/v1/status/subscriptions/{name}", json=selection(**extra))
    assert response.status_code == 200, response.text
    return response.json()


def event(api, kind="assistant.turn_end", identity="event-1", **extra):
    response = api.post("/api/v1/live-sessions/agent-a/events", json={
        "events": [{"type": kind, "id": identity, "data": {}, **extra}],
    })
    assert response.status_code == 200, response.text


def page(api, after=0):
    response = api.get("/api/v1/status/subscriptions/watch/feed", params={"after": after})
    assert response.status_code == 200, response.text
    return response.json()


def test_real_http_inbox_consumer_gets_one_notice_and_consumption_is_not_ack(api, tmp_db):
    sub = subscribe(api)
    assert subscribe(api)["generation"] == sub["generation"]
    event(api)
    api.app.state.status_runtime.dispatch()
    messages = api.get("/api/v1/live-sessions/watcher/messages?exact_native=true").json()["messages"]
    assert len(messages) == 1
    message = messages[0]
    assert message["kind"] == "notify" and message["delivery"] == "steer"
    assert "/api/v1/status/subscriptions/watch/feed" in message["body"]
    first = page(api)
    # Reading and consuming the notice do not acknowledge the feed.
    page(api)
    tmp_db.consume_live_message("watcher", message["id"], time.time())
    event(api, identity="event-2")
    api.app.state.status_runtime.dispatch()
    assert len(tmp_db.execute_read("SELECT id FROM live_messages WHERE sender='bridge-status'")) == 1
    current = page(api)
    assert current["subscription"]["acked"] == 0
    assert api.post("/api/v1/status/subscriptions/watch/ack", json={
        "epoch": first["epoch"], "through": first["through"],
    }).status_code == 200
    api.app.state.status_runtime.dispatch()
    assert len(tmp_db.execute_read("SELECT id FROM live_messages WHERE sender='bridge-status'")) == 2
    current = page(api)
    assert api.post("/api/v1/status/subscriptions/watch/ack", json={
        "epoch": current["epoch"], "through": current["through"],
    }).status_code == 200
    event(api, identity="event-3")
    api.app.state.status_runtime.dispatch()
    assert len(tmp_db.execute_read("SELECT id FROM live_messages WHERE sender='bridge-status'")) == 3


def test_selection_root_filter_duplicate_restart_and_metadata_only(api, tmp_db):
    subscribe(api)
    event(api, identity="nested", agentId="child")
    event(api, "permission.requested", "permission", data={
        "permissionRequest": {"kind": "shell", "command": "private text"},
    })
    event(api, identity="root")
    event(api, identity="root")
    root = page(api)
    assert [e["kind"] for e in root["events"]] == ["permission_pending", "idle"]
    assert "private text" not in str(root)
    tmp_db.flush()
    other = Database(tmp_db._db_path)
    try:
        restored = StatusStore(other).page(after=0, epoch=root["epoch"], subscription_id="watch")
        assert restored["events"] == root["events"]
        assert deliver_notice(other, "watch")
        assert deliver_notice(tmp_db, "watch") is None
    finally:
        other.close()


def test_nested_subscription_and_unselected_agent(api, tmp_db):
    subscribe(api, include_nested=True)
    event(api, identity="nested", agentId="child")
    StatusStore(tmp_db).publish("agent-b", "idle", "unselected")
    result = page(api)
    assert len(result["events"]) == 1 and result["events"][0]["scope"] == "nested"
    assert result["through"] == 2


def test_ack_notice_and_concurrent_event_share_database_transaction(api, tmp_db):
    subscribe(api)
    event(api)
    deliver_notice(tmp_db, "watch")
    first = page(api)
    store = StatusStore(tmp_db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ack = pool.submit(store.acknowledge, "watch", first["through"], first["epoch"])
        added = pool.submit(store.publish, "agent-a", "error", "concurrent")
        ack.result()
        added.result()
    deliver_notice(tmp_db, "watch")
    result = page(api)
    assert result["subscription"]["unread"] > result["subscription"]["acked"]
    assert len(tmp_db.execute_read("SELECT id FROM live_messages")) == 2
    # Duplicate acknowledgement never rearms again.
    store.acknowledge("watch", first["through"], first["epoch"])
    assert deliver_notice(tmp_db, "watch") is None


def test_retention_gap_is_explicit_and_never_acks_read(api, tmp_db, monkeypatch):
    import agent_bridge.status_store as module
    monkeypatch.setattr(module, "RETENTION", 2)
    subscribe(api)
    for i in range(4):
        event(api, identity=f"event-{i}")
    response = api.get("/api/v1/status/subscriptions/watch/feed")
    assert response.status_code == 409
    info = response.json()["detail"]
    assert info["floor"] == 3 and info["head"] == 4
    assert api.post("/api/v1/status/subscriptions/watch/ack", json={
        "epoch": info["epoch"], "through": 1,
    }).status_code == 409
    assert api.get("/api/v1/status/feed", params={"after": 2}).json()["through"] == 4
    with pytest.raises(StatusGap):
        StatusStore(tmp_db).page(after=4, epoch="replaced")


def test_strict_http_validation_and_subscription_identity(api):
    subscribe(api)
    assert api.put("/api/v1/status/subscriptions/watch", json=selection(watcher="another")).status_code == 409
    for body in (selection(events=["typo"]), selection(agents=[]), selection(misspelled=True)):
        assert api.put("/api/v1/status/subscriptions/new", json=body).status_code == 422
    assert api.post("/api/v1/status/subscriptions/watch/ack", json={
        "epoch": "epoch", "through": -1,
    }).status_code == 422
    assert api.get("/api/v1/status/feed", params={"limit": 999}).status_code == 422


def test_deleted_subscription_recreation_has_new_notice_identity(api, tmp_db):
    old = subscribe(api)
    event(api)
    deliver_notice(tmp_db, "watch")
    api.delete("/api/v1/status/subscriptions/watch")
    new = subscribe(api)
    assert old["generation"] != new["generation"]
    event(api, identity="next")
    deliver_notice(tmp_db, "watch")
    assert len(tmp_db.execute_read("SELECT id FROM live_messages")) == 2


def test_notice_consumption_never_generates_a_recursive_status_notice(api, tmp_db):
    subscribe(api, agents=[{"session_id": "watcher"}])
    selected = StatusStore(tmp_db)
    selected.publish("watcher", "idle", "first")
    notice_id = deliver_notice(tmp_db, "watch")
    record_consumption(tmp_db, "watcher", [{
        "type": "user.message", "id": "notice-consumed", "data": {
            "source": "agent-bridge",
            "transformedContent": f'<agent-message msg-id="{notice_id}">notice</agent-message>',
        },
    }])
    result = page(api)
    assert [e["kind"] for e in result["events"]] == ["idle"]
    assert result["subscription"]["acked"] == 0
    assert tmp_db.live_message_receipt("watcher", notice_id)["stage"] == "consumed"


def test_root_consumption_is_correlated_and_nested_consumption_is_not_published(api, tmp_db):
    subscribe(api)
    message_id = tmp_db.enqueue_live_message("agent-a", "peer", "body", time.time())
    payload = {
        "source": "agent-bridge", "content": "Message from peer",
        "transformedContent": f'<agent-message msg-id="{message_id}">private body</agent-message>',
    }
    event(api, "user.message", "nested", data=payload, agentId="child")
    assert page(api)["events"] == []
    event(api, "user.message", "consumed", data=payload)
    event(api, "user.message", "consumed", data=payload)
    assert [e["kind"] for e in page(api)["events"]] == ["message_consumed"]
    assert "private body" not in str(page(api))


def test_owned_event_seam_and_fresh_database_migration(tmp_db):
    tmp_db.create_session("owned", "owned", None, ".", "local", "idle", time.time())
    log = EventLog(db=tmp_db, session_id="owned")
    log.append("permission_request", {"command": "private"})
    log.append("turn_complete", {"agent_id": "nested"})
    result = StatusStore(tmp_db).page(after=0, epoch=None)
    assert [e["scope"] for e in result["events"]] == ["root", "nested"]
    assert "private" not in str(result)
    tmp_db.execute_write("UPDATE schema_version SET version=43")
    restored = Database(tmp_db._db_path)
    try:
        assert StatusStore(restored).page(after=0, epoch=result["epoch"])["events"] == result["events"]
    finally:
        restored.close()


def test_stale_watcher_is_honest_pending_and_stall_is_observation(api, tmp_db):
    subscribe(api)
    tmp_db.update_live_turn_state("agent-a", turn_state="running", last_activity_at=time.time() - 100)
    tmp_db.execute_write("UPDATE live_sessions SET updated_at=0 WHERE session_id='watcher'")
    api.app.state.status_runtime.dispatch()
    result = page(api)
    assert result["events"][0]["kind"] == "stalled"
    api.app.state.status_runtime.dispatch()
    assert page(api)["subscription"]["notice_error"] == "stale"
    assert tmp_db.list_pending_live_messages("watcher") == []


def test_local_runtime_starts_and_stops_without_a_new_daemon(api):
    async def run():
        runtime = api.app.state.status_runtime
        def forbidden(*args, **kwargs):
            pytest.fail("Empty status runtime inspected an inbox owner or remote topology")
        api.app.state.live_inbox_runtime = SimpleNamespace(producer_admission=forbidden)
        api.app.state.resolver = SimpleNamespace(resolve_ssh_environment=forbidden)
        runtime.start()
        await asyncio.sleep(0)
        assert not runtime.task.done()
        await runtime.close()
        assert runtime.task is None
    asyncio.run(run())


def test_status_runtime_handles_distinct_legacy_asyncio_timeout(api, monkeypatch):
    class LegacyTimeout(Exception):
        pass

    calls = []

    async def wait_for(*args, **kwargs):
        calls.append(True)
        # Close the created wait coroutine because this fixture supplies the outcome.
        args[0].close()
        if len(calls) == 1:
            raise LegacyTimeout()
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "TimeoutError", LegacyTimeout)
    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    api.app.state.ready = False
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(api.app.state.status_runtime.run())
    assert len(calls) == 2


def test_host_stream_is_one_hop_cursor_neutral_bounded_and_releases_reader(api, tmp_db):
    async def run():
        selected = StatusStore(tmp_db)
        selected.publish("agent-a", "idle", "local")
        selected.publish("agent-a", "idle", "remote", source="other-host")
        async def receive():
            await asyncio.Event().wait()
        request = Request({"type": "http", "app": api.app, "headers": []}, receive=receive)
        response = await status.events(request, after=0, epoch=None)
        chunk = await anext(response.body_iterator)
        assert '"source":"local"' in chunk and '"source":"other-host"' not in chunk
        assert api.app.state.status_runtime.streams == 1
        await response.body_iterator.aclose()
        assert api.app.state.status_runtime.streams == 0
        api.app.state.status_runtime.streams = 32
        full = await status.events(request, after=0, epoch=None)
        assert "status_backpressure" in await anext(full.body_iterator)
        await full.body_iterator.aclose()
        api.app.state.status_runtime.streams = 0
    asyncio.run(run())
    assert api.get("/api/v1/status/events?after=999").status_code == 409


@pytest.mark.parametrize("storage", ["prior", "missing", "malformed"])
def test_status_storage_requires_writable_preparation_and_preserves_prior_facts(tmp_path, storage):
    path = tmp_path / "status.db"
    db = Database(path)
    db.create_session("retained", "retained", None, ".", "local", "stopped", 1)
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE bridge_status_events")
        if storage == "prior":
            conn.execute("UPDATE schema_version SET version=43")
        elif storage == "malformed":
            conn.execute("CREATE TABLE bridge_status_events(id INTEGER)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema|preparation"):
        Database(path, read_only=True)
    assert before == path.read_bytes()
    if storage == "prior":
        migrated = Database(path)
        try:
            assert StatusStore(migrated).page(after=0, epoch=None)["events"] == []
            assert migrated.get_session("retained")["status"] == "stopped"
        finally:
            migrated.close()
    else:
        with pytest.raises(ValueError, match="schema|preparation"):
            Database(path)
