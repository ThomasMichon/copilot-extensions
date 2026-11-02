"""Agenda heartbeat election uses the admitted-read lifecycle."""

from types import SimpleNamespace

import pytest

from agent_worktrees import agenda_feed


def test_heartbeat_uses_read_election_without_writer_election(monkeypatch):
    admitted = [True]
    closed = []
    reply = {
        "ok": True,
        "generation": "a" * 32,
        "sequence": 1,
        "availability": "not-published",
        "observation": None,
    }
    agendas = SimpleNamespace(read=lambda *a, **kw: reply)
    feed = agenda_feed.AgendaFeed(agendas)

    def close_admission(reason):
        closed.append(reason)
        feed.close(reason)

    agendas.owner = SimpleNamespace(
        _read_elected=lambda: admitted[0],
        close_admission=close_admission,
    )
    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.001)
    feed.activate()
    stream = feed.stream(
        {
            "contract_version": 1,
            "project": "project",
            "worktree_id": "worktree",
            "session_id": "session",
        },
        lambda: False,
    )
    try:
        assert next(stream) == {"type": "snapshot", **reply}
        assert next(stream)["type"] == "heartbeat"
        assert closed == []
        admitted[0] = False
        assert next(stream)["reason"] == "superseded"
        with pytest.raises(StopIteration):
            next(stream)
        assert closed == ["superseded"]
        assert feed.active_count() == 0
    finally:
        stream.close()
