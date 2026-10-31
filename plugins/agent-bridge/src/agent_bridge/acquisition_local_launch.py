"""ACP acquisition adapter over the existing start, resume and binding owners."""

from __future__ import annotations

from dataclasses import replace
import time
from typing import TYPE_CHECKING, Any
import uuid

from .acquisition_contract import AcquisitionRefused
from .models import SessionStatus

if TYPE_CHECKING:
    from .acquisition_contract import AcquisitionRequest
    from .acquisition_launch_owner import AcquisitionFrontend
    from .acquisition_policy import AcquisitionDecision
    from .acquisition_runtime import AcquisitionRuntime


async def launch(
    runtime: AcquisitionRuntime, req: AcquisitionRequest, decision: AcquisitionDecision,
    row: dict[str, Any], target: Any, request_env: dict[str, str],
    *, custody: AcquisitionFrontend | None = None,
) -> tuple[str, str]:
    selected = None
    if custody is not None:
        from .acquisition_launch_owner import require_local
        from .runtime_custody import RuntimeCustodyError

        require_local(target)
        if decision.acquired == "reused":
            raise RuntimeCustodyError("Live conversation acquisition custody is not supported")
        selected = custody.prepare(
            req,
            session_id=decision.conversation.session_id if decision.conversation else str(uuid.uuid4())[:12],
            native_session_id=decision.conversation.native_session_id if decision.conversation else None,
        )
    if decision.conversation:
        session = runtime.manager.get_session(decision.conversation.session_id)
        if decision.acquired == "resumed":
            session = await runtime.manager.resume_session(
                decision.conversation.session_id, drain=False, allow_recreate=False,
                **({"acquisition_launch": selected} if selected is not None else {}),
            )
    else:
        target = replace(target, cwd=row["path"], worktree_id=req.worktree_id, explicit_cwd=True)
        session = await runtime.manager.start_session(
            target,
            agent_name=req.target,
            caller_id=f"acquisition:{req.delivery_id}",
            caller_owner_ref=req.caller_owner_ref,
            caller_session_id=req.caller_session_id,
            copilot_args=["--agent", req.charter] if req.charter else None,
            model=req.model,
            effort=req.effort,
            env_overrides=request_env or None,
            **({
                "creation_session_id": selected.session_id, "acquisition_launch": selected,
            } if selected is not None else {}),
        )
    if not session or not session.acp_session_id or session.status != SessionStatus.IDLE:
        raise AcquisitionRefused("native_start_unproven", "ACP launch did not prove an idle native conversation")
    if selected is not None:
        from .acquisition_host_transport import AcquisitionHostTransport

        AcquisitionHostTransport.capture(selected, session)
    context = runtime._prepared_context(
        session.target, row, session.acp_session_id, require_prepared=decision.conversation is None,
    )
    _token, stage, data = runtime.store.recovery(req)
    runtime.store.stage(req, stage, **{
        **data, "prepared_context": context,
        "session_id": session.session_id, "native_session_id": session.acp_session_id,
    })
    if not runtime.db.reserve_worktree_ownership(req.worktree_id, session.session_id, now=time.time()):
        raise AcquisitionRefused("live_holder", "ACP ownership fence was not acquired")
    # Preserve the runtime's existing injectable owner command seam.
    from . import acquisition_runtime

    await acquisition_runtime.worktree_command(
        ["bind-session", "--session-id", session.acp_session_id, "--worktree-id", req.worktree_id,
         "--worktree-dir", row["path"]], req.project,
    )
    return session.session_id, session.acp_session_id
