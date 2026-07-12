"""Tests for Phase 5 live-session representation (translate + store + routes)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.db import Database
from agent_bridge.live_representation import LiveEventStore, translate_sdk_event
from agent_bridge.routes import live_sessions


# -- Translation ------------------------------------------------------------


class TestTranslateSdkEvent:
    def test_user_and_assistant_message(self) -> None:
        assert translate_sdk_event("user.message", {"content": "hi"}) == [
            ("user_message", {"content": "hi"})
        ]
        assert translate_sdk_event("assistant.message", {"content": "yo"}) == [
            ("agent_message", {"text": "yo"})
        ]

    def test_reasoning_maps_to_thought(self) -> None:
        assert translate_sdk_event(
            "assistant.reasoning", {"content": "thinking"}
        ) == [("agent_thought", {"text": "thinking"})]

    def test_empty_text_is_dropped(self) -> None:
        assert translate_sdk_event("assistant.message", {"content": ""}) == []
        assert translate_sdk_event("assistant.message", {}) == []

    def test_tool_start(self) -> None:
        out = translate_sdk_event(
            "tool.execution_start",
            {"toolCallId": "t1", "toolName": "bash", "arguments": {"cmd": "ls"}},
        )
        assert out == [(
            "tool_call_start",
            {
                "tool_call_id": "t1",
                "title": "bash",
                "kind": "bash",
                "raw_input": {"cmd": "ls"},
            },
        )]

    def test_tool_start_without_id_dropped(self) -> None:
        assert translate_sdk_event("tool.execution_start", {"toolName": "x"}) == []

    def test_tool_complete_success(self) -> None:
        out = translate_sdk_event(
            "tool.execution_complete",
            {
                "toolCallId": "t1",
                "success": True,
                "result": {"content": "short", "detailedContent": "full diff"},
            },
        )
        assert out == [(
            "tool_call_update",
            {
                "tool_call_id": "t1",
                "status": "completed",
                "content": ["full diff"],
                "raw_output": None,
            },
        )]

    def test_tool_complete_failure_carries_error(self) -> None:
        out = translate_sdk_event(
            "tool.execution_complete",
            {
                "toolCallId": "t1",
                "success": False,
                "error": {"message": "boom"},
            },
        )
        assert out[0][0] == "tool_call_update"
        data = out[0][1]
        assert data["status"] == "failed"
        assert "boom" in data["content"]

    def test_usage_and_context(self) -> None:
        assert translate_sdk_event(
            "assistant.usage",
            {"inputTokens": 10, "outputTokens": 5, "model": "gpt"},
        ) == [(
            "usage_update",
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "model": "gpt",
                "context_size": None,
                "context_used": None,
            },
        )]
        assert translate_sdk_event(
            "session.usage_info", {"currentTokens": 100, "tokenLimit": 2000}
        ) == [(
            "usage_update",
            {
                "input_tokens": None,
                "output_tokens": None,
                "model": None,
                "context_size": 2000,
                "context_used": 100,
            },
        )]

    def test_turn_end(self) -> None:
        assert translate_sdk_event("assistant.turn_end", {"turnId": "x"}) == [
            ("turn_complete", {"stop_reason": None})
        ]

    def test_permission_is_read_only_without_request_id(self) -> None:
        out = translate_sdk_event(
            "permission.requested",
            {
                "requestId": "req-42",
                "permissionRequest": {
                    "kind": "shell",
                    "intention": "list files",
                    "fullCommandText": "ls -la",
                },
            },
        )
        assert len(out) == 1
        event_type, data = out[0]
        assert event_type == "permission_request"
        assert data["read_only"] is True
        assert data["kind"] == "shell"
        assert data["intention"] == "list files"
        # The two-writer safety line: NEVER carry a correlation id a remote
        # viewer could use to answer the prompt.
        assert "requestId" not in data
        assert "request_id" not in data
        assert "req-42" not in str(data)

    def test_agent_id_passed_through(self) -> None:
        out = translate_sdk_event(
            "assistant.message", {"content": "sub", "agentId": "sub-1"}
        )
        assert out == [("agent_message", {"text": "sub", "agent_id": "sub-1"})]

    def test_unknown_type_dropped(self) -> None:
        assert translate_sdk_event("assistant.streaming_delta", {"x": 1}) == []
        assert translate_sdk_event("session.plan_changed", {}) == []
        assert translate_sdk_event("nonsense", {}) == []


# -- LiveEventStore ---------------------------------------------------------


class TestLiveEventStore:
    def test_get_or_create_is_stable(self) -> None:
        store = LiveEventStore()
        assert store.get("s") is None
        log = store.get_or_create("s")
        assert store.get("s") is log
        assert store.get_or_create("s") is log

    def test_ingest_translates_and_appends(self) -> None:
        store = LiveEventStore()
        n = store.ingest(
            "s",
            [
                {"type": "user.message", "data": {"content": "hi"}},
                {"type": "assistant.message", "data": {"content": "yo"}},
                {"type": "assistant.streaming_delta", "data": {"x": 1}},  # dropped
            ],
        )
        assert n == 2
        log = store.get("s")
        assert log is not None
        events = log.get_events()
        assert [e.event for e in events] == ["user_message", "agent_message"]
        assert log.latest_id == 2

    def test_ingest_skips_malformed_items(self) -> None:
        store = LiveEventStore()
        n = store.ingest(
            "s",
            ["not a dict", {"no_type": 1}, {"type": 5}, {"type": "user.message"}],
        )
        # only the last (type=user.message, empty data -> no content) contributes
        # nothing; all are safely skipped without error.
        assert n == 0

    def test_drop_forgets_log(self) -> None:
        store = LiveEventStore()
        store.ingest("s", [{"type": "assistant.message", "data": {"content": "a"}}])
        assert store.get("s") is not None
        store.drop("s")
        assert store.get("s") is None
        # dropping an unknown id is a no-op
        store.drop("nope")


# -- Routes -----------------------------------------------------------------


@pytest.fixture
def client(tmp_db: Database) -> TestClient:
    app = FastAPI()
    app.state.db = tmp_db
    app.state.live_event_store = LiveEventStore()
    app.include_router(live_sessions.router)
    return TestClient(app)


def _register(client: TestClient, sid: str = "cli-1") -> None:
    r = client.post("/api/v1/live-sessions", json={"session_id": sid})
    assert r.status_code == 200, r.text


def test_ingest_requires_registration(client: TestClient) -> None:
    r = client.post(
        "/api/v1/live-sessions/ghost/events",
        json={"events": [{"type": "assistant.message", "data": {"content": "x"}}]},
    )
    assert r.status_code == 404


def test_ingest_translates_and_counts(client: TestClient) -> None:
    _register(client)
    r = client.post(
        "/api/v1/live-sessions/cli-1/events",
        json={
            "events": [
                {"type": "user.message", "data": {"content": "hello"}},
                {"type": "assistant.message", "data": {"content": "hi there"}},
                {"type": "assistant.usage", "data": {"model": "gpt"}},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["session_id"] == "cli-1"
    assert body["ingested"] == 3
    assert body["last_id"] == 3


def test_deregister_drops_represented_log(client: TestClient) -> None:
    _register(client)
    client.post(
        "/api/v1/live-sessions/cli-1/events",
        json={"events": [{"type": "assistant.message", "data": {"content": "a"}}]},
    )
    store: LiveEventStore = client.app.state.live_event_store
    assert store.get("cli-1") is not None
    assert client.delete("/api/v1/live-sessions/cli-1").json()["ok"] is True
    assert store.get("cli-1") is None


def test_stream_requires_registration(client: TestClient) -> None:
    r = client.get("/api/v1/live-sessions/ghost/events")
    assert r.status_code == 404


def test_stream_replays_represented_tail() -> None:
    """The represented SSE reuses the ACP ``_sse_event_stream`` helper.

    Driven directly (rather than over TestClient's infinite stream) so the tail
    replay is deterministic: break out once both buffered events are seen, which
    ``aclose()``s the generator before its first 30s quiet-period wait.
    """
    import asyncio

    from agent_bridge.routes.live_sessions import _RepresentedSession
    from agent_bridge.routes.sessions import _sse_event_stream

    store = LiveEventStore()
    store.ingest(
        "cli-1",
        [
            {"type": "user.message", "data": {"content": "q"}},
            {"type": "assistant.message", "data": {"content": "a"}},
        ],
    )
    shim = _RepresentedSession(session_id="cli-1", event_log=store.get("cli-1"))

    async def _run() -> str:
        collected: list[str] = []
        gen = _sse_event_stream(
            shim, 0, server=None, is_disconnected=None, mgr=None
        )
        seen = 0
        async for chunk in gen:
            collected.append(chunk)
            if chunk.startswith("id:"):
                seen += 1
            if seen >= 2:
                break
        await gen.aclose()
        return "".join(collected)

    blob = asyncio.run(_run())
    assert "user_message" in blob
    assert "agent_message" in blob

