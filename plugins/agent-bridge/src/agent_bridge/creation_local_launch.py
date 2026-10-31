"""Selected original creation across the existing local launch and turn owners."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import time
from typing import TYPE_CHECKING, Any

from .acquisition_contract import AcquisitionRefused
from .acquisition_custody import decode
from .acquisition_executor import executor
from .acquisition_store import AcquisitionStore
from .creation_custody import CreationJournal
from .local_host_transport import LocalHostTransport
from .local_launch_owner import require_local
from .runtime_custody import RuntimeCustodyError
from .transport import SpawnTarget

if TYPE_CHECKING:
    from .creation_allocation import CreationFrontend
    from .session_manager import Session

_OPTIONS = {
    "agent_name", "caller_id", "caller_session_id", "mcp_servers", "copilot_args",
    "env_overrides", "caller_owner_ref", "model", "effort",
}


async def start(
    frontend: CreationFrontend, target: SpawnTarget, *, key: str,
    prompt: str | None, **options: Any,
) -> tuple[Session, dict]:
    from . import workspace_creation as workspace
    from .creation_allocation import creation_request
    from .hosting_policy import require_target_hosting

    require_local(target)
    if options.keys() - _OPTIONS or target.native_resume_id is not None:
        raise RuntimeCustodyError("Original creation supports only new local native launch options")
    frontend.require_current(effect=False)
    request = creation_request(target, key, prompt, options)
    manager, db = frontend.manager, frontend.db
    journal = CreationJournal(db)
    with executor(db, f"creation:{request.key_digest}"):
        retained = db.execute_read(
            "SELECT 1 FROM workspace_embodiments WHERE key_digest=?", (request.key_digest,),
        )
        if retained:
            prior = journal.read(request)
            if prior["runtime_custody_json"] != frontend.admission.document():
                raise RuntimeCustodyError("Original creation cannot replace its frontend admission")
            if prior["state"] == "complete":
                journal.complete_launch(request, journal.read_launch(request)["handoff"]["id"])
                session = manager.get_session(prior["session_id"])
                saved = decode(prior["worktree_json"])
                if (
                    session is None or session.acp_session_id != prior["native_session_id"]
                    or session.target.project != target.project
                    or session.target.worktree_id != saved["id"] or session.target.cwd != saved["path"]
                ):
                    raise AcquisitionRefused("creation_pending", "original creation session is not retained here")
                require_local(session.target)
                return session, {
                    "version": 1, "status": "complete", "worktree": saved,
                    "native_session_id": prior["native_session_id"], "turn_index": prior["turn_index"],
                    "replayed": True,
                }
    # Reconstruct from the serialized input, not mutable request-owned containers.
    target = SpawnTarget.from_json(target.to_json())
    row = await frontend.allocate(target, key=key, prompt=prompt, **options)
    facts = decode(request.facts_json)
    target = SpawnTarget.from_json(json.dumps(facts["target"]))
    options, prompt = facts["options"], facts["prompt"]
    target.env = {**target.env, **(options.get("env_overrides") or {})}
    saved = decode(row["worktree_json"])
    worktree_id, delivery_id = saved["id"], f"creation:{request.key_digest}"
    with executor(db, f"creation:{request.key_digest}"):
        async with workspace._fresh_start_locks.setdefault(worktree_id, asyncio.Lock()):
            with executor(db, worktree_id):
                frontend.require_current()
                current, head = await workspace._workspace(target.project, worktree_id, expected=saved)
                if not workspace._sessionless(manager, current, head, worktree_id):
                    raise AcquisitionRefused("creation_history_occupied", "workspace is not positively sessionless")
                target = replace(target, worktree_id=worktree_id, cwd=saved["path"], explicit_cwd=True)
                await require_target_hosting(target, operation="original creation launch")
                frontend.require_current()
                owner = frontend.prepare(request)
                store = AcquisitionStore(db)
                store.gate_workspace(worktree_id, delivery_id, "acp", owner.session_id, None, "agent-bridge")

                async def before_launch() -> None:
                    frontend.require_current()
                    current, head = await workspace._workspace(target.project, worktree_id, expected=saved)
                    frontend.require_current()
                    if not workspace._sessionless(manager, current, head, worktree_id):
                        raise AcquisitionRefused("live_holder", "workspace changed during original preparation")
                    owner.require_current()

                session = await manager.start_session(
                    target, creation_session_id=owner.session_id, acquisition_launch=owner,
                    creation_before_launch=before_launch,
                    **{**options, "caller_id": f"acquisition:{delivery_id}"},
                )
                owner.require_ready()
                if not db.reserve_worktree_ownership(worktree_id, session.session_id, now=time.time()):
                    raise AcquisitionRefused("live_holder", "workspace ownership fence refused original creation")
                current, head = await workspace._workspace(target.project, worktree_id, expected=saved)
                owner.require_ready()
                if head is not None or current.get("session_count") != 0:
                    raise AcquisitionRefused("live_holder", "workspace changed before original binding")

                def before_bind() -> None:
                    owner.require_ready()
                    if not journal.begin_binding(
                        request, frontend.installation, frontend.admission, owner.handoff_id, now=time.time(),
                    ):
                        raise RuntimeCustodyError("Original creation binding remains held; do not replay")
                    owner.require_ready()

                await workspace.worktree_command(
                    ["bind-session", "--session-id", session.acp_session_id, "--worktree-id", worktree_id,
                     "--worktree-dir", saved["path"]], target.project, before_spawn=before_bind,
                )
                _, head = await workspace._workspace(target.project, worktree_id, expected=saved)
                if head != session.acp_session_id:
                    raise RuntimeCustodyError("Original creation binding did not prove its native head")
                journal.report_binding(request, owner.handoff_id, now=time.time())
                owner.require_ready()
                LocalHostTransport.capture(owner, session)
                turn_index = None
                if prompt:
                    async with session._turn_start_lock:
                        _, head = await workspace._workspace(target.project, worktree_id, expected=saved)
                        owner.require_ready()
                        if head != session.acp_session_id or session.turn_count != 0:
                            raise RuntimeCustodyError("Original creation first prompt lost its native head or turn")
                        journal.begin_delivery(
                            request, frontend.installation, frontend.admission, owner.handoff_id,
                            turn_index=session.turn_count,
                        )
                        turn_index = await manager._submit_prompt_locked(
                            session.session_id, prompt, no_resume=True, delivery_id=delivery_id,
                        )
                journal.complete_launch(request, owner.handoff_id)
                store.release_workspace_gate(worktree_id, delivery_id)
                return session, {
                    "version": 1, "status": "complete", "worktree": saved,
                    "native_session_id": session.acp_session_id, "turn_index": turn_index, "replayed": False,
                }
