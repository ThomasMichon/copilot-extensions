"""Tests for the resident, coalescing ``status-monitor``.

The monitor consolidates the per-session ``status-updater`` loops into a single
process (the work-coalescing-singleton service tier): one coalesced sweep
refreshes every live ``wt-*`` session's bar, the per-session registry doubles as
a refcount, and the monitor idle-exits when the last session goes. It is opt-in
via ``AGENT_WORKTREES_STATUS_MONITOR``; unset, the per-session updater is
unchanged. These tests drive the pure sweep + registry + routing seams without a
real mux.
"""

from __future__ import annotations

import argparse

import pytest

from agent_worktrees import __main__ as m


def test_status_monitor_registered():
    assert m.COMMAND_MAP["status-monitor"] is m.cmd_status_monitor
    assert m._WORKTREE_VERBS["status-monitor"] == "status-monitor"
    # main() must not try to resolve a project for it (it resolves per-session),
    # and the launcher reap must never kill the resident tracker.
    assert "status-monitor" in m._NO_PROJECT_COMMANDS
    assert "status-monitor" in m._LAUNCHER_REAP_VETOES


@pytest.mark.parametrize(
    "val,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True),
     ("0", False), ("", False), ("nope", False)],
)
def test_enabled_env(monkeypatch, val, expected):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", val)
    assert m._status_monitor_enabled() is expected


def test_enabled_env_unset(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    assert m._status_monitor_enabled() is False


def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    m._register_session_for_monitor("wt-a", "/w/a")
    m._register_session_for_monitor("wt-b", "/w/b")
    m._register_session_for_monitor("", "/w/x")        # ignored (no session)
    m._register_session_for_monitor("wt-c", None)      # ignored (no path)
    reg = m._read_monitor_registry(tmp_path / "reg")
    assert reg == {"wt-a": "/w/a", "wt-b": "/w/b"}


def _capture_set(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        m, "_monitor_mux_set",
        lambda mux_bin, sess, opt, val: calls.append((sess, opt, val)))
    return calls


def test_sweep_serves_live_registered_and_prunes_gone(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    m._register_session_for_monitor("wt-b", "/w/b")
    m._register_session_for_monitor("wt-gone", "/w/g")

    # wt-a + wt-b are live; wt-gone is not; a non-wt session is ignored.
    monkeypatch.setattr(
        m.sessions, "_list_mux_sessions",
        lambda: {"wt-a": 1, "wt-b": 0, "other": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    calls = _capture_set(monkeypatch)

    ctx_done: set[str] = set()
    served = m._monitor_sweep("tmux", "TOK", "PFX", ctx_done)

    assert served == 2                                  # wt-a, wt-b
    assert not (reg / "wt-gone").exists()               # pruned
    for sess in ("wt-a", "wt-b"):
        assert (sess, "@aw_updater", "TOK") in calls    # won the election
        assert (sess, "@aw_updater_prefix", "PFX") in calls
        assert (sess, "@aw_ctx", "CTX") in calls        # identity once
        assert (sess, "@aw_seg", "SEG") in calls        # disposition
    assert ctx_done == {"wt-a", "wt-b"}
    # no work for the gone or non-wt sessions
    assert not any(s in ("wt-gone", "other") for s, _, _ in calls)


def test_sweep_ctx_rendered_once(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m.sessions, "_list_mux_sessions", lambda: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    calls = _capture_set(monkeypatch)

    ctx_done: set[str] = set()
    m._monitor_sweep("tmux", "T", "P", ctx_done)
    m._monitor_sweep("tmux", "T", "P", ctx_done)        # second pass

    ctx_sets = [c for c in calls if c[1] == "@aw_ctx"]
    seg_sets = [c for c in calls if c[1] == "@aw_seg"]
    assert len(ctx_sets) == 1                            # identity: once
    assert len(seg_sets) == 2                            # disposition: every pass


def test_sweep_transient_mux_failure_holds(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    # None == the mux couldn't be enumerated -> transient; must NOT prune/exit.
    monkeypatch.setattr(m.sessions, "_list_mux_sessions", lambda: None)
    calls = _capture_set(monkeypatch)

    assert m._monitor_sweep("tmux", "T", "P", set()) == -1
    assert calls == []
    assert (reg / "wt-a").exists()                       # registry untouched


def test_ensure_monitor_noop_when_live(tmp_path, monkeypatch):
    """A live, current-runtime monitor lock suppresses a duplicate spawn."""
    from agent_worktrees import locks
    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    spawned: list[list[str]] = []
    monkeypatch.setattr(m, "_spawn_detached", lambda argv: spawned.append(argv))

    # This test process is a live pid; its sys.prefix is not under a versions/
    # slot, so _runtime_superseded() is False -> treated as a live current owner.
    locks.write_lock(lock, extra={"prefix": m.os.path.realpath(m.sys.prefix)})
    m._ensure_status_monitor()
    assert spawned == []                                 # no duplicate

    locks.remove_lock(lock)
    m._ensure_status_monitor()
    assert len(spawned) == 1                             # spawned when absent


def test_status_updater_delegates_when_enabled(monkeypatch):
    """With the monitor opted in, the per-session updater registers + ensures the
    monitor and returns without running its own loop."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    registered: list[tuple[str, str]] = []
    ensured: list[bool] = []
    monkeypatch.setattr(
        m, "_register_session_for_monitor",
        lambda sess, path: registered.append((sess, path)))
    monkeypatch.setattr(m, "_ensure_status_monitor", lambda: ensured.append(True))
    # If it fell through to the real loop it would call _render_status_segment;
    # make that explode so a regression is loud.
    monkeypatch.setattr(m, "_render_status_segment", _boom)

    rc = m.cmd_status_updater(
        argparse.Namespace(session="wt-a", mux="tmux", path="/w/a", interval=5))
    assert rc == 0
    assert registered == [("wt-a", "/w/a")]
    assert ensured == [True]


def _boom(*a, **k):  # pragma: no cover - only fires on regression
    raise AssertionError("per-session loop ran despite monitor being enabled")
