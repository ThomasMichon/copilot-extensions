"""Coming-up settle guard for the send/reuse path (ce#606).

A reused ``codespace:`` session caught mid-startup (``created``/``starting``)
must not be handed straight to ``submit_prompt`` -- that races the bring-up and
the bridge rejects it with a 409 "... is starting, not idle". The CLI now waits
for the session to settle first; these tests pin that behavior.
"""

from __future__ import annotations

import agent_bridge.__main__ as m


class _FakeClient:
    """A client whose ``get_session`` yields a scripted status sequence."""

    def __init__(self, statuses: list[str]) -> None:
        self._statuses = list(statuses)
        self.calls = 0

    def get_session(self, sid: str) -> dict:
        self.calls += 1
        idx = min(self.calls - 1, len(self._statuses) - 1)
        return {"session_id": sid, "name": "t", "status": self._statuses[idx]}


def test_already_settled_returns_without_polling() -> None:
    c = _FakeClient(["idle"])
    s = m._await_coming_up_settled(c, {"session_id": "s1", "status": "idle"})
    assert s["status"] == "idle"
    assert c.calls == 0  # not coming up -> no poll


def test_starting_then_idle_settles(monkeypatch) -> None:
    monkeypatch.setattr(m, "_COMING_UP_POLL_INTERVAL", 0.001)
    c = _FakeClient(["starting", "starting", "idle"])
    s = m._await_coming_up_settled(c, {"session_id": "s1", "status": "starting"})
    assert s["status"] == "idle"


def test_created_then_running_settles_to_running(monkeypatch) -> None:
    # A session that becomes someone else's live turn settles to 'running' so
    # the caller routes into the busy-guard (not a 409, not a silent pile-on).
    monkeypatch.setattr(m, "_COMING_UP_POLL_INTERVAL", 0.001)
    c = _FakeClient(["created", "running"])
    s = m._await_coming_up_settled(c, {"session_id": "s1", "status": "created"})
    assert s["status"] == "running"


def test_starting_then_terminal_settles_to_terminal(monkeypatch) -> None:
    # A coming-up session whose boot fails settles to a terminal status; the
    # caller routes it away from reuse (start fresh) rather than submitting to a
    # dead session.
    monkeypatch.setattr(m, "_COMING_UP_POLL_INTERVAL", 0.001)
    c = _FakeClient(["starting", "failed"])
    s = m._await_coming_up_settled(c, {"session_id": "s1", "status": "starting"})
    assert s["status"] == "failed"
    assert s["status"] not in m._REUSABLE_SESSION_STATES


def test_timeout_returns_last_seen(monkeypatch) -> None:
    monkeypatch.setattr(m, "_COMING_UP_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(m, "_COMING_UP_SETTLE_TIMEOUT", 0.02)
    c = _FakeClient(["starting"])  # never settles
    s = m._await_coming_up_settled(c, {"session_id": "s1", "status": "starting"})
    assert s["status"] == "starting"  # falls through after the bound
