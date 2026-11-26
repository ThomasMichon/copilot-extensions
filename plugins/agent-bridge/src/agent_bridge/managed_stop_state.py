"""Exact-stop admission fencing and current-client native prompt evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .session_manager import SessionManager


TOKEN_FAMILY = "managed-exact-stop:"
TOKEN_PREFIX = TOKEN_FAMILY + "1:"
_EVIDENCE_ATTRIBUTE = "_managed_stop_native_evidence"


def manual_resume_admitted(
    manager: SessionManager, session_id: str, stop_token: str | None,
) -> bool:
    from .cold_outages import manual_recovery_stop_token

    return (
        isinstance(stop_token, str) and stop_token.startswith(TOKEN_FAMILY)
        and manual_recovery_stop_token(manager, session_id) == stop_token
    )


def prompt_fenced(manager: SessionManager, session_id: str) -> bool:
    row = manager.db.get_session(session_id)
    token = row.get("cold_recovery_stop_token") if row is not None else None
    if not isinstance(token, str) or not token.startswith(TOKEN_FAMILY):
        return False
    return not manual_resume_admitted(manager, session_id, token)


@dataclass
class _NativeEvidence:
    callback: Callable[[str, dict[str, Any]], None]
    quiet: bool = False


def begin_prompt(client: AcpClient) -> None:
    evidence = getattr(client, _EVIDENCE_ATTRIBUTE, None)
    if isinstance(evidence, _NativeEvidence) and client._on_event is evidence.callback:
        evidence.quiet = False
        return
    previous = client._on_event

    def observe(event_type: str, data: dict[str, Any]) -> None:
        if event_type == "session_state_changed" and data.get("status") == "running":
            state.quiet = False
        if previous is not None:
            previous(event_type, data)

    state = _NativeEvidence(observe)
    setattr(client, _EVIDENCE_ATTRIBUTE, state)
    client._on_event = observe


def record_completion(client: AcpClient) -> None:
    evidence = getattr(client, _EVIDENCE_ATTRIBUTE, None)
    if isinstance(evidence, _NativeEvidence) and client._on_event is evidence.callback:
        evidence.quiet = bool(
            client._connection is not None and client._prompt_complete
            and not client._prompt_in_flight and client._prompt_error is None
            and not client._out_of_turn_open
        )


def settlement_available(client: AcpClient | None) -> bool:
    if client is None or client._connection is None:
        return False
    if client._prompt_in_flight:
        return True
    evidence = getattr(client, _EVIDENCE_ATTRIBUTE, None)
    return (
        isinstance(evidence, _NativeEvidence) and evidence.quiet
        and client._on_event is evidence.callback and client._prompt_complete
        and client._prompt_error is None and not client._out_of_turn_open
    )
