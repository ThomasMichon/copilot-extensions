"""Verification/event-note/run-waiter client methods for :class:`DispatchClient`."""

from __future__ import annotations


class VerificationClientMixin:
    """Coordinator calls for whole-goal verification and detached run waiters."""

    def verify_submitted(self, task_id: str) -> dict:
        return self._unwrap(self._http.post(f"/tasks/{task_id}/verify-submitted"))

    def append_event_note(self, task_id: str, *, sender: str, note: str) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/event-note",
                json={"sender": sender, "note": note},
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
        pid: int,
        host: str | None,
        start_token: str | None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/run-waiter/finish",
                json={
                    "pid": pid,
                    "host": host,
                    "start_token": start_token,
                },
            )
        )
