"""Capture native conversation titles under the authoritative record lock."""

from __future__ import annotations

import yaml

from . import config as cfg, output, sessions, tracking


def capture_session_title(
    worktree_id: str, session_id: str, *, ended_activation: dict | None = None,
) -> bool:
    """Read a native title, re-authorizing head and ended activation at persistence."""
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
            entry = fresh.session_entry(session_id)
            if ended_activation is not None:
                if (
                    not isinstance(ended_activation, dict)
                    or type(ended_activation.get("ordinal")) is not int
                    or ended_activation["ordinal"] <= 0
                    or not isinstance(ended_activation.get("source_identity"), str)
                ):
                    output.err(f"Invalid ended-activation title receipt for {worktree_id}")
                    return False
                latest = max(entry.activations, key=lambda item: item.ordinal, default=None) if entry else None
                if (
                    entry is None or latest is None or latest.ordinal != ended_activation["ordinal"]
                    or latest.source_identity != ended_activation["source_identity"]
                    or latest.source_ambiguous or latest.ended_at is None
                    or entry.ended_at != latest.ended_at
                ):
                    return False
            elif entry and any(item.source_identity is not None for item in entry.activations):
                output.err(f"Ended-activation title receipt is unavailable for {worktree_id}")
                return False
            fresh.title = display_text
            tracking.save_record(fresh, yaml_path)
            return True
    except TimeoutError as exc:
        output.err(f"Session title capture for {worktree_id} contended: {exc}")
        return False
