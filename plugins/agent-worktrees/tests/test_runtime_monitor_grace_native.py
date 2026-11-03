"""Finite native expiry proof, not installed-monitor or whole-launch acceptance."""

from __future__ import annotations

import os
import socket
import sys

import pytest
from zdd import breadcrumb, routing

from agent_worktrees import runtime_monitor_grace as grace
from agent_worktrees import runtime_monitor_retirements as retirements
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import status_monitor_recovery_contract as recovery
from agent_worktrees.status_monitor_handover import MonitorHandover
from test_status_monitor_guarded_recovery_native import start_provider

pytestmark = [
    pytest.mark.timeout(150),
    pytest.mark.skipif(sys.platform != "win32", reason="Windows native process/Job proof"),
    pytest.mark.skipif(
        os.environ.get("AGENT_WORKTREES_NATIVE_RECOVERY") != "1",
        reason="opt in with AGENT_WORKTREES_NATIVE_RECOVERY=1",
    ),
]


@pytest.mark.parametrize("mode", ["old-provider", "health-timeout"])
def test_actual_native_grace_preserves_old_until_expiry_and_keeps_successor(tmp_path, mode):
    root = tmp_path / "runtime"
    with start_provider(root / "status-monitor-routing", mode) as incumbent:
        directory, data, old, _token_path = incumbent
        token = control.load_or_create_control_token(root)
        health = {
            "status": "ready", "published": True, "pid": os.getpid(),
            "worktrees": {"status": "ready"}, "repositories": {"admitting": True},
        }
        server = control.ControlServer(lambda action, payload: health, token=token)
        server.start()
        try:
            successor = routing.publish_active(
                directory, bind="127.0.0.1", port=server.port, pid=os.getpid(),
                demote_existing=True,
            )
            predecessor = {
                "pid": old.pid, "start": old.process_start_time,
                "slot": None, "release_claim": None,
            }
            retirements.reserve(root, predecessor, committed=True)
            breadcrumb.write_breadcrumb(
                directory, state="committed", old=old.to_dict(),
                new_pid=successor.pid, new_port=successor.port,
                new_process_start_time=successor.process_start_time, self_retirement=True,
            )
            MonitorHandover(passive=True).announce(directory, os.getpid())

            def ready():
                return recovery.successor_healthy(
                    successor.client_host, successor.port,
                    lambda url: control.ControlClient(url, token=token), expected=successor,
                )

            assert ready() and data["process"].poll() is None
            grace.arm(root, ready=ready, clock=lambda: 1000.0)
            breadcrumb.clear_breadcrumb(directory)
            grace.reconcile(root, clock=lambda: 1299.999)
            assert data["process"].poll() is None and ready()
            grace.reconcile(root, clock=lambda: 1300.0)
            assert data["process"].wait(timeout=5) is not None
            assert ready() and routing.read_table(directory)["active"] == successor.to_dict()
            assert "previous" not in routing.read_table(directory)
            assert not retirements.reconcile(root)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as released:
                released.bind((old.client_host, old.port))
                released.listen(1)
        finally:
            server.close()
