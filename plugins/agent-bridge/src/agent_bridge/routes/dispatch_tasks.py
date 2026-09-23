"""GET /api/v1/dispatch-tasks/{id}/session -- resolve a dispatch-task
reference to the session that worked (or last worked) it.

*resolve-by-any-origin-reference* (``visions/plugins/agent-bridge``,
``agent-dispatch-session-worktree-history`` Phase 2): a caller holding only
an agent-dispatch task id -- not a session id directly -- resolves through
this route to the SAME answer a caller with the session id would get from
``GET /api/v1/sessions/{id}``, through the same any-session-any-registered-
worktree resolver.

**Phase 2.5a (#3389):** this route no longer imports an
``agent_dispatch_client.py`` HTTP client or calls agent-dispatch's
coordinator directly -- that was agent-bridge (the *lower* tier) calling
**upward** into agent-dispatch (a *higher* tier), which
``docs/patterns/a-la-carte-independence.md``'s plugin-stack layering rule
forbids. agent-dispatch is now a namespace **provider** (the same
provider-manifest sub-pattern already proven for agent-codespaces/
agent-containers): it registers a ``dispatch`` namespace into agent-bridge's
own ``providers.d`` registry and answers ``namespace-resolve <task_id>``
over the same process-boundary contract every other provider already
drives through. The task's raw record + attachment history come back as
opaque passthrough data on the resolved ``SpawnTarget.venue`` (never parsed
or interpreted by agent-bridge as agent-dispatch's private wire format,
only handed to :mod:`agent_bridge.dispatch_task_resolution`'s pure ranking
function, unchanged since before this phase).

Resolution order:

1. Resolve ``dispatch:{task_id}`` through the registered namespace resolver
   (agent-dispatch's own CLI, invoked over a process boundary) -- a
   not-found task maps to ``KeyError`` (404), an unbound/cross-machine task
   or a coordinator failure maps to ``ValueError`` (degrades to the same
   404 as "nothing resolved", never a hard error -- see point 4).
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

from fastapi import APIRouter, HTTPException, Request

from ..cold_store_views import cold_store_session_info, live_registration_to_session_info
from ..dispatch_task_resolution import candidate_session_ids, task_worktree_id
from ..models import SessionInfo
from ..session_manager import SessionManager
from .sessions import _session_info
from .worktrees import _latest_session_for_worktree

log = logging.getLogger("agent-bridge")

router = APIRouter(prefix="/api/v1/dispatch-tasks", tags=["dispatch-tasks"])

_NAMESPACE = "dispatch"


@router.get("/{task_id}/session", response_model=SessionInfo)
async def get_dispatch_task_session(task_id: str, request: Request) -> SessionInfo:
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is None:
        raise HTTPException(
            status_code=503, detail="agent-bridge agent registry is not available",
        )
    resolver.refresh_provider_resolvers()
    if _NAMESPACE not in resolver.namespace_resolvers:
        raise HTTPException(
            status_code=503,
            detail="no agent-dispatch coordinator is registered as a "
            "`dispatch:` namespace provider",
        )

    try:
        spawn_target = await resolver.resolve_async(f"{_NAMESPACE}:{task_id}")
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"dispatch task {task_id} not found",
        ) from None
    except ValueError as exc:
        # Bad-state (not yet bound to a worktree, or cross-machine) -- a
        # legitimate, expected outcome for a caller that must degrade
        # gracefully, not a hard error. The exception text can carry a raw
        # coordinator error, so keep it out of the client-facing response
        # (still captured via exc_info for server-side diagnosis).
        log.info(
            "dispatch task %s: not resolvable via `dispatch:` namespace (%s)",
            task_id, exc,
        )
        raise HTTPException(
            status_code=404,
            detail=f"no resolvable session for dispatch task {task_id}",
        ) from None

    venue = spawn_target.venue or {}
    task = venue.get("task")
    attachments = venue.get("attachments")
    if not isinstance(task, dict):
        log.warning(
            "dispatch task %s: `dispatch:` namespace resolver returned no "
            "usable task record", task_id,
        )
        raise HTTPException(
            status_code=502,
            detail="agent-dispatch coordinator returned an unexpected response",
        )
    if not isinstance(attachments, list):
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

    worktree_id = task_worktree_id(task) or spawn_target.worktree_id
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
