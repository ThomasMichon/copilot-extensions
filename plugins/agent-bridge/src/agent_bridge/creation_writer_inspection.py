"""Read-only requalification of an exact session's original keyed writer."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
import json
from typing import Any

from .acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from .acquisition_writer_inspection import _instance
from .creation_worktree_writer import JOURNAL_KEY, _binding, _read, _refuse
from .acquisition_worktree_writer import _qualify

CAPABILITY = "retained_workspace_creation_writer_inspection"
SCHEMA = "copilot-extensions.creation-worktree-writer"


async def inspect_writer(manager: Any, session: Any, ready: Callable[[], bool]) -> dict:
    from .acquisition_authority import read_worktree_record, worktree_command

    before = _instance()
    target = session.target.to_json()
    native = session.acp_session_id
    digest = (session.target.venue or {}).get(JOURNAL_KEY)
    original = _read(manager.db, digest)
    if original is None or not ready() or manager.is_draining:
        raise _refuse("original keyed writer or active Bridge readiness is unavailable")
    scope, writer = _binding(original)
    if (
        session.target.type != "local" or session.target.worktree_id != scope.worktree_id
        or session.target.cwd != scope.worktree_path or session.target.project != scope.project
        or session.target.spawn_command or session.target.container or session.target.codespace
        or session.target.remote_acp
        or original["state"] not in {"allocated", "preparing", "launching", "delivering", "complete", "terminal"}
    ):
        raise _refuse("exact session changed its original keyed workspace")
    if session.session_id == original["session_id"]:
        expected_native = original["native_session_id"]
    else:
        try:
            handoffs = json.loads(original["creation_handoffs_json"] or "{}")
            if not isinstance(handoffs, dict):
                raise ValueError("invalid handoffs")
            matches = [item for item in handoffs.values() if item["successor_id"] == session.session_id]
        except (ValueError, TypeError, KeyError) as exc:
            raise _refuse("exact session lost its original keyed lineage") from exc
        if len(matches) != 1:
            raise _refuse("exact session lost its original keyed lineage")
        expected_native = matches[0].get("native_session_id")
    if not expected_native or expected_native != native:
        raise _refuse("exact native identity is not retained by its keyed creation")
    try:
        support = await _qualify(writer)
        record = await read_worktree_record(
            scope.worktree_id, scope.project, command=partial(worktree_command, owner_prefix=writer.prefix),
        )
    except OSError as exc:
        raise _refuse("original keyed writer is unavailable") from exc
    if (
        record["repo"] != scope.project or record["path"] != scope.worktree_path
        or record["creation_nonce"] != scope.worktree_writer.creation_nonce
        or record["status"] in {"finalizing", "finalized", "archived", "complete", "completed"}
        or session.target.to_json() != target or session.acp_session_id != native
        or _read(manager.db, digest) != original or _instance() != before
        or not ready() or manager.is_draining or manager.get_session(session.session_id) is not session
    ):
        raise _refuse("keyed creation writer owner changed during inspection")
    return {
        "schema": SCHEMA, "version": 1, "bridge_instance": before,
        "key_digest": digest, "session_id": session.session_id, "native_session_id": native,
        "project": scope.project, "worktree_id": scope.worktree_id, "worktree_path": scope.worktree_path,
        "worktree_writer": scope.worktree_writer.model_dump(),
        "writer_scope": support["writer_scope"], "writer_contract": support["writer_contract"],
        "retained_session_writes": support["retained_session_writes"],
        "retained_native_resume": support.get("retained_native_resume"),
        "automatic_recovery_stopped": (
            manager.db.get_session(session.session_id)["cold_recovery_stop_token"] is not None
        ),
    }


async def session_info(
    session_id: str, request: Any, render: Callable, *, worktree_writer: bool = False,
) -> Any:
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from .cold_store_views import cold_store_session_info

    manager = request.app.state.session_manager
    session = manager.get_session(session_id)
    if worktree_writer:
        if session is None or session.session_id != session_id:
            raise HTTPException(409, "retained writer inspection requires an exact managed session")
        try:
            observation = await inspect_writer(
                manager, session, lambda: bool(getattr(request.app.state, "ready", False)),
            )
        except AcquisitionRefused as exc:
            raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc
        return JSONResponse({
            **render(session, manager).model_dump(mode="json"),
            "worktree_writer_observation": observation,
        })
    if session:
        return render(session, manager)
    cold = await manager.fetch_cold_store_session(session_id)
    if cold is not None:
        return cold_store_session_info(cold)
    raise HTTPException(404, detail=f"Session {session_id} not found")


def get_session(client: Any, session_id: str) -> dict:
    """Negotiate optional evidence and reject an older reader's ignored query."""
    from .client import BridgeClientError
    from .client_protocol_floor import protocol_floored_request
    from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION
    from urllib.parse import quote

    send = protocol_floored_request(
        client, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={CAPABILITY: 1},
    )
    result = send("GET", f"/api/v1/sessions/{quote(session_id, safe='')}?worktree_writer=true") or {}
    observed = result.get("worktree_writer_observation")
    if not (
        isinstance(observed, dict) and observed.get("schema") == SCHEMA
        and type(observed.get("version")) is int and observed["version"] == 1
        and observed.get("session_id") == session_id == result.get("session_id")
        and observed.get("writer_scope") == "executing-process"
        and type(observed.get("retained_session_writes")) is int
        and observed["retained_session_writes"] == 1
    ):
        raise BridgeClientError(502, "original keyed creation writer observation is unconfirmed")
    try:
        WorktreeWriterRequirement.model_validate(observed.get("worktree_writer"))
    except ValueError as exc:
        raise BridgeClientError(502, "original keyed creation writer requirement is unconfirmed") from exc
    return result
