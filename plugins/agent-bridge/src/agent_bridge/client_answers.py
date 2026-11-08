"""Structured answer operations on the existing authenticated bridge API."""

from __future__ import annotations

from typing import Any

from .client_worktree_restart import _Base


class AnswerClientMixin(_Base):
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
