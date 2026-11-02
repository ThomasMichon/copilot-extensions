"""MCP task recovery surface using the normal client and its authority."""

from __future__ import annotations

from typing import Literal

from .client_task_recovery import RecoveryOperation, WakeOutbox, strict_reset_request


class TaskRecoveryToolsMixin:
    """Composed with DispatchTools' existing short-lived client factory."""

    def task_diagnostics(self, task_id: str) -> dict:
        """Read redacted task recovery evidence and its opaque CAS revision."""
        with self._observation_client_factory() as client:
            return client.task_diagnostics(task_id)

    def recover_task(
        self,
        task_id: str,
        operation: RecoveryOperation,
        expected_revision: str,
        request_id: str,
        actor: str,
        reason: str,
    ) -> dict:
        """Apply one explicit recovery action using coordinator control authority."""
        with self._recovery_client_factory() as client:
            return client.recover_task(
                task_id,
                operation=operation,
                expected_revision=expected_revision,
                request_id=request_id,
                actor=actor,
                reason=reason,
            )

    def detach_agent(
        self,
        task_id: str,
        expected_revision: str,
        request_id: str,
        actor: str,
        reason: str,
    ) -> dict:
        """Detach the exact agent assignment safely; never unpin its target or stop it."""
        return self.recover_task(
            task_id,
            operation="detach_agent",
            expected_revision=expected_revision,
            request_id=request_id,
            actor=actor,
            reason=reason,
        )

    def reset(
        self,
        task_id: str,
        to: Literal["proposed", "queued"] = "proposed",
        reason: str | None = None,
        expected_status: str | None = None,
        expected_revision: str | None = None,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict:
        """Reset to Proposed by default; strict recovery needs all fencing/audit fields.

        Queued always uses strict recovery. Supplying expected_revision,
        request_id or actor also selects strict Proposed recovery.
        """
        request = strict_reset_request(
            to=to,
            expected_revision=expected_revision,
            request_id=request_id,
            actor=actor,
            reason=reason,
            expected_status=expected_status,
        )
        if request is None:
            with self._client_factory() as client:
                return client.reset(task_id, reason=reason, expected_status=expected_status)
        operation, fields = request
        with self._recovery_client_factory() as client:
            return client.recover_task(task_id, operation=operation, **fields)

    def reconcile_wake(
        self,
        task_id: str,
        outbox: WakeOutbox,
        wake_id: str,
        expected_revision: str,
        actor: str,
        reason: str,
    ) -> dict:
        """Observe retained original send evidence without sending or accepting an outcome."""
        with self._recovery_client_factory() as client:
            return client.reconcile_wake(
                task_id,
                outbox=outbox,
                wake_id=wake_id,
                expected_revision=expected_revision,
                actor=actor,
                reason=reason,
            )
