"""HTTP MCP recovery bindings to the same owning queue and wake service as REST."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import Context

from .client_task_recovery import RecoveryOperation, WakeOutbox, strict_reset_request
from .coordinator_task_recovery import ReconcileWakeBody, RecoveryBody
from .events import EventBus
from .queue import TaskError, TaskQueue


def register_task_recovery_tools(
    mcp: Any,
    queue: TaskQueue,
    bus: EventBus,
    *,
    control_error: Callable[[Context], dict[str, object] | None],
    legacy_reset: Callable[..., dict],
) -> None:
    """Keep control authorization separate from ordinary MCP read authority."""

    def authorize(ctx: Context) -> dict[str, object] | None:
        error = control_error(ctx)
        if error is None:
            return None
        unavailable = error["reason"] == "control_authority_not_configured"
        return {
            "code": "task_recovery_unavailable" if unavailable else "task_recovery_forbidden",
            "operation": "task_recovery",
            "reason": error["reason"],
            "message": "task recovery requires configured control authority",
            "retryable": False,
        }

    @mcp.tool(name="dispatch_task_diagnostics")
    def task_diagnostics(task_id: str) -> dict:
        """Read redacted task recovery evidence without repair or optional activation."""
        try:
            return queue.task_diagnostics(task_id)
        except TaskError as exc:
            return {"error": str(exc)}

    @mcp.tool(name="dispatch_recover_task")
    def recover_task(
        ctx: Context,
        task_id: str,
        operation: RecoveryOperation,
        expected_revision: str,
        request_id: str,
        actor: str,
        reason: str,
    ) -> dict:
        """Apply one explicit fenced action with coordinator control authority."""
        error = authorize(ctx)
        if error is not None:
            return {"error": error}
        body = RecoveryBody(
            expected_revision=expected_revision,
            request_id=request_id,
            actor=actor,
            reason=reason,
        )
        try:
            result = queue.recover_task(task_id, operation=operation, **body.model_dump())
        except TaskError as exc:
            return {"error": str(exc)}
        if not result["replayed"]:
            bus.publish({"type": "task.recovered", **result})
        return result

    @mcp.tool(name="dispatch_detach_agent")
    def detach_agent(
        ctx: Context,
        task_id: str,
        expected_revision: str,
        request_id: str,
        actor: str,
        reason: str,
    ) -> dict:
        """Detach an exact agent assignment safely, without target unpinning or stopping."""
        return recover_task(
            ctx,
            task_id,
            "detach_agent",
            expected_revision,
            request_id,
            actor,
            reason,
        )

    @mcp.tool(name="dispatch_reset")
    def reset(
        ctx: Context,
        task_id: str,
        to: Literal["proposed", "queued"] = "proposed",
        reason: str | None = None,
        expected_status: str | None = None,
        expected_revision: str | None = None,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict:
        """Keep Proposed reset default; Queued and explicit strict resets need all fields."""
        request = strict_reset_request(
            to=to,
            expected_revision=expected_revision,
            request_id=request_id,
            actor=actor,
            reason=reason,
            expected_status=expected_status,
        )
        if request is None:
            return legacy_reset(task_id, reason=reason, expected_status=expected_status)
        operation, fields = request
        return recover_task(ctx, task_id, operation=operation, **fields)

    @mcp.tool(name="dispatch_reconcile_wake")
    def reconcile_wake(
        ctx: Context,
        task_id: str,
        outbox: WakeOutbox,
        wake_id: str,
        expected_revision: str,
        actor: str,
        reason: str,
    ) -> dict:
        """Observe original send custody through its owner; never accept caller outcomes."""
        error = authorize(ctx)
        if error is not None:
            return {"error": error}
        body = ReconcileWakeBody(
            outbox=outbox,
            wake_id=wake_id,
            expected_revision=expected_revision,
            actor=actor,
            reason=reason,
        )
        from .task_wake_recovery import reconcile_wake as observe_wake

        try:
            result = observe_wake(queue, task_id, **body.model_dump())
        except TaskError as exc:
            return {"error": str(exc)}
        if not result["replayed"]:
            bus.publish({"type": "task.wake_reconciled", **result})
        return result
