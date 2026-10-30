"""Offline analysis orchestration; no hooks, provider calls or raw-content output."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from snapshot_inventory import collect_inventory

MAX_REQUEST_BYTES = 16 * 1024 * 1024


def analyze_file(
    path: Path, working_directory: Path, *, session_id: str | None = None,
    agent_worktrees_command: str | None = None, launch_preferences: dict | None = None,
) -> dict:
    from snapshot_wire import analyze_request

    with path.open("rb") as stream:
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("analysis_request_size_limit")
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError("analysis_request_not_object")
    if not isinstance(request.get("messages"), list):
        raise ValueError("analysis_requires_chat_completion_messages")
    inventory = collect_inventory(
        working_directory, session_id=session_id,
        agent_worktrees_command=agent_worktrees_command,
    )
    report = analyze_request(request, inventory)
    report["request"]["original_wire_utf8_bytes"] = len(raw)
    report["request"]["model"] = request.get("model") if isinstance(request.get("model"), str) else None
    report["captured_wire_json_bytes"] = len(raw)
    report["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    report["inventory_coverage"] = inventory["coverage"]
    if launch_preferences is not None:
        report["launch_preferences"] = launch_preferences
    return report
