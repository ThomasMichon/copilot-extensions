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
   through the live-then-cold-store-then-live-registration resolver:
   ``SessionManager.get_session`` (bridge-owned) /
   ``fetch_cold_store_session`` (archived) /
   ``Database.get_live_session`` (a CLI-embodied task's *interactive*
   session, represented but not owned -- see ``routes/live_sessions.py``) --
   stopping at the first hit.
3. If nothing resolves, fall back to the task's target worktree's own
   latest known session: first the bridge-owned tier
   (:func:`agent_bridge.routes.worktrees._latest_session_for_worktree`, live
   only), then the live-sessions registry's own worktree-scoped lookup
   (``Database.current_represented_session_for_worktree``, also live only --
   there is no cold-store "latest session for a worktree" query capability,
   only exact-session-id lookups).
4. Otherwise 404 -- never an error; a task with no resolvable session at
   all is a legitimate, expected outcome for a caller that must degrade
   gracefully (e.g. hide a "View reviewer" link rather than show a broken
   one).
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..agent_dispatch_client import fetch_attachments, fetch_task
from ..cold_store_views import cold_store_session_info, live_registration_to_session_info
from ..config import resolve_dispatch_url
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
    base_url = resolve_dispatch_url(getattr(cfg, "agent_dispatch_url", None))
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
    db = getattr(request.app.state, "db", None)
    for session_id in candidate_session_ids(task, attachments):
        session = mgr.get_session(session_id)
        if session is not None:
            return _session_info(session)
        cold = await mgr.fetch_cold_store_session(session_id)
        if cold is not None:
            return cold_store_session_info(cold)
        # A CLI-embodied task's owner_session_id is a real ACP session id
        # registered in the *live-sessions* registry (an interactive CLI
        # session the bridge represents but does not own -- see
        # `routes/live_sessions.py`), never bridge-owned SessionManager or
        # the cold-store provider. Check it too before moving to the next
        # candidate.
        if db is not None:
            live_row = db.get_live_session(session_id)
            if live_row is not None:
                return live_registration_to_session_info(live_row)

    worktree_id = task_worktree_id(task)
    if worktree_id:
        fallback = _latest_session_for_worktree(mgr, worktree_id)
        if fallback is not None:
            return _session_info(fallback)
        # Live-only tier above is bridge-owned sessions; a worktree whose
        # *interactive* CLI session is current (e.g. an `embody`-spawned
        # task) has no bridge-owned session at all -- check the
        # live-sessions registry's own worktree-scoped lookup before giving
        # up. Deliberately live-only, same as the SessionManager tier above:
        # there is no cold-store "latest session for a worktree" query
        # capability (only exact-session-id lookups), so a reclaimed
        # worktree's history is only reachable via a resolvable candidate
        # session id above, not through this fallback.
        if db is not None:
            live_session_id = db.current_represented_session_for_worktree(
                worktree_id, now=time.time(),
            )
            if live_session_id:
                live_row = db.get_live_session(live_session_id)
                if live_row is not None:
                    return live_registration_to_session_info(live_row)

    raise HTTPException(
        status_code=404,
        detail=f"no resolvable session for dispatch task {task_id}",
    )
