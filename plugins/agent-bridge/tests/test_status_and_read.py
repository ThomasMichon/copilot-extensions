"""CLI tests for `status <sid>` and incremental `read` (#46.1, #46.2)."""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m


class _FakeClient:
    """Records read_range calls and serves canned status/cursor data."""

    def __init__(self, *, head=0, status=None):
        self._head = head
        self._status = status or {}
        self.read_calls: list[tuple[int, int | None]] = []

    def get_cursor_info(self, sid, *, caller_id=None):
        return {"last_acked_id": 0, "head_id": self._head}

    def read_range(self, sid, *, start=0, end=None):
        self.read_calls.append((start, end))
        return []

    def get_session_status(self, sid, *, caller_id=None):
        return self._status


def _read_args(**kw):
    base = dict(
        session_id="s1", caller=None, json=False, no_follow=False,
        range=None, event=None, tail=None, since=None,
        expand=None, no_color=True,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_read_tail_reads_last_n_events(monkeypatch):
    client = _FakeClient(head=100)
    monkeypatch.setattr(m, "_get_client", lambda: client)
    m._cmd_read(_read_args(tail=10))
    # last 10 of head=100 -> [91, 100]
    assert client.read_calls == [(91, 100)]


def test_read_tail_clamps_at_one(monkeypatch):
    client = _FakeClient(head=3)
    monkeypatch.setattr(m, "_get_client", lambda: client)
    m._cmd_read(_read_args(tail=10))
    assert client.read_calls == [(1, 3)]


def test_read_since_reads_after_id(monkeypatch):
    client = _FakeClient(head=100)
    monkeypatch.setattr(m, "_get_client", lambda: client)
    m._cmd_read(_read_args(since=42))
    # since 42 -> start at 43, open-ended
    assert client.read_calls == [(43, None)]


def _status_args(**kw):
    base = dict(
        session_id="s1", caller=None, json=False, steps=0,
        expand=None, no_color=True,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_status_renders_inflight_tool(monkeypatch, capsys):
    client = _FakeClient(status={
        "session_id": "s1", "name": "calm-lake", "agent_name": "codespace:x",
        "caller_id": "host-A", "status": "running", "turn_count": 3,
        "context_pct": 12.0, "head_id": 50, "last_acked_id": 40, "behind": 10,
        "active_tool": {"title": "Build", "command": "rush build", "elapsed_s": 17.4},
        "progress": {"build": "ok", "pr": "42"},
        "updated_at": "2026-06-15T18:00:00+00:00",
    })
    monkeypatch.setattr(m, "_get_client", lambda: client)
    m._cmd_status(_status_args())
    out = capsys.readouterr().out
    assert "[running]" in out
    assert "Running: Build (17s)" in out
    assert "rush build" in out
    assert "10 new" in out  # cursor-lag hint
    assert "Progress: build=ok  pr=42" in out


def test_status_idle_when_no_tool(monkeypatch, capsys):
    client = _FakeClient(status={
        "session_id": "s1", "name": "n", "agent_name": None, "caller_id": None,
        "status": "idle", "turn_count": 0, "context_pct": None,
        "head_id": 5, "last_acked_id": 5, "behind": 0, "active_tool": None,
        "updated_at": "2026-06-15T18:00:00+00:00",
    })
    monkeypatch.setattr(m, "_get_client", lambda: client)
    m._cmd_status(_status_args())
    out = capsys.readouterr().out
    assert "caught up" in out
    assert "idle -- no tool in flight" in out


def test_status_404_exits(monkeypatch):
    from agent_bridge.client import BridgeClientError

    class _C:
        def get_session_status(self, sid, *, caller_id=None):
            raise BridgeClientError(404, "Session s1 not found")

    monkeypatch.setattr(m, "_get_client", lambda: _C())
    with pytest.raises(SystemExit) as ei:
        m._cmd_status(_status_args())
    assert ei.value.code == 1
