"""Verification/event-note/run-waiter client methods for :class:`DispatchClient`."""

from __future__ import annotations

from .coordinator_auth import scoped_control_token


class VerificationClientMixin:
    """Coordinator calls for whole-goal verification and detached run waiters."""

    def verify_submitted(self, task_id: str) -> dict:
        return self._unwrap(self._http.post(f"/tasks/{task_id}/verify-submitted"))

    def append_event_note(self, task_id: str, *, sender: str, note: str) -> dict:
        headers = None
        if self._control_token:
            headers = {
                "Authorization": "Bearer "
                + scoped_control_token(self._control_token, f"event-note:{sender}")
            }
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/event-note",
                json={"sender": sender, "note": note},
                headers=headers,
            )
        )

    def register_run_waiter(
        self,
        task_id: str,
        *,
        pid: int,
        host: str | None,
        start_token: str | None,
        resume_worktree: str,
        command: list[str],
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/run-waiter/register",
                json={
                    "pid": pid,
                    "host": host,
                    "start_token": start_token,
                    "resume_worktree": resume_worktree,
                    "command": command,
                },
            )
        )

    def finish_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        pid: int,
        host: str | None,
        start_token: str | None,
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
