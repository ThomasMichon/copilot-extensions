"""Tests for CLI session selection (issue #39).

`send` reuses (and resumes) this caller's existing session and never starts a
fresh one over it; `--new` is removed. `create` forces a fresh session and
refuses (rather than silently reusing) when a one-session-per-CodeSpace agent
is already busy.
"""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClientError


class FakeClient:
    """Minimal in-memory stand-in for BridgeClient."""

    def __init__(self, sessions=None, agents=None, conflict_sid=None):
        self.sessions = list(sessions or [])  # newest-first, like the server
        self._agents = agents or []
        self._conflict_sid = conflict_sid
        self.resumed: list[str] = []
        self.started: list[dict] = []

    def list_agents(self):
        return [{"name": n} for n in self._agents]

    def list_sessions(self, *, status=None):
        if status:
            return [s for s in self.sessions if s.get("status") == status]
        return list(self.sessions)

    def get_session(self, sid):
        for s in self.sessions:
            if s.get("session_id") == sid:
                return dict(s)
        raise BridgeClientError(404, "not found")

    def resume_session(self, sid):
        self.resumed.append(sid)
        for s in self.sessions:
            if s.get("session_id") == sid:
                s["status"] = "idle"
        return {"status": "idle"}

    def start_session(self, *, agent=None, caller_id=None, force_new=False):
        self.started.append(
            {"agent": agent, "caller_id": caller_id, "force_new": force_new}
        )
        if self._conflict_sid is not None:
            raise BridgeClientError(
                409,
                {
                    "error": "session_conflict",
                    "existing_session_id": self._conflict_sid,
                },
            )
        new = {
            "session_id": "fresh-sid",
            "name": "neat-forge",
            "status": "idle",
            "agent_name": agent,
            "caller_id": caller_id,
            "turn_count": 0,
        }
        self.sessions.insert(0, new)
        return {"session_id": "fresh-sid", "name": "neat-forge"}


def _sess(sid, *, agent, caller, status, turns=1):
    return {
        "session_id": sid,
        "name": f"name-{sid}",
        "agent_name": agent,
        "caller_id": caller,
        "status": status,
        "turn_count": turns,
    }


@pytest.fixture
def fixed_caller(monkeypatch):
    monkeypatch.setattr(m, "_get_caller_id", lambda: "host-A")
    return "host-A"


# -- _find_caller_session ----------------------------------------------------


def test_find_caller_session_includes_stopped():
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="stopped"),
    ])
    found = m._find_caller_session(client, "codespace:cs", "host-A")
    assert found is not None and found["session_id"] == "s1"


def test_find_caller_session_excludes_other_caller():
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-B", status="idle"),
    ])
    assert m._find_caller_session(client, "codespace:cs", "host-A") is None


# -- send (force_new=False) implied reuse ------------------------------------


def test_send_reuses_caller_idle_session(fixed_caller):
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="idle"),
    ])
    sid = m._start_agent_session(client, "codespace:cs")
    assert sid == "s1"
    assert client.started == []  # no new spawn
    assert client.resumed == []  # idle needs no resume


def test_send_resumes_caller_stopped_session(fixed_caller):
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="stopped"),
    ])
    sid = m._start_agent_session(client, "codespace:cs")
    assert sid == "s1"
    assert client.resumed == ["s1"]  # stopped session resumed, not orphaned
    assert client.started == []


def test_send_starts_new_when_no_caller_session(fixed_caller, monkeypatch):
    monkeypatch.setattr(m, "_wait_for_idle", lambda *a, **k: None)
    client = FakeClient(sessions=[])
    sid = m._start_agent_session(client, "codespace:cs")
    assert sid == "fresh-sid"
    assert client.started and client.started[0]["force_new"] is False


def test_send_conflict_reuses_other_callers_session(fixed_caller):
    # No session for this caller, but the codespace already has one (another
    # caller). The server 409s; send adopts and resumes it.
    other = _sess("s9", agent="codespace:cs", caller="host-B", status="stopped")
    client = FakeClient(sessions=[other], conflict_sid="s9")
    sid = m._start_agent_session(client, "codespace:cs")
    assert sid == "s9"
    assert client.resumed == ["s9"]


# -- create (force_new=True) -------------------------------------------------


def test_create_force_new_passes_flag_and_skips_reuse(fixed_caller, monkeypatch):
    monkeypatch.setattr(m, "_wait_for_idle", lambda *a, **k: None)
    # A reusable caller session exists, but force_new must ignore it.
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="idle"),
    ])
    sid = m._start_agent_session(client, "codespace:cs", force_new=True)
    assert sid == "fresh-sid"
    assert client.started and client.started[0]["force_new"] is True


def test_create_refuse_on_conflict_raises(fixed_caller):
    client = FakeClient(sessions=[], conflict_sid="s9")
    with pytest.raises(m._AgentSessionConflict) as ei:
        m._start_agent_session(
            client, "codespace:cs", force_new=True, refuse_on_conflict=True,
        )
    assert ei.value.existing_session_id == "s9"
    assert client.resumed == []  # never silently adopts


# -- CLI command guards ------------------------------------------------------


def test_cmd_send_rejects_new_flag():
    args = argparse.Namespace(target="codespace:cs", prompt="hi", new=True)
    with pytest.raises(SystemExit) as ei:
        m._cmd_send(args)
    assert ei.value.code == 2


def test_cmd_create_refuses_on_conflict(monkeypatch):
    client = FakeClient(
        sessions=[], agents=["codespace:cs"], conflict_sid="s9",
    )
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_get_caller_id", lambda: "host-A")
    args = argparse.Namespace(
        target="codespace:cs", prompt=None, caller=None, json=False,
        no_wait=False,
    )
    with pytest.raises(SystemExit) as ei:
        m._cmd_create(args)
    assert ei.value.code == 1
    assert client.resumed == []
