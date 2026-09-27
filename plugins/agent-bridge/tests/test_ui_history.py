"""Tests for the /ui history routes (routes/ui_history.py)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.routes import ui, ui_history

COPILOT_LOG = [
    {"type": "session.start", "data": {}, "timestamp": "2026-09-20T10:00:00.000Z"},
    {"type": "user.message", "data": {"content": "Fix the login page"}, "timestamp": "2026-09-20T10:00:01.000Z"},
    {"type": "assistant.message", "data": {"content": "", "toolRequests": [{"name": "view"}]},
     "timestamp": "2026-09-20T10:00:02.000Z"},
    {"type": "tool.execution_start", "data": {"toolCallId": "t1", "toolName": "view",
                                               "arguments": {"path": "/src/login.ts"}},
     "timestamp": "2026-09-20T10:00:02.500Z"},
    {"type": "tool.execution_complete", "data": {"toolCallId": "t1", "success": True,
                                                  "result": {"content": "export function login() {}"}},
     "timestamp": "2026-09-20T10:00:03.000Z"},
    {"type": "tool.execution_start", "data": {"toolCallId": "t2", "toolName": "powershell",
                                               "arguments": {"command": "npm test"}},
     "timestamp": "2026-09-20T10:00:04.000Z"},
    {"type": "tool.execution_complete", "data": {"toolCallId": "t2", "success": False,
                                                  "error": {"message": "1 test failed"}},
     "timestamp": "2026-09-20T10:00:05.000Z"},
    {"type": "assistant.message", "data": {"content": "Fixed the redirect.", "parentToolCallId": None},
     "timestamp": "2026-09-20T10:00:06.000Z"},
    {"type": "assistant.turn_end", "data": {"turnId": "0"}, "timestamp": "2026-09-20T10:00:07.000Z"},
    {"type": "session.compaction_complete", "data": {"success": True, "tokensRemoved": 1200},
     "timestamp": "2026-09-20T10:00:08.000Z"},
    {"type": "hook.start", "data": {}, "timestamp": "2026-09-20T10:00:09.000Z"},
    "not a record",
]


def test_a_copilot_log_maps_to_the_live_streams_kinds() -> None:
    events = ui_history.to_stream_events(COPILOT_LOG)
    assert [e["event"] for e in events] == [
        "user_message", "tool_call_start", "tool_call_update", "tool_call_start", "tool_call_update",
        "agent_message", "turn_complete", "compaction_complete",
    ]
    assert events[0]["data"] == {"content": "Fix the login page"}
    assert events[1]["data"]["kind"] == "view" and events[1]["data"]["raw_input"] == {"path": "/src/login.ts"}
    assert events[2]["data"] == {"tool_call_id": "t1", "status": "completed", "content": "export function login() {}"}
    assert events[4]["data"]["status"] == "failed" and events[4]["data"]["content"] == "1 test failed"
    assert events[5]["data"]["text"] == "Fixed the redirect."
    assert events[7]["data"] == {"success": True, "tokens_removed": 1200}
    from datetime import datetime, timezone
    assert events[0]["ts"] == datetime(2026, 9, 20, 10, 0, 1, tzinfo=timezone.utc).timestamp()


def test_events_already_in_stream_shape_pass_through_and_long_text_is_clipped() -> None:
    events = ui_history.to_stream_events([
        {"event": "agent_message", "data": {"text": "hi"}, "timestamp": 12.5},
        {"type": "user.message", "data": {"content": "x" * (ui_history.MAX_TEXT + 50)}},
    ])
    assert events[0] == {"event": "agent_message", "data": {"text": "hi"}, "ts": 12.5}
    assert len(events[1]["data"]["content"]) == ui_history.MAX_TEXT + 1


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ui.router)
    return TestClient(app, base_url="http://127.0.0.1:10756")


def test_history_is_mapped_and_bounded(client, monkeypatch) -> None:
    async def fetch(request, sid):
        return (COPILOT_LOG * 400, "ended") if sid == "sess-1" else None

    monkeypatch.setattr(ui_history, "fetch_transcript", fetch)
    body = client.get("/api/v1/ui/sessions/sess-1/history").json()
    assert len(body["events"]) == ui_history.MAX_HISTORY_EVENTS
    assert body["truncated_before"] is True and body["total"] == 8 * 400
    assert body["events"][-1]["event"] == "compaction_complete"  # the newest part is kept


@pytest.mark.parametrize("provider, expected", [
    (False, "no cold-store provider is registered"),
    (True, "has no record of this session"),
])
def test_a_missing_transcript_says_why(client, monkeypatch, provider, expected) -> None:
    async def fetch(request, sid):
        return None

    monkeypatch.setattr(ui_history, "fetch_transcript", fetch)
    monkeypatch.setattr(ui_history, "discover_cold_store_manifests",
                        lambda: {"session-fetch": object()} if provider else {})
    resp = client.get("/api/v1/ui/sessions/sess-2/history")
    assert resp.status_code == 404
    assert expected in resp.json()["detail"] and resp.json()["provider"] is provider


def test_history_refuses_a_malformed_session_id(client) -> None:
    assert client.get("/api/v1/ui/sessions/..%2F..%2Fx/history").status_code in (400, 404)
    assert client.get("/api/v1/ui/sessions/a b/history").status_code == 400


def test_commits_list_the_branchs_own_commits(client, monkeypatch) -> None:
    st = {"cache": {"workspaces": [{"id": "w1", "project": "harness", "path": "/w/w1"}],
                    "project_info": {"harness": {"default_branch": "trunk"}}, "fetched_at": 0},
          "refresh": None, "launches": [], "prs": {}, "subjects": {}}
    client.app.state.ui_tasks = st
    seen = []

    async def fake_exec(cmd, *, timeout=None):
        seen.append(cmd)
        return "abc1234\x1fFix the redirect\x1f1790000000\nbad line\n", ""

    monkeypatch.setattr(ui_history.shutil, "which", lambda name: "git")
    monkeypatch.setattr(ui_history._wt, "_exec_ex", fake_exec)
    body = client.get("/api/v1/ui/tasks/w1/commits").json()
    assert body["commits"] == [{"sha": "abc1234", "subject": "Fix the redirect", "ts": 1790000000}]
    assert body["base"] == "origin/trunk" and seen[0][-3:] == ["HEAD", "--not", "origin/trunk"]
    assert client.get("/api/v1/ui/tasks/nope/commits").status_code == 404
