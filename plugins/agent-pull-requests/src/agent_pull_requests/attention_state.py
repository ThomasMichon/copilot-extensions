"""Bounded attention data, not selection, feedback confirmation or a task queue."""

from __future__ import annotations

import copy

from .attention import Candidate, Cursor, Facts, Handled, Interest, PRKey
from .review_intent import MAX_ACTIONS, encoded, identifier, text

PROTOCOL = "agent-pull-requests.attention-state/v1"
MAX_INTERESTS = 4096
MAX_RECORD_BYTES = 16 * 1024


def _object(value: dict, names: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("invalid attention data fields")
    return value


def _scoped(value: dict) -> dict:
    result = dict(value)
    result["key"] = PRKey(**value["key"])
    result["candidate"] = Candidate(**value["candidate"])
    result["cursor"] = Cursor(**value["cursor"])
    return result


def _validate_record(value: dict) -> tuple[PRKey, str, str]:
    _object(value, {"facts", "interest", "handled", "provenance"})
    facts = Facts(**_scoped(_object(value["facts"], set(Facts.__dataclass_fields__))))
    interest = Interest(**_object(value["interest"], set(Interest.__dataclass_fields__)))
    if value["handled"] is None:
        if value["provenance"] is not None:
            raise ValueError("feedback provenance requires a handled baseline")
    else:
        handled = Handled(**_scoped(
            _object(value["handled"], set(Handled.__dataclass_fields__))
        ))
        if (
            (handled.key, handled.consumer, handled.role)
            != (facts.key, interest.consumer, interest.role)
            or handled.cursor.epoch > facts.cursor.epoch
            or handled.cursor.epoch == facts.cursor.epoch
            and handled.cursor.sequence > facts.cursor.sequence
            or handled.cursor == facts.cursor and handled.candidate != facts.candidate
        ):
            raise ValueError("handled baseline differs from its captured interest")
        proof = _object(value["provenance"], {"operation_id", "generation_id", "receipt_refs"})
        text(proof["operation_id"], 128)
        identifier(proof["generation_id"])
        refs = proof["receipt_refs"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_ACTIONS:
            raise ValueError("bounded original receipt references required")
        seen = set()
        for ref in refs:
            _object(ref, {"action_id", "attempt_id"})
            action = text(ref["action_id"], 128)
            text(ref["attempt_id"], 128)
            if action in seen:
                raise ValueError("duplicate feedback receipt reference")
            seen.add(action)
    if len(encoded(value)) > MAX_RECORD_BYTES:
        raise ValueError("attention record exceeds its byte bound")
    return facts.key, interest.consumer, interest.role


class AttentionState:
    """Strict detached value object; provenance references never grant authority."""

    def __init__(self, state: dict | None = None) -> None:
        self.records: list[dict] = []
        if state is None:
            return
        try:
            _object(state, {"protocol", "records"})
            if state["protocol"] != PROTOCOL or not isinstance(state["records"], list):
                raise ValueError
            if len(state["records"]) > MAX_INTERESTS:
                raise ValueError
            seen = set()
            for record in state["records"]:
                identity = _validate_record(record)
                if identity in seen:
                    raise ValueError
                seen.add(identity)
            self.records = copy.deepcopy(state["records"])
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("invalid persisted attention state") from None

    def snapshot(self) -> dict:
        return copy.deepcopy({"protocol": PROTOCOL, "records": self.records})
