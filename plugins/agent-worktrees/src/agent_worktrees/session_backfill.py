"""The single sanctioned repair sweep behind sessions.backfill_sessions."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

from . import sessions as session_api
from .push_timeout import ObservationUnavailable


def _workspace_path(value: str):
    windows = PureWindowsPath(value)
    return windows if windows.is_absolute() else PurePosixPath(value)


def backfill_sessions(records: list, *, strict: bool = False) -> dict[str, list[str]]:
    """Discover existing session IDs without changing tracking records."""
    session_dir = session_api._session_state_dir()
    if not strict and not session_dir.exists():
        return {}

    path_to_wt: dict[PurePosixPath | PureWindowsPath, str] = {}
    for rec in records:
        sessions = getattr(rec, "sessions", None)
        if sessions:
            continue
        if strict and (
            not isinstance(rec.worktree_path, str)
            or not (
                PurePosixPath(rec.worktree_path).is_absolute()
                or PureWindowsPath(rec.worktree_path).is_absolute()
            )
        ):
            raise ObservationUnavailable("Session discovery requires an absolute worktree path")
        if not rec.worktree_path:
            continue
        path_to_wt[_workspace_path(rec.worktree_path)] = rec.worktree_id

    if not path_to_wt:
        return {}

    if strict:
        try:
            with os.scandir(session_dir) as scanned:
                entries = [Path(entry.path) for entry in scanned if entry.is_dir()]
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise ObservationUnavailable("Session discovery is incomplete") from error
    else:
        entries = session_dir.iterdir()
    discovered: dict[str, list[tuple[str, str]]] = {}
    for entry in entries:
        if not strict and not entry.is_dir():
            continue
        ws_file = entry / "workspace.yaml"
        if strict:
            try:
                with os.scandir(entry) as scanned:
                    names = {item.name for item in scanned}
            except OSError as error:
                raise ObservationUnavailable("Session discovery is incomplete") from error
            if session_api._DETACHED_MARKER in names:
                continue
            conversation = "session.db" in names or "events.jsonl" in names
            ownership = "bridge.lock" in names or any(
                name.startswith("inuse.") and name.endswith(".lock") for name in names
            )
            if not conversation and not ownership:
                continue
            if "workspace.yaml" not in names:
                raise ObservationUnavailable("Session workspace discovery is incomplete")
        else:
            if session_api._is_detached_session(entry):
                continue
            if not ws_file.exists():
                continue
            if not (entry / "session.db").exists() and not (entry / "events.jsonl").exists():
                continue
        try:
            with open(ws_file, encoding="utf-8") as stream:
                ws_data = yaml.load(stream, Loader=session_api._YamlSafeLoader)
        except Exception as error:
            if strict:
                raise ObservationUnavailable("Session workspace discovery is incomplete") from error
            continue
        if not ws_data or not isinstance(ws_data, dict):
            if strict:
                raise ObservationUnavailable("Session workspace metadata is malformed")
            continue
        cwd = ws_data.get("cwd", "")
        if not cwd or not isinstance(cwd, str):
            if strict:
                raise ObservationUnavailable("Session workspace has no conclusive path")
            continue
        if strict and not (PurePosixPath(cwd).is_absolute() or PureWindowsPath(cwd).is_absolute()):
            raise ObservationUnavailable("Session workspace path is not absolute")
        norm_cwd = _workspace_path(cwd)
        for wt_path, wt_id in path_to_wt.items():
            if norm_cwd == wt_path or wt_path in norm_cwd.parents:
                if strict and not conversation:
                    raise ObservationUnavailable("Owned session history is not yet discoverable")
                updated_at = str(ws_data.get("updated_at", ""))
                discovered.setdefault(wt_id, []).append((entry.name, updated_at))
                break

    result: dict[str, list[str]] = {}
    for wt_id, entries in discovered.items():
        entries.sort(key=lambda item: item[1])
        result[wt_id] = [sid for sid, _ in entries]
    return result
