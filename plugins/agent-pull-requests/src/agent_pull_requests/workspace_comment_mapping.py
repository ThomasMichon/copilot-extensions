"""Optional retained-comment reader on the existing workspace discovery seam."""

from __future__ import annotations

from .review_comment_mapping import CommentMappingStore


def contract() -> dict[str, str]:
    return {
        "protocol": "agent-pull-requests.retained-comment-reader/v1",
        "result_protocol": "agent-pull-requests.retained-comment-observation/v1",
        "storage_extension": "review_comment_mapping_version:1",
        "acquisition": "workspace-projection/v1",
        "effects": "none",
    }


def supports(value: object) -> bool:
    return isinstance(value, dict) and all(value.get(name) == expected for name, expected in contract().items())


def available(operations, *, expires_at, checkpoint) -> bool:
    """Selection is not adoption; query the executing storage owner each time."""
    return (
        operations.comment_mappings is True and operations.conversations is True
        and operations.prepared_reviews is True
        and CommentMappingStore(operations.store).supported(
            expires_at=expires_at, checkpoint=checkpoint,
        )
    )


def valid_result(value: object, payload: dict) -> bool:
    if (
        not isinstance(value, dict) or not isinstance(value.get("result"), dict)
        or not isinstance(value.get("scope"), dict)
    ):
        return False
    result = value["result"]
    return (
        result.get("protocol") == contract()["result_protocol"]
        and result.get("scope") == value["scope"]
        and all(result.get(name) == payload.get(name) for name in ("operation_id", "action_id"))
        and result.get("effects") == "none"
        and all(result.get(name) is False for name in (
            "fresh_native_read", "original_version_proven", "feedback_progress",
        ))
    )
