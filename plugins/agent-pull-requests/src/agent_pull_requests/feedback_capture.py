"""Body-free selection context; not effect confirmation or handled progress."""

from __future__ import annotations

import copy
from dataclasses import asdict

from .attention import Candidate, Cursor, Interest, PRKey
from .attention_state import MAX_INTERESTS, _object
from .review_intent import MAX_OPERATIONS, encoded, identifier, text
from .review_workspace import _digest

PROTOCOL = "agent-pull-requests.feedback-captures/v1"
READBACK_PROTOCOL = "agent-pull-requests.feedback-captures/v2"
MAX_CAPTURE_BYTES = 64 * 1024
MAX_READBACK_BYTES = 64 * 1024


def _validate_capture(value: dict) -> str:
    _object(value, {
        "operation_id", "intent_id", "generation_id", "source_id", "key",
        "candidate", "cursor", "observed_at", "interests",
    })
    operation_id = text(value["operation_id"], 128)
    for name in ("intent_id", "generation_id", "source_id"):
        identifier(value[name])
    PRKey(**_object(value["key"], set(PRKey.__dataclass_fields__)))
    Candidate(**_object(value["candidate"], set(Candidate.__dataclass_fields__)))
    cursor = Cursor(**_object(value["cursor"], set(Cursor.__dataclass_fields__)))
    if (
        cursor.sequence == 0
        or type(value["observed_at"]) is not int or value["observed_at"] < 0
    ):
        raise ValueError("actual conversation position and observation time required")
    interests = value["interests"]
    if not isinstance(interests, list) or not 1 <= len(interests) <= MAX_INTERESTS:
        raise ValueError("bounded captured interests required")
    seen = set()
    for interest in interests:
        _object(interest, {"consumer", "role"})
        selected = Interest(**interest, matches=True)
        identity = selected.consumer, selected.role
        if identity in seen:
            raise ValueError("duplicate captured interest")
        seen.add(identity)
    if len(encoded(value)) > MAX_CAPTURE_BYTES:
        raise ValueError("feedback capture exceeds its byte bound")
    return operation_id


def readback_references(capture: dict, operation: dict) -> dict:
    """Body-free bindings only; the protected owner must establish actual readback."""
    if (
        any(row["state"] != "confirmed" for row in operation["actions"])
        or any(row["kind"] == "verdict" for row in operation["intent"]["actions"])
    ):
        raise ValueError("readback requires supported confirmed actions")
    return {
        "capture_id": _digest({name: value for name, value in capture.items() if name != "readback"}),
        "receipt_refs": [
            {"action_id": row["id"], "attempt_id": row["attempt_id"], "receipt_id": _digest(row["receipt"])}
            for row in operation["actions"]
        ],
    }


class FeedbackCaptures:
    """Strict detached records; capture admission stays with the protected owner."""

    def __init__(self, state: dict | None = None, *, protocol: str = PROTOCOL) -> None:
        if protocol not in (PROTOCOL, READBACK_PROTOCOL):
            raise ValueError("unsupported feedback capture protocol")
        self.protocol = protocol
        self.records: list[dict] = []
        if state is None:
            return
        try:
            _object(state, {"protocol", "records"})
            if state["protocol"] != protocol or not isinstance(state["records"], list):
                raise ValueError
            if len(state["records"]) > MAX_OPERATIONS:
                raise ValueError
            seen = set()
            for record in state["records"]:
                original = record
                if protocol == READBACK_PROTOCOL:
                    if not isinstance(record, dict) or "readback" not in record:
                        raise ValueError
                    original = {name: value for name, value in record.items() if name != "readback"}
                    proof = record["readback"]
                    if proof is not None:
                        _object(proof, {"capture_id", "receipt_refs"})
                        identifier(proof["capture_id"])
                        if not isinstance(proof["receipt_refs"], list) or len(encoded(proof)) > MAX_READBACK_BYTES:
                            raise ValueError
                identity = _validate_capture(original)
                if identity in seen:
                    raise ValueError
                seen.add(identity)
            self.records = copy.deepcopy(state["records"])
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("invalid persisted feedback captures") from None

    def validate_bindings(self, operations: list[dict], namespace: str) -> None:
        """Check retained references, not freshness, selection or permission to send."""
        from .workspace_conversation import conversation_key

        retained = {row["intent"]["operation_id"]: row for row in operations}
        for capture in self.records:
            operation = retained.get(capture["operation_id"])
            if operation is None:
                raise ValueError("feedback capture requires its original review operation")
            intent = operation["intent"]
            if (
                capture["intent_id"] != operation["intent_id"]
                or capture["generation_id"] != intent["generation_id"]
                or capture["source_id"] != intent["source_id"]
                or capture["key"] != asdict(conversation_key(intent["scope"], namespace))
                or capture["candidate"]["head"] != intent["revision"]["head_sha"]
            ):
                raise ValueError("feedback capture differs from its original review intent")
            if self.protocol == READBACK_PROTOCOL and capture["readback"] is not None:
                if capture["readback"] != readback_references(capture, operation):
                    raise ValueError("readback proof differs from its original confirmed operation")

    def capture_bytes(self) -> int:
        records = self.records
        if self.protocol == READBACK_PROTOCOL:
            records = [{name: value for name, value in row.items() if name != "readback"} for row in records]
        return len(encoded({"protocol": self.protocol, "records": records}))

    def readback_reservation(self) -> int:
        return len(self.records) * (MAX_READBACK_BYTES + 32) + 1024

    def snapshot(self) -> dict:
        return copy.deepcopy({"protocol": self.protocol, "records": self.records})
