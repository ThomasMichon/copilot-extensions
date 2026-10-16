"""Keep fixed-workspace singleton initialization separate from worktree import."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import replace
from typing import Any

from .acquisition_store import AcquisitionStore
from .models import SessionInfo, SessionStatus
from .singleton_anchor import find_singleton_repo, repo_name_from_anchor_key


async def resume_singleton_anchor(
    anchor: str, request: Any, mode: str, *, strict: bool,
) -> SessionInfo:
    from .routes import worktrees as wt
    from .routes.sessions import _session_info
    from .worktree_native_resume import _refuse

    name = repo_name_from_anchor_key(anchor)
    repo = await asyncio.to_thread(find_singleton_repo, name) if name else None
    if repo is None:
        raise _refuse("singleton_unknown", "No registered singleton owns this anchor", status=404)
    if mode != "acp":
        raise _refuse("singleton_mode_unsupported", "This anchor has no native mux owner", status=503)
    manager, db = request.app.state.session_manager, request.app.state.db
    if db.list_fresh_live_sessions(anchor, now=time.time()):
        raise _refuse("live_holder", "A represented session holds this singleton")
    sessions = [
        session for session in manager.list_sessions()
        if session.target.worktree_id == anchor and session.status != SessionStatus.ENDED
    ]
    active = [
        session for session in sessions
        if session.status in {SessionStatus.IDLE, SessionStatus.RUNNING}
    ]
    heads = active or [
        session for session in sessions
        if not (db.get_session(session.session_id) or {}).get("successor_id")
    ]
    store = AcquisitionStore(db)
    delivery = f"singleton-anchor:{anchor}"
    expected_native = None
    if heads:
        if len(heads) != 1:
            raise _refuse("singleton_head_ambiguous", "Singleton conversation identity is ambiguous")
        session = heads[0]
        if session.target.cwd != repo.path or not session.acp_session_id:
            raise _refuse("singleton_binding_unconfirmed", "Singleton conversation binding is incomplete")
        expected_native = session.acp_session_id
        held = db.execute_read(
            "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?", (anchor,),
        )
        if held:
            gate = held[0]
            if (
                gate["delivery_id"] != delivery or gate["mode"] != "acp"
                or gate["session_id"] != session.session_id
                or gate["native_session_id"] not in {None, expected_native}
            ):
                raise _refuse("launch_gate_held", "Another operation owns this singleton")
        else:
            store.gate_workspace(
                anchor, delivery, "acp", session.session_id, expected_native, delivery,
            )
        if not db.reserve_worktree_ownership(anchor, session.session_id, now=time.time()):
            raise _refuse("live_holder", "Singleton ownership changed before native load")
        ready = (
            await wt._resolve_already_live(manager, anchor, session)
            and session.client is not None and session.client.is_running
        )
        if not ready:
            if session.status != SessionStatus.STOPPED:
                raise _refuse("singleton_execution_unconfirmed", "Singleton execution is not confirmed stopped")
            session = await manager.resume_session(session.session_id, drain=False, allow_recreate=False)
    else:
        known = db.execute_read(
            "SELECT id FROM sessions WHERE json_extract(target_json, '$.worktree_id')=?",
            (anchor,),
        )
        if strict or sessions or known:
            raise _refuse("singleton_head_unavailable", "No exact singleton conversation can be resumed")
        owner, entry = await wt._find_cached_worktree_entry(anchor, request.app.state.resolver)
        if owner is None or entry is None or entry.path != repo.path:
            raise _refuse("singleton_owner_unavailable", "Singleton launch owner is unavailable", status=503)
        target = replace(
            request.app.state.resolver.resolve(owner),
            cwd=repo.path, worktree_id=anchor, explicit_cwd=True,
        )
        session_id = str(uuid.uuid4())[:12]
        store.gate_workspace(anchor, delivery, "acp", session_id, None, delivery)
        session = await manager.start_session(
            target, agent_name=owner, caller_id=f"acquisition:{delivery}",
            creation_session_id=session_id,
        )
    if (
        session.status not in {SessionStatus.IDLE, SessionStatus.RUNNING} or not session.acp_session_id
        or (expected_native is not None and session.acp_session_id != expected_native)
    ):
        raise _refuse("singleton_native_unconfirmed", "Singleton native initialization or load is unconfirmed", status=502)
    if not db.reserve_worktree_ownership(anchor, session.session_id, now=time.time()):
        raise _refuse("live_holder", "Singleton ownership changed during native acquisition")
    store.release_workspace_gate(anchor, delivery)
    return _session_info(session)
