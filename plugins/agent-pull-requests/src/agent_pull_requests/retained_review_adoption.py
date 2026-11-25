"""Original active-operation metadata under the existing complete-envelope owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .attention import PRKey
from .review_workspace import _scope
from .workspace_conversation import conversation_key


def contract() -> dict[str, str]:
    return {
        "protocol": "agent-pull-requests.retained-active-review-adoption/v1",
        "storage_extension": "retained_active_review_version:1",
        "operations": "original_active_v1",
        "admission": "permanently_recovery_only",
        "effects": "none",
    }


def stage(
    document: dict[str, Any], targets: set[PRKey], checkpoint: Callable[[], None],
) -> None:
    """Stage before the existing adoption owner's complete capacity preview."""
    checkpoint()
    if document["version"] != 3 or any(name in document for name in (
        "review_intent_version", "review_selection_version", "review_comment_mapping_version",
    )) or document.get("terminal_recovery", {}).get("operations"):
        raise ValueError("retained active review adoption requires original unprepared operations")
    adopted = "retained_active_review_version" in document
    for operation in document["reviews"]:
        checkpoint()
        selected = operation["intent"]
        scope = _scope(selected["scope"])
        if (
            selected["version"] != 1 or "selection" in operation
            or "comment_observations" in operation
            or conversation_key({**scope, "number": 1}, document["namespace"]) not in targets
        ):
            raise ValueError("retained operation is outside the selected original recovery reader")
        if adopted and operation.get("recovery_only") is not True:
            # Later ordinary operations are not part of the original import.
            continue
        operation["recovery_only"] = True
    document["retained_active_review_version"] = 1
    checkpoint()
