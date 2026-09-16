"""General pre-render launch-checkpoint tracing.

Relocated out of ``picker_tui/frame_health.py`` (2026-09-16,
worktree-manager-control-plane Phase 3/6 Step 1.5): ``append_launch_event`` is
called from ``cmd_resolve``/``_run_new_picker`` in ``__main__.py`` to record
launch checkpoints (``resolve_handler_start``, ``housekeeping_start``, etc.)
independent of whether the Textual Picker TUI itself ends up running, so it
can't be deleted along with the rest of the bundled Picker. The Picker's own
render-loop frame-gap diagnostics (``FrameHealthReporter``) stay in
``picker_tui/frame_health.py`` and import ``_timestamp``/``_launch_trace_path``
from here.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _launch_trace_path() -> Path | None:
    raw = os.environ.get("AGENT_WORKTREES_LAUNCH_TRACE", "").strip()
    if not raw or raw.lower() in ("0", "false", "no", "off"):
        return None
    if raw.lower() in ("1", "true", "yes", "on"):
        return (
            Path.home()
            / ".agent-worktrees"
            / "logs"
            / "picker-launches.jsonl"
        )
    return Path(raw).expanduser()


def append_launch_event(event: str, **fields) -> None:
    """Append one pre-render launch checkpoint. Best-effort and fail-silent."""
    path = _launch_trace_path()
    if path is None:
        return
    payload = {
        "timestamp": _timestamp(),
        "event": event,
        "launch_id": os.environ.get("AGENT_WORKTREES_LAUNCH_ID", ""),
        **fields,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        return
