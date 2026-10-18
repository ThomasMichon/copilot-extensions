"""Ordinary resume derives native history from its workspace owner, not a cache."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from .acquisition_contract import AcquisitionRefused
from .acquisition_gates import AcquisitionGateHeld
from .acquisition_executor import executor
from .acquisition_store import AcquisitionStore
from .models import SessionInfo, SessionStatus
from .session_manager import DaemonDrainingError, ProviderTargetRefreshError
from .singleton_anchor import repo_name_from_anchor_key
from .worktree_charter import _apply_bound_charter
from .worktree_owner_operation import (
    OwnerOperationRefused,
    OwnerOperationUnconfirmed,
    run_owner_operation,
)

log = logging.getLogger("agent-bridge")
Mode = Literal["acp", "cli"]
router = APIRouter(tags=["worktrees"])


def _refuse(reason: str, detail: str, *, status: int = 409) -> HTTPException:
    log.warning("Native worktree resume refused (%s): %s", reason, detail)
    return HTTPException(status, detail={"reason": reason, "detail": detail})


@router.post("/api/v1/worktrees/{worktree_id}/resume/{mode}", response_model=SessionInfo)
async def resume_native_worktree(
    worktree_id: str, request: Request, mode: Mode, reclaim: bool = False, strict: bool = False,
) -> SessionInfo:
    from .routes import worktrees as wt

    singleton = repo_name_from_anchor_key(worktree_id) if isinstance(worktree_id, str) else None
    if not singleton and (
        not isinstance(worktree_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", worktree_id) is None
        or ".." in worktree_id
    ):
        raise _refuse("invalid_worktree_id", "Expected one exact owner-record worktree ID", status=400)
    manager = request.app.state.session_manager
    db = request.app.state.db
    resolver = request.app.state.resolver
    if manager.is_draining:
        raise _refuse("draining", "Bridge is draining", status=503)
    async with wt._fresh_start_locks.setdefault(worktree_id, asyncio.Lock()):
        try:
            with executor(db, worktree_id):
                manager._active_acquisitions = getattr(manager, "_active_acquisitions", 0) + 1
                try:
                    if singleton:
                        from .singleton_resume import resume_singleton_anchor

                        return await resume_singleton_anchor(worktree_id, request, mode, strict=strict)
                    return await _resume(worktree_id, request, mode, manager, db, resolver)
                finally:
                    manager._active_acquisitions -= 1
        except AcquisitionRefused as exc:
            raise _refuse(exc.code, str(exc)) from exc
        except AcquisitionGateHeld as exc:
            raise _refuse("launch_gate_held", str(exc)) from exc
        except DaemonDrainingError as exc:
            raise _refuse("draining", str(exc), status=503) from exc
        except ProviderTargetRefreshError as exc:
            raise _refuse("provider_target_unavailable", exc.public_message, status=502) from exc
        except sqlite3.IntegrityError as exc:
            if str(exc) != "acquisition launch fence held":
                raise
            raise _refuse("launch_gate_held", str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise _refuse("native_resume_refused", str(exc)) from exc
        except RuntimeError as exc:
            raise _refuse("native_resume_failed", str(exc), status=502) from exc


async def _resume(
    worktree_id: str, request: Request, mode: Mode,
    manager: Any, db: Any, resolver: Any,
) -> SessionInfo:
    from .routes import worktrees as wt
    from .routes.sessions import _session_info

    owner, entry = await wt._find_cached_worktree_entry(
        worktree_id, resolver, cache=wt.cache_for_app(request.app),
    )
    if owner is None or entry is None or not entry.path:
        raise _refuse("worktree_unknown", "No owning workspace was found", status=404)
    config = resolver.agents.get(owner)
    if config is None:
        raise _refuse("owner_unavailable", "The workspace owner is unavailable", status=503)

    async def command(args: list[str], *, launch: bool = False) -> dict[str, Any]:
        try:
            raw = (
                await run_owner_operation(owner, config, resolver, args)
                if launch else await wt._run_for_agent(owner, config, resolver, args)
            )
        except OwnerOperationUnconfirmed as exc:
            raise _refuse("owner_operation_unconfirmed", str(exc), status=503) from exc
        except OwnerOperationRefused as exc:
            raise _refuse("owner_refused", str(exc), status=502) from exc
        if raw is None:
            raise _refuse(
                "owner_operation_unconfirmed",
                "The owner operation did not complete; native history is preserved",
                status=503,
            )
        try:
            result = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise _refuse("owner_response_invalid", "Owner returned invalid JSON", status=502) from exc
        if not isinstance(result, dict) or result.get("error"):
            raise _refuse("owner_refused", "Owner did not return a successful object", status=502)
        return result

    async def head() -> dict[str, Any]:
        value = await command(["head-session", "--worktree-id", worktree_id, "--json"])
        if value.get("tracked") is not True or value.get("worktree_id") != worktree_id:
            raise _refuse("head_unknown", "The owner did not assert this workspace's head")
        if value.get("state") != "active" or value.get("pending_handoffs"):
            raise _refuse("head_unavailable", "No active native head, or a handoff is pending")
        if not isinstance(value.get("head_session"), str) or not value["head_session"]:
            raise _refuse("head_unavailable", "The owner has no native conversation to resume")
        return value

    initial = await head()
    native = initial["head_session"]
    exact = await command(["record", "--worktree-id", worktree_id, "--json"])
    row = exact.get("worktree")
    if (
        exact.get("ok") is not True or not isinstance(row, dict)
        or row.get("id") != worktree_id or row.get("path") != entry.path
        or not isinstance(row.get("repo"), str) or not row["repo"].strip()
        or not isinstance(row.get("status"), str) or not row["status"].strip()
        or row.get("last_session_id") != native
        or "bound_agent" not in row
        or (row["bound_agent"] is not None and not isinstance(row["bound_agent"], str))
        or row.get("execution_leg_opaque") is not False
        or row.get("session_backend_opaque") is not False
        or row.get("status") in {"finalizing", "finalized", "complete", "completed", "archived", "gone"}
    ):
        raise _refuse("workspace_identity_changed", "Owner did not confirm the active execution workspace")
    entry = replace(entry, bound_agent=row["bound_agent"])
    delivery = f"ordinary-resume:{worktree_id}:{native}:{mode}"
    store = AcquisitionStore(db)
    owner_sessions = await command(["list-sessions", "--worktree", worktree_id, "--json"])
    owner_live = [
        row for row in owner_sessions.get("sessions", [])
        if isinstance(row, dict) and row.get("live") is True
    ]
    owned = [
        session for session in manager.list_sessions()
        if session.target.worktree_id == worktree_id
    ]
    for session in owned:
        if session.status not in {SessionStatus.STOPPED, SessionStatus.ENDED, SessionStatus.FAILED}:
            already_live = await wt._resolve_already_live(manager, worktree_id, session)
            if (
                mode == "acp" and session.acp_session_id == native
                and session.target.cwd == entry.path and session.target.project == config.project
                and session.client is not None and session.client.is_running
                and already_live
            ):
                store.release_workspace_gate(worktree_id, delivery)
                return _session_info(session)
            if session.status != SessionStatus.STOPPED:
                raise _refuse("live_holder", "An owned execution leg still holds this workspace")
    if owner_live:
        if (
            mode == "cli" and len(owner_live) == 1
            and owner_live[0].get("id") == native
            and owner_sessions.get("head_session") == native
        ):
            represented = db.list_fresh_live_sessions(worktree_id, now=time.time())
            if any(row["session_id"] != native for row in represented):
                raise _refuse("live_holder", "A conflicting represented execution holds this workspace")
            registration = next((row for row in represented if row["session_id"] == native), None)
            store.release_workspace_gate(worktree_id, delivery)
            _release_cli_reservation(db, worktree_id, native)
            return _cli_info(native, owner, entry, config, registration)
        retained = [
            session for session in owned
            if session.acp_session_id == native and session.status == SessionStatus.STOPPED
            and session.target.project == config.project and session.target.cwd == entry.path
        ]
        if (
            mode == "acp" and len(retained) == 1 and len(owner_live) == 1
            and owner_live[0].get("id") == native
        ):
            return await _load_acp(manager, db, store, retained[0], worktree_id, native, delivery, head)
        raise _refuse("live_holder", "The workspace owner reports a live execution leg")
    live = db.list_fresh_live_sessions(worktree_id, now=time.time())
    if live:
        if mode == "cli" and len(live) == 1 and live[0]["session_id"] == native:
            store.release_workspace_gate(worktree_id, delivery)
            _release_cli_reservation(db, worktree_id, native)
            return _cli_info(native, owner, entry, config, live[0])
        raise _refuse("live_holder", "A represented execution leg still holds this workspace")

    preflight = await command([
        "embody", "--worktree-id", worktree_id, "--json",
        "--bridge-acquisition", native, "--require-native-admission", "--dry-run",
    ])
    if (
        type(preflight.get("native_admission_version")) is not int
        or preflight["native_admission_version"] != 1
    ):
        raise _refuse("owner_capability_missing", "Owner cannot prove fenced native admission", status=503)
    if (
        preflight.get("ok") is not True or preflight.get("dry_run") is not True
        or preflight.get("worktree_id") != worktree_id
        or preflight.get("work_dir") != entry.path
        or preflight.get("resume_session") != native
    ):
        raise _refuse("native_resume_unproven", "Owner did not prove the exact dormant native head")
    current = await head()
    if (
        current["head_session"] != native
        or current.get("head_revision") != initial.get("head_revision")
    ):
        raise _refuse("head_changed", "The authoritative native head changed during preparation")
    if manager.is_draining:
        raise _refuse("draining", "Bridge drained during preparation", status=503)

    session = None
    if mode == "acp":
        target = replace(
            resolver.resolve(owner), cwd=entry.path, worktree_id=worktree_id,
            explicit_cwd=True,
        )
        target = _apply_bound_charter(target, resolver, entry, worktree_id)
        try:
            session = manager.import_native_session(target, native, agent_name=owner)
        except ValueError as exc:
            raise _refuse("native_import_refused", str(exc)) from exc
    if mode == "acp":
        assert session is not None
        return await _load_acp(manager, db, store, session, worktree_id, native, delivery, head)
    _claim_gate(db, store, worktree_id, delivery, mode, None, native)
    reservation = db.get_cli_mode_reservation(worktree_id)
    if reservation and reservation["expires_at"] > time.time():
        raise _refuse("cli_launch_pending", "The existing CLI launch remains unconfirmed")
    reservation_id = db.create_cli_mode_reservation(worktree_id, now=time.time(), ttl_seconds=300)
    if reservation_id is None:
        raise _refuse("cli_launch_pending", "Another CLI launch holds this workspace")
    launched = await command([
        "embody", "--worktree-id", worktree_id, "--json",
        "--bridge-acquisition", native, "--require-native-admission", "--ensure-mux",
        "--driver", delivery, "--verify-timeout", "30",
    ], launch=True)
    if (
        launched.get("ok") is not True or launched.get("worktree_id") != worktree_id
        or launched.get("work_dir") != entry.path
        or launched.get("resume_session") != native
        or launched.get("mux_verified") is not True
    ):
        raise _refuse("cli_launch_unconfirmed", "Owner did not confirm the native CLI launch")
    deadline = time.monotonic() + 30
    while True:
        actual = await head()
        listed = await command(["list-sessions", "--worktree", worktree_id, "--json"])
        matches = [
            row for row in listed.get("sessions", [])
            if isinstance(row, dict) and row.get("id") == native and row.get("live") is True
        ]
        if actual["head_session"] != native or listed.get("head_session") != native:
            raise _refuse("native_identity_changed", "CLI launch changed the native conversation")
        represented = db.list_fresh_live_sessions(worktree_id, now=time.time())
        registration = next((row for row in represented if row["session_id"] == native), None)
        if (
            any(row.get("live") is True and row.get("id") != native
                for row in listed.get("sessions", []) if isinstance(row, dict))
            or any(row["session_id"] != native for row in represented)
        ):
            raise _refuse("live_holder", "Another execution leg appeared during CLI launch")
        # Reuse the same fresh represented-session evidence accepted before
        # launch; a display-only owner liveness flag must not veto its runtime.
        if len(matches) == 1 or (len(represented) == 1 and registration is not None):
            store.release_workspace_gate(worktree_id, delivery)
            _release_cli_reservation(db, worktree_id, native, owned_id=reservation_id)
            return _cli_info(native, owner, entry, config, registration)
        if time.monotonic() >= deadline:
            raise _refuse("cli_launch_unconfirmed", "The exact native CLI is not yet confirmed live")
        await asyncio.sleep(0.25)


def _release_cli_reservation(
    db: Any, worktree_id: str, native: str, *, owned_id: str | None = None,
) -> None:
    reservation = db.get_cli_mode_reservation(worktree_id)
    if reservation is None:
        return
    if reservation.get("claimed_by_session_id") == native:
        db.release_cli_mode_reservation(worktree_id, reservation_id=reservation["reservation_id"])
    elif owned_id is not None and reservation["reservation_id"] == owned_id:
        db.release_cli_mode_reservation(
            worktree_id, reservation_id=owned_id, unclaimed_only=True,
        )


def _claim_gate(
    db: Any, store: AcquisitionStore, worktree_id: str, delivery: str,
    mode: Mode, session: str | None, native: str,
) -> None:
    existing = db.execute_read(
        "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?", (worktree_id,),
    )
    if existing:
        gate = existing[0]
        if (
            gate["delivery_id"] != delivery or gate["mode"] != mode
            or gate["native_session_id"] != native
            or gate["session_id"] != session
        ):
            raise _refuse("launch_gate_held", "Another operation owns the native launch fence")
    else:
        store.gate_workspace(
            worktree_id, delivery, mode, session, native, delivery,
        )
    # Keep this exact fence on uncertain launch/load outcomes; retries retain
    # the same native identity instead of creating a replacement conversation.


async def _load_acp(
    manager: Any, db: Any, store: AcquisitionStore, session: Any,
    worktree_id: str, native: str, delivery: str,
    head: Callable[[], Awaitable[dict[str, Any]]],
) -> SessionInfo:
    from .routes.sessions import _session_info

    _claim_gate(db, store, worktree_id, delivery, "acp", session.session_id, native)
    if not db.reserve_worktree_ownership(worktree_id, session.session_id, now=time.time()):
        raise _refuse("live_holder", "Could not acquire native workspace ownership")
    session = await manager.resume_session(
        session.session_id, drain=False, allow_recreate=False, owner_native_resume=True,
    )
    if session.acp_session_id != native or session.status not in {SessionStatus.IDLE, SessionStatus.RUNNING}:
        raise _refuse("native_identity_changed", "ACP resume did not preserve the exact native head")
    actual = await head()
    if actual["head_session"] != native:
        raise _refuse("head_changed", "Owner no longer asserts the resumed native conversation")
    store.release_workspace_gate(worktree_id, delivery)
    return _session_info(session)


def _cli_info(
    native: str, owner: str, entry: Any, config: Any, registration: dict[str, Any] | None,
) -> SessionInfo:
    now = datetime.now(timezone.utc)
    turn_state = str(registration.get("turn_state") or "") if registration else ""
    status = {
        "running": SessionStatus.RUNNING, "idle": SessionStatus.IDLE,
    }.get(turn_state, SessionStatus.STARTING)
    return SessionInfo(
        session_id=native, name=f"resumed-{native[:8]}", agent_name=owner,
        acp_session_id=native, durable_session_id=native, target_dir=entry.path,
        target_type="ssh" if config.host else "local", target_host=config.host,
        project=config.project, worktree_id=entry.id, mode="cli",
        read_only=registration is None,
        status=status,
        pid=registration.get("pid") if registration else None,
        created_at=now, updated_at=now,
    )
