"""Separately negotiated, body-free active/compact mapping reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .terminal_comment_mapping import PROTOCOL, TerminalCommentMappingStore

if TYPE_CHECKING:
    from .workspace_operations import WorkspaceOperations

SELECTION = "agent-pull-requests.terminal-comment-mapping-reader/v1"
FIELD = "terminal_comment_mapping_contract"


def contract() -> dict:
    return {
        "protocol": SELECTION, "operations": ["retained-comment"],
        "result_protocol": PROTOCOL, "representations": ["active", "compact"],
        "terminal_comment_mapping_version": 1, "effects": "none",
    }


def supports(value: object) -> bool:
    return (
        isinstance(value, dict) and value == contract()
        and type(value.get("terminal_comment_mapping_version")) is int
    )


def selected(capabilities: object) -> bool:
    from .workspace_terminal_read import supports as supports_terminal

    return (
        isinstance(capabilities, dict) and supports_terminal(capabilities)
        and supports(capabilities.get(FIELD))
    )


def available(workspace: WorkspaceOperations, *, expires_at: int,
              checkpoint: Callable[[], None]) -> bool:
    return (
        workspace.terminal_reads is True and workspace.terminal_mapping_reads is True
        and workspace.store.terminal_comment_mapping is True
        and workspace.store._review_transaction(
            lambda document: document.get("terminal_comment_mapping_version") == 1,
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )
    )


def read(workspace: WorkspaceOperations, payload: dict, scope: dict, *,
         expires_at: int, checkpoint: Callable[[], None]) -> dict:
    from .workspace_operations import WorkspaceUnavailable

    if not available(workspace, expires_at=expires_at, checkpoint=checkpoint):
        raise WorkspaceUnavailable("terminal comment mapping receiver unavailable")
    return TerminalCommentMappingStore(workspace.store).read(
        payload["operation_id"], scope, payload["action_id"],
        expires_at=expires_at, checkpoint=checkpoint,
    )


def valid_result(value: object, payload: dict) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
        return False
    result = value["result"]
    return (
        result.get("protocol") == PROTOCOL
        and result.get("representation") in ("active", "compact")
        and isinstance(value.get("scope"), dict) and result.get("scope") == value["scope"]
        and all(result.get(name) == payload.get(name) for name in ("operation_id", "action_id"))
        and {"intent_id", "generation_id", "source_id", "revision", "prepared",
             "action_state", "receipt", "mapping"} <= result.keys()
        and type(result.get("mapping_supported")) is bool
        and (result["mapping_supported"] or result["mapping"] is None)
        and result.get("effects") == "none"
        and all(result.get(name) is False for name in (
            "fresh_native_read", "original_version_proven", "feedback_progress",
        ))
    )
