"""Shared ``zdd`` daemon-health wiring for the resident status-monitor."""

from __future__ import annotations

import os
import subprocess
import sys

from zdd import diagnostics

from . import locks, procs, self_retire, status_monitor_cutover, status_monitor_runtime


def _candidates() -> list[diagnostics.DaemonCandidate]:
    runtime_root = str(status_monitor_runtime._aw_runtime_home())
    if sys.platform == "darwin":
        scoped_pids = {
            pid
            for pid in status_monitor_cutover._iter_status_monitor_pids()
            if _darwin_pid_under_root(pid, runtime_root)
        }
    else:
        scoped_pids = {
            item["pid"]
            for item in procs.processes_with_cwd_under(runtime_root)
            if isinstance(item, dict) and isinstance(item.get("pid"), int)
        }
    return [
        diagnostics.DaemonCandidate(
            pid=pid,
            start_time=locks.process_start_time(pid),
        )
        for pid in sorted(status_monitor_cutover._iter_status_monitor_pids())
        if pid in scoped_pids
    ]


def _darwin_pid_under_root(pid: int, runtime_root: str) -> bool:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    prefix = os.path.normcase(os.path.normpath(os.path.abspath(runtime_root)))
    for line in (result.stdout or "").splitlines():
        if not line.startswith("n"):
            continue
        cwd = line[1:].strip()
        norm = os.path.normcase(os.path.normpath(os.path.abspath(cwd)))
        if norm == prefix or norm.startswith(prefix + os.sep):
            return True
    return False


def _reachability_check(host: str, port: int) -> bool:
    try:
        return bool(
            status_monitor_cutover.ControlClient(
                f"http://{status_monitor_cutover.routing.format_authority(host, port)}",
                timeout=5.0,
            ).health()
        )
    except Exception:
        return False


def _context() -> diagnostics.DiagnosticContext:
    return diagnostics.DiagnosticContext(
        service="agent-worktrees status-monitor",
        config_dir=status_monitor_cutover.routing_dir(),
        read_lock=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
        lock_is_live=locks.lock_is_live,
        list_candidates=_candidates,
        acquire_cutover_guard=lambda timeout: status_monitor_cutover._acquire_cutover_lock(
            status_monitor_cutover.routing_dir(),
            timeout_s=timeout,
        ),
        repair_supported=(sys.platform != "darwin"),
        is_superseded=lambda pid, generation: self_retire.is_superseded(
            status_monitor_cutover.routing_dir(), pid, generation
        ),
        reachability_check=_reachability_check,
        terminate_pid_if_identity=procs.terminate_pid_if_identity,
        make_client=status_monitor_cutover._make_client,
        health_check=status_monitor_cutover._health_check,
    )


def doctor_report(*, apply: bool) -> dict[str, object]:
    ctx = _context()
    if apply:
        return diagnostics.apply_daemon_health(ctx)
    return diagnostics.audit_daemon_health(ctx)
