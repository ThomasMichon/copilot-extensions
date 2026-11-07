"""Optional pre-approval selection under the existing workspace receiver."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import review_intent
from .generation_cache import GenerationCache
from .review_selection_store import ReviewSelectionStore
from .review_workspace import _digest

if TYPE_CHECKING:
    from .workspace_operations import WorkspaceOperations


def selection_contract() -> dict:
    return {
        "protocol": "agent-pull-requests.preapproval-selection/v1",
        "input_versions": [1], "intent_versions": [2],
        "effects": "none", "approval_required": True, "owned_edits": False,
    }


def supports_selection(value: object) -> bool:
    expected = selection_contract()
    return (
        type(value) is dict and value == expected
        and all(type(value[key]) is list and all(type(item) is int for item in value[key])
                for key in ("input_versions", "intent_versions"))
        and type(value["approval_required"]) is bool and type(value["owned_edits"]) is bool
    )


def selection_preview(intent: dict) -> dict:
    return {
        "protocol": selection_contract()["protocol"], "intent": copy.deepcopy(intent),
        "effects": "none", "approval_required": True,
    }


def allocate_selection(
    workspace: WorkspaceOperations, payload: dict, scope: dict, cache: GenerationCache,
    *, expires_at: int, checkpoint: Callable[[], None],
    validate_intent: Callable[[dict], None] | None = None,
) -> dict:
    from .workspace_operations import WorkspaceUnavailable

    if workspace.prepared_reviews is not True or workspace.selection_preparer is None or workspace.write_context is None:
        raise WorkspaceUnavailable("pre-approval selection receiver unavailable")
    original = review_intent.intent(payload["intent"])
    if original["scope"] != scope:
        raise ValueError("selected preparation differs from authorized workspace scope")
    attachment = payload["source_attachment"]
    source = attachment.get("source") if isinstance(attachment, dict) else None
    if (
        not isinstance(source, dict) or attachment.get("state") != "ready"
        or attachment.get("scope") != scope or attachment.get("generation_id") != original["generation_id"]
        or source.get("schema") != "agent-pull-requests.local-source"
        or type(source.get("version")) is not int or source["version"] != 1
        or source.get("complete") is not True or source.get("errors") != []
        or source.get("scope") != scope
        or not isinstance(source.get("revision"), dict)
        or any(source["revision"].get(key) != value for key, value in original["revision"].items())
        or source.get("id") != original["source_id"]
        or source["id"] != _digest({key: value for key, value in source.items() if key != "id"})
    ):
        raise ValueError("selected preparation requires its original source attachment")
    selections = ReviewSelectionStore(workspace.store)
    retained = selections.recover(original, expires_at=expires_at, checkpoint=checkpoint)
    if retained is None:
        generation = cache.lookup(scope, generation_id=original["generation_id"])["generation"]
        if generation is None or any(
            generation["revision"].get(key) != value for key, value in original["revision"].items()
        ):
            raise WorkspaceUnavailable("selected preparation generation unavailable")
        # Resolve policy/profile/origin outside the replay transaction; only pure
        # synchronous composition runs while the existing storage owner is locked.
        compose = workspace.selection_preparer(copy.deepcopy(generation), checkpoint=checkpoint)
        checkpoint()
        retained = selections.allocate(
            original, compose=compose, expires_at=expires_at, checkpoint=checkpoint,
            validate_intent=validate_intent,
        )["operation"]
    if validate_intent is not None:
        validate_intent(copy.deepcopy(retained["intent"]))
    return selection_preview(retained["intent"])
