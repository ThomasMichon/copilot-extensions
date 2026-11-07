"""Structured answer operations on the existing authenticated bridge API."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .client_worktree_restart import _Base


class AnswerClientMixin(_Base):
    def _mux_question_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        from .client_protocol_floor import protocol_floored_request
        from .mux_questions import CAPABILITY

        return protocol_floored_request(self, 1, capabilities={CAPABILITY: 1})(
            method, path, **kwargs,
        ) or {}

    def mux_questions(self, session_id: str) -> dict[str, Any]:
        """Inspect one local rendered form without granting input authority."""
        return self._mux_question_request(
            "GET", f"/api/v1/live-sessions/{quote(session_id, safe='')}/questions/mux",
        )

    def mux_question_answer(self, session_id: str, inspection_id: str) -> dict[str, Any]:
        """Read the original durable audit without issuing another inspection."""
        return self._mux_question_request(
            "GET", f"/api/v1/live-sessions/{quote(session_id, safe='')}/questions/mux/answers/"
            + quote(inspection_id, safe=""),
        )

    def answer_mux_question(
        self, session_id: str, inspection_id: str, *, sender: str,
        option: int | None = None, text: str | None = None,
    ) -> dict[str, Any]:
        """Submit one inspection exactly once; never retry an uncertain input."""
        return self._mux_question_request(
            "POST", f"/api/v1/live-sessions/{quote(session_id, safe='')}/questions/mux",
            body={"inspection_id": inspection_id, "sender": sender, "option": option, "text": text},
            allow_retry=False, request_timeout=max(self._timeout, 70),
        )

    def _question_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        from .client_protocol_floor import protocol_floored_request
        from .live_questions import CAPABILITY

        return protocol_floored_request(self, 1, capabilities={CAPABILITY: 1})(
            method, path, **kwargs,
        ) or {}

    def live_questions(self, session_id: str) -> dict[str, Any]:
        """Inspect the extension's fresh snapshot, not historical tool arguments."""
        return self._question_request(
            "GET", f"/api/v1/live-sessions/{quote(session_id, safe='')}/questions",
        )

    def answer_live_question(
        self, session_id: str, request_id: str, generation: str,
        content: dict[str, Any], *, family: str, sender: str, action: str = "accept",
        wait_timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Resolve exactly one native question; ambiguous writes are never replayed."""
        return self._question_request(
            "POST", f"/api/v1/live-sessions/{quote(session_id, safe='')}/questions/answers",
            body={
                "request_id": request_id, "generation": generation, "family": family,
                "content": content, "sender": sender, "action": action,
                "wait_timeout": wait_timeout,
            },
            request_timeout=max(self._timeout, wait_timeout + 5.0), allow_retry=False,
        )

    def live_question_answer(self, session_id: str, answer_id: int) -> dict[str, Any]:
        """Read the durable outcome of an admitted answer without resubmitting."""
        return self._question_request(
            "GET",
            f"/api/v1/live-sessions/{quote(session_id, safe='')}/questions/answers/{answer_id}",
        )

    def answer_ask_user(
        self,
        session_id: str,
        tool_call_id: str,
        content: dict[str, Any] | None = None,
        *,
        action: str = "accept",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Answer one live structured elicitation, retaining its correlation."""
        from .client import BridgeClientError
        from .protocol import REMOTE_ACP_PROTOCOL_VERSION

        if request_id is not None and not self.daemon_supports(REMOTE_ACP_PROTOCOL_VERSION):
            raise BridgeClientError(426, "daemon does not support live-token-qualified answers")
        return self._request(
            "POST", f"/api/v1/sessions/{session_id}/ask-user",
            body={
                "tool_call_id": tool_call_id,
                "content": content or {},
                "action": action,
                **({"request_id": request_id} if request_id is not None else {}),
            },
        ) or {}
