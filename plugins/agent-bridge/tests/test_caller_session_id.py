"""``caller_session_id``: the Copilot session that created a bridge session.

Covers env capture in the CLI, validation, the additive v25 schema migration,
the DB round trip, exposure next to ``caller_id`` in the HTTP session rows
(list, detail, status) that ``agent-bridge --json sessions`` prints and in the
create response, and the client's HTTP-protocol gate against older daemons.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_bridge import __main__ as m
from agent_bridge import caller_session
from agent_bridge.app import create_app
from agent_bridge.caller_session import (
    CALLER_SESSION_ENV,
    MAX_CALLER_SESSION_ID_LENGTH,
    caller_session_id_from_env,
    normalize_caller_session_id,
)
from agent_bridge.client import BridgeClient
from agent_bridge.db import SCHEMA_VERSION, Database
from agent_bridge.models import (
    ServiceConfig,
    SessionStatus,
    StartSessionRequest,
    StartSessionResponse,
)
from agent_bridge.protocol import CALLER_SESSION_ID_PROTOCOL_VERSION
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget

SID = "3f2a9c1e-7b4d-4e8a-9c0f-1a2b3c4d5e6f"


# -- validation + env capture -------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (SID, SID),
        (f"  {SID}\n", SID),
        ("session_1.a:b@c-d", "session_1.a:b@c-d"),
        (None, None),
        ("", None),
        ("   ", None),
        ("-leading-dash", None),
        ("has space", None),
        ("semi;colon", None),
        ("a/b", None),
        ("x" * (MAX_CALLER_SESSION_ID_LENGTH + 1), None),
        (12345, None),
    ],
)
def test_normalize_caller_session_id(value, expected) -> None:
    assert normalize_caller_session_id(value) == expected


def test_env_capture_present_absent_invalid() -> None:
    assert caller_session_id_from_env({CALLER_SESSION_ENV: SID}) == SID
    assert caller_session_id_from_env({}) is None
    assert caller_session_id_from_env({CALLER_SESSION_ENV: ""}) is None
    assert caller_session_id_from_env({CALLER_SESSION_ENV: "bad id!"}) is None


def test_env_capture_reads_process_env(monkeypatch) -> None:
    monkeypatch.setenv(CALLER_SESSION_ENV, SID)
    assert m._get_caller_session_id() == SID
    monkeypatch.delenv(CALLER_SESSION_ENV)
    assert m._get_caller_session_id() is None


# -- CLI create path ----------------------------------------------------------


class _RecordingClient:
    def __init__(self) -> None:
        self.started: list[dict] = []

    def list_agents(self):
        return []

    def list_sessions(self, *, status=None):
        return []

    def start_session(self, **kwargs):
        self.started.append(kwargs)
        return {"session_id": "fresh-sid", "name": "neat-forge"}


@pytest.fixture
def _cli(monkeypatch):
    monkeypatch.setattr(m, "_get_caller_id", lambda: "host-A")
    monkeypatch.setattr(m, "_wait_for_idle", lambda *a, **k: None)
    return _RecordingClient()


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(SID, SID), (None, None), ("", None), ("not valid!", None)],
)
def test_cli_create_captures_env(monkeypatch, _cli, env_value, expected) -> None:
    if env_value is None:
        monkeypatch.delenv(CALLER_SESSION_ENV, raising=False)
    else:
        monkeypatch.setenv(CALLER_SESSION_ENV, env_value)
    sid = m._start_agent_session(_cli, "codespace:cs", force_new=True)
    assert sid == "fresh-sid"  # never fails the command
    assert _cli.started[0]["caller_session_id"] == expected
    assert _cli.started[0]["caller_id"] == "host-A"


def test_client_start_session_body_carries_field() -> None:
    client = BridgeClient("http://127.0.0.1:0", "t")
    client._daemon_proto = (CALLER_SESSION_ID_PROTOCOL_VERSION, 1)
    with patch.object(client, "_request", return_value={}) as req:
        client.start_session(agent="a", caller_id="c", caller_session_id=SID)
        client.start_session(agent="a", caller_id="c")
    assert req.call_args_list[0].args[2]["caller_session_id"] == SID
    assert "caller_session_id" not in req.call_args_list[1].args[2]


@pytest.mark.parametrize(
    "daemon_proto", [(CALLER_SESSION_ID_PROTOCOL_VERSION - 1, 1), (0, 0)],
)
def test_client_omits_field_for_older_daemon_and_warns_once(
    monkeypatch, capsys, daemon_proto,
) -> None:
    """An older (or unversioned) daemon would silently ignore the field, so the
    client omits it, still creates the session, and says so exactly once."""
    monkeypatch.setattr(caller_session, "_unsupported_warned", False)
    client = BridgeClient("http://127.0.0.1:0", "t")
    client._daemon_proto = daemon_proto
    with patch.object(client, "_request", return_value={}) as req:
        client.start_session(agent="a", caller_id="c", caller_session_id=SID)
        client.start_session(agent="a", caller_id="c", caller_session_id=SID)
    for call in req.call_args_list:
        assert "caller_session_id" not in call.args[2]
        assert call.args[2]["caller_id"] == "c"
    err = capsys.readouterr().err
    assert err.count("predates caller_session_id") == 1
    assert f"v{CALLER_SESSION_ID_PROTOCOL_VERSION}" in err


def test_client_skips_protocol_probe_without_field() -> None:
    """No caller session id -> no /health probe is spent on the gate."""
    client = BridgeClient("http://127.0.0.1:0", "t")
    with patch.object(client, "_request", return_value={}) as req, \
            patch.object(client, "daemon_protocol") as proto:
        client.start_session(agent="a", caller_id="c")
    proto.assert_not_called()
    assert req.call_count == 1


def test_response_model_defaults_field_to_none() -> None:
    resp = StartSessionResponse(session_id="s", name="n", status="idle")
    assert resp.caller_session_id is None


def test_request_model_tolerates_absent_field() -> None:
    assert StartSessionRequest(agent="a").caller_session_id is None
    assert StartSessionRequest(agent="a", caller_session_id=SID).caller_session_id == SID


# -- DB: migration + round trip ----------------------------------------------


def _session_columns(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    finally:
        conn.close()


def test_migrates_existing_v24_database(tmp_path) -> None:
    path = tmp_path / "v24.db"
    Database(str(path)).close()
    conn = sqlite3.connect(str(path))
    conn.execute("ALTER TABLE sessions DROP COLUMN caller_session_id")
    conn.execute(
        "INSERT INTO sessions (id, name, caller_id, created_at, updated_at) "
        "VALUES ('old', 'legacy', '/wt', 1.0, 1.0)"
    )
    conn.execute("UPDATE schema_version SET version=24")
    conn.commit()
    conn.close()
    assert "caller_session_id" not in _session_columns(path)

    db = Database(str(path))
    try:
        assert "caller_session_id" in _session_columns(path)
        probe = sqlite3.connect(str(path))
        try:
            version = probe.execute("SELECT version FROM schema_version").fetchone()[0]
        finally:
            probe.close()
        assert version == SCHEMA_VERSION == 25
        old = db.get_session("old")
        assert old["caller_id"] == "/wt"
        assert old["caller_session_id"] is None
    finally:
        db.close()


def test_db_round_trip(tmp_path) -> None:
    db = Database(str(tmp_path / "rt.db"))
    try:
        db.create_session(
            session_id="s1", name="n", agent_name="a", target_dir="/r",
            target_type="local", status="idle", now=1.0,
            caller_id="/wt", caller_session_id=SID,
        )
        row = db.get_session("s1")
        assert row["caller_id"] == "/wt"
        assert row["caller_session_id"] == SID
    finally:
        db.close()


# -- HTTP exposure -------------------------------------------------------------


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none-projects.yaml"),
    )
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path / "bridge"))
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "t.db"))
    app = create_app(config=cfg, token="test-token")
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test-token"
        yield app, c


def test_live_session_rows_expose_field(http) -> None:
    app, c = http
    session = Session(
        "live-1", "reviewer", SpawnTarget(type="local", cwd="/repo"),
        caller_id="/wt", caller_session_id=SID,
    )
    session.status = SessionStatus.IDLE
    app.state.session_manager._sessions[session.session_id] = session

    listed = c.get("/api/v1/sessions").json()["sessions"]
    row = next(s for s in listed if s["session_id"] == "live-1")
    assert row["caller_id"] == "/wt"
    assert row["caller_session_id"] == SID
    detail = c.get("/api/v1/sessions/live-1").json()
    assert detail["caller_session_id"] == SID
    status = c.get("/api/v1/sessions/live-1/status").json()
    assert status["caller_session_id"] == SID


def test_create_route_round_trip(http) -> None:
    """POST with the field -> persisted -> listed; an invalid value is dropped."""
    app, c = http
    app.state.resolver = MagicMock()
    app.state.resolver.resolve_async = AsyncMock(
        return_value=SpawnTarget(type="local", cwd="/repo", project="p")
    )
    mgr = app.state.session_manager
    started: list[dict] = []

    async def _fake_start(target, **kwargs):
        started.append(kwargs)
        n = len(started)
        session = Session(
            f"new-{n}", f"name-{n}", target,
            agent_name=kwargs.get("agent_name"),
            caller_id=kwargs.get("caller_id"),
            caller_session_id=normalize_caller_session_id(
                kwargs.get("caller_session_id")
            ),
        )
        session.status = SessionStatus.IDLE
        mgr._sessions[session.session_id] = session
        mgr.db.create_session(
            session_id=session.session_id, name=session.name, agent_name=None,
            target_dir=target.cwd, target_type=target.type, status="idle",
            now=1.0, caller_id=session.caller_id,
            caller_session_id=session.caller_session_id,
        )
        return session

    with patch.object(mgr, "start_session", side_effect=_fake_start):
        ok = c.post(
            "/api/v1/sessions",
            json={"agent": "a", "caller_id": "/wt", "caller_session_id": SID,
                  "force_new": True},
        )
        bad = c.post(
            "/api/v1/sessions",
            json={"agent": "a", "caller_id": "/wt2",
                  "caller_session_id": "bad id!", "force_new": True},
        )
        # Caller-affinity reuse: the response echoes the reused session's
        # recorded caller session, not the new request's.
        reused = c.post(
            "/api/v1/sessions",
            json={"agent": "a", "caller_id": "/wt", "caller_session_id": "other-1"},
        )
    assert ok.status_code == 201, ok.text
    assert bad.status_code == 201, bad.text
    assert reused.status_code == 201, reused.text
    assert started[0]["caller_session_id"] == SID
    assert len(started) == 2
    assert ok.json()["caller_session_id"] == SID
    assert bad.json()["caller_session_id"] is None
    assert reused.json()["session_id"] == "new-1"
    assert reused.json()["caller_session_id"] == SID
    by_id = {s["session_id"]: s for s in c.get("/api/v1/sessions").json()["sessions"]}
    assert by_id["new-1"]["caller_session_id"] == SID
    assert by_id["new-2"]["caller_session_id"] is None
    assert mgr.db.get_session("new-1")["caller_session_id"] == SID


def test_persisted_rows_expose_field(http) -> None:
    """A session known only from the DB (e.g. after a restart) still shows it."""
    from agent_bridge.routes.sessions import _persisted_session_info

    app, _ = http
    db = app.state.session_manager.db
    db.create_session(
        session_id="cold-1", name="n", agent_name=None, target_dir="/r",
        target_type="local", status="stopped", now=1.0,
        caller_id="/wt", caller_session_id=SID,
    )
    info = _persisted_session_info(db.get_session("cold-1"), daemon_running=False)
    assert info.caller_id == "/wt"
    assert info.caller_session_id == SID


# -- Remote-venue CLI -> local daemon -> listing (integration) -----------------


class _InProcessClient(BridgeClient):
    """A real BridgeClient whose HTTP goes to the in-process daemon app."""

    def __init__(self, http_client) -> None:
        super().__init__("http://127.0.0.1:0", "test-token")
        self._http = http_client

    def _request(self, method, path, body=None, *, params=None, request_timeout=None):
        from agent_bridge.client import BridgeClientError

        resp = self._http.request(method, path, json=body, params=params)
        if resp.status_code >= 400:
            raise BridgeClientError(resp.status_code, resp.text)
        return None if resp.status_code == 204 else resp.json()


@pytest.mark.parametrize(
    ("agent", "target"),
    [
        ("codespace:demo", SpawnTarget(type="command", project="p")),
        ("ssh:devbox", SpawnTarget(type="ssh", host="devbox", project="p")),
    ],
)
@pytest.mark.parametrize("verb", ["create", "send"])
def test_remote_venue_cli_records_caller_session(
    http, monkeypatch, capsys, agent, target, verb,
) -> None:
    """A local CLI create/send against a remote venue is served by the LOCAL
    daemon, so the caller session lands on the local row and is listed by
    ``agent-bridge --json sessions`` and ``GET /api/v1/sessions``."""
    from agent_bridge import session_maintenance_cli, session_targeting_cli

    app, c = http
    app.state.resolver = MagicMock()
    app.state.resolver.resolve_async = AsyncMock(return_value=target)
    mgr = app.state.session_manager

    async def _fake_spawn(spawn_target, **kwargs):
        session = Session(
            "remote-1", "remote-name", spawn_target,
            agent_name=kwargs.get("agent_name"),
            caller_id=kwargs.get("caller_id"),
            caller_session_id=normalize_caller_session_id(
                kwargs.get("caller_session_id")
            ),
        )
        session.status = SessionStatus.IDLE
        mgr._sessions[session.session_id] = session
        return session

    client = _InProcessClient(c)
    monkeypatch.setattr(client, "list_agents", lambda: [{"name": agent}])
    monkeypatch.setattr(m, "_get_client", lambda **_: client)
    monkeypatch.setattr(m, "_get_caller_id", lambda: "/wt")
    monkeypatch.setattr(m, "_wait_for_idle", lambda *a, **k: None)
    monkeypatch.setattr(m, "_submit_and_stream", lambda *a, **k: None)
    monkeypatch.setattr(session_targeting_cli, "_mark_resume_if_behind", lambda *a, **k: False)
    monkeypatch.setenv(CALLER_SESSION_ENV, SID)

    args = argparse.Namespace(
        target=agent, prompt="hello", prompt_file=None, json=False,
        caller=None, force=False, new=False, cli=False, detach=False,
        charter=None, model=None, effort=None, target_dir=None,
        worktree_id=None, session_id_file=None, min_daemon_protocol=None,
        expected_session_id=None, full_history=False,
    )
    with patch.object(mgr, "start_session", side_effect=_fake_spawn) as spawn:
        getattr(session_targeting_cli, f"_cmd_{verb}")(args)
    assert spawn.call_args.kwargs["caller_session_id"] == SID

    row = next(
        s for s in c.get("/api/v1/sessions").json()["sessions"]
        if s["session_id"] == "remote-1"
    )
    assert (row["caller_id"], row["caller_session_id"]) == ("/wt", SID)

    capsys.readouterr()
    session_maintenance_cli._cmd_sessions(argparse.Namespace(status=None, json=True))
    listed = json.loads(capsys.readouterr().out)
    assert {s["session_id"]: s["caller_session_id"] for s in listed} == {
        "remote-1": SID,
    }