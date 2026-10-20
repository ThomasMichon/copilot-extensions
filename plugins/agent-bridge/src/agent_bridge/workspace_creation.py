"""Caller-keyed standalone allocation and embodiment, without dispatch authority."""

from __future__ import annotations

import hashlib
import asyncio
import json
import logging
import time
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from .acquisition_authority import read_worktree_record, worktree_command
from .acquisition_contract import AcquisitionRefused
from .acquisition_executor import executor
from .acquisition_store import AcquisitionStore
from .models import SessionStatus, StartSessionRequest, StartSessionResponse
from .preference_models import PreferenceSource
from .routes.worktrees import _fresh_start_locks
from .worktree_head import parse_head_payload
from .db import Database
from .transport import SpawnTarget

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager


def creation_fact_options(options: dict[str, Any]) -> dict[str, Any]:
    facts = {name: value for name, value in options.items()
             if name not in {"caller_id", "caller_session_id"}}
    owner_ref = facts.get("caller_owner_ref")
    if isinstance(owner_ref, str):
        owner_scope = owner_ref.partition("#")[0]
        owner_parts = owner_scope.split("/")
        if len(owner_parts) == 3 and all(owner_parts):
            facts["caller_owner_ref"] = owner_scope
    return facts


async def start_request(
    request: StartSessionRequest, manager: SessionManager, target: SpawnTarget, *,
    agent_name: str | None, env: dict[str, str], source: PreferenceSource,
) -> StartSessionResponse | None:
    """Adapt keyed requests without changing the legacy unkeyed start path."""
    if request.creation_key is None:
        if request.provider_instance:
            raise HTTPException(status_code=422, detail="provider_instance requires creation_key")
        if request.creation_prompt is not None:
            raise HTTPException(status_code=422, detail="creation_prompt requires creation_key")
        return None
    if request.parity_fault:
        raise HTTPException(status_code=422, detail="keyed creation cannot inject a parity fault")
    manager._active_acquisitions = getattr(manager, "_active_acquisitions", 0) + 1
    try:
        session, creation = await start(
            manager, target, key=request.creation_key, prompt=request.creation_prompt,
            provider_instance=request.provider_instance,
            agent_name=agent_name, caller_id=request.caller_id,
            caller_session_id=request.caller_session_id, mcp_servers=request.mcp_servers,
            copilot_args=request.copilot_args, env_overrides=env or None,
            caller_owner_ref=request.caller_owner_ref, model=request.model, effort=request.effort,
        )
    except AcquisitionRefused as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc), "effect": exc.effect,
        }) from exc
    finally:
        manager._active_acquisitions -= 1
    return StartSessionResponse(
        session_id=session.session_id, name=session.name, status=session.status,
        preference_source=source, caller_session_id=session.caller_session_id, creation=creation,
    )


async def _workspace(
    project: str, worktree_id: str, *, expected: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    row = await read_worktree_record(worktree_id, project, command=worktree_command)
    if expected is not None and any(
        row.get(field) != expected.get(field) for field in ("id", "repo", "path", "creation_nonce")
    ):
        raise AcquisitionRefused("creation_conflict", "original workspace identity changed")
    if row.get("status") == "finalizing":
        raise AcquisitionRefused("transition_pending", "original workspace finalization is in progress")
    if row.get("status") in {"finalized", "archived", "complete", "completed"}:
        raise AcquisitionRefused("creation_terminal", "original workspace is terminal")
    head = parse_head_payload(json.dumps(await worktree_command(
        ["head-session", "--worktree-id", worktree_id, "--json"], project,
    )))
    if not head.tracked:
        raise AcquisitionRefused("head_unknown", "workspace head authority is unavailable")
    if head.occupied and head.state != "active":
        raise AcquisitionRefused("transition_pending", "workspace transition is pending")
    return row, head.head_session


def _update(db: Database, digest: str, **fields: Any) -> None:
    allowed = {"state", "worktree_json", "native_session_id", "turn_index"}
    if not fields or not fields.keys() <= allowed:
        raise ValueError("invalid embodiment journal update")
    db.execute_write(
        "UPDATE workspace_embodiments SET "
        + ", ".join(f"{name}=?" for name in fields)
        + " WHERE key_digest=?",
        (*fields.values(), digest),
    )


def _sessionless(
    manager: SessionManager, row: dict[str, Any], head: str | None, worktree_id: str,
) -> bool:
    return not (
        row.get("status") != "active"
        or row.get("session_count") != 0 or row.get("last_session_id") or head
        or row.get("execution_leg") is not None or row.get("execution_leg_live")
        or row.get("execution_leg_opaque") or row.get("session_backend") is not None
        or row.get("session_backend_opaque") or row.get("sessions_indexed") is not True
        or manager.db.list_fresh_live_sessions(worktree_id, now=time.time())
        or manager.db.get_worktree_ownership(worktree_id)
        or any(item.target.worktree_id == worktree_id for item in manager.list_sessions())
    )


async def start(
    manager: SessionManager, target: SpawnTarget, *, key: str,
    prompt: str | None = None, provider_instance: bool = False, **options: Any,
) -> tuple[Session, dict[str, Any]]:
    """Persist intent before effects; replay never allocates or starts twice."""
    if provider_instance:
        from .provider_creation import start as start_provider

        return await start_provider(manager, target, key=key, prompt=prompt, **options)
    if not isinstance(key, str) or not key.strip() or len(key) > 1024:
        raise AcquisitionRefused("creation_key_invalid", "creation key must be non-empty and at most 1024 characters")
    if (
        target.type != "local" or not target.project or target.explicit_cwd
        or target.codespace or target.container or target.spawn_command
    ):
        raise AcquisitionRefused(
            "creation_venue_unsupported", "keyed creation requires a local worktree-backed target",
            effect="not_started",
        )
    if manager.is_draining:
        raise AcquisitionRefused("creation_draining", "bridge is draining", effect="not_started")
    db = manager.db
    store = AcquisitionStore(db)
    digest = hashlib.sha256(json.dumps([target.project, key]).encode()).hexdigest()
    facts = {
        "target": json.loads(target.to_json()), "prompt": prompt,
        "options": creation_fact_options(options),
    }
    facts_digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    with executor(db, f"creation:{digest}"):
        db.execute_write(
            "INSERT OR IGNORE INTO workspace_embodiments "
            "(key_digest,facts_digest,state,session_id) VALUES (?,?,'allocating',?)",
            (digest, facts_digest, str(uuid.uuid4())[:12]),
        )
        operation = dict(db.execute_read(
            "SELECT * FROM workspace_embodiments WHERE key_digest=?", (digest,),
        )[0])
        if operation["facts_digest"] != facts_digest:
            raise AcquisitionRefused("creation_conflict", "creation key is bound to different embodiment facts")
        saved = json.loads(operation["worktree_json"]) if operation["worktree_json"] else None
        if operation["state"] == "terminal" and saved is None:
            raise AcquisitionRefused("creation_terminal", "terminal embodiment has no retained allocation")
        if saved is None:
            if target.worktree_id:
                saved, _ = await _workspace(target.project, target.worktree_id)
            else:
                args = ["create", "--creation-key", key, "--no-owner",
                        "--interface", "acp", "--origin", "delegate", "--json"]
                owner_ref = options.get("caller_owner_ref")
                if owner_ref:
                    args += ["--owner-ref", owner_ref]
                allocation = await worktree_command(args, target.project)
                creation = allocation.get("creation")
                saved = allocation.get("worktree")
                if (
                    not isinstance(creation, dict) or creation.get("version") != 1
                    or creation.get("status") != "complete" or not isinstance(saved, dict)
                ):
                    raise AcquisitionRefused("creation_unproven", "ground owner did not prove keyed allocation")
        if (
            not isinstance(saved.get("id"), str) or not saved["id"]
            or saved.get("repo") != target.project or not saved.get("path")
            or not isinstance(saved.get("creation_nonce"), str) or not saved["creation_nonce"].strip()
        ):
            raise AcquisitionRefused("creation_unproven", "allocation lacks authoritative owner identity")
        if operation["worktree_json"] is None:
            _update(db, digest, worktree_json=json.dumps(saved), state="allocated")
            operation["state"] = "allocated"
        worktree_id = saved["id"]
        delivery_id = f"creation:{digest}"
        async with _fresh_start_locks.setdefault(worktree_id, asyncio.Lock()):
            with executor(db, worktree_id):
                try:
                    row, head = await _workspace(target.project, worktree_id, expected=saved)
                except AcquisitionRefused as exc:
                    if exc.code == "creation_terminal":
                        _update(db, digest, state="terminal")
                    raise
                if any(row.get(field) != saved.get(field) for field in ("id", "repo", "path", "creation_nonce")):
                    raise AcquisitionRefused("creation_conflict", "original workspace identity changed")
                if operation["state"] == "terminal":
                    if (
                        operation["native_session_id"] is not None
                        or operation["turn_index"] is not None
                        or db.get_session(operation["session_id"]) is not None
                        or db.execute_read(
                            "SELECT 1 FROM turns WHERE session_id=? LIMIT 1",
                            (operation["session_id"],),
                        )
                        or db.execute_read(
                            "SELECT 1 FROM acquisition_launch_gates WHERE worktree_id=? LIMIT 1",
                            (worktree_id,),
                        )
                        or not _sessionless(manager, row, head, worktree_id)
                    ):
                        raise AcquisitionRefused(
                            "creation_terminal", "terminal embodiment has native or launch history",
                        )
                    logging.getLogger(__name__).warning(
                        "Reconciling pre-launch terminal embodiment for retained workspace %s",
                        worktree_id,
                    )
                    _update(db, digest, state="allocated")
                    operation["state"] = "allocated"
                session = manager.get_session(operation["session_id"])
                if session is not None and session.status == SessionStatus.ENDED:
                    _update(db, digest, state="terminal")
                    raise AcquisitionRefused("creation_terminal", "original native conversation was ended")
                if operation["state"] == "allocated":
                    if not _sessionless(manager, row, head, worktree_id):
                        raise AcquisitionRefused("creation_history_occupied", "workspace is not positively sessionless")
                    store.gate_workspace(
                        worktree_id, delivery_id, "acp", operation["session_id"], None, "agent-bridge",
                    )
                    try:
                        current, current_head = await _workspace(target.project, worktree_id, expected=saved)
                        if any(
                            current.get(field) != saved.get(field)
                            for field in ("id", "repo", "path", "creation_nonce")
                        ):
                            raise AcquisitionRefused("creation_conflict", "workspace identity changed before native start")
                        if not _sessionless(manager, current, current_head, worktree_id):
                            raise AcquisitionRefused("live_holder", "workspace changed before native start")
                    except AcquisitionRefused:
                        store.release_workspace_gate(worktree_id, delivery_id)
                        raise
                    _update(db, digest, state="launching")
                    target = replace(target, worktree_id=worktree_id, cwd=saved["path"], explicit_cwd=True)
                    from .session_manager import DaemonDrainingError

                    try:
                        session = await manager.start_session(
                            target, creation_session_id=operation["session_id"],
                            **{**options, "caller_id": f"acquisition:{delivery_id}"},
                        )
                    except DaemonDrainingError as exc:
                        _update(db, digest, state="allocated")
                        store.release_workspace_gate(worktree_id, delivery_id)
                        raise AcquisitionRefused("creation_draining", str(exc), effect="not_started") from exc
                if (
                    session is None or not session.acp_session_id
                    or session.status not in {SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.STOPPED}
                    or session.target.project != target.project
                    or session.target.worktree_id != worktree_id or session.target.cwd != saved["path"]
                    or operation["native_session_id"]
                    and operation["native_session_id"] != session.acp_session_id
                ):
                    raise AcquisitionRefused("creation_pending", "original native start requires reconciliation")
                native = session.acp_session_id
                if head and head != native:
                    raise AcquisitionRefused("foreign_live_holder", "another native head owns the workspace")
                if operation["state"] == "complete" and head != native:
                    _update(db, digest, state="terminal")
                    raise AcquisitionRefused("creation_terminal", "original native head was retired")
                turn_index = operation["turn_index"]
                result = {
                    "version": 1, "status": "complete", "worktree": saved,
                    "native_session_id": native, "turn_index": turn_index,
                    "replayed": operation["state"] == "complete",
                }
                if operation["state"] == "complete":
                    if prompt:
                        admitted = db.execute_read(
                            "SELECT session_id,turn_index,prompt FROM turns WHERE acquisition_delivery_id=?",
                            (delivery_id,),
                        )
                        if (
                            len(admitted) != 1 or admitted[0]["session_id"] != session.session_id
                            or admitted[0]["turn_index"] != turn_index or admitted[0]["prompt"] != prompt
                        ):
                            raise AcquisitionRefused("creation_delivery_unproven", "completed first-turn receipt is missing")
                    elif session.status == SessionStatus.STOPPED and not db.execute_read(
                        "SELECT 1 FROM turns WHERE session_id=? LIMIT 1", (session.session_id,),
                    ):
                        session = await manager.resume_session(
                            session.session_id, drain=False, allow_recreate=False,
                        )
                        if (
                            session.acp_session_id != native or session.target.project != target.project
                            or session.target.worktree_id != worktree_id or session.target.cwd != saved["path"]
                        ):
                            raise AcquisitionRefused("native_identity_changed", "strict creation replay changed native identity")
                    store.release_workspace_gate(worktree_id, delivery_id)
                    return session, result
                gates = db.execute_read(
                    "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?", (worktree_id,),
                )
                if gates and (
                    gates[0]["delivery_id"] != delivery_id or gates[0]["mode"] != "acp"
                    or gates[0]["session_id"] != session.session_id
                ):
                    raise AcquisitionRefused("launch_gate_held", "another acquisition holds this workspace")
                if not db.reserve_worktree_ownership(worktree_id, session.session_id, now=time.time()):
                    raise AcquisitionRefused("live_holder", "workspace ownership fence refused embodiment")
                await worktree_command(
                    ["bind-session", "--session-id", native, "--worktree-id", worktree_id,
                     "--worktree-dir", saved["path"]], target.project,
                )
                _update(db, digest, native_session_id=native)
                if prompt:
                    admitted = db.execute_read(
                        "SELECT 1 FROM turns WHERE acquisition_delivery_id=?", (delivery_id,),
                    )
                    if not admitted and session.status == SessionStatus.STOPPED:
                        session = await manager.resume_session(
                            session.session_id, drain=False, allow_recreate=False,
                        )
                        if (
                            session.acp_session_id != native or session.target.project != target.project
                            or session.target.worktree_id != worktree_id or session.target.cwd != saved["path"]
                        ):
                            raise AcquisitionRefused("native_identity_changed", "strict creation recovery changed native identity")
                    async with session._turn_start_lock:
                        _, current_head = await _workspace(target.project, worktree_id, expected=saved)
                        if current_head != native:
                            raise AcquisitionRefused("head_changed", "native head changed before first-turn admission")
                        turns = db.execute_read(
                            "SELECT session_id,turn_index,prompt FROM turns WHERE acquisition_delivery_id=?",
                            (delivery_id,),
                        )
                        if turns:
                            if len(turns) != 1 or turns[0]["session_id"] != session.session_id or turns[0]["prompt"] != prompt:
                                raise AcquisitionRefused("creation_delivery_conflict", "original prompt admission changed")
                            turn_index = turns[0]["turn_index"]
                        else:
                            if turn_index is not None and turn_index != session.turn_count:
                                raise AcquisitionRefused("creation_pending", "original prompt admission is ambiguous")
                            _update(db, digest, state="delivering", turn_index=session.turn_count)
                            try:
                                turn_index = await manager._submit_prompt_locked(
                                    session.session_id, prompt, no_resume=True, delivery_id=delivery_id,
                                )
                            except ValueError as exc:
                                raise AcquisitionRefused(
                                    "creation_pending", f"native first-turn admission refused: {exc}",
                                ) from exc
                _update(db, digest, state="complete", turn_index=turn_index)
                store.release_workspace_gate(worktree_id, delivery_id)
                result["turn_index"] = turn_index
                return session, result
