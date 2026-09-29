"""Shared ``zdd`` daemon-health wiring for the resident mux-daemon."""

from __future__ import annotations

import sys
from pathlib import Path

from zdd import diagnostics

from . import mux_daemon, mux_daemon_cutover
from .self_install import default_root


def _candidates(root: Path) -> list[diagnostics.DaemonCandidate]:
    return [
        diagnostics.DaemonCandidate(
            pid=pid,
            start_time=diagnostics.process_start_time(pid),
        )
        for pid in sorted(mux_daemon_cutover._iter_mux_daemon_pids())
        if mux_daemon_cutover._pid_matches_root(pid, root=root)
    ]


def _context(root: Path | None = None) -> diagnostics.DiagnosticContext:
    resolved_root = root if root is not None else default_root()

    def _reachability_check(host: str, port: int) -> bool:
        try:
            return bool(
                mux_daemon_cutover.ControlClient(
                    f"http://{mux_daemon_cutover.routing.format_authority(host, port)}",
                    root=resolved_root,
                    timeout=5.0,
                ).health()
            )
        except Exception:
            return False

    return diagnostics.DiagnosticContext(
        service="worktree-manager mux-daemon",
        config_dir=mux_daemon_cutover.routing_dir(resolved_root),
        read_lock=lambda: mux_daemon.read_lock_data(mux_daemon.lock_path(resolved_root)),
        list_candidates=lambda: _candidates(resolved_root),
        acquire_cutover_guard=lambda timeout: mux_daemon_cutover._acquire_cutover_lock(
            resolved_root,
            timeout_s=timeout,
        ),
        repair_supported=(sys.platform != "darwin"),
        is_superseded=lambda pid, generation: mux_daemon_cutover.is_superseded(
            resolved_root, pid, generation
        ),
        reachability_check=_reachability_check,
        make_client=lambda base_url: mux_daemon_cutover._make_client(base_url, root=resolved_root),
        health_check=lambda host, port: mux_daemon_cutover._health_check(
            host, port, root=resolved_root
        ),
    )


def doctor_report(*, root: Path | None = None, apply: bool) -> dict[str, object]:
    ctx = _context(root)
    if apply:
        return diagnostics.apply_daemon_health(ctx)
    return diagnostics.audit_daemon_health(ctx)
