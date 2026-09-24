"""Tests for the built-in ``/ui`` page and the ``agent-bridge ui`` opener."""

from __future__ import annotations

import argparse
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import service_start_cli
from agent_bridge.routes import ui


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ui.router)
    return TestClient(app)


def test_ui_is_served_with_a_restrictive_csp() -> None:
    resp = _client().get("/ui")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_ui_exposes_live_sessions_watch_and_message_via_existing_routes() -> None:
    page = _client().get("/ui").text
    assert 'id="live"' in page and 'id="feed"' in page and 'id="msg"' in page
    assert 'id="msg-delivery"' in page
    assert 'value="interrupt">Interrupt &amp; send' in page
    assert 'value="steer">Steer (next step)' in page
    assert 'value="queue">Queue (after turn)' in page
    # Only existing, token-protected API surfaces are used.
    assert "/api/v1/live-sessions" in page
    assert "/events?after=" in page and "/messages" in page
    assert "/turns" in page and "queue: true" in page
    assert "idempotency_key" in page
    assert "delivery," in page
    assert "interrupting the current turn" in page


def test_ui_renders_event_content_as_text_only() -> None:
    page = ui._PAGE
    feed_code = page[page.index("function eventLine"):page.index("function handleBlock")]
    assert "innerHTML" not in feed_code
    assert "createTextNode" in feed_code


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ui_script_is_valid_javascript(tmp_path) -> None:
    script = ui._PAGE.split("<script>")[1].split("</script>")[0]
    path = tmp_path / "ui.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


class _FakeClient:
    def __init__(self, code="c0de-1", fail=False):
        self.code, self.fail, self.calls = code, fail, []

    def _request(self, method, path, *a, **k):
        self.calls.append((method, path))
        if self.fail:
            raise RuntimeError("old daemon")
        return {"code": self.code, "expires_in": 60}


def _core_with(client):
    class _Core:
        @staticmethod
        def _service_port():
            return 43210

        @staticmethod
        def _get_client(**_kw):
            return client

    return _Core


def test_ui_command_prints_a_one_time_link_never_the_token(monkeypatch, capsys) -> None:
    client = _FakeClient()
    monkeypatch.setattr(service_start_cli, "_core", lambda: _core_with(client))
    monkeypatch.setattr("agent_bridge.config.load_or_create_auth_token", lambda: "t/k+n")
    service_start_cli._cmd_ui(argparse.Namespace(print_url=True))
    out = capsys.readouterr().out
    assert out.startswith("http://127.0.0.1:43210/ui#code=c0de-1")
    assert "t/k+n" not in out
    assert client.calls == [("POST", "/api/v1/ui/login-codes")]


def test_ui_command_opens_the_browser_signed_in_with_a_code(monkeypatch, capsys) -> None:
    """Browsers keep visited URLs (fragment included) in synced history: only a code goes there."""
    opened = []
    monkeypatch.setattr(service_start_cli, "_core", lambda: _core_with(_FakeClient()))
    monkeypatch.setattr("agent_bridge.config.load_or_create_auth_token", lambda: "t/k+n")
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    service_start_cli._cmd_ui(argparse.Namespace(print_url=False))
    assert opened == ["http://127.0.0.1:43210/ui#code=c0de-1"]
    assert "t/k+n" not in capsys.readouterr().out + opened[0]


def test_ui_command_falls_back_to_pasting_on_an_older_daemon(monkeypatch, capsys) -> None:
    opened = []
    monkeypatch.setattr(service_start_cli, "_core", lambda: _core_with(_FakeClient(fail=True)))
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    service_start_cli._cmd_ui(argparse.Namespace(print_url=False))
    assert opened == ["http://127.0.0.1:43210/ui"]
    assert "agent-bridge token" in capsys.readouterr().out


def _authed_app(token="secret-token"):
    from agent_bridge.auth import BearerAuthMiddleware

    app = FastAPI()
    app.state.auth_token = token
    app.include_router(ui.router)
    app.add_middleware(BearerAuthMiddleware, token=token)
    return TestClient(app)


def test_login_code_minting_requires_the_token() -> None:
    assert _authed_app().post("/api/v1/ui/login-codes").status_code == 401


def test_login_code_exchanges_once_for_the_token() -> None:
    client = _authed_app()
    code = client.post(
        "/api/v1/ui/login-codes", headers={"Authorization": "Bearer secret-token"},
    ).json()["code"]
    first = client.post("/ui/exchange", json={"code": code})
    assert first.status_code == 200 and first.json() == {"token": "secret-token"}
    assert first.headers["cache-control"] == "no-store"
    assert client.post("/ui/exchange", json={"code": code}).status_code == 403


def test_unknown_or_expired_login_code_is_refused(monkeypatch) -> None:
    client = _authed_app()
    assert client.post("/ui/exchange", json={"code": "guess"}).status_code == 403
    assert client.post("/ui/exchange", content=b"not json").status_code == 403
    code = client.post(
        "/api/v1/ui/login-codes", headers={"Authorization": "Bearer secret-token"},
    ).json()["code"]
    later = ui.time.monotonic() + ui.LOGIN_CODE_TTL + 1
    monkeypatch.setattr(ui.time, "monotonic", lambda: later)
    assert client.post("/ui/exchange", json={"code": code}).status_code == 403


def test_page_trades_a_login_code_and_never_reads_a_token_from_the_url() -> None:
    page = ui._PAGE
    assert "/ui/exchange" in page and "code=" in page
    assert "token=" not in page.split("<script>")[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ui_feed_renders_real_server_sse_frames(tmp_path) -> None:
    """End to end on the wire format: SDK events -> bridge translator ->
    EventLog -> the server's own SSE framing -> the page's handleBlock/addEvent
    (run in node against a minimal DOM stub). Guards the payload-wrapper
    regression where every feed line rendered without its text."""
    import asyncio
    import json

    from agent_bridge.events import EventLog
    from agent_bridge.live_representation import translate_sdk_event
    from agent_bridge.routes.live_sessions import _RepresentedSession
    from agent_bridge.routes.sessions import _sse_event_stream

    log = EventLog(session_id="sim-1")
    sdk = [
        ("user.message", {"content": "Add a haiku"}),
        ("assistant.reasoning", {"content": "Plan it"}),
        ("tool.execution_start", {"toolCallId": "t1", "toolName": "bash", "arguments": {"command": "git status"}}),
        ("tool.execution_complete", {"toolCallId": "t1", "success": True, "result": {"content": "On branch main"}}),
        ("assistant.message", {"content": "done <b>not html</b>"}),
        ("assistant.turn_end", {}),
    ]
    for sdk_type, data in sdk:
        for event_type, payload in translate_sdk_event(sdk_type, data):
            log.append(event_type, payload)
    shim = _RepresentedSession(session_id="sim-1", event_log=log)

    async def frames(n):
        out = []
        gen = _sse_event_stream(shim, 0, server=None, is_disconnected=None, mgr=None)
        async for chunk in gen:
            if chunk.startswith("id:"):
                out.append(chunk)
            if len(out) >= n:
                break
        await gen.aclose()
        return out

    wire = asyncio.run(frames(len(log._events)))
    page = ui._PAGE
    fns = page[page.index("function clip"):page.index("async function streamLive")]
    harness = tmp_path / "feed.js"
    harness.write_text(
        "const rows = [];\n"
        "const feed = {childElementCount: 0, scrollHeight: 0, scrollTop: 0, clientHeight: 0,\n"
        "  appendChild(r) { rows.push(r); this.childElementCount++; }, removeChild() {}, firstChild: null};\n"
        "const $ = () => feed;\n"
        "const document = {createElement: () => ({kids: [], className: '', textContent: '',\n"
        "  appendChild(k) { this.kids.push(k); }}), createTextNode: (t) => ({textContent: t})};\n"
        "const MAX_FEED = 500;\n"
        + fns +
        "\nconst w = {lastId: 0};\n"
        f"for (const f of {json.dumps(wire)}) handleBlock(w, f.replace(/\\n\\n$/, ''));\n"
        "console.log(JSON.stringify({lastId: w.lastId, rows: rows.map((r) => ({cls: r.className,\n"
        "  text: r.kids.map((k) => k.textContent).join(' ')}))}));\n",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(harness)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    texts = [r["text"] for r in out["rows"]]
    assert out["lastId"] == len(wire)
    assert texts[0] == "user Add a haiku"
    assert texts[1] == "thinking Plan it"
    assert texts[2].startswith("tool bash") and "git status" in texts[2]
    assert texts[3] == "tool done On branch main"
    assert texts[4] == "agent done <b>not html</b>"  # literal text, never markup
    assert texts[5] == "— turn complete —"
