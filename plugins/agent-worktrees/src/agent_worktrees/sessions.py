"""Copilot CLI session-state scanning.

Scans ~/.copilot/session-state/ to detect active Copilot sessions
(by lock file + process check) and extract latest session summaries
for worktree annotation.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SessionContext:
    """Aggregated session info for a set of worktree paths."""

    active_sessions: dict[str, list[str]] = field(default_factory=dict)
    """normalized_path → list of session_ids with live Copilot processes"""

    latest_summary: dict[str, str] = field(default_factory=dict)
    """normalized_path → most recent session summary text"""


def _normalize_path(p: str) -> str:
    """Normalize a path for comparison — lowercase on Windows, strip trailing sep."""
    p = p.rstrip("/\\")
    if platform.system() == "Windows":
        return p.lower()
    return p


def _session_state_dir() -> Path:
    """Return the Copilot session-state directory."""
    if platform.system() == "Windows":
        home = os.environ.get("USERPROFILE", str(Path.home()))
    else:
        home = str(Path.home())
    return Path(home) / ".copilot" / "session-state"


def _is_process_alive(pid: int) -> bool:
    """Check if a process is running."""
    if platform.system() == "Windows":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _is_copilot_process(pid: int) -> bool:
    """Check if a PID belongs to a Copilot CLI process."""
    if platform.system() == "Windows":
        try:
            import subprocess

            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
            )
            return "copilot" in result.stdout.lower()
        except Exception:
            return _is_process_alive(pid)
    else:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        try:
            content = cmdline_path.read_bytes()
            return b"copilot" in content
        except OSError:
            return False


def scan_sessions(worktree_paths: list[str]) -> SessionContext:
    """Scan Copilot session-state for active sessions and summaries.

    Args:
        worktree_paths: List of worktree filesystem paths to match against.

    Returns:
        SessionContext with active sessions and latest summaries.
    """
    ctx = SessionContext()
    session_dir = _session_state_dir()

    if not session_dir.exists() or not worktree_paths:
        return ctx

    # Build normalized lookup set
    path_set: set[str] = {_normalize_path(p) for p in worktree_paths}

    # Track latest summary per path by updated_at
    latest_ts: dict[str, str] = {}

    for entry in session_dir.iterdir():
        if not entry.is_dir():
            continue

        ws_file = entry / "workspace.yaml"
        if not ws_file.exists():
            continue

        try:
            with open(ws_file, encoding="utf-8") as f:
                ws_data = yaml.safe_load(f)
        except Exception:
            continue

        if not ws_data or not isinstance(ws_data, dict):
            continue

        cwd = ws_data.get("cwd", "")
        if not cwd:
            continue

        norm_cwd = _normalize_path(cwd)

        # Match against worktree roots — session cwd may be a subdirectory
        matched_path: str | None = None
        for wt_path in path_set:
            if norm_cwd == wt_path or norm_cwd.startswith(wt_path + os.sep):
                matched_path = wt_path
                break

        if matched_path is None:
            continue

        # Track latest usable summary
        summary = ws_data.get("summary", "")
        if isinstance(summary, str) and summary not in ("", "|-", "|", ">-", ">"):
            summary = summary.strip()
            if summary:
                updated_at = ws_data.get("updated_at", "")
                if not latest_ts.get(matched_path) or updated_at > latest_ts[matched_path]:
                    latest_ts[matched_path] = updated_at
                    if len(summary) > 60:
                        summary = summary[:57] + "..."
                    ctx.latest_summary[matched_path] = summary

        # Check for live lock files
        live_found = False
        for lock_file in entry.glob("inuse.*.lock"):
            parts = lock_file.stem.split(".")
            if len(parts) >= 2:
                try:
                    lock_pid = int(parts[1])
                except ValueError:
                    continue
                if _is_copilot_process(lock_pid):
                    live_found = True
                    break

        if live_found:
            if matched_path not in ctx.active_sessions:
                ctx.active_sessions[matched_path] = []
            ctx.active_sessions[matched_path].append(entry.name)

    return ctx


def find_latest_session_id(worktree_path: str) -> str | None:
    """Find the most recent Copilot session ID for a worktree path.

    Scans ``~/.copilot/session-state/`` for sessions whose ``cwd``
    matches *worktree_path* and returns the session directory name
    (which is the session ID) of the most recently updated match.

    Returns None if no matching session is found.
    """
    session_dir = _session_state_dir()
    if not session_dir.exists():
        return None

    norm_wt = _normalize_path(worktree_path)
    best_id: str | None = None
    best_ts: str = ""

    for entry in session_dir.iterdir():
        if not entry.is_dir():
            continue

        ws_file = entry / "workspace.yaml"
        if not ws_file.exists():
            continue

        try:
            with open(ws_file, encoding="utf-8") as f:
                ws_data = yaml.safe_load(f)
        except Exception:
            continue

        if not ws_data or not isinstance(ws_data, dict):
            continue

        cwd = ws_data.get("cwd", "")
        if not cwd:
            continue

        norm_cwd = _normalize_path(cwd)
        if norm_cwd != norm_wt and not norm_cwd.startswith(norm_wt + os.sep):
            continue

        updated_at = str(ws_data.get("updated_at", ""))
        if updated_at > best_ts:
            best_ts = updated_at
            best_id = entry.name

    return best_id


def has_mux_session(worktree_id: str) -> bool:
    """Check if a multiplexer session exists for a worktree (without killing it).

    Uses tmux on Linux/WSL and psmux on Windows.

    Returns True if the mux session is alive, False otherwise.
    """
    import subprocess

    sess_name = f"wt-{worktree_id}"
    if platform.system() == "Windows":
        cmd = ["psmux", "has-session", "-t", sess_name]
    else:
        cmd = ["tmux", "has-session", "-t", f"={sess_name}"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def kill_tmux_session(worktree_id: str) -> bool:
    """Kill the multiplexer session associated with a worktree, if one exists.

    Uses tmux on Linux/WSL and psmux on Windows.

    Returns True if a session was killed, False if none existed or the
    multiplexer is not available.
    """
    import subprocess

    sess_name = f"wt-{worktree_id}"
    if platform.system() == "Windows":
        cmd = ["psmux", "kill-session", "-t", sess_name]
    else:
        cmd = ["tmux", "kill-session", "-t", f"={sess_name}"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
