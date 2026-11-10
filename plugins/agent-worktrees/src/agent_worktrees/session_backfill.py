"""The single sanctioned repair sweep behind sessions.backfill_sessions."""

from __future__ import annotations

import os

import yaml

from . import sessions as session_api


def backfill_sessions(records: list) -> dict[str, list[str]]:
    """Discover existing session IDs without changing tracking records."""
    session_dir = session_api._session_state_dir()
    if not session_dir.exists():
        return {}

    path_to_wt: dict[str, str] = {}
    for rec in records:
        sessions = getattr(rec, "sessions", None)
        if sessions:
            continue
        if not rec.worktree_path:
            continue
        path_to_wt[session_api._normalize_path(rec.worktree_path)] = rec.worktree_id

    if not path_to_wt:
        return {}

    discovered: dict[str, list[tuple[str, str]]] = {}
    for entry in session_dir.iterdir():
        if not entry.is_dir():
            continue
        if session_api._is_detached_session(entry):
            continue
        ws_file = entry / "workspace.yaml"
        if not ws_file.exists():
            continue
        if not (entry / "session.db").exists() and not (entry / "events.jsonl").exists():
            continue
        try:
            with open(ws_file, encoding="utf-8") as stream:
                ws_data = yaml.load(stream, Loader=session_api._YamlSafeLoader)
        except Exception:
            continue
        if not ws_data or not isinstance(ws_data, dict):
            continue
        cwd = ws_data.get("cwd", "")
        if not cwd:
            continue
        norm_cwd = session_api._normalize_path(cwd)
        for wt_path, wt_id in path_to_wt.items():
            if norm_cwd == wt_path or norm_cwd.startswith(wt_path + os.sep):
                updated_at = str(ws_data.get("updated_at", ""))
                discovered.setdefault(wt_id, []).append((entry.name, updated_at))
                break

    result: dict[str, list[str]] = {}
    for wt_id, entries in discovered.items():
        entries.sort(key=lambda item: item[1])
        result[wt_id] = [sid for sid, _ in entries]
    return result
