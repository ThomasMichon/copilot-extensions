"""History for the ``/ui`` control surface: what an earlier task left behind.

- ``GET /api/v1/ui/sessions/{session_id}/history`` -- an ended session's
  transcript from the registered cold-store provider (the same seam as
  ``/api/v1/sessions/{id}/transcript``), mapped to the live event stream's own
  kinds and bounded, so the page renders it with the live viewer and never
  loads a whole raw log.
- ``GET /api/v1/ui/tasks/{worktree_id}/commits`` -- the task branch's own
  commits (those not on its repository's default branch), newest first.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..cold_store_sources import discover_cold_store_manifests
from . import ui_tasks as _tasks
from . import worktrees as _wt

router = APIRouter()

#: Newest mapped events returned for one transcript.
MAX_HISTORY_EVENTS = 1500
MAX_TEXT = 8000
MAX_TOOL_OUTPUT = 2000
MAX_COMMITS = 40
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$")


def _ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text if len(text) <= limit else text[:limit] + "\u2026"


def _tool_output(result: Any) -> str:
    if isinstance(result, dict):
        result = result.get("content") or result.get("detailedContent") or result.get("message") or ""
    if isinstance(result, list):
        result = "\n".join(
            str(p.get("text") if isinstance(p, dict) else p) for p in result if p is not None
        )
    return _clip(result, MAX_TOOL_OUTPUT)


def to_stream_events(raw: list[Any]) -> list[dict[str, Any]]:
    """Map a Copilot session log (``events.jsonl`` records) -- or events that
    are already in the bridge's stream shape -- to ``{event, data, ts}``."""
    out: list[dict[str, Any]] = []
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        if isinstance(rec.get("event"), str):  # already a bridge stream event
            out.append({"event": rec["event"], "data": rec.get("data") or {},
                        "ts": _ts(rec.get("timestamp") or rec.get("ts"))})
            continue
        kind, d, ts = rec.get("type"), rec.get("data") or {}, _ts(rec.get("timestamp"))
        if not isinstance(d, dict):
            continue
        if kind == "user.message":
            out.append({"event": "user_message", "data": {"content": _clip(d.get("content"), MAX_TEXT)}, "ts": ts})
        elif kind == "assistant.message":
            text = d.get("content")
            if isinstance(text, str) and text.strip():
                out.append({"event": "agent_message", "ts": ts, "data": {
                    "text": _clip(text, MAX_TEXT), "agent_id": d.get("parentToolCallId")}})
        elif kind == "tool.execution_start":
            out.append({"event": "tool_call_start", "ts": ts, "data": {
                "tool_call_id": d.get("toolCallId"), "kind": d.get("toolName") or "tool",
                "raw_input": d.get("arguments"), "agent_id": d.get("parentToolCallId")}})
        elif kind == "tool.execution_complete":
            failed = d.get("success") is False
            out.append({"event": "tool_call_update", "ts": ts, "data": {
                "tool_call_id": d.get("toolCallId"), "status": "failed" if failed else "completed",
                "content": _tool_output(d.get("error") if failed and d.get("error") else d.get("result"))}})
        elif kind == "assistant.turn_end":
            out.append({"event": "turn_complete", "data": {}, "ts": ts})
        elif kind == "session.compaction_start":
            out.append({"event": "compaction_start", "data": {}, "ts": ts})
        elif kind == "session.compaction_complete":
            out.append({"event": "compaction_complete", "ts": ts, "data": {
                "success": d.get("success", True), "tokens_removed": d.get("tokensRemoved")}})
        elif kind == "session.model_change" and d.get("newModel"):
            out.append({"event": "usage_update", "data": {"model": d.get("newModel")}, "ts": ts})
    return out


def _has_provider() -> bool:
    try:
        return "session-fetch" in discover_cold_store_manifests()
    except Exception:  # noqa: BLE001 -- a broken registry reads as "no provider"
        return False


async def fetch_transcript(request: Request, session_id: str) -> tuple[list[Any], str | None] | None:
    """``(raw events, status)`` for an ended session, or ``None`` when no
    cold-store provider has it. (A dev server replaces this seam.)"""
    mgr = request.app.state.session_manager
    cold = await mgr.fetch_cold_store_session(session_id)
    if cold is None:
        return None
    return list(cold.events), cold.status


@router.get("/api/v1/ui/sessions/{session_id}/history", include_in_schema=False)
async def session_history(session_id: str, request: Request) -> JSONResponse:
    if not _SESSION_ID.match(session_id):
        return JSONResponse({"detail": "not a session id"}, status_code=400)
    found = await fetch_transcript(request, session_id)
    if found is None:
        provider = _has_provider()
        detail = ("the transcript provider has no record of this session" if provider else
                  "no cold-store provider is registered on this machine, so ended sessions "
                  "can't be read back (agent-logger provides one)")
        return JSONResponse({"detail": detail, "provider": provider}, status_code=404)
    raw, status = found
    events = to_stream_events(raw)
    cut = max(0, len(events) - MAX_HISTORY_EVENTS)
    return JSONResponse({
        "session_id": session_id, "status": status, "events": events[cut:],
        "truncated_before": cut > 0, "total": len(events),
    })


@router.get("/api/v1/ui/tasks/{worktree_id}/commits", include_in_schema=False)
async def task_commits(worktree_id: str, request: Request) -> JSONResponse:
    st = _tasks._state(request)
    cache = st.get("cache") or await _tasks._refresh(st)
    row = next((r for r in cache["workspaces"] if r.get("id") == worktree_id), None)
    if row is None:
        return JSONResponse({"detail": "unknown worktree"}, status_code=404)
    git = shutil.which("git")
    path = row.get("path")
    if not git or not path:
        return JSONResponse({"worktree_id": worktree_id, "commits": [], "available": False})
    info = (cache.get("project_info") or {}).get(row.get("project"), {})
    base = "origin/" + str(info.get("default_branch") or "main")
    out, _err = await _wt._exec_ex(
        [git, "-C", str(path), "log", f"-{MAX_COMMITS}", "--format=%h%x1f%s%x1f%ct", "HEAD", "--not", base],
        timeout=15.0)
    if out is None:
        return JSONResponse({"worktree_id": worktree_id, "commits": [], "available": False})
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3 and parts[2].isdigit():
            commits.append({"sha": parts[0], "subject": parts[1][:200], "ts": int(parts[2])})
    return JSONResponse({"worktree_id": worktree_id, "base": base, "commits": commits, "available": True})
