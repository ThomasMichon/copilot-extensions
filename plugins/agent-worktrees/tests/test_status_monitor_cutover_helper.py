"""Rehearsals for the installer-driven status-monitor cutover helper."""

from __future__ import annotations

import threading
import time

from zdd import routing

from agent_worktrees import status_monitor_cutover as smc


class _Handle:
    def __init__(self, daemon):
        self.daemon = daemon
        self.pid = daemon.pid

    def terminate(self) -> None:
        self.daemon.force_terminate()

    def poll(self) -> int | None:
        return None if self.daemon.alive else 0


class _FakeDaemon:
    def __init__(self, pid: int, *, port: int | None = None, token: str = "test-token"):
        self.pid = pid
        self.port = port
        self.alive = True
        self.draining = False
        self.promoted = False
        self.shutdown_requested = False
        self.shutdown_completed = threading.Event()
        self.busy = threading.Event()
        self.control = None
        self.promote_calls = 0
        self.token = token
        if port is not None:
            self.start(port)

    def start(self, port: int | None = None) -> None:
        self.control = smc.ControlServer(self._handle, port=port, token=self.token)
        self.control.start()
        self.port = self.control.port

    def _handle(self, action: str, payload: dict) -> dict:
        if action == "health":
            return {"status": "draining" if self.draining else "ready"}
        if action == "promote":
            self.promoted = True
            self.promote_calls += 1
            return {"adopted": True}
        if action == "drain":
            self.draining = True
            timeout = float(payload["timeout"])
            poll = max(0.01, float(payload["poll"]))
            deadline = time.time() + timeout
            while self.busy.is_set() and time.time() < deadline:
                time.sleep(poll)
            return {
                "drained": not self.busy.is_set(),
                "clean": not self.busy.is_set(),
                "forced": False,
                "busy_sessions": ["busy"] if self.busy.is_set() else [],
            }
        if action == "undrain":
            self.draining = False
            return {"draining": False}
        if action == "shutdown":
            self.shutdown_requested = True

            def _finish() -> None:
                time.sleep(0.2)
                self.alive = False
                self.shutdown_completed.set()
            if self.control is not None:
                self.control.close()

            threading.Thread(target=_finish, daemon=True).start()
            return {"shutdown": True}
        return {"ok": False}

    def force_terminate(self) -> None:
        if self.alive:
            self.alive = False
            self.shutdown_completed.set()
            if self.control is not None:
                self.control.close()


def _wait_for(predicate, *, timeout: float = 10.0, interval: float = 0.05, message: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


def test_activate_after_update_cuts_over_and_converges(tmp_path, monkeypatch):
    route_dir = tmp_path / "routing"
    lock_path = tmp_path / "status-monitor.lock"
    route_dir.mkdir(parents=True)
    lock_path.write_text("{}", encoding="utf-8")

    old = _FakeDaemon(101, port=0)
    first_new = _FakeDaemon(202)
    second_new = _FakeDaemon(303)
    daemons = {old.pid: old, first_new.pid: first_new, second_new.pid: second_new}
    pending = [first_new, second_new]

    routing.publish_active(route_dir, bind="127.0.0.1", port=old.port, pid=old.pid, version="old")

    def _self_retire(daemon: _FakeDaemon) -> None:
        while daemon.alive:
            active = routing.read_active_endpoint(route_dir, verify_listener=False)
            if active is not None and active.pid != daemon.pid and not daemon.busy.is_set():
                daemon.force_terminate()
                return
            time.sleep(0.05)

    threading.Thread(target=_self_retire, args=(old,), daemon=True).start()
    threading.Thread(target=_self_retire, args=(first_new,), daemon=True).start()

    old.busy.set()
    release_old = threading.Thread(
        target=lambda: (time.sleep(1.0), old.busy.clear()),
        daemon=True,
    )
    release_old.start()

    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: route_dir)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda runtime_home=None: "test-token")
    def _spawn(runtime_python, *, port):
        daemon = pending.pop(0)
        daemon.start(port)
        return _Handle(daemon)

    monkeypatch.setattr(smc, "spawn_passive", _spawn)
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_runtime._status_monitor_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_runtime._restart_status_monitor",
        lambda: {"spawned": False},
    )
    # Never spawn a REAL detached subprocess during this test -- the async
    # daemon-health backstop (copilot-extensions#5453 mitigation) is
    # exercised in its own dedicated unit tests instead.
    schedule_calls: list[float] = []
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_reap_stale.schedule_delayed_daemon_health_reap",
        lambda *a, **k: schedule_calls.append(True),
    )

    first_result: dict[str, object] = {}

    def _run_first() -> None:
        first_result.update(smc.activate_after_update(runtime_python="python"))

    first_thread = threading.Thread(target=_run_first, daemon=True)
    first_thread.start()

    first_pid = _wait_for(
        lambda: (
            (ep := routing.read_active_endpoint(route_dir, verify_listener=False))
            and ep.pid != old.pid
            and ep.pid
        ),
        message="first cutover never published an active route",
    )
    assert first_pid == first_new.pid
    assert old.alive, "the old daemon exited before the new route was active"

    second_result = smc.activate_after_update(runtime_python="python")
    assert second_result["action"] == "cutover"
    assert second_result["result"]["ok"] is True

    first_thread.join(timeout=10)
    assert not first_thread.is_alive(), "first cutover never completed"
    assert first_result["action"] == "cutover"
    assert "result" in first_result
    assert second_new.promoted is True

    final_pid = _wait_for(
        lambda: routing.read_active_endpoint(route_dir, verify_listener=False).pid,
        message="second cutover never published the newest route",
    )
    assert final_pid == second_new.pid

    _wait_for(
        lambda: (not old.alive) and (not first_new.alive) and second_new.alive,
        timeout=10,
        message="superseded generations did not retire to one live daemon",
    )

    # Each cutover attempt schedules the async daemon-health backstop
    # (copilot-extensions#5453 mitigation), regardless of this cutover's own
    # outcome -- never blocking, never a real subprocess in this test.
    assert len(schedule_calls) == 2

    for daemon in daemons.values():
        daemon.force_terminate()
