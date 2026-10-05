"""Phase 1 observability for resident mux-daemons (copilot-extensions#5001).

Resident per-version mux-daemons accumulate indefinitely today: a cutover is
only attempted opportunistically, and a superseded daemon with even one
still-attached client blocks its own retirement forever (see the issue for
the full background). Before any retirement sweep can be built (later
phases), there needs to be ground truth on what is actually resident right
now -- this module enumerates every resident mux-daemon matched to a given
root and reports its identity (pid/port), whether it is the routing table's
current active endpoint, and -- for a reachable one -- its own reported
version, attached-client count, and busy state.

This is read-only: it never drains, terminates, or otherwise mutates any
daemon. Phases 2+ (retirement) are tracked separately in the same issue.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from zdd import routing

from . import mux_daemon_cutover
from .self_install import default_root


def _cmdline_for_pid(pid: int) -> str:
    """Best-effort cmdline text for an already-identified mux-daemon pid.

    Approximate by design: this is used only to recover the ``--listen-port``
    a resident daemon was started with, for a purely observational report --
    never for an identity-sensitive decision (those stay routed through
    ``mux_daemon_cutover``'s own, more careful root-matching helpers).
    """
    if os.name == "nt":
        argv = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
            "ForEach-Object { $_.CommandLine }" % pid,
        ]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603
        return (out.stdout or "").strip()
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.is_file():
        try:
            parts = [part for part in proc_cmdline.read_text("utf-8").split("\0") if part]
            if parts:
                return " ".join(parts)
        except OSError:
            pass
    out = subprocess.run(  # noqa: S603
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env=mux_daemon_cutover._ps_env_without_width_override(),
    )
    return (out.stdout or "").strip()


def _parse_listen_port(cmdline: str) -> int | None:
    for token in cmdline.split():
        token = token.strip("\"'")
        if token.startswith("--listen-port="):
            try:
                return int(token.split("=", 1)[1])
            except ValueError:
                return None
    return None


def daemon_statuses(root: Path | None = None) -> list[dict[str, Any]]:
    """Enumerate every resident mux-daemon matched to ``root``.

    Each entry always carries ``pid``, ``port`` (``None`` if it couldn't be
    recovered from the process's own command line), and ``active`` (whether
    the routing table's current endpoint points at this pid). When ``port``
    and a reachable control endpoint are both available, the entry is
    enriched with the daemon's own self-reported ``status``/``version``/
    ``attached_clients``/``busy``; otherwise ``status`` is ``"unreachable"``
    (a live health request failed) or ``"unknown"`` (no port could be
    recovered at all).
    """
    resolved_root = root if root is not None else default_root()
    table = routing.read_table(mux_daemon_cutover.routing_dir(resolved_root))
    active_raw = table.get("active") if isinstance(table, dict) else None
    active_endpoint = (
        routing.Endpoint.from_dict(active_raw) if isinstance(active_raw, dict) else None
    )

    results: list[dict[str, Any]] = []
    for pid in sorted(mux_daemon_cutover._iter_mux_daemon_pids()):
        if not mux_daemon_cutover._pid_matches_root(pid, root=resolved_root):
            continue
        cmdline = _cmdline_for_pid(pid)
        port = _parse_listen_port(cmdline)
        entry: dict[str, Any] = {
            "pid": pid,
            "port": port,
            # Matched on BOTH pid and port: pid alone can misattribute
            # "active" after PID reuse -- the routing table's active row
            # could retain a now-stale endpoint while an unrelated, later
            # daemon happens to reuse that exact pid on a different port.
            # An unresolved port (None) never matches, so an unverifiable
            # candidate is reported as not-active rather than guessed.
            "active": bool(
                active_endpoint is not None
                and active_endpoint.pid == pid
                and active_endpoint.port == port
            ),
        }
        if port is None:
            entry["status"] = "unknown"
            results.append(entry)
            continue
        try:
            client = mux_daemon_cutover.ControlClient(
                f"http://{routing.format_authority('127.0.0.1', port)}",
                root=resolved_root,
                timeout=3.0,
            )
            health = client.health()
        except Exception:
            entry["status"] = "unreachable"
            results.append(entry)
            continue
        entry["status"] = health.get("status", "unknown")
        # A reachable daemon running code older than this feature (exactly
        # the long-resident stale daemons #5001 is about) answers with the
        # old, smaller health shape -- no "version" key at all. Representing
        # that as null version/attached_clients/busy would misleadingly look
        # like "live, measured, and idle" rather than "can't be measured";
        # mark it explicitly unsupported instead so a caller (and the text
        # renderer) can tell the two apart.
        if "version" in health:
            entry["version"] = health.get("version")
            entry["attached_clients"] = health.get("attached_clients")
            entry["busy"] = health.get("busy")
        else:
            entry["telemetry"] = "unsupported"
        results.append(entry)
    return results
