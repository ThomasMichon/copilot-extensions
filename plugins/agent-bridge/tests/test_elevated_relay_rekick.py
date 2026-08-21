"""Elevated relay re-kick + re-resolve on (re)spawn (dotfiles#1610).

The elevated sub-daemon idle-exits after 600s. A session whose target relays to
it must, on (re)spawn, **re-kick** the sub-daemon and **re-resolve** the relay
with the sub-daemon's current port + token -- so a resume after the sub-daemon
went away re-launches it and cold-resumes from disk, instead of 500ing on a dead
port / stale token. All elevation side effects are mocked here.
"""

from __future__ import annotations

import time

import pytest

from agent_bridge import elevated
from agent_bridge.db import Database
import agent_bridge.transport as transport
from agent_bridge.transport import SpawnTarget, spawn_raw


# -- detection ---------------------------------------------------------------

def test_relay_agent_for_detects_elevated_relay(monkeypatch) -> None:
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: False)
    cmd = ["python", "-m", "agent_bridge", "acp-connect",
           "ws://127.0.0.1:65000/acp/SPO.Core", "--token", "abc", "--stdio"]
    assert elevated.relay_agent_for(cmd) == "SPO.Core"


def test_relay_agent_for_url_encoded_agent(monkeypatch) -> None:
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: False)
    cmd = ["python", "-m", "agent_bridge", "acp-connect",
           "ws://127.0.0.1:65000/acp/SPO.Core%40cloud1", "--token", "t", "--stdio"]
    assert elevated.relay_agent_for(cmd) == "SPO.Core@cloud1"


def test_relay_agent_for_none_for_non_relay(monkeypatch) -> None:
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: False)
    assert elevated.relay_agent_for(
        ["agent-codespaces", "ssh", "x", "--stdio"]) is None
    assert elevated.relay_agent_for(None) is None
    assert elevated.relay_agent_for([]) is None


def test_relay_agent_for_none_when_process_elevated(monkeypatch) -> None:
    # An elevated daemon spawns such agents LOCALLY (never relays), so it must
    # never try to re-kick itself.
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: True)
    cmd = ["python", "-m", "agent_bridge", "acp-connect",
           "ws://127.0.0.1:65000/acp/SPO.Core", "--token", "abc", "--stdio"]
    assert elevated.relay_agent_for(cmd) is None


def test_rekick_relay_command_rebuilds_with_fresh_port_and_token(monkeypatch) -> None:
    monkeypatch.setattr(elevated, "ensure_running", lambda **k: "fresh-token")
    monkeypatch.setattr(elevated, "discovered_port", lambda **k: 54321)
    cmd = elevated.rekick_relay_command("SPO.Core")
    assert "acp-connect" in cmd
    assert "ws://127.0.0.1:54321/acp/SPO.Core" in cmd
    assert "fresh-token" in cmd


def test_persisted_session_rows_reads_without_starting_daemon(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    db = Database(tmp_path / "elevated" / "sessions.db")
    now = time.time()
    db.create_session(
        "elevated-session",
        "quiet-river",
        "admin-agent",
        "/repo",
        "local",
        "stopped",
        now,
    )
    db.close()

    rows = elevated.persisted_session_rows()

    assert len(rows) == 1
    assert rows[0]["id"] == "elevated-session"
    assert rows[0]["turn_count"] == 0


# -- spawn_raw re-resolve ----------------------------------------------------

class _FakeProc:
    pid = 4242


@pytest.mark.asyncio
async def test_spawn_raw_rekicks_and_reresolves_elevated_relay(monkeypatch) -> None:
    monkeypatch.setattr(elevated, "relay_agent_for", lambda cmd: "SPO.Core")
    fresh = ["python", "-c", "pass"]
    monkeypatch.setattr(elevated, "rekick_relay_command", lambda agent, **k: fresh)

    captured: dict = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", _fake_exec)

    target = SpawnTarget(type="command", spawn_command=["stale", "relay", "cmd"])
    await spawn_raw(target)

    # The relay was rebuilt fresh and THAT is what got spawned (not the stale one).
    assert target.spawn_command == fresh
    assert "pass" in captured["args"]


@pytest.mark.asyncio
async def test_spawn_raw_elevated_relay_fail_soft(monkeypatch) -> None:
    monkeypatch.setattr(elevated, "relay_agent_for", lambda cmd: "SPO.Core")

    def _boom(agent, **k):
        raise RuntimeError("elevation declined")

    monkeypatch.setattr(elevated, "rekick_relay_command", _boom)

    target = SpawnTarget(type="command", spawn_command=["stale", "relay"])
    with pytest.raises(RuntimeError, match="elevated sub-daemon for 'SPO.Core'"):
        await spawn_raw(target)


@pytest.mark.asyncio
async def test_spawn_raw_non_relay_command_untouched(monkeypatch) -> None:
    monkeypatch.setattr(elevated, "relay_agent_for", lambda cmd: None)
    rekicked = {"called": False}

    def _mark(*a, **k):
        rekicked["called"] = True
        return []

    monkeypatch.setattr(elevated, "rekick_relay_command", _mark)

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", _fake_exec)

    target = SpawnTarget(type="command", spawn_command=["python", "-c", "pass"])
    await spawn_raw(target)

    assert rekicked["called"] is False
    assert target.spawn_command == ["python", "-c", "pass"]
