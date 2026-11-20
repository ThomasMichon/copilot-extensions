"""Separately negotiated inspection; never a protected recovery/send receiver."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import review_intent
from .review_workspace import _scope

if TYPE_CHECKING:
    from .workspace_operations import WorkspaceOperations

SELECTION = "agent-pull-requests.retained-active-review-inspection/v1"
FIELD = "retained_active_review_contract"
Document = dict[str, Any]


def contract() -> Document:
    return {
        "protocol": SELECTION, "operations": ["inspect"],
        "representation": "original_active_v1",
        "storage_extension": "retained_active_review_version:1",
        "effects": "none", "reconciliation": False,
    }


def supports(value: object) -> bool:
    return isinstance(value, dict) and value == contract() and value.get("reconciliation") is False


def selected(capabilities: object) -> bool:
    from .workspace_terminal_read import supports as supports_terminal

    return (
        isinstance(capabilities, dict) and supports_terminal(capabilities)
        and supports(capabilities.get(FIELD))
    )


def available(
    workspace: WorkspaceOperations, *, expires_at: int, checkpoint: Callable[[], None],
) -> bool:
    return (
        workspace.terminal_reads is True and workspace.retained_active_reviews is True
        and workspace.store.retained_active_reviews is True
        and workspace.store._review_transaction(
            lambda document: document.get("retained_active_review_version") == 1,
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )
    )


def read(
    workspace: WorkspaceOperations, payload: Document, scope: Document, *,
    expires_at: int, checkpoint: Callable[[], None],
) -> Document:
    from .workspace_operations import WorkspaceUnavailable

    if not available(workspace, expires_at=expires_at, checkpoint=checkpoint):
        raise WorkspaceUnavailable("retained active review inspection unavailable")
    result = workspace.store.inspect_retained_review(
        payload["operation_id"], scope, expires_at=expires_at, checkpoint=checkpoint,
    )
    if result.get("recovery_only") is not True:
        raise WorkspaceUnavailable("operation is not an imported original active review")
    return result


def valid_result(value: object, payload: Document) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
        return False
    result = value["result"]
    try:
        scope = _scope(value["scope"])
        if (
            result.get("recovery_only") is not True
            or result["intent"]["scope"] != scope
            or result["intent"]["operation_id"] != payload.get("operation_id")
        ):
            return False
        reserved = result["reserved_bytes"]
        if type(reserved) is not int:
            return False
        review_intent.validate_operations(
            [result], receipt_bytes=4096, review_bytes=1024 + reserved, retained_active=True,
        )
    except (ValueError, KeyError, TypeError):
        return False
    return True
