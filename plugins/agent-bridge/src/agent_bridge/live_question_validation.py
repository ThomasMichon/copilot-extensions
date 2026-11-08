"""Actor-free response validation, separate from transport and model imports."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .live_questions import LiveQuestion, LiveQuestionAnswer


def validate_answer(question: LiveQuestion, answer: LiveQuestionAnswer) -> None:
    """Check response shape without enforcing enums the CLI permits overriding."""
    if not question.answerable:
        raise ValueError(question.reason or "this native question is read-only")
    if len(json.dumps(answer.content)) > 64_000:
        raise ValueError("answer exceeds the response budget")
    if question.family == "user_input":
        if answer.action != "accept" or set(answer.content) != {"answer"}:
            raise ValueError("user_input requires exactly one answer field and action accept")
        text = answer.content["answer"]
        if not isinstance(text, str):
            raise ValueError("user_input answer must be text")
        if not question.allow_freeform and text not in (question.choices or []):
            raise ValueError("this question requires one of its offered choices")
    elif answer.action == "accept":
        schema = question.requested_schema or {}
        properties = schema.get("properties")
        if isinstance(properties, dict) and (
            set(answer.content) - set(properties)
            or set(schema.get("required") or []) - set(answer.content)
        ):
            raise ValueError("answer fields do not match the pending form")
        for value in answer.content.values():
            if not isinstance(value, (str, bool, int, float, list)) or (
                isinstance(value, list) and not all(isinstance(item, str) for item in value)
            ):
                raise ValueError("form answers must be scalar values or lists of strings")
    elif answer.content:
        raise ValueError("decline/cancel must not include form content")
