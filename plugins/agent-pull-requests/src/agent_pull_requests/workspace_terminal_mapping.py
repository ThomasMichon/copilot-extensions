"""Separately negotiated, body-free active/compact mapping reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import review_intent
from .review_comment_mapping import validate_slot
from .review_preparation import _guid
from .review_workspace import _scope
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
    if not (
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
    ):
        return False
    try:
        _scope(result["scope"])
        for name in ("operation_id", "action_id"):
            review_intent.text(result[name], 128)
        for name in ("intent_id", "generation_id", "source_id"):
            review_intent.identifier(result[name])
        review_intent.revision(result["revision"])
        prepared = result["prepared"]
        if prepared is not None:
            if not isinstance(prepared, dict) or set(prepared) != {"comment_id", "write_id", "body_id"}:
                return False
            _guid(prepared["comment_id"])
            _guid(prepared["write_id"])
            review_intent.identifier(prepared["body_id"])
            if prepared["comment_id"] == prepared["write_id"]:
                return False
        elif result["mapping_supported"]:
            return False
        state = result["action_state"]
        if state not in (
            "unknown", "confirmed", "retired" if result["representation"] == "compact" else "unattempted",
        ):
            return False
        receipt = result["receipt"]
        if receipt is not None:
            if state not in ("unknown", "confirmed"):
                return False
            review_intent.receipt(
                receipt, operation={
                    "intent": {"scope": result["scope"], "operation_id": result["operation_id"]},
                    "intent_id": result["intent_id"],
                }, action={"id": result["action_id"]}, limit=len(review_intent.encoded(result)),
            )
            if state == "confirmed" and receipt["origin"] == "no_send":
                return False
        elif state == "confirmed":
            return False
        validate_slot(result["mapping"], action_state=state)
    except (ValueError, KeyError, TypeError):
        return False
    return True
