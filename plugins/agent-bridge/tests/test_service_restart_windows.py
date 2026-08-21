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
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a: None)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_win_task_exists", lambda: True)
    monkeypatch.setattr(m, "_service_port", lambda: 12345)
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
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a: None)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_win_task_exists", lambda: False)
    monkeypatch.setattr(m, "_service_port", lambda: 12345)

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
