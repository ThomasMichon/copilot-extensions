"""Tests for `agent-bridge handoff*` verbs.

`handoff <target>` first tries an owned ACP session; on 404 it treats the
target as a worktree handle and hands off that worktree's current session.
Mirrors the resume verb's resolution order.
"""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClientError


class _FakeClient:
    def __init__(
        self, *, session_handoff=None, worktree_handoff=None, handoff_request=None
    ):
        self._session_handoff = session_handoff
        self._worktree_handoff = worktree_handoff
        self._handoff_request = handoff_request
        self.session_calls: list[tuple[str, str | None, bool]] = []
        self.worktree_calls: list[tuple[str, str | None, bool]] = []
        self.request_calls: list[tuple[str, str, str, str | None]] = []

    def handoff_session(self, session_id, *, reason=None, seed=True):
        self.session_calls.append((session_id, reason, seed))
        if isinstance(self._session_handoff, Exception):
            raise self._session_handoff
        return self._session_handoff

    def handoff_worktree(self, worktree_id, *, reason=None, seed=True):
        self.worktree_calls.append((worktree_id, reason, seed))
        if isinstance(self._worktree_handoff, Exception):
            raise self._worktree_handoff
        return self._worktree_handoff

    def handoff_request(
        self, worktree_id, *, session_id, seed_text, handoff_token=None
    ):
        self.request_calls.append(
            (worktree_id, session_id, seed_text, handoff_token)
        )
        if isinstance(self._handoff_request, Exception):
            raise self._handoff_request
        return self._handoff_request


def _args(target, *, reason=None, no_seed=False):
    return argparse.Namespace(session_id=target, reason=reason, no_seed=no_seed)


def _request_args(
    *,
    worktree_id="wt-1",
    session_id="sess-1",
    handoff_token="task:1",
    seed="/consume-handoff task:1",
    json=False,
):
    return argparse.Namespace(
        worktree_id=worktree_id,
        session_id=session_id,
        handoff_token=handoff_token,
        seed=seed,
        json=json,
    )


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(m, "_get_client", lambda *a, **k: client)


def test_owned_session_handoff_wins(monkeypatch, capsys):
    client = _FakeClient(
        session_handoff={"status": "idle", "session_id": "sess-2"}
    )
    _patch_client(monkeypatch, client)

    m._cmd_handoff(_args("sess-1", reason="ctx"))

    assert client.session_calls == [("sess-1", "ctx", True)]
    assert client.worktree_calls == []  # never fell through
    assert "Session sess-1 handed off -> successor sess-2 (idle)" in (
        capsys.readouterr().out
    )


def test_worktree_fallback_on_404(monkeypatch, capsys):
    client = _FakeClient(
        session_handoff=BridgeClientError(404, "Session wt-1 not found"),
        worktree_handoff={"status": "idle", "session_id": "owned-9"},
    )
    _patch_client(monkeypatch, client)

    m._cmd_handoff(_args("wt-1", no_seed=True))

    assert client.session_calls == [("wt-1", None, False)]  # seed disabled
    assert client.worktree_calls == [("wt-1", None, False)]
    assert "Worktree wt-1 handed off -> successor owned-9 (idle)" in (
        capsys.readouterr().out
    )


def test_conflict_409_is_not_worktree_fallback(monkeypatch, capsys):
    # A 409 (mid-turn / command agent) is a hard failure, NOT a signal to try
    # the worktree path (only 404 = "not an owned session" falls through).
    client = _FakeClient(
        session_handoff=BridgeClientError(409, "cannot hand off mid-turn"),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit):
        m._cmd_handoff(_args("sess-1"))
    assert client.worktree_calls == []


def test_worktree_not_found_reports_and_exits(monkeypatch, capsys):
    client = _FakeClient(
        session_handoff=BridgeClientError(404, "not an owned session"),
        worktree_handoff=BridgeClientError(404, "no session for worktree"),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit):
        m._cmd_handoff(_args("ghost"))
    err = capsys.readouterr().err
    assert "neither a bridge-owned session nor a worktree" in err


def test_handoff_request_json_round_trips(monkeypatch, capsys):
    client = _FakeClient(
        handoff_request={
            "session_id": "succ-2",
            "acp_session_id": "acp-succ-2",
            "status": "idle",
        }
    )
    _patch_client(monkeypatch, client)

    m._cmd_handoff_request(_request_args(json=True))

    assert client.request_calls == [
        ("wt-1", "sess-1", "/consume-handoff task:1", "task:1")
    ]
    payload = m.json.loads(capsys.readouterr().out)
    assert payload == {
        "accepted": True,
        "worktree_id": "wt-1",
        "requested_session_id": "sess-1",
        "handoff_token": "task:1",
        "successor_session_id": "succ-2",
        "successor_acp_session_id": "acp-succ-2",
        "successor_status": "idle",
    }


def test_handoff_request_no_session_degrades_cleanly(monkeypatch, capsys):
    client = _FakeClient(
        handoff_request=BridgeClientError(
            404, "No current session found for worktree wt-1 matching sess-1"
        )
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit):
        m._cmd_handoff_request(_request_args())
    err = capsys.readouterr().err
    assert "No current session for worktree wt-1 matches sess-1" in err
