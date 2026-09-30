"""Verification/event-note/run-waiter client methods for :class:`DispatchClient`."""

from __future__ import annotations

from .coordinator_auth import scoped_control_token


class VerificationClientMixin:
    """Coordinator calls for whole-goal verification and detached run waiters."""

    def verify_submitted(self, task_id: str, *, evaluator_ref: str | None = None) -> dict:
        payload = None if evaluator_ref is None else {"evaluator_ref": evaluator_ref}
        return self._unwrap(self._http.post(f"/tasks/{task_id}/verify-submitted", json=payload))

    def append_event_note(self, task_id: str, *, sender: str, note: str) -> dict:
        headers = dict(self._control_headers())
        if self._control_token:
            headers["X-Agent-Dispatch-Sender-Proof"] = scoped_control_token(
                self._control_token,
                f"event-note:{sender}",
            )
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/event-note",
                json={"sender": sender, "note": note},
                headers=headers,
            )
        )

    def prepare_run_waiter(
        self,
        task_id: str,
        *,
        worker_id: str,
        reason: str,
        resume_worktree: str,
        command: list[str],
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/run-waiter/register",
                json={
                    "worker_id": worker_id,
                    "reason": reason,
                    "resume_worktree": resume_worktree,
                    "command": command,
                },
            )
        )

    def arm_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        pid: int,
        host: str,
        start_token: str,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/run-waiter/arm",
                json={
                    "generation": generation,
                    "pid": pid,
                    "host": host,
                    "start_token": start_token,
                },
            )
        )

    def finish_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        pid: int,
        host: str,
        start_token: str,
        message: str,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/run-waiter/finish",
                json={
                    "generation": generation,
                    "pid": pid,
                    "host": host,
                    "start_token": start_token,
                    "message": message,
                },
            )
        )

    def abort_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        message: str,
        wake: bool = True,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/run-waiter/abort",
                json={
                    "generation": generation,
                    "message": message,
                    "wake": wake,
                },
            )
        )
