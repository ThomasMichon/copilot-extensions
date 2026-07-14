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
        self.ended: list[str] = []

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

    def end_session(self, sid, *, force=False):
        self.ended.append(sid)
        self.sessions = [
            s for s in self.sessions if s.get("session_id") != sid
        ]

    def get_session_status(self, sid, *, caller_id=None):
        for s in self.sessions:
            if s.get("session_id") == sid:
                return {
                    "name": s.get("name", ""),
                    "status": s.get("status", ""),
                    "agent_name": s.get("agent_name"),
                    "caller_id": s.get("caller_id"),
                    "turn_count": s.get("turn_count", 0),
                    "behind": 0,
                    "active_tool": {
                        "title": "rush build",
                        "elapsed_s": 42,
                        "command": "rush build -t @ms/app",
                    },
                }
        raise BridgeClientError(404, "not found")

    def start_session(self, *, agent=None, caller_id=None, sender_repo=None, force_new=False):
        self.started.append(
            {"agent": agent, "caller_id": caller_id,
             "sender_repo": sender_repo, "force_new": force_new}
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


# -- D3: worktree-handle addressing + reply-to ------------------------------


def test_live_reply_to_prefers_explicit(monkeypatch):
    monkeypatch.setattr(m, "_worktrees_get", lambda key: "/home/x/wt-cwd")
    monkeypatch.setenv("SESSION_ID", "env-sess")
    args = argparse.Namespace(reply_to="explicit-handle")
    assert m._live_reply_to(args) == "explicit-handle"


def test_live_reply_to_uses_worktree_handle(monkeypatch):
    # The durable, handoff-surviving address is the worktree handle (basename of
    # the worktree dir) -- preferred over the ephemeral env session id.
    monkeypatch.setattr(
        m, "_worktrees_get",
        lambda key: "/home/x/src/.worktrees/repo/wt-abc" if key == "worktree-dir" else None,
    )
    monkeypatch.setenv("SESSION_ID", "env-sess")
    args = argparse.Namespace(reply_to=None)
    assert m._live_reply_to(args) == "wt-abc"


def test_live_reply_to_falls_back_to_env_session(monkeypatch):
    # Outside any worktree (e.g. a bridge-owned agent), fall back to the session
    # id from the environment.
    monkeypatch.setattr(m, "_worktrees_get", lambda key: None)
    monkeypatch.delenv("AGENT_BRIDGE_SESSION_ID", raising=False)
    monkeypatch.setenv("SESSION_ID", "env-sess")
    args = argparse.Namespace(reply_to=None)
    assert m._live_reply_to(args) == "env-sess"


class _LiveFakeClient:
    """Stand-in exercising the live-session delivery path of `send`."""

    def __init__(self, resolved):
        self._resolved = resolved
        self.delivered: list[dict] = []

    def resolve_live_session(self, handle):
        return dict(self._resolved) if self._resolved else {}

    def send_live_message(self, session_id, *, sender, body, reply_to=None,
                          kind="prompt", wait=False, wait_timeout=None):
        self.delivered.append(
            {"session_id": session_id, "sender": sender,
             "body": body, "reply_to": reply_to, "kind": kind, "wait": wait}
        )
        return {"message_id": 1, "replied": False}


def test_cmd_send_resolves_worktree_handle_and_delivers(monkeypatch, capsys):
    # `send <worktree-handle>` resolves to the live session and delivers there.
    client = _LiveFakeClient(resolved={"session_id": "live-sess-1"})
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_live_sender_label", lambda args: "cjohnson@peer")
    monkeypatch.setattr(m, "_live_reply_to", lambda args: "wt-caller")
    args = argparse.Namespace(
        target="wt-target", prompt="please rebase", new=False, json=False,
        no_wait=True,
    )
    m._cmd_send(args)
    assert client.delivered == [
        {"session_id": "live-sess-1", "sender": "cjohnson@peer",
         "body": "please rebase", "reply_to": "wt-caller",
         "kind": "prompt", "wait": False}
    ]


class _ReplyingLiveClient(_LiveFakeClient):
    def send_live_message(self, session_id, *, sender, body, reply_to=None,
                          kind="prompt", wait=False, wait_timeout=None):
        self.delivered.append({"wait": wait, "wait_timeout": wait_timeout})
        return {"message_id": 7, "replied": True, "reply": "done - rebased",
                "stop_reason": "end_turn"}


def test_cmd_send_waits_and_prints_reply(monkeypatch, capsys):
    # By default (no --no-wait) a live send waits for the reply turn and prints
    # the receiver's assistant output.
    client = _ReplyingLiveClient(resolved={"session_id": "live-sess-1"})
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_live_sender_label", lambda args: "peer")
    monkeypatch.setattr(m, "_live_reply_to", lambda args: "wt-caller")
    args = argparse.Namespace(
        target="wt-target", prompt="rebase please", new=False, json=False,
        no_wait=False, reply_timeout=90.0,
    )
    m._cmd_send(args)
    assert client.delivered == [{"wait": True, "wait_timeout": 90.0}]
    out = capsys.readouterr().out
    assert "Reply from live-sess-1" in out
    assert "done - rebased" in out


def test_live_message_kind_precedence():
    # --notify / --status-check are shorthands; else --kind; else prompt.
    assert m._live_message_kind(argparse.Namespace(notify=True)) == "notify"
    assert m._live_message_kind(
        argparse.Namespace(notify=False, status_check=True)
    ) == "status-check"
    assert m._live_message_kind(
        argparse.Namespace(notify=False, status_check=False, kind="notify")
    ) == "notify"
    assert m._live_message_kind(
        argparse.Namespace(notify=False, status_check=False, kind="prompt")
    ) == "prompt"
    assert m._live_message_kind(argparse.Namespace()) == "prompt"


class _KindCapturingClient(_LiveFakeClient):
    def send_live_message(self, session_id, *, sender, body, reply_to=None,
                          kind="prompt", wait=False, wait_timeout=None):
        self.delivered.append({"kind": kind, "wait": wait})
        return {"message_id": 3, "replied": False}


def test_cmd_send_passes_status_check_kind(monkeypatch):
    client = _KindCapturingClient(resolved={"session_id": "live-1"})
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_live_sender_label", lambda args: "peer")
    monkeypatch.setattr(m, "_live_reply_to", lambda args: "wt-caller")
    args = argparse.Namespace(
        target="wt-1", prompt="alive?", new=False, json=False,
        no_wait=False, reply_timeout=120.0,
        notify=False, status_check=True, kind="prompt",
    )
    m._cmd_send(args)
    assert client.delivered == [{"kind": "status-check", "wait": True}]


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


# -- end is idempotent + quiet (#48) -----------------------------------------


def test_cmd_end_treats_404_as_already_ended(monkeypatch, capsys):
    class _C:
        def end_session(self, sid, *, force=False):
            raise BridgeClientError(404, f"Session {sid} not found")

    monkeypatch.setattr(m, "_get_client", lambda: _C())
    # Must be a clean no-op success -- no SystemExit, no traceback.
    m._cmd_end(argparse.Namespace(session_id="abc"))
    assert "already ended" in capsys.readouterr().out


def test_cmd_end_reports_error_without_traceback(monkeypatch, capsys):
    class _C:
        def end_session(self, sid, *, force=False):
            raise BridgeClientError(500, "boom")

    monkeypatch.setattr(m, "_get_client", lambda: _C())
    with pytest.raises(SystemExit) as ei:
        m._cmd_end(argparse.Namespace(session_id="abc"))
    assert ei.value.code == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "boom" in out


# -- send concurrent-dispatch guard (#21) ------------------------------------


def test_send_busy_running_session_rejected(fixed_caller, capsys):
    # Caller's own session is mid-turn -- send must fail fast, not adopt+block.
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="running"),
    ])
    with pytest.raises(SystemExit) as ei:
        m._start_agent_session(client, "codespace:cs")
    assert ei.value.code == m._SEND_BUSY_EXIT
    assert client.started == []   # did not spawn over the busy one
    assert client.ended == []     # did not terminate it (no --force)
    err = capsys.readouterr().err
    assert "BUSY" in err
    assert "s1" in err
    assert "--force" in err       # take-over guidance
    assert "wait" in err.lower()  # wait/observe guidance


def test_send_force_takes_over_busy_session(fixed_caller, monkeypatch):
    monkeypatch.setattr(m, "_wait_for_idle", lambda *a, **k: None)
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="running"),
    ])
    sid = m._start_agent_session(client, "codespace:cs", force=True)
    assert client.ended == ["s1"]          # in-flight turn terminated
    assert sid == "fresh-sid"              # fresh session started
    assert client.started and client.started[0]["force_new"] is False


def test_send_conflict_busy_other_caller_rejected(fixed_caller, capsys):
    # Another caller holds the single codespace session and it is mid-turn.
    other = _sess("s9", agent="codespace:cs", caller="host-B", status="running")
    client = FakeClient(sessions=[other], conflict_sid="s9")
    with pytest.raises(SystemExit) as ei:
        m._start_agent_session(client, "codespace:cs")
    assert ei.value.code == m._SEND_BUSY_EXIT
    assert client.ended == []
    assert "BUSY" in capsys.readouterr().err


def test_send_conflict_busy_other_caller_force(fixed_caller, monkeypatch):
    monkeypatch.setattr(m, "_wait_for_idle", lambda *a, **k: None)
    other = _sess("s9", agent="codespace:cs", caller="host-B", status="running")
    # First start 409s (conflict); after we end s9 the retry must succeed, so
    # clear the conflict once s9 is gone.
    client = FakeClient(sessions=[other], conflict_sid="s9")
    orig_start = client.start_session

    def start_session(**kw):
        # Once s9 is ended, drop the conflict so the retry spawns fresh.
        if "s9" in client.ended:
            client._conflict_sid = None
        return orig_start(**kw)

    client.start_session = start_session
    sid = m._start_agent_session(client, "codespace:cs", force=True)
    assert client.ended == ["s9"]
    assert sid == "fresh-sid"


def test_resolve_target_busy_session_id_rejected(fixed_caller, capsys):
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="running"),
    ])
    with pytest.raises(SystemExit) as ei:
        m._resolve_target(client, "s1")
    assert ei.value.code == m._SEND_BUSY_EXIT
    assert "BUSY" in capsys.readouterr().err


def test_resolve_target_busy_session_id_force_takes_over(fixed_caller, monkeypatch):
    monkeypatch.setattr(m, "_wait_for_idle", lambda *a, **k: None)
    client = FakeClient(sessions=[
        _sess("s1", agent="codespace:cs", caller="host-A", status="running"),
    ])
    sid = m._resolve_target(client, "s1", force=True)
    assert client.ended == ["s1"]
    assert sid == "fresh-sid"
