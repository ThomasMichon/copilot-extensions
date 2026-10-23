"""Prompt submission and observed admission on the existing bridge API."""

from __future__ import annotations

import http.client
import json
from typing import Any

from .client_worktree_restart import _Base
from .session_status import SessionStatus


class PromptClientMixin(_Base):
    def submit_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        queue: bool = False,
        caller_id: str | None = None,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/turns.

        ``queue=True`` opts into durable send-or-queue: if the session is busy
        the prompt is persisted server-side and delivered FIFO on settle
        (surviving a caller remount / bridge restart) rather than 409'd. The
        response then carries ``queued: true`` with the queue position.
        """
        from .client import BridgeConnectionError
        from .startup_observation import StartupObservationError, current

        payload: dict[str, Any] = {"prompt": prompt}
        if queue:
            payload["queue"] = True
            if caller_id:
                payload["caller_id"] = caller_id
        observation = current()
        if observation is not None:
            # Readiness was already established by the observed resume. A race
            # back to STOPPED must not silently start a second startup attempt.
            payload["no_resume"] = True
            observation.local_phase("prompt-admission")
            failure = None
            try:
                result = self._request(
                    "POST", f"/api/v1/sessions/{session_id}/turns", payload,
                    request_timeout=observation.clock.policy.communication,
                    allow_retry=False,
                )
            except (BridgeConnectionError, OSError, http.client.HTTPException,
                    UnicodeError, json.JSONDecodeError) as exc:
                failure = exc
            else:
                if (
                    isinstance(result, dict)
                    and isinstance(result.get("status"), str)
                    and result["status"] in {status.value for status in SessionStatus}
                    and type(result.get("turn_index")) is int
                    and result["turn_index"] >= 0
                ):
                    return result
            expired_clock = "communication" if isinstance(failure, TimeoutError) else None
            observation.report(
                "observation_lost", boundary="prompt-admission", clock=expired_clock,
            )
            raise StartupObservationError(
                expired_clock, "prompt-admission", observation.clock.elapsed,
                reason="admission_unknown",
            ) from failure
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/turns",
            payload,
            request_timeout=request_timeout,
        ) or {}
