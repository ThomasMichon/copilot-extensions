"""Opening/Refresh snapshot loading for the current-worktree Companion."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import companion_client as cc
from . import companion_head_observation as head
from . import engine_client as ec
from . import handoff_client as hc


@dataclass
class CompanionData:
    """Facts loaded for one selected view, without a persistent Companion ledger."""

    worktree: dict = field(default_factory=dict)
    sessions: list[dict] = field(default_factory=list)
    pending_handoff: dict | None = None
    error: str | None = None
    bundle: dict = field(default_factory=dict)
    bundle_error: str | None = None
    sessions_error: str | None = None
    head_observation: dict | None = None
    head_observation_error: str | None = "not-loaded"
    session_observation: dict | None = None
    observation_error: str | None = "not-loaded"
    attention: dict | None = None
    attention_error: str | None = "not-loaded"
    agenda: dict | None = None
    agenda_error: str | None = "not-loaded"
    agenda_pending: bool = False


def load_current_worktree(cwd: str | None = None, *, runner=None) -> CompanionData:
    """Load status, lineage and optional comparison through the selected engine."""
    target = cwd if cwd is not None else os.getcwd()
    try:
        payload = ec.current_worktree_status(path=target, runner=runner)
    except ec.EngineError as exc:
        return CompanionData(error=str(exc))
    if not isinstance(payload, dict):
        return CompanionData(error="No current worktree status is available.")
    if payload.get("error"):
        return CompanionData(error=str(payload["error"]))
    data = CompanionData(worktree=payload)
    worktree_id = payload.get("id")
    if worktree_id:
        try:
            engine_command = ec.engine_base_command()
            if engine_command is None:
                raise ec.EngineError("The selected engine is unavailable.")
            snapshot = ec.list_worktree_session_snapshot(
                None, worktree_id, runner=runner, engine_command=engine_command
            )
            rows = snapshot.get("sessions")
            data.sessions = (
                [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            )
            data.head_observation, data.head_observation_error = head.read_observation(
                payload.get("repo"),
                worktree_id,
                snapshot,
                runner=runner,
                engine_command=engine_command,
            )
        except ec.EngineError as exc:
            data.sessions_error = str(exc)
        try:
            data.bundle = cc.worktree_status_bundle(None, worktree_id, runner=runner)
        except ec.EngineError as exc:
            data.bundle_error = str(exc)
    worktree_path = payload.get("path")
    if worktree_path:
        try:
            data.pending_handoff = hc.pending_handoff(None, worktree_path, runner=runner)
        except ec.EngineError:
            pass
    return data
