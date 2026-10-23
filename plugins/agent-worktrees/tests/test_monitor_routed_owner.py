"""A dead legacy lock cannot hide an authenticated live routed monitor."""

from __future__ import annotations

import os
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from zdd import diagnostics, routing

from agent_worktrees import locks, status_monitor_cutover as control
from agent_worktrees import status_monitor_runtime, status_monitor_reap_stale


@pytest.mark.parametrize("failure", [None, "pid", "native", "authentication", "route"])
def test_missing_legacy_lock_uses_only_a_verified_routed_owner(tmp_path, monkeypatch, failure):
    token = "fixture-token"
    pid = os.getpid()
    events = []

    def handle(action, payload):
        assert action == "health"
        if failure == "route":
            routing.clear_if_owner(tmp_path, pid=pid)
        return {
            "status": "ready", "pid": pid + 1 if failure == "pid" else pid,
            "published": True,
            "worktrees": {"status": "unavailable", "error": "fixture fenced writer"},
        }

    server = control.ControlServer(handle, token=token)
    server.start()
    try:
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=server.port, pid=pid, version="old",
        )
        monkeypatch.setattr(control, "routing_dir", lambda: tmp_path)
        monkeypatch.setattr(control, "_monitor_lock_is_live", lambda: False)
        monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
        monkeypatch.setattr(
            status_monitor_runtime, "_restart_status_monitor",
            lambda **kwargs: pytest.fail("live routed owner bypassed the normal cutover"),
        )
        monkeypatch.setattr(
            control, "_acquire_cutover_lock",
            lambda directory: SimpleNamespace(release=lambda: events.append("released")),
        )
        monkeypatch.setattr(control, "_reap_abandoned_passive", lambda record: {})
        monkeypatch.setattr(
            status_monitor_reap_stale, "schedule_delayed_daemon_health_reap", lambda: None,
        )
        original_client = control.ControlClient

        class Client(original_client):
            def __init__(self, url, **kwargs):
                super().__init__(
                    url, token="wrong-token" if failure == "authentication" else token, **kwargs,
                )

        monkeypatch.setattr(control, "ControlClient", Client)
        if failure == "native":
            monkeypatch.setattr(locks, "process_start_time", lambda pid: None)

        class Orchestrator:
            def __init__(self, *args, **kwargs):
                events.append("cutover")

            def run(self, **kwargs):
                return SimpleNamespace(to_dict=lambda: {"ok": True, "committed": True})

        monkeypatch.setattr(control, "CutoverOrchestrator", Orchestrator)
        result = control.activate_after_update(
            runtime_python="fixture-python", monitor_was_live=False,
        )
        if failure is None:
            assert result["action"] == "cutover"
            assert result["result"]["ok"] is True
            assert events == ["cutover", "released"]
        else:
            assert result["action"] == "refused"
            assert result["reason"] == "routed-monitor-unverified"
            assert events == []
    finally:
        server.close()


def test_absent_route_and_legacy_lock_keep_no_monitor_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(control, "_monitor_lock_is_live", lambda: False)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    result = control.activate_after_update(monitor_was_live=False)
    assert result["action"] == "noop"
    assert result["reason"] == "no-live-monitor"


def test_darwin_routed_owner_uses_native_birth_identity(tmp_path, monkeypatch):
    pid = os.getpid()
    server = control.ControlServer(
        lambda *_args: {"status": "ready", "pid": pid, "runtime_slot": "serving"},
        token="fixture-token",
    )
    server.start()
    try:
        monkeypatch.setattr(diagnostics, "process_start_time", lambda *_args: None)
        routing.publish_active(tmp_path, bind="127.0.0.1", port=server.port, pid=pid)
        monkeypatch.setattr(control, "routing_dir", lambda: tmp_path)
        monkeypatch.setattr(locks.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(locks, "_start_time_darwin", lambda observed: "123456:789" if observed == pid else None)
        monkeypatch.setattr(locks, "lock_is_live", lambda *_args: True)
        monkeypatch.setattr(diagnostics, "_pid_alive", lambda observed: observed == pid)
        original_client = control.ControlClient
        monkeypatch.setattr(
            control, "ControlClient",
            lambda url: original_client(url, token="fixture-token"),
        )
        assert control.routed_monitor_identity() == (pid, "123456:789", "serving")
    finally:
        server.close()


@pytest.mark.parametrize("damage", ["json", "object", "active", "previous", "fallback"])
def test_damaged_or_fallback_routing_cannot_certify_monitor_absence(tmp_path, monkeypatch, damage):
    pid = os.getpid()
    server = control.ControlServer(lambda *_args: {"status": "ready", "pid": pid}, token="fixture-token")
    server.start()
    try:
        endpoint = routing.publish_active(tmp_path, bind="127.0.0.1", port=server.port, pid=pid)
        table = routing.read_table(tmp_path)
        if damage in ("active", "previous"):
            table[damage] = {**endpoint.to_dict(), "process_start_time": "invalid-token"}
        elif damage == "fallback":
            table["previous"] = endpoint.to_dict()
            table["active"] = {**endpoint.to_dict(), "pid": 2**30, "port": 1}
        path = routing.routing_table_path(tmp_path)
        path.write_text(
            "{" if damage == "json" else json.dumps([] if damage == "object" else table),
            encoding="utf-8",
        )
        monkeypatch.setattr(control, "routing_dir", lambda: tmp_path)
        monkeypatch.setattr(control, "_monitor_lock_is_live", lambda: False)
        monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
        monkeypatch.setattr(
            status_monitor_runtime, "_restart_status_monitor",
            lambda **_kwargs: pytest.fail("unverified routing must not restart a monitor"),
        )
        monkeypatch.setattr(
            control, "CutoverOrchestrator",
            lambda *_args, **_kwargs: pytest.fail("unverified routing must not enter cutover"),
        )
        with pytest.raises(ValueError):
            control.routed_monitor_identity()
        result = control.activate_after_update(monitor_was_live=False)
        assert result["action"] == "refused"
        assert result["reason"] == "routed-monitor-unverified"
    finally:
        server.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained process object semantics")
@pytest.mark.parametrize("route_role", ["active", "previous"])
def test_exited_routed_process_with_retained_handle_is_not_a_live_monitor(
    tmp_path, monkeypatch, route_role,
):
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "pass"],
        stdin=subprocess.DEVNULL, **no_window_kwargs(),
    )
    try:
        endpoint = routing.publish_active(
            tmp_path, bind="127.0.0.1", port=1, pid=process.pid,
            process_start_time=locks.process_start_time(process.pid),
        )
        if route_role == "previous":
            routing.routing_table_path(tmp_path).write_text(
                json.dumps({"active": None, "previous": endpoint.to_dict()}),
                encoding="utf-8",
            )
        assert process.wait(timeout=10) == 0
        assert locks.lock_is_live({"pid": process.pid}), "fixture must retain the exited process object"
        assert not diagnostics._pid_alive(process.pid)
        monkeypatch.setattr(control, "routing_dir", lambda: tmp_path)
        monkeypatch.setattr(control, "_monitor_lock_is_live", lambda: False)
        monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
        assert control.routed_monitor_identity() is None
        result = control.activate_after_update(monitor_was_live=False)
        assert result["action"] == "noop"
        assert result["reason"] == "no-live-monitor"
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
