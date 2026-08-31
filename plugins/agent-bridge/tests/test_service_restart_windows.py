"""Windows service restart robustness -- scheduled-task detection + fallback (#227).

Two coupled dotfiles#227 fixes:

* ``_win_task_exists`` must classify a non-elevated ``schtasks /Query`` that
  returns ``ERROR: Access is denied.`` (an elevated/S4U task the caller can't
  read) as **exists**, not absent.
* ``_service_start`` must fall back to a direct, job-surviving detached spawn
  when the platform manager (systemd unit / scheduled task) issues a start but
  the daemon never comes up -- the live cloud1 failure where an S4U /
  RunLevel-Limited boot task cannot be run on-demand into the user session.
"""

from __future__ import annotations

import subprocess

from agent_bridge import __main__ as m


class _Completed:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fast_time(monkeypatch):
    import time as _time

    now = {"value": 0.0}
    monkeypatch.setattr(_time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        _time,
        "sleep",
        lambda seconds: now.__setitem__("value", now["value"] + seconds),
    )
    return now


def test_win_task_exists_true_on_rc0(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0, "TaskName: Agent Bridge"))
    assert m._win_task_exists() is True


def test_win_task_exists_true_on_access_denied(monkeypatch):
    # Elevated/S4U task, non-elevated query: exists but unreadable.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _Completed(1, "", "ERROR: Access is denied."),
    )
    assert m._win_task_exists() is True


def test_win_task_exists_false_on_not_found(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _Completed(
            1, "", "ERROR: The system cannot find the file specified."
        ),
    )
    assert m._win_task_exists() is False


def test_win_task_exists_false_on_oserror(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no schtasks")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert m._win_task_exists() is False


def test_service_start_falls_back_when_task_start_leaves_daemon_down(monkeypatch):
    """A scheduled-task start that never brings the daemon up must fall back to
    the direct detached spawn (the S4U on-demand-restart failure, #227)."""
    _fast_time(monkeypatch)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_win_task_exists", lambda: True)
    monkeypatch.setattr(m, "_service_port", lambda: 12345)
    monkeypatch.setattr(m, "_active_endpoint", lambda: None)
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    # The task-run schtasks call is a no-op that changes nothing.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0))

    state = {"spawned": False}

    def _fake_spawn():
        state["spawned"] = True

    monkeypatch.setattr(m, "_spawn_detached_daemon", _fake_spawn)
    # Down until the direct spawn happens; up on the next probe afterwards.
    monkeypatch.setattr(m, "_service_is_running", lambda: state["spawned"])

    m._service_start()
    assert state["spawned"] is True


def test_service_start_no_double_spawn_on_direct_path(monkeypatch):
    """When there is no systemd/task manager, the direct spawn runs once and the
    fallback branch is not taken (no double spawn)."""
    _fast_time(monkeypatch)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_win_task_exists", lambda: False)
    monkeypatch.setattr(m, "_service_port", lambda: 12345)
    monkeypatch.setattr(m, "_active_endpoint", lambda: None)
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)

    calls = {"n": 0}

    def _fake_spawn():
        calls["n"] += 1

    monkeypatch.setattr(m, "_spawn_detached_daemon", _fake_spawn)
    # Not running at the initial guard, up immediately after the direct spawn.
    monkeypatch.setattr(m, "_service_is_running", lambda: calls["n"] > 0)

    m._service_start()
    assert calls["n"] == 1


def test_service_start_early_returns_when_already_running(monkeypatch):
    monkeypatch.setattr(m, "_service_is_running", lambda: True)
    monkeypatch.setattr(m, "_service_port", lambda: 999)
    spawned = {"n": 0}
    monkeypatch.setattr(
        m, "_spawn_detached_daemon", lambda: spawned.__setitem__("n", spawned["n"] + 1)
    )
    m._service_start()
    assert spawned["n"] == 0


def test_ensure_waits_for_live_generation_before_spawning(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_NO_ENSURE", raising=False)
    health = iter([False])
    monkeypatch.setattr(
        m, "_service_is_running", lambda: next(health, False)
    )
    monkeypatch.setattr(m, "_service_process_is_live", lambda: True)
    monkeypatch.setattr(m, "_wait_for_service_start", lambda: True)
    monkeypatch.setattr(
        m,
        "_acquire_ensure_lock",
        lambda: (_ for _ in ()).throw(
            AssertionError("live generation must be awaited before locking")
        ),
    )
    monkeypatch.setattr(
        m,
        "_spawn_detached_daemon",
        lambda: (_ for _ in ()).throw(
            AssertionError("live generation must not be duplicated")
        ),
    )

    assert m._ensure_daemon() is True


def test_ensure_lock_loser_follows_winner_into_full_start_wait(
    monkeypatch, tmp_path
):
    _fast_time(monkeypatch)
    monkeypatch.delenv("AGENT_BRIDGE_NO_ENSURE", raising=False)
    lock = tmp_path / ".ensure.lock"
    lock.write_text("winner", encoding="utf-8")
    monkeypatch.setattr(m, "_ENSURE_LOCK", str(lock))
    monkeypatch.setattr(m, "_ENSURE_MARKER", str(tmp_path / ".ensure-attempt"))
    monkeypatch.setattr(m, "_acquire_ensure_lock", lambda: None)

    health = iter([False, False])
    monkeypatch.setattr(
        m, "_service_is_running", lambda: next(health, False)
    )
    live = iter([False, True])
    monkeypatch.setattr(
        m, "_service_process_is_live", lambda: next(live, True)
    )
    waited = {"count": 0}
    monkeypatch.setattr(
        m,
        "_wait_for_service_start",
        lambda: waited.__setitem__("count", waited["count"] + 1) or True,
    )

    assert m._ensure_daemon() is True
    assert waited["count"] == 1


def test_ensure_lock_handoff_keeps_launch_grace_for_process_appearance(
    monkeypatch, tmp_path
):
    _fast_time(monkeypatch)
    lock = tmp_path / ".ensure.lock"
    lock.write_text("winner", encoding="utf-8")
    monkeypatch.setattr(m, "_ENSURE_LOCK", str(lock))
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_service_process_is_live", lambda: False)

    exists = iter([True, False])
    monkeypatch.setattr(
        m.os.path, "exists", lambda _path: next(exists, False)
    )
    waited = {"count": 0}
    monkeypatch.setattr(
        m,
        "_wait_for_service_start",
        lambda: waited.__setitem__("count", waited["count"] + 1) or True,
    )

    assert m._wait_for_ensure_owner() is True
    assert waited["count"] == 1


def test_service_start_waits_for_slow_live_daemon_without_fallback(monkeypatch):
    _fast_time(monkeypatch)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_win_task_exists", lambda: True)
    monkeypatch.setattr(m, "_service_port", lambda: 54321)
    monkeypatch.setattr(m, "_active_endpoint", lambda: None)
    monkeypatch.setattr(m, "_read_pid_file", lambda: 222)
    monkeypatch.setattr(
        m, "_pid_is_agent_bridge", lambda pid, _timeout=15: pid == 222
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0))

    probes = {"count": 0}

    def _health():
        probes["count"] += 1
        return probes["count"] >= 82

    monkeypatch.setattr(m, "_service_is_running", _health)
    spawned = {"count": 0}
    monkeypatch.setattr(
        m,
        "_spawn_detached_daemon",
        lambda: spawned.__setitem__("count", spawned["count"] + 1),
    )

    m._service_start()

    assert probes["count"] == 82
    assert spawned["count"] == 0


def test_service_start_waits_for_new_pid_when_route_still_names_dead_daemon(
    monkeypatch,
):
    from types import SimpleNamespace

    _fast_time(monkeypatch)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_win_task_exists", lambda: True)
    monkeypatch.setattr(m, "_service_port", lambda: 54321)
    monkeypatch.setattr(
        m, "_active_endpoint", lambda: SimpleNamespace(pid=111, port=54321)
    )
    monkeypatch.setattr(m, "_read_pid_file", lambda: 222)
    monkeypatch.setattr(
        m, "_pid_is_agent_bridge", lambda pid, _timeout=15: pid == 222
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0))

    probes = {"count": 0}

    def _health():
        probes["count"] += 1
        return probes["count"] >= 42

    monkeypatch.setattr(m, "_service_is_running", _health)
    spawned = {"count": 0}
    monkeypatch.setattr(
        m,
        "_spawn_detached_daemon",
        lambda: spawned.__setitem__("count", spawned["count"] + 1),
    )

    m._service_start()

    assert probes["count"] == 42
    assert spawned["count"] == 0
