"""Read-only lifecycle evidence for an observer owned by one registered CLI."""

from __future__ import annotations

import os
import re
import subprocess
from threading import Lock

from .companion_pane import _call
from .mux_mapping_registry import get_mapping

_PANE = re.compile(r"%[0-9]+\Z")
_INCARNATION = re.compile(r"\$[0-9]+:[0-9]+\Z")
_bindings: dict[tuple[str, str, str], tuple[str, str, str, str]] = {}
_ended: set[tuple[str, str, str]] = set()
_lock = Lock()


def observer_visible(
    project: str, worktree_id: str, pane_id: str | None, session_incarnation: str,
) -> bool | None:
    """Best-effort native visibility; uncertainty never licenses background reads."""
    if not isinstance(pane_id, str) or not _PANE.fullmatch(pane_id):
        return None
    try:
        mapping = get_mapping(project, worktree_id)
        if (
            not isinstance(mapping, dict) or mapping.get("project") != project
            or mapping.get("worktree_id") != worktree_id or mapping.get("live") is not True
            or mapping.get("session_incarnation") != session_incarnation
            or not isinstance(session_incarnation, str)
            or not _INCARNATION.fullmatch(session_incarnation)
        ):
            return None
        raw = _call(
            mapping, "display-message", "-p", "-t", pane_id,
            "#{pane_id}\t#{session_id}:#{session_created}\t#{pane_dead}\t"
            "#{pane_active}\t#{window_active}\t#{window_zoomed_flag}\t#{session_attached}",
            timeout=2,
        )
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
    row = raw.split("\t")
    if len(row) != 7 or row[0] != pane_id:
        return None
    if (
        row[1] != session_incarnation or any(flag not in {"0", "1"} for flag in row[2:6])
        or not row[6].isdigit() or len(row[6]) > 10
    ):
        return None
    return row[2] == "0" and row[4] == "1" and int(row[6]) > 0 and (
        row[3] == "1" or row[5] == "0")


def _finished(key: tuple[str, str, str]) -> bool:
    with _lock:
        _ended.add(key)
    return False


def cli_alive(
    project: str, worktree_id: str, cli_pane: str, *, session_incarnation: str | None = None,
) -> bool | None:
    """Return live/dead/unknown evidence without changing any external state.

    The observer process pins the first valid registered owner it sees. Once
    that owner ends or is displaced, a recycled native pane ID cannot revive
    this observer; a newly launched observer has its own fresh binding.
    Supplying the launch incarnation also fences replacement before first poll.
    Missing metadata never replaces a binding: its pinned native owner is polled.
    """
    if (
        not isinstance(project, str) or not project
        or not isinstance(worktree_id, str) or not worktree_id
        or not isinstance(cli_pane, str) or not _PANE.fullmatch(cli_pane)
        or (session_incarnation is not None and (
            not isinstance(session_incarnation, str) or not _INCARNATION.fullmatch(session_incarnation)
        ))
    ):
        return None
    key = (project, worktree_id, cli_pane)
    with _lock:
        if key in _ended:
            return False
        original = _bindings.get(key)
    try:
        mapping = get_mapping(project, worktree_id)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        mapping = None
    if not isinstance(mapping, dict):
        if original is None:
            return None
        mapping = dict(zip(("mux_bin", "mux_session", "session_incarnation", "worktree_path"), original))
        mapping.update(project=project, worktree_id=worktree_id, live=True, panes=[])
    if mapping.get("project") != project or mapping.get("worktree_id") != worktree_id:
        return None
    fields = ("mux_bin", "mux_session", "session_incarnation", "worktree_path")
    if any(not isinstance(mapping.get(field), str) or not mapping[field] for field in fields):
        return None
    if not _INCARNATION.fullmatch(mapping["session_incarnation"]):
        return None
    if not isinstance(mapping.get("live"), bool):
        return None
    if session_incarnation is not None and mapping["session_incarnation"] != session_incarnation:
        return _finished(key)
    try:
        path = os.path.normcase(os.path.realpath(mapping["worktree_path"]))
    except (OSError, ValueError):
        return None
    identity = (mapping["mux_bin"], mapping["mux_session"], mapping["session_incarnation"], path)
    if mapping.get("live") is False:
        return _finished(key)
    if mapping.get("live") is not True:
        return None
    with _lock:
        original = _bindings.get(key)
    if original is not None and original != identity:
        return _finished(key)
    panes = mapping.get("panes")
    if panes is None and original is not None:
        panes = []
    if not isinstance(panes, list) or any(not isinstance(pane, dict) for pane in panes):
        return None
    candidates = [pane for pane in panes if pane.get("role") == "cli"]
    if not candidates and original is not None:
        candidates = [{"pane_id": cli_pane, "role": "cli", "live": True}]
    if len(candidates) != 1:
        return None
    registered = candidates[0]
    pane_id = registered.get("pane_id")
    if not isinstance(pane_id, str) or not _PANE.fullmatch(pane_id):
        return None
    if pane_id != cli_pane or registered.get("live") is False:
        return _finished(key)
    if registered.get("live") is not True:
        return None
    with _lock:
        original = _bindings.setdefault(key, identity)
    if original != identity:
        return _finished(key)
    try:
        raw = _call(
            mapping, "list-panes", "-s", "-t", "=" + mapping["mux_session"], "-F",
            "#{pane_id}\t#{pane_dead}\t#{session_name}\t"
            "#{session_id}:#{session_created}\t#{pane_current_path}",
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
    if not isinstance(raw, str):
        return None
    rows = [line.split("\t") for line in raw.splitlines() if line.strip()]
    if any(
        len(row) != 5 or not _PANE.fullmatch(row[0]) or row[1] not in {"0", "1"}
        or row[2] != mapping["mux_session"] or not _INCARNATION.fullmatch(row[3])
        for row in rows
    ):
        return None
    if len({row[0] for row in rows}) != len(rows) or len({row[3] for row in rows}) > 1:
        return None
    if rows and rows[0][3] != mapping["session_incarnation"]:
        return _finished(key)
    matches = [row for row in rows if row[0] == cli_pane]
    if not matches or matches[0][1] == "1":
        return _finished(key)
    if not matches[0][4]:
        return None
    try:
        if os.path.normcase(os.path.realpath(matches[0][4])) != path:
            return None
    except (OSError, ValueError):
        return None
    with _lock:
        return False if key in _ended else True
