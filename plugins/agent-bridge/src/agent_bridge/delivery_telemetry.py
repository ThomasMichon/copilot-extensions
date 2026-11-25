"""Content-free inbox observations; never delivery or consumption authority."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from . import telemetry

if TYPE_CHECKING:
    from process_telemetry.buffer import BufferedSink
    from .db import Database

_IDENTITY = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_REASONS = frozenset({
    "invalid_receipt", "handoff_not_started", "not_root_input", "session_mismatch",
    "source_mismatch", "content_unavailable", "header_mismatch", "header_unterminated",
    "message_id_mismatch",
})
_STAGES = frozenset({"admitted", "handoff_started", "sdk_accepted", "consumed", "unknown",
                     "match_failed", "hook_notice"})
_SOURCES = frozenset({"backend-admission", "backend-receipt", "retained-sdk-recovery",
                      "pending-input-set-v1"})


def _identity(value: object) -> str | None:
    return value if isinstance(value, str) and _IDENTITY.fullmatch(value) else None


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= (1 << 53) - 1


def _buffer() -> BufferedSink | None:
    slot = telemetry._slot
    writer = getattr(slot, "_owned_buffer", None)
    if writer is None:
        return None
    try:
        from process_telemetry.buffer import BufferedSink
    except ImportError:
        return None
    if (
        isinstance(writer, BufferedSink) and writer is slot.sink
        and getattr(writer, "closed", True) is False
    ):
        return writer
    return None


def publish(stage: str, session_id: str, *, source: str, fields: dict[str, object]) -> None:
    """Offer known metadata only to the existing owner-started bounded writer."""
    writer = _buffer()
    if writer is None or stage not in _STAGES or source not in _SOURCES:
        return
    sid = _identity(session_id)
    if sid is None:
        return
    record: dict[str, object] = {
        "kind": "event", "name": "inbox_delivery", "event": stage,
        "session_id": sid, "source": source, "observed_at": time.time(),
    }
    for key in ("handoff_id", "consumer_incarnation"):
        value = _identity(fields.get(key))
        if value is not None:
            record[key] = value
    for key in ("message_id", "event_count", "pending_count"):
        count = fields.get(key)
        if _count(count):
            record[key] = count
    if type(fields.get("incomplete")) is bool:
        record["incomplete"] = fields["incomplete"]
    if fields.get("delivery_path") in ("retained", "legacy"):
        record["delivery_path"] = fields["delivery_path"]
    failures = fields.get("failures")
    if isinstance(failures, dict):
        record["failures"] = {
            key: value for key, value in failures.items() if key in _REASONS and _count(value)
        }
    writer(record)


def committed(db: Database, session_id: str, message_id: int, stage: str) -> None:
    """Read original committed identities without selecting message content."""
    if _buffer() is None:
        return
    try:
        receipt = (
            db.live_message_receipt(session_id, message_id) if stage == "admitted"
            else db.live_message_handoff_receipt(session_id, message_id)
        )
        if receipt is not None:
            fields: dict[str, object] = {
                "message_id": receipt["message_id"], "handoff_id": receipt.get("handoff_id"),
            }
            consumer = receipt.get("consumer")
            if isinstance(consumer, dict):
                fields["consumer_incarnation"] = consumer.get("incarnation")
            publish(stage, receipt["session_id"],
                    source="backend-admission" if stage == "admitted" else "backend-receipt",
                    fields=fields)
    except Exception:
        pass


def represented(session_id: str, data: object) -> None:
    """Accept diagnostic metadata without changing a receipt or status projection."""
    if not isinstance(data, dict) or data.get("session_id") != session_id:
        return
    stage, source = data.get("stage"), data.get("source")
    if not isinstance(stage, str) or not isinstance(source, str) or (stage, source) not in {
        ("match_failed", "retained-sdk-recovery"), ("hook_notice", "pending-input-set-v1"),
    }:
        return
    if stage == "hook_notice":
        from .pending_queue_notice import observations

        observations.record(session_id, data)
    publish(stage, session_id, source=source, fields=data)
