"""GET /api/v1/dispatch-tasks/{id}/session -- resolve a dispatch-task
reference to the session that worked (or last worked) it.

*resolve-by-any-origin-reference* (``visions/plugins/agent-bridge``,
``agent-dispatch-session-worktree-history`` Phase 2): a caller holding only
an agent-dispatch task id -- not a session id directly -- resolves through
this route to the SAME answer a caller with the session id would get from
``GET /api/v1/sessions/{id}``, through the same any-session-any-registered-
worktree resolver.

Resolution order:

1. Fetch the task and its durable attachment history (Phase 1,
   ``GET {agent_dispatch_url}/tasks/{id}`` and
   ``.../tasks/{id}/attachments``) from the local agent-dispatch
   coordinator.
2. Rank candidate session IDs with
   :func:`agent_bridge.dispatch_task_resolution.candidate_session_ids`
   (current owner first, then attachment history newest-first) and try each
   through the existing live-then-cold-store resolver
   (``SessionManager.get_session`` / ``fetch_cold_store_session`` -- the
   same pair ``routes.sessions.get_session`` uses), stopping at the first
   hit.
3. If nothing resolves, fall back to the task's target worktree's own
   latest known bridge session
   (:func:`agent_bridge.routes.worktrees._latest_session_for_worktree`).
4. Otherwise 404 -- never an error; a task with no resolvable session at
   all is a legitimate, expected outcome for a caller that must degrade
   gracefully (e.g. hide a "View reviewer" link rather than show a broken
   one).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..agent_dispatch_client import fetch_attachments, fetch_task
from ..cold_store_views import cold_store_session_info
from ..dispatch_task_resolution import candidate_session_ids, task_worktree_id
from ..models import SessionInfo
from ..session_manager import SessionManager
from .sessions import _session_info
from .worktrees import _latest_session_for_worktree

log = logging.getLogger("agent-bridge")

router = APIRouter(prefix="/api/v1/dispatch-tasks", tags=["dispatch-tasks"])


@router.get("/{task_id}/session", response_model=SessionInfo)
async def get_dispatch_task_session(task_id: str, request: Request) -> SessionInfo:
    cfg = request.app.state.config
    base_url = getattr(cfg, "agent_dispatch_url", "") or ""
    token = getattr(cfg, "agent_dispatch_token", "") or ""
    if not base_url:
        raise HTTPException(
            status_code=503,
            detail="agent-dispatch coordinator is not configured "
            "(agent_dispatch_url is empty)",
        )

    try:
        task = await fetch_task(base_url, token, task_id)
    except (httpx.HTTPError, ValueError) as exc:
        # The exception text itself can carry sensitive connection details
        # (URLs, host/port, auth hints) -- keep it out of the formatted log
        # message; exc_info=True still captures the full traceback server-side.
        log.warning(
            "dispatch task %s: coordinator fetch failed", task_id, exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="agent-dispatch coordinator is unreachable or returned "
            "an unexpected response",
        ) from exc
    if task is None:
        raise HTTPException(
            status_code=404, detail=f"dispatch task {task_id} not found",
        )

    try:
        attachments = await fetch_attachments(base_url, token, task_id)
    except (httpx.HTTPError, ValueError):
        # Attachment history is an enrichment, not a hard requirement -- the
        # current owner session (from the task record itself) is still a
        # valid candidate without it.
        log.warning(
            "dispatch task %s: attachment history fetch failed, "
            "resolving from current owner only", task_id, exc_info=True,
        )
        attachments = []

    mgr: SessionManager = request.app.state.session_manager
    for session_id in candidate_session_ids(task, attachments):
        session = mgr.get_session(session_id)
        if session is not None:
            return _session_info(session)
        cold = await mgr.fetch_cold_store_session(session_id)
        if cold is not None:
            return cold_store_session_info(cold)

    worktree_id = task_worktree_id(task)
    if worktree_id:
        fallback = _latest_session_for_worktree(mgr, worktree_id)
        if fallback is not None:
            return _session_info(fallback)

    raise HTTPException(
        status_code=404,
        detail=f"no resolvable session for dispatch task {task_id}",
    )
