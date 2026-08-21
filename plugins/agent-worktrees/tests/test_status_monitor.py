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
    [("1", True), ("true", True), ("YES", True), ("on", True), ("", True),
     ("nope", True), ("0", False), ("false", False), ("no", False),
     ("off", False)],
)
def test_enabled_env(monkeypatch, val, expected):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", val)
    assert m._status_monitor_enabled() is expected


def test_enabled_env_unset(monkeypatch):
    """Default-on: absent the env var, the monitor is enabled (opt-out)."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    assert m._status_monitor_enabled() is True


def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    assert m._register_session_for_monitor("wt-a", "/w/a") is True
    assert m._register_session_for_monitor("wt-b", "/w/b") is True
    assert m._register_session_for_monitor("", "/w/x") is False    # no session
    assert m._register_session_for_monitor("wt-c", None) is False  # no path
    reg = m._read_monitor_registry(tmp_path / "reg")
    assert reg == {"wt-a": "/w/a", "wt-b": "/w/b"}


@pytest.mark.parametrize(
    "bad", ["../evil", "wt-../../x", "/abs/path", "wt-a/b", "wt-a\\b", "notwt"])
def test_registry_rejects_unsafe_session(tmp_path, monkeypatch, bad):
    """``--session`` is untrusted: a traversal / absolute / non-wt name must be
    rejected (never escape the registry dir), so it writes nothing."""
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    assert m._valid_monitor_session(bad) is False
    assert m._register_session_for_monitor(bad, "/w/a") is False
    if reg.exists():
        assert list(reg.iterdir()) == []


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
        m, "_monitor_list_sessions",
        lambda mux_bin: {"wt-a": 1, "wt-b": 0, "other": 1})
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
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
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
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: None)
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
        lambda sess, path: registered.append((sess, path)) or True)
    monkeypatch.setattr(
        m, "_ensure_status_monitor", lambda: ensured.append(True) or True)
    # If it fell through to the real loop it would call _render_status_segment;
    # make that explode so a regression is loud.
    monkeypatch.setattr(m, "_render_status_segment", _boom)

    rc = m.cmd_status_updater(
        argparse.Namespace(session="wt-a", mux="tmux", path="/w/a", interval=5))
    assert rc == 0
    assert registered == [("wt-a", "/w/a")]
    assert ensured == [True]


def test_status_updater_falls_back_when_monitor_cannot_start(monkeypatch):
    """If the monitor can't be ensured, the per-session updater must still run --
    a session is never left without a status bar (a-la-carte inline fallback)."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    monkeypatch.setattr(m, "_register_session_for_monitor", lambda s, p: True)
    monkeypatch.setattr(m, "_ensure_status_monitor", lambda: False)  # spawn failed
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    # Prove we REACHED the per-session loop (did not early-return via the monitor
    # path) by short-circuiting it at its first self-retire check.
    reached = []
    monkeypatch.setattr(
        m, "_runtime_superseded", lambda *a, **k: bool(reached.append(True)) or True)

    rc = m.cmd_status_updater(
        argparse.Namespace(session="wt-a", mux="tmux", path="/w/a", interval=5))
    assert rc == 0
    assert reached  # fell through into the per-session loop


def test_activate_force_clears_prior_project_on_unresolved(monkeypatch):
    """Under force, an unresolved path must NOT leave a prior session's project
    active (else the monitor renders one session with another's context)."""
    from agent_worktrees import config as cfg
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)  # unresolved
    try:
        cfg.set_active_project("prev")
        m._activate_project_for_path("/no/repo", force=True)
        assert cfg.active_project() is None                 # cleared under force
        # without force, an already-active project is left untouched
        cfg.set_active_project("prev")
        m._activate_project_for_path("/no/repo", force=False)
        assert cfg.active_project() == "prev"
    finally:
        cfg.set_active_project(None)


def _boom(*a, **k):  # pragma: no cover - only fires on regression
    raise AssertionError("per-session loop ran despite monitor being enabled")


# ---------------------------------------------------------------------------
# _restart_status_monitor -- the auto-update cutover seam (consolidated-status-
# daemon Phase 1, dotfiles#1696): reap a superseded monitor + spawn the current
# one so a deploy never leaves live sessions' bars frozen.
# ---------------------------------------------------------------------------

def _wire_restart(monkeypatch, *, lock_data, live, superseded, spawn_ok=True):
    """Stub the lock read/liveness/supersession/spawn/terminate seams."""
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: "/tmp/mon.lock")
    import agent_worktrees.locks as _locks
    monkeypatch.setattr(_locks, "read_lock", lambda p: lock_data)
    monkeypatch.setattr(_locks, "lock_is_live", lambda d: live)
    removed = {"n": 0}

    def _rm(p):
        removed["n"] += 1
    monkeypatch.setattr(_locks, "remove_lock", _rm)
    monkeypatch.setattr(m, "_runtime_superseded", lambda **k: superseded)
    spawned = {"argv": None}
    def _spawn(argv):
        spawned["argv"] = argv
        return spawn_ok
    monkeypatch.setattr(m, "_spawn_detached", _spawn)
    import agent_worktrees.procs as _procs
    reaped = {"pid": None}
    def _term(pid):
        reaped["pid"] = pid
        return True
    monkeypatch.setattr(_procs, "terminate_pid", _term)
    return spawned, reaped, removed


def test_restart_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
    spawned, _r, _rm = _wire_restart(monkeypatch, lock_data=None, live=False, superseded=False)
    r = m._restart_status_monitor()
    assert r["enabled"] is False
    assert r["spawned"] is False
    assert spawned["argv"] is None  # never spawned when opted out


def test_restart_reaps_superseded_and_spawns(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, removed = _wire_restart(
        monkeypatch, lock_data={"pid": 4242, "prefix": "/old/slot"},
        live=True, superseded=True)
    r = m._restart_status_monitor()
    assert r["reaped"] == 4242          # old monitor reaped
    assert reaped["pid"] == 4242
    assert removed["n"] >= 1            # stale lock cleared
    assert r["spawned"] is True
    assert spawned["argv"][-1] == "status-monitor"  # current one spawned


def test_restart_leaves_current_monitor_alone(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, _rm = _wire_restart(
        monkeypatch, lock_data={"pid": 999, "prefix": "/cur/slot"},
        live=True, superseded=False)
    r = m._restart_status_monitor()
    assert r["already_current"] is True
    assert r["spawned"] is False        # no duplicate spawn
    assert reaped["pid"] is None        # never reap a current monitor
    assert spawned["argv"] is None


def test_restart_clears_dead_lock_then_spawns(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, removed = _wire_restart(
        monkeypatch, lock_data={"pid": 1, "prefix": "/x"},
        live=False, superseded=False)  # lock present but dead
    r = m._restart_status_monitor()
    assert removed["n"] >= 1            # dead lock cleared
    assert reaped["pid"] is None        # nothing live to reap
    assert r["spawned"] is True
    assert spawned["argv"][-1] == "status-monitor"


def test_cmd_restart_always_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_restart(monkeypatch, lock_data=None, live=False, superseded=False)
    rc = m.cmd_status_monitor_restart(argparse.Namespace())
    assert rc == 0
    assert "status-monitor:" in capsys.readouterr().out


def test_installers_invoke_monitor_restart_at_cutover():
    # Consolidated-status-daemon Phase 1 contract: BOTH runtime installers must
    # invoke `status-monitor-restart` at the version cutover, or a deploy silently
    # regresses to frozen bars. Pin it so an installer refactor can't drop it.
    from pathlib import Path
    scripts = Path(m.__file__).resolve().parents[2] / "scripts"
    for name in ("install.ps1", "install.sh"):
        text = (scripts / name).read_text("utf-8")
        assert "status-monitor-restart" in text, (
            f"{name} must invoke `status-monitor-restart` after activating the "
            "new runtime slot (consolidated-status-daemon Phase 1, dotfiles#1696)")
