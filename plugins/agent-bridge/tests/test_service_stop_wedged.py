"""``_service_stop`` must reclaim a *wedged* daemon via the singleton-lock holder.

Regression: a daemon that is alive and still holding the OS singleton lock but no
longer LISTENing / answering health (a "wedged" daemon) was invisible to
``_service_stop`` -- it consulted only the pid file and the port -- so ``service
restart`` reported success while the wedged process lived on and blocked the next
start via the duplicate-start guard (#129).
"""

from __future__ import annotations

from pathlib import Path

import agent_bridge.__main__ as m


def _write_lock(config_dir: Path, port: int, pid: int) -> Path:
    """Reproduce the on-disk singleton lock format (pid at offset 0, fixed
    width) without taking a real OS lock."""
    lock = config_dir / f"agent-bridge.{port}.lock"
    lock.write_text(f"{pid:<20}", encoding="ascii")
    return lock


def test_pid_from_lock_returns_live_daemon_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    _write_lock(tmp_path, 9280, 4242)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: pid == 4242)
    assert m._pid_from_lock(9280) == 4242


def test_pid_from_lock_ignores_recycled_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    _write_lock(tmp_path, 9280, 4242)
    # A pid the OS reused for something else -> identity gate fails -> not a target.
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: False)
    assert m._pid_from_lock(9280) is None


def test_pid_from_lock_none_when_no_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    assert m._pid_from_lock(9280) is None


def test_pid_from_lock_ignores_self(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    _write_lock(tmp_path, 9280, os.getpid())
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: True)
    assert m._pid_from_lock(9280) is None


def test_service_stop_kills_wedged_lock_holder(tmp_path, monkeypatch):
    """The wedged daemon (lock holder, not listening) is killed even when the pid
    file is empty and nothing is on the port."""
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_PID_FILE", str(tmp_path / "agent-bridge.pid"))
    port = m._service_port()
    lock = _write_lock(tmp_path, port, 4242)

    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    monkeypatch.setattr(m, "_pid_on_port", lambda p: None)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: pid == 4242)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m, "_win_task_exists", lambda: False)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)

    killed: list[int] = []

    def fake_kill(pid: int) -> None:
        killed.append(pid)
        # Mimic the dead holder releasing (the OS frees) the lock.
        lock.unlink(missing_ok=True)

    monkeypatch.setattr(m, "_kill_pid", fake_kill)

    m._service_stop()

    assert 4242 in killed


def test_service_stop_skips_when_nothing_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_PID_FILE", str(tmp_path / "agent-bridge.pid"))
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    monkeypatch.setattr(m, "_pid_on_port", lambda p: None)
    monkeypatch.setattr(m, "_pid_from_lock", lambda p: None)
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m, "_win_task_exists", lambda: False)

    m._service_stop()

    assert "[SKIP]" in capsys.readouterr().out
