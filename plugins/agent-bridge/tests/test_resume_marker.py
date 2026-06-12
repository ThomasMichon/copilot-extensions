"""Tests for the send resume-marker (issue A: don't dump prior history).

A host attaching to a CodeSpace agent's pre-existing session should NOT have
the entire backlog replayed. `_mark_resume_if_behind` fast-forwards a
first-time caller's delivery cursor to the head and prints a marker.
"""

from __future__ import annotations

from agent_bridge import __main__ as m


class _FakeClient:
    def __init__(self, *, cursor_info, session):
        self._cursor_info = cursor_info
        self._session = session
        self.acked: tuple | None = None

    def get_cursor_info(self, session_id, *, caller_id=None):
        return self._cursor_info

    def get_session(self, session_id):
        return self._session

    def ack_cursor(self, session_id, last_id, *, caller_id=None):
        self.acked = (session_id, last_id, caller_id)
        return last_id


def test_marks_and_fast_forwards_when_behind(capsys):
    client = _FakeClient(
        cursor_info={"last_acked_id": 0, "head_id": 42},
        session={"turn_count": 3},
    )
    marked = m._mark_resume_if_behind(client, "sess-1", caller_id="host-A")
    assert marked is True
    # cursor fast-forwarded to the live head
    assert client.acked == ("sess-1", 42, "host-A")
    out = capsys.readouterr().out
    assert "Resuming existing session sess-1" in out
    assert "3 prior turn(s)" in out
    assert "--range 1-42" in out


def test_no_marker_for_brand_new_session(capsys):
    # caller is new (cursor 0) but the session has no turns yet -> stream normally
    client = _FakeClient(
        cursor_info={"last_acked_id": 0, "head_id": 0},
        session={"turn_count": 0},
    )
    assert m._mark_resume_if_behind(client, "sess-1", caller_id="host-A") is False
    assert client.acked is None
    assert capsys.readouterr().out == ""


def test_no_marker_when_caller_already_mid_stream(capsys):
    # caller has already consumed from this session -> continue, don't skip
    client = _FakeClient(
        cursor_info={"last_acked_id": 12, "head_id": 42},
        session={"turn_count": 3},
    )
    assert m._mark_resume_if_behind(client, "sess-1", caller_id="host-A") is False
    assert client.acked is None
    assert capsys.readouterr().out == ""


def test_no_marker_when_head_zero_despite_turns(capsys):
    # defensive: turns reported but no events -> nothing to skip
    client = _FakeClient(
        cursor_info={"last_acked_id": 0, "head_id": 0},
        session={"turn_count": 2},
    )
    assert m._mark_resume_if_behind(client, "sess-1", caller_id="host-A") is False
    assert client.acked is None
