"""Disposable receiver process for the retained metadata concurrency control."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from agent_bridge.acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from agent_bridge.creation_worktree_writer import _read
from agent_bridge.db import Database
from agent_bridge.models import RetainedRecoveryRequest
from agent_bridge.retained_metadata_recovery import prepare
from agent_bridge.session_manager import SessionManager


async def run(database: str, request_json: str, writer_json: str) -> dict[str, Any]:
    request = RetainedRecoveryRequest.model_validate_json(request_json)
    requirement = WorktreeWriterRequirement.model_validate_json(writer_json)
    db = Database(database)
    try:
        manager = SessionManager(db, read_only_rehydrate=True)
        manager._host_index = SimpleNamespace(locked_inventory=lambda: SimpleNamespace(
            storage_complete=True, records=[],
        ))
        connect = AsyncMock(side_effect=AssertionError("no native launch"))
        row = _read(db, request.journal_key)
        assert row is not None
        workspace = json.loads(row["worktree_json"])
        instance = {"installation_root": request.bridge_installation, "pid": 42, "generation": 1}
        identity = {**requirement.identity.model_dump(), "runtime_generation": "b" * 32}

        async def command(args: list[str], project: str, **_kwargs: Any) -> dict[str, Any]:
            assert project == workspace["repo"]
            if args[0] == "resource-relationship":
                return {
                    "schema": "copilot-extensions.resource-relationship", "version": 1,
                    "expected_creation_nonce": True, "expected_writer_identity": True,
                    "writer_scope": "executing-process", "retained_session_writes": 1,
                    "retained_native_resume": 1, "writer_identity": identity,
                    "writer_contract": {
                        "schema": "copilot-extensions.worktree-record-writer", "version": 1,
                        "retained_relationships": 1, "creation_nonce": 1,
                    },
                }
            if args[0] == "record":
                return {"ok": True, "version": 1, "worktree": workspace}
            if args[0] == "head-session":
                return {
                    "tracked": True, "state": "active", "active": True, "pending_handoffs": [],
                    "head_session": request.native_session_id, "head_revision": request.head_revision,
                }
            assert args[0] == "embody" and "--dry-run" in args
            return {
                "ok": True, "dry_run": True, "native_admission_version": 1,
                "worktree_id": workspace["id"], "work_dir": workspace["path"],
                "resume_session": request.native_session_id,
            }

        async def hosting(*_args: Any, **_kwargs: Any) -> None:
            return None

        with ExitStack() as patches:
            patches.enter_context(patch.object(manager, "_connect_via_session_host", connect))
            patches.enter_context(patch(
                "agent_bridge.session_lifecycle_cli._agent_worktrees_pinned_selection",
                return_value=(("fixture-current", "compatible"), Path(requirement.identity.installation_root),
                              Path(sys.executable)),
            ))
            patches.enter_context(patch("agent_bridge.acquisition_authority.worktree_command", command))
            patches.enter_context(patch("agent_bridge.workspace_creation.worktree_command", command))
            patches.enter_context(patch("agent_bridge.acquisition_writer_inspection._instance", lambda: instance))
            patches.enter_context(patch("agent_bridge.hosting_policy.require_target_hosting", hosting))
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "start":
                raise ValueError("fixture caller closed before admission")
            try:
                session, boundary = await prepare(
                    manager, row["session_id"], request, requirement, lambda: True,
                )
                connect.assert_not_awaited()
                return {"state": "committed", "session_id": session.session_id, "boundary": boundary}
            except AcquisitionRefused as exc:
                return {"state": "held", "code": exc.code}
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(*sys.argv[1:]))), flush=True)
