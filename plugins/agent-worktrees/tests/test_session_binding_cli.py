"""Focused CLI coverage for the public session-binding query."""

from __future__ import annotations

import json

from agent_worktrees import __main__ as m


def test_session_binding_found(monkeypatch, capfd):
    monkeypatch.setattr(
        m.sessions,
        "mux_binding_for_session",
        lambda sid: {
            "worktree_id": "wt-example",
            "session_name": "wt-wt-example",
            "pane_id": "%4",
            "pane_pid": 100,
            "pane_start_time": "pane-start",
            "copilot_pid": 200,
            "copilot_start_time": "copilot-start",
        },
    )

    assert m.main([
        "session-binding", "--session-id", "session-1", "--json",
    ]) == 0
    assert json.loads(capfd.readouterr().out) == {
        "version": 1,
        "found": True,
        "session_id": "session-1",
        "worktree_id": "wt-example",
        "mux_session": "wt-wt-example",
        "pane_id": "%4",
        "pane_pid": 100,
        "pane_start_time": "pane-start",
        "copilot_pid": 200,
        "copilot_start_time": "copilot-start",
        "terminal": None,
    }


def test_session_binding_not_found(monkeypatch, capfd):
    monkeypatch.setattr(
        m.sessions, "mux_binding_for_session", lambda sid: None,
    )

    assert m.main([
        "session-binding", "--session-id", "missing", "--json",
    ]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["found"] is False
    assert result["session_id"] == "missing"
    assert all(
        result[key] is None
        for key in (
            "worktree_id", "mux_session", "pane_id", "pane_pid",
            "pane_start_time", "copilot_pid", "copilot_start_time",
        )
    )


def _terminal_payload(session_id, probe):
    return {"sessionId": session_id, "_agentWorktrees": {"terminal": probe}}


def _fake_processes(monkeypatch, starts, copilot_pids):
    from agent_worktrees import locks, terminal_identity

    monkeypatch.setattr(locks, "process_start_time", lambda pid: starts.get(pid))
    monkeypatch.setattr(
        terminal_identity.sessions, "_is_copilot_process", lambda pid: pid in copilot_pids,
    )


def test_terminal_record_for_non_mux_session(monkeypatch, tmp_path, capfd):
    from agent_worktrees import terminal_identity

    monkeypatch.setattr(m.sessions, "_session_state_dir", lambda: tmp_path)
    monkeypatch.setattr(m.sessions, "mux_binding_for_session", lambda sid: None)
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    # Two live Copilot locks: the one in the hook's ancestry wins.
    (session_dir / "inuse.300.lock").write_text("")
    (session_dir / "inuse.200.lock").write_text("")
    _fake_processes(monkeypatch, {200: "s200", 300: "s300"}, {200, 300})
    probe = {
        "ancestors": [150, 200, 90],
        "console_hwnd": 1001,
        "console_class": "PseudoConsoleWindow",
        "host_hwnd": 2002,
        "host_pid": 77,
        "host_class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "host_exe": "C:\\Program Files\\Terminal\\WindowsTerminal.exe",
    }
    environment = {"WT_SESSION": "wt-guid", "SSH_CONNECTION": "a b c d"}

    assert terminal_identity.record_session_terminal(
        _terminal_payload("session-1", probe), environment,
    )
    assert not list(session_dir.glob("*.tmp"))
    assert m.main(["session-binding", "--session-id", "session-1", "--json"]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["found"] is False
    terminal = result["terminal"]
    assert terminal["copilot_pid"] == 200
    assert terminal["copilot_start_time"] == "s200"
    assert terminal["live"] is True
    assert terminal["console_hwnd"] == 1001
    assert terminal["host_hwnd"] == 2002
    assert terminal["host_pid"] == 77
    assert terminal["host_exe"] == "WindowsTerminal.exe"
    assert terminal["wt_session"] == "wt-guid"
    assert terminal["term_program"] is None
    assert terminal["ssh"] is True
    assert terminal["mux"] is None

    # A reused pid (different start identity) reads back as not live.
    _fake_processes(monkeypatch, {200: "other"}, {200})
    assert terminal_identity.read_session_terminal("session-1")["live"] is False

    # An unreadable start identity is unknown while the pid exists, dead once gone.
    from agent_worktrees import locks

    _fake_processes(monkeypatch, {}, {200})
    monkeypatch.setattr(locks, "pid_alive", lambda pid: True)
    assert terminal_identity.read_session_terminal("session-1")["live"] is None
    monkeypatch.setattr(locks, "pid_alive", lambda pid: False)
    assert terminal_identity.read_session_terminal("session-1")["live"] is False


def test_terminal_record_unknowns_and_mux(monkeypatch, tmp_path):
    from agent_worktrees import terminal_identity

    monkeypatch.setattr(m.sessions, "_session_state_dir", lambda: tmp_path)
    monkeypatch.setattr(terminal_identity.platform, "system", lambda: "Linux")
    (tmp_path / "session-2").mkdir()
    _fake_processes(monkeypatch, {}, set())

    record = terminal_identity.build_record(
        _terminal_payload("session-2", {"ancestors": "junk", "host_hwnd": -5}),
        {"TMUX": "/sock,1,0", "TMUX_PANE": "%3", "TERM_PROGRAM": "tmux"},
    )
    assert record["copilot_pid"] is None
    assert record["host_hwnd"] is None
    assert record["console_hwnd"] is None
    assert record["mux"] == {"kind": "tmux", "pane": "%3"}
    assert record["term_program"] == "tmux"
    assert record["ssh"] is False
    assert record["platform"] == "linux"


def test_terminal_record_rejects_unsafe_session_ids_and_never_raises(monkeypatch, tmp_path):
    from agent_worktrees import terminal_identity

    monkeypatch.setattr(m.sessions, "_session_state_dir", lambda: tmp_path)
    for session_id in ("", "..", "a/b", "a\\b", None):
        assert terminal_identity.record_session_terminal({"sessionId": session_id}, {}) is False
        assert terminal_identity.read_session_terminal(session_id) is None

    def boom(*args, **kwargs):
        raise RuntimeError("probe failure")

    monkeypatch.setattr(terminal_identity, "build_record", boom)
    assert terminal_identity.record_session_terminal({"sessionId": "ok"}, {}) is False
