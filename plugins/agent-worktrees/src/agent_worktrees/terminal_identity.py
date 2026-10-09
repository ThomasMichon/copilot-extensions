"""Best-effort terminal identity recorded for every Copilot session at start.

The sessionStart hook client probes what only a process inside the session's
terminal can observe (its ancestry and, on Windows, the console window and
that window's hosting top-level terminal window). The lifecycle normalizes
those facts with environment-derived ones into a small, bounded record in the
session-state directory, so a later tool can locate the terminal showing a
session -- including sessions that started before that tool was running.

Recording is fail-soft: nothing here may raise into, or measurably delay,
session start. Unknown facts are ``None``. The record never carries window
titles, absolute paths, or secrets; executable names are basenames only.

Windows Terminal does not expose tab identity to its child processes, so
``host_hwnd`` identifies the terminal *window*; a consumer that needs the
exact tab combines it with its own UI Automation tab matching.
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

from . import sessions

FILE_NAME = "agent-worktrees-terminal.json"
VERSION = 1
_MAX_FILE_BYTES = 8 * 1024
_MAX_TEXT = 128


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:_MAX_TEXT] if value else None


def _int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _basename(value) -> str | None:
    text = _text(value)
    if not text:
        return None
    return _text(text.replace("\\", "/").rsplit("/", 1)[-1])


def _platform_name() -> str:
    system = platform.system().lower()
    return {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(system, system or "unknown")


def _safe_session_dir(session_id) -> Path | None:
    if (
        not isinstance(session_id, str)
        or not session_id
        or session_id in (".", "..")
        or "/" in session_id
        or "\\" in session_id
        or len(session_id) > 128
    ):
        return None
    return sessions._session_state_dir() / session_id


def _live_lock_pids(session_dir: Path) -> dict[int, str]:
    from . import locks

    result: dict[int, str] = {}
    try:
        lock_files = list(session_dir.glob("inuse.*.lock"))[:8]
    except OSError:
        return result
    for lock_file in lock_files:
        parts = lock_file.stem.split(".")
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        pid = int(parts[1])
        start_time = locks.process_start_time(pid)
        if start_time and sessions._is_copilot_process(pid):
            result[pid] = start_time
    return result


def _copilot_process(session_dir: Path, ancestors: list[int]) -> tuple[int | None, str | None]:
    """Choose this session's Copilot pid: the live lock holder in the hook's ancestry."""
    from . import locks

    live = _live_lock_pids(session_dir)
    for pid in ancestors:
        if pid in live:
            return pid, live[pid]
    if len(live) == 1:
        pid, start_time = next(iter(live.items()))
        return pid, start_time
    for pid in ancestors:
        if sessions._is_copilot_process(pid):
            return pid, locks.process_start_time(pid)
    return None, None


def _mux(environment: dict[str, str]) -> dict | None:
    pane_id = _text(environment.get("TMUX_PANE")) or _text(environment.get("PSMUX_PANE"))
    if not pane_id and not _text(environment.get("TMUX")):
        return None
    # psmux is the tmux-compatible multiplexer on Windows and exports tmux's variables.
    windows = platform.system() == "Windows"
    kind = "psmux" if environment.get("PSMUX_PANE") or windows else "tmux"
    return {"kind": kind, "pane": pane_id}


def _tty(pid: int | None) -> str | None:
    if not pid or platform.system() != "Linux":
        return None
    try:
        target = os.readlink(f"/proc/{pid}/fd/0")
    except OSError:
        return None
    if target.startswith("/dev/pts/") or target.startswith("/dev/tty"):
        return _text(target)
    return None


def build_record(payload: dict, environment: dict[str, str]) -> dict | None:
    """Normalize the hook probe and environment into the persisted record."""
    session_dir = _safe_session_dir(payload.get("sessionId"))
    if session_dir is None:
        return None
    metadata = payload.get("_agentWorktrees")
    probe = metadata.get("terminal") if isinstance(metadata, dict) else None
    if not isinstance(probe, dict):
        probe = {}
    raw_ancestors = probe.get("ancestors")
    ancestors = [
        pid for pid in (raw_ancestors if isinstance(raw_ancestors, list) else [])[:32]
        if _int(pid)
    ]
    copilot_pid, copilot_start_time = _copilot_process(session_dir, ancestors)
    return {
        "version": VERSION,
        "platform": _platform_name(),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "copilot_pid": copilot_pid,
        "copilot_start_time": copilot_start_time,
        "term_program": _text(environment.get("TERM_PROGRAM")),
        "wt_session": _text(environment.get("WT_SESSION")),
        "console_hwnd": _int(probe.get("console_hwnd")),
        "console_class": _text(probe.get("console_class")),
        "host_hwnd": _int(probe.get("host_hwnd")),
        "host_pid": _int(probe.get("host_pid")),
        "host_exe": _basename(probe.get("host_exe")),
        "host_class": _text(probe.get("host_class")),
        "tty": _tty(copilot_pid),
        "mux": _mux(environment),
        "ssh": any(
            environment.get(key)
            for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
        ),
    }


def record_session_terminal(payload: dict, environment: dict[str, str]) -> bool:
    """Persist this session's terminal identity; never raises."""
    try:
        record = build_record(payload, environment)
        if record is None:
            return False
        session_dir = _safe_session_dir(payload.get("sessionId"))
        if session_dir is None or not session_dir.is_dir():
            return False
        target = session_dir / FILE_NAME
        temporary = session_dir / f".{FILE_NAME}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, target)
        return True
    except Exception:
        return False


def read_session_terminal(session_id) -> dict | None:
    """Return the recorded terminal identity plus a fresh ``live`` verdict."""
    from . import locks

    session_dir = _safe_session_dir(session_id)
    if session_dir is None:
        return None
    path = session_dir / FILE_NAME
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("version") != VERSION:
        return None
    pid = _int(record.get("copilot_pid"))
    recorded_start = record.get("copilot_start_time")
    live = None
    if pid and isinstance(recorded_start, str) and recorded_start:
        try:
            live = locks.process_start_time(pid) == recorded_start
        except Exception:
            live = None
    record["live"] = live
    return record
