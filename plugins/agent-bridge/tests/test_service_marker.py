"""Post-cutover service-marker reconciliation (dotfiles #533 caveat #1).

After ``agent-bridge deploy`` cuts over to a new detached daemon, the pid-file
and running-version.json still describe the retired daemon. ``_reconcile_service
_marker`` rewrites both to the live active daemon so ``service`` commands, the
launcher's already-running guard, and the reconciler track the right process.
"""

from __future__ import annotations

import json

from agent_bridge import __main__ as m
from agent_bridge import runtime_version
from agent_bridge.runtime_version import RUNNING_VERSION_FILE


def test_reconcile_service_marker_rewrites_pid_and_running_version(
    tmp_path, monkeypatch
):
    pid_file = tmp_path / "agent-bridge.pid"
    pid_file.write_text("111", encoding="utf-8")  # stale retired-daemon pid
    monkeypatch.setattr(m, "_PID_FILE", str(pid_file))
    monkeypatch.setattr(runtime_version, "install_dir", lambda: tmp_path)

    m._reconcile_service_marker(222, "9.9.9")

    assert pid_file.read_text(encoding="utf-8").strip() == "222"
    data = json.loads(
        (tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8")
    )
    assert data["pid"] == 222
    assert data["version"] == "9.9.9"


def test_reconcile_service_marker_pidfile_failure_is_nonfatal(
    tmp_path, monkeypatch
):
    # A pid-file that cannot be written (parent missing) must not abort the
    # reconcile -- the running-version marker is still recorded.
    monkeypatch.setattr(m, "_PID_FILE", str(tmp_path / "nope" / "agent-bridge.pid"))
    monkeypatch.setattr(runtime_version, "install_dir", lambda: tmp_path)

    m._reconcile_service_marker(333, "9.9.9")  # must not raise

    data = json.loads(
        (tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8")
    )
    assert data["pid"] == 333


def test_reconcile_reads_new_daemon_from_routing_table(tmp_path, monkeypatch):
    # Integration: mirror the _cmd_deploy post-cutover block against the *real*
    # routing table. The orchestrator publishes the new active (its pid+version);
    # the reconcile reads it back and stamps both service markers -- so pid-file
    # and running-version.json converge on the freshly cut-over daemon.
    from zdd import routing

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=49472, pid=54321, version="9.9.9",
    )

    pid_file = tmp_path / "agent-bridge.pid"
    monkeypatch.setattr(m, "_PID_FILE", str(pid_file))
    monkeypatch.setattr(runtime_version, "install_dir", lambda: tmp_path)

    active = routing.read_active_endpoint(cfg_dir, verify_listener=False)
    assert active is not None and active.pid == 54321
    m._reconcile_service_marker(active.pid, active.version)

    assert pid_file.read_text(encoding="utf-8").strip() == "54321"
    data = json.loads(
        (tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8")
    )
    assert data["pid"] == 54321
    assert data["version"] == "9.9.9"
