"""Shared ``zdd`` daemon-health wiring for the resident status-monitor."""

from __future__ import annotations

import os
import subprocess
import sys

from zdd import diagnostics

from . import locks, procs, self_retire, status_monitor_cutover, status_monitor_runtime


def _candidates() -> list[diagnostics.DaemonCandidate]:
    runtime_root = str(status_monitor_runtime._aw_runtime_home())
    if sys.platform == "win32":
        from . import windows_process_census

        matched = []
        for item in windows_process_census.snapshot():
            if not status_monitor_cutover._is_status_monitor_cmdline(item["command_line"]):
                continue
            if not item["cwd"]:
                raise OSError(f"Cannot establish runtime scope for monitor PID {item['pid']}")
            if procs._is_under(item["cwd"], runtime_root):
                matched.append(item)
        census = {item["pid"] for item in matched}
        candidates = []
        for item in matched:
            # A venv-redirector launcher and its base-interpreter worker
            # both match; the parent PID and executable let the shared
            # doctor group them into one generation (and refuse repair
            # when it cannot, including when the census missed a live
            # parent that may be the worker's launcher).
            ppid, image = _structure(item)
            parent_image, parent_gone = None, False
            if isinstance(ppid, int) and ppid > 1 and ppid not in census:
                parent_image, parent_gone = _outside_parent(ppid, item["start_time"])
            candidates.append(
                diagnostics.DaemonCandidate(
                    pid=item["pid"],
                    start_time=item["start_time"],
                    ppid=ppid,
                    image=image,
                    parent_image=parent_image,
                    parent_gone=parent_gone,
                )
            )
        return candidates
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


def _structure(item: dict) -> tuple[int | None, str | None]:
    """``(ppid, image)`` read while ``item``'s PID still names the snapshot's process.

    The start token is re-read before and after the structural reads; on any
    mismatch (the process exited, or its PID was reused) both are reported
    unreadable, which keeps generation grouping uncertain rather than
    describing a replacement process as half of this generation.
    """
    pid, start = item["pid"], item["start_time"]
    if start is None or locks.process_start_time(pid) != start:
        return None, None
    ppid = procs.parent_pid(pid)
    image = procs.process_executable_path(pid)
    if locks.process_start_time(pid) != start:
        return None, None
    return ppid, image


def _outside_parent(ppid: int, child_start: object) -> tuple[str | None, bool]:
    """``(image, gone)`` for a parent the census did not capture.

    ``gone`` only when the parent is provably dead or its PID now names a
    process started after the child (PID reuse). The image is reported only
    when the parent's identity held steady across the read; otherwise it is
    ``None`` (unknown), which keeps the grouping uncertain.
    """
    if not locks.pid_alive(ppid):
        return None, True
    before = locks.process_start_time(ppid)
    if before is None:
        return None, False
    try:
        if int(before) > int(str(child_start)):
            return None, True
    except ValueError:
        return None, False
    image = procs.process_executable_path(ppid)
    if locks.process_start_time(ppid) != before:
        return None, False
    return image, False


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


def _is_runtime_launcher(image: str) -> bool:
    """A venv redirector inside this runtime's own install root."""
    return diagnostics.is_venv_redirector(image) and procs._is_under(
        image, str(status_monitor_runtime._aw_runtime_home())
    )


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
        group_launchers=(sys.platform == "win32"),
        is_launcher_image=_is_runtime_launcher,
    )


def doctor_report(*, apply: bool) -> dict[str, object]:
    ctx = _context()
    if apply:
        return diagnostics.apply_daemon_health(ctx)
    return diagnostics.audit_daemon_health(ctx)
