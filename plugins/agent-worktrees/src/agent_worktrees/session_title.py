"""Capture native conversation titles under the authoritative record lock."""

from __future__ import annotations

import yaml

from . import config as cfg, output, sessions, tracking


def capture_session_title(worktree_id: str, session_id: str) -> bool:
    """Read a native title, re-authorizing the current head before persistence."""
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return False

    rec = tracking.load_record(yaml_path)
    if rec.title and rec.title != "null":
        return False

    session_dir = sessions._session_state_dir() / session_id
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.exists():
        return False
    if sessions._is_detached_session(session_dir):
        return False

    try:
        with open(ws_file, encoding="utf-8") as f:
            ws_data = yaml.safe_load(f)
    except Exception:
        return False

    if not ws_data or not isinstance(ws_data, dict):
        return False

    _placeholder = ("", "|-", "|", ">-", ">", "null", "Untitled")
    display_text = ""
    summary = ws_data.get("summary", "")
    if isinstance(summary, str) and summary.strip() and summary not in _placeholder:
        display_text = summary.strip()
    if not display_text:
        name = ws_data.get("name", "")
        if isinstance(name, str) and name.strip() and name not in _placeholder:
            display_text = name.strip()
    if not display_text:
        return False

    from .execution_spaces import ExecutionSpaceError, require_project_record_mutation
    try:
        with tracking._RecordLock(yaml_path, require_sidecar=True):
            fresh = tracking.load_record(yaml_path)
            try:
                require_project_record_mutation(fresh)
            except ExecutionSpaceError as exc:
                output.err(f"Rejecting session title capture for {worktree_id}: {exc}")
                return False
            if fresh.title and fresh.title != "null":
                return False
            if fresh.resolved_head_session not in (None, session_id):
                return False
            fresh.title = display_text
            tracking.save_record(fresh, yaml_path)
            return True
    except TimeoutError as exc:
        output.err(f"Session title capture for {worktree_id} contended: {exc}")
        return False
