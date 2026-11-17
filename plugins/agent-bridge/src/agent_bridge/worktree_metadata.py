"""Hydrate an exact discovered native head without starting its execution."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import re
import time
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .acquisition_contract import AcquisitionRefused
from .acquisition_executor import executor

CAPABILITY = "worktree_metadata_hydration"


def _refuse(reason: str, detail: str, *, status: int = 409) -> HTTPException:
    return HTTPException(status, detail={"reason": reason, "detail": detail})


async def hydrate(worktree_id: str, request: Any) -> JSONResponse:
    """Resolve the current owner and persist metadata only, never native effects."""
    from . import retained_worktree_resume
    from .routes import worktrees
    from .session_manager import DaemonDrainingError

    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", worktree_id) is None
        or ".." in worktree_id
    ):
        raise _refuse("invalid_worktree_id", "Expected an exact workspace ID", status=400)
    manager = request.app.state.session_manager
    if manager.is_draining or not getattr(request.app.state, "ready", False):
        raise _refuse("owner_unavailable", "Metadata hydration requires a ready owner", status=503)
    discovered = await worktrees.list_worktrees(request, render=False, refresh=False)
    if discovered["discovery"].get("completeness") != "complete":
        raise _refuse(
            "target_inventory_incomplete", "Discovery cannot exclude another workspace owner", status=503,
        )
    candidates = [
        (owner, entry.path)
        for owner, entries in worktrees.cache_for_app(request.app).get_all().items()
        for entry in entries if entry.id == worktree_id
    ]
    if len(set(candidates)) > 1:
        raise _refuse("ambiguous_workspace", "Several workspace owners publish this handle")
    async with worktrees._fresh_start_locks.setdefault(worktree_id, asyncio.Lock()):
        manager._active_acquisitions = getattr(manager, "_active_acquisitions", 0) + 1
        try:
            retained = await retained_worktree_resume.resume(
                request, worktree_id, "acp", metadata_only=True,
            )
            if retained is not None:
                return retained
            with executor(manager.db, worktree_id):
                return await _hydrate(worktree_id, request)
        except AcquisitionRefused as exc:
            raise _refuse(exc.code, str(exc)) from exc
        except DaemonDrainingError as exc:
            raise _refuse("owner_unavailable", "The metadata owner is draining", status=503) from exc
        except (OSError, ValueError) as exc:
            raise _refuse("metadata_held", "Owner metadata or custody is unavailable") from exc
        finally:
            manager._active_acquisitions -= 1


async def _hydrate(worktree_id: str, request: Any) -> JSONResponse:
    from .acquisition_authority import read_worktree_record
    from .routes import worktrees
    from .routes.sessions import _session_info
    from .worktree_charter import _apply_bound_charter

    manager, resolver = request.app.state.session_manager, request.app.state.resolver
    owner, entry = await getattr(worktrees, "_find_cached_worktree_entry")(
        worktree_id, resolver, cache=worktrees.cache_for_app(request.app),
    )
    if owner is None or entry is None:
        raise _refuse("worktree_unknown", "No discovered workspace has this ID", status=404)
    config = resolver.agents.get(owner)
    if config is None:
        raise _refuse("owner_unavailable", "The discovered workspace owner is unavailable", status=503)

    async def command(args: list[str], _project: str | None) -> dict[str, Any]:
        raw = await worktrees._run_for_agent(owner, config, resolver, args)
        if raw is None:
            raise _refuse("owner_unavailable", "The workspace owner did not complete its metadata read")
        try:
            value = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise _refuse("owner_unavailable", "The workspace owner returned no complete object") from exc
        if not isinstance(value, dict) or value.get("error"):
            raise _refuse("owner_unavailable", "The workspace owner refused this metadata read")
        return value

    async def observe() -> tuple[dict[str, Any], dict[str, Any]]:
        record = await read_worktree_record(worktree_id, config.project, command=command)
        head = await command(["head-session", "--worktree-id", worktree_id, "--json"], config.project)
        if (
            record["path"] != entry.path
            or record["status"] not in {"active", "pushed"}
            or "bound_agent" not in record
            or record["bound_agent"] is not None and not isinstance(record["bound_agent"], str)
            or not isinstance(record.get("creation_nonce"), str) or not record["creation_nonce"]
            or head.get("tracked") is not True or head.get("worktree_id") != worktree_id
            or head.get("state") != "active" or head.get("pending_handoffs")
            or type(head.get("head_revision")) is not int
            or not isinstance(head.get("head_session"), str) or not head["head_session"]
            or record.get("last_session_id") != head["head_session"]
        ):
            raise _refuse("head_unavailable", "No unchanged authoritative workspace head can be hydrated")
        return record, head

    record, head = await observe()
    native = head["head_session"]
    expected_target = resolver.resolve(owner)
    matches = [
        session for session in manager.list_sessions()
        if session.target.worktree_id == worktree_id and session.acp_session_id == native
        and session.target.cwd == entry.path and session.target.project == config.project
        and session.agent_name == owner
        and expected_target.type in {"local", "ssh"}
        and session.target.type == expected_target.type
        and session.target.host == expected_target.host
    ]
    if len(matches) > 1:
        raise _refuse("ambiguous_metadata", "Multiple managed records claim this native workspace")
    if matches:
        session = matches[0]
    else:
        if manager.db.execute_read(
            "SELECT 1 FROM sessions WHERE acp_session_id=? "
            "AND json_extract(target_json,'$.worktree_id')=?",
            (native, worktree_id),
        ):
            raise _refuse("metadata_owner_changed", "The persisted native control requires its current owner")
        if any(
            session.target.worktree_id == worktree_id
            and session.status.value not in {"stopped", "ended", "failed"}
            for session in manager.list_sessions()
        ):
            raise _refuse("live_transport", "Another managed execution retains this workspace")
        gates_present = manager.db.execute_read(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='acquisition_launch_gates'",
        )
        if (
            record.get("execution_leg_live") is not False
            or record.get("execution_leg_opaque") is not False
            or record.get("session_backend_opaque") is not False
            or manager.db.list_fresh_live_sessions(worktree_id, now=time.time())
            or gates_present and manager.db.execute_read(
                "SELECT 1 FROM acquisition_launch_gates WHERE worktree_id=?", (worktree_id,),
            )
        ):
            raise _refuse("live_transport", "The current native transport retains this workspace")
        reservation = manager.db.get_cli_mode_reservation(worktree_id)
        if reservation is not None and reservation["expires_at"] > time.time():
            raise _refuse("live_transport", "A native CLI launch still retains this workspace")
        index = getattr(manager, "_host_index", None)
        if index is None:
            raise _refuse("host_custody", "Host inventory is unavailable")
        inventory = index.locked_inventory()
        if not inventory.storage_complete or any(
            item.session_id == native or item.extra.get("cwd") == entry.path
            for item in inventory.records
        ):
            raise _refuse("host_custody", "Host inventory cannot exclude an existing native owner")
        target = replace(
            expected_target, cwd=entry.path, worktree_id=worktree_id, explicit_cwd=True,
        )
        entry = replace(entry, bound_agent=record["bound_agent"])
        target = _apply_bound_charter(target, resolver, entry, worktree_id)
        if await observe() != (record, head):
            raise _refuse("head_changed", "The native workspace changed during metadata hydration")
        session = manager.import_native_session(target, native, agent_name=owner)
    return JSONResponse({
        **_session_info(session, manager).model_dump(mode="json"),
        "metadata_hydration": {"version": 1, "native_execution": False},
    })
