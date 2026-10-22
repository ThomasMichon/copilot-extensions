"""Helpers that read sessions already published to a hub destination.

Hub compaction judges each published session by its ``workspace.yaml``
timestamps and working directory, never by filesystem mtimes, which are
unreliable on OneDrive online-only placeholders.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from agent_logger import sessions


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``)."""
    raw = (ts or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hub_session_age_days(session_dir: Path, now: datetime) -> float | None:
    """Age in days from a hub session's ``workspace.yaml`` timestamps.

    Uses ``updated_at`` then ``created_at`` -- never filesystem mtime, which is
    unreliable on OneDrive online-only placeholders. ``None`` if no timestamp.
    """
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.is_file():
        return None
    try:
        ws = sessions.parse_workspace_text(
            ws_file.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return None
    for key in ("updated_at", "created_at"):
        dt = _parse_iso(ws.get(key, ""))
        if dt is not None:
            return (now - dt).total_seconds() / 86400.0
    return None


def _hub_session_cwd(session_dir: Path) -> str:
    """Normalized cwd/git_root from a hub session's ``workspace.yaml`` ("" if none)."""
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.is_file():
        return ""
    try:
        ws = sessions.parse_workspace_text(
            ws_file.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return ""
    raw = (ws.get("cwd") or ws.get("git_root") or "").strip()
    return os.path.normcase(os.path.normpath(raw)) if raw else ""
