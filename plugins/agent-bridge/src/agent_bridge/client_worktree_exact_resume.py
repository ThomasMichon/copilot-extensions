"""One negotiated resume attempt bound to the selected native conversation."""

from __future__ import annotations

import http.client
import json
import re
from typing import Any
from urllib.parse import quote

from .client_read_budget import ReadDeadlineExpired, command_deadline, remaining

CAPABILITY = "worktree_exact_resume"


def resume(
    client: Any, worktree_id: str, native_session_id: str, *,
    mode: str = "acp", request_timeout: float | None = None,
) -> dict[str, Any]:
    from .client import BridgeClientError, BridgeConnectionError
    from .client_protocol_floor import protocol_floored_request
    from .protocol import WORKTREE_NATIVE_RESUME_PROTOCOL_VERSION

    if mode not in {"acp", "cli"}:
        raise ValueError("worktree resume mode must be acp or cli")
    if not isinstance(worktree_id, str) or not worktree_id.strip():
        raise ValueError("worktree resume requires an exact workspace ID")
    if not isinstance(native_session_id, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", native_session_id,
    ) is None:
        raise ValueError("worktree resume requires an exact native session ID")
    attempted = False

    def mark_attempt() -> None:
        nonlocal attempted
        attempted = True

    def unknown() -> BridgeClientError:
        return BridgeClientError(502, {
            "code": "native_resume_unconfirmed",
            "worktree_id": worktree_id, "native_session_id": native_session_id, "mode": mode,
            "message": "Resume outcome is unconfirmed; inspect this conversation before retrying",
            "reconnectable": False,
        })

    try:
        with command_deadline(request_timeout):
            send = protocol_floored_request(
                client, WORKTREE_NATIVE_RESUME_PROTOCOL_VERSION, capabilities={CAPABILITY: 1},
            )
            result = send(
                "POST",
                f"/api/v1/worktrees/{quote(worktree_id, safe='')}/resume/{mode}/exact",
                {"native_session_id": native_session_id},
                allow_retry=False, request_timeout=request_timeout, _on_request=mark_attempt,
            )
            remaining()
            if (
                not isinstance(result, dict)
                or result.get("acp_session_id") != native_session_id
                or result.get("durable_session_id") != native_session_id
                or result.get("worktree_id") != worktree_id or result.get("mode") != mode
                or not isinstance(result.get("session_id"), str) or not result["session_id"]
            ):
                raise unknown()
            return result
    except (
        BridgeConnectionError, OSError, http.client.HTTPException, UnicodeError,
        json.JSONDecodeError, ReadDeadlineExpired, KeyboardInterrupt,
    ) as exc:
        if attempted:
            raise unknown() from exc
        raise
