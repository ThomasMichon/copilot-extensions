"""Bounded start-event evidence; target capacity is never active pre-capacity."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from itertools import islice
from typing import Any, Literal

from .events import SseEvent

MAX_COUNT = (1 << 63) - 1
MAX_TEXT = 256
MAX_RECORD_BYTES = 16 * 1024
START_FIELDS = (
    "current_tokens",
    "token_limit",
    "conversation_tokens",
    "system_tokens",
    "tool_definitions_tokens",
    "compaction_attempt_id",
    "model",
    "switch_target_model",
    "target_prompt_token_limit",
    "execution",
    "trigger",
)


class CompactionEvidenceError(ValueError):
    """The event or evidence cannot be represented within this contract."""


@dataclass(frozen=True)
class CompactionSource:
    """Optional owner context; identifiers are not manufactured or authenticated."""

    kind: Literal["represented-sdk", "owned-acp", "unknown"] = "unknown"
    bridge_session_id: str | None = None
    durable_session_id: str | None = None
    sdk_session_id: str | None = None
    sdk_event_id: str | None = None
    continuity_id: str | None = None
    context_epoch: str | None = None
    source_timestamp: str | None = None


@dataclass(frozen=True)
class PreCompactionUsage:
    used_tokens: int | None
    coverage: Literal["total-system-conversation-tools-at-start", "unknown"]
    relation: Literal["same-start-event", "unknown"]
    active_capacity_tokens: None
    target_capacity_tokens: int | None
    target_prompt_budget_tokens: int | None
    qualification: Literal["unknown"]
    reasons: list[str]


@dataclass(frozen=True)
class CompactionEvidence:
    schema: Literal["bridge-compaction-evidence-v1"]
    version: Literal[1]
    source: dict[str, Any]
    compaction: dict[str, Any]
    pre_usage: PreCompactionUsage
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return finite JSON, not a policy decision or admission receipt."""
        try:
            result = asdict(self)
            encoded = json.dumps(
                result, allow_nan=False, ensure_ascii=False, separators=(",", ":")
            )
            size = len(encoded.encode("utf-8"))
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise CompactionEvidenceError("compaction evidence must be finite UTF-8 JSON") from exc
        if size > MAX_RECORD_BYTES:
            raise CompactionEvidenceError("compaction evidence exceeds its 16 KiB record bound")
        return result


def _diagnostic(value: Any, depth: int = 0) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() <= 128:
            return value
        return {
            "type": "integer",
            "bit_length": value.bit_length(),
            "sign": -1 if value < 0 else 1,
        }
    if type(value) is float:
        if math.isfinite(value):
            return value
        return {"type": "nonfinite-number", "value": str(value)}
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return {
                "type": "invalid-unicode-text",
                "escaped_prefix": value[:MAX_TEXT].encode("unicode_escape").decode("ascii"),
                "length": len(value),
                "truncated": len(value) > MAX_TEXT,
            }
        if len(value) <= MAX_TEXT:
            return value
        return {
            "type": "text",
            "prefix": value[:MAX_TEXT],
            "length": len(value),
            "truncated": True,
        }
    if depth >= 2:
        return {"type": type(value).__name__, "truncated": True}
    if isinstance(value, list):
        return {
            "type": "array",
            "items": [_diagnostic(item, depth + 1) for item in value[:8]],
            "length": len(value),
        }
    if isinstance(value, dict):
        return {
            "type": "object",
            "items": [
                {"key": _diagnostic(key, depth + 1), "value": _diagnostic(item, depth + 1)}
                for key, item in islice(value.items(), 8)
            ],
            "length": len(value),
        }
    return {"type": type(value).__name__, "unsupported": True}


def _count(
    data: dict[str, Any],
    key: str,
    reasons: list[str],
    *,
    positive: bool = False,
) -> int | None:
    if key not in data:
        reasons.append(f"{key}:missing")
        return None
    value = data[key]
    if type(value) is not int:
        reasons.append(f"{key}:invalid-integer-type")
        return None
    if value < (1 if positive else 0) or value > MAX_COUNT:
        reasons.append(f"{key}:outside-count-range")
        return None
    return value


def _text(value: Any, key: str, reasons: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT or "\x00" in value:
        reasons.append(f"{key}:invalid-bounded-text")
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        reasons.append(f"{key}:invalid-utf8-text")
        return None
    return value


def from_start_event(
    event: SseEvent,
    *,
    source: CompactionSource | None = None,
) -> CompactionEvidence:
    """Construct evidence from the actual translated start event, without lookups.

    Current SDK start data has a total numerator but only a target denominator.
    There is deliberately no metadata/cached-usage override that can qualify an
    active capacity. A future admitted producer contract must own that addition.
    """
    if event.event != "compaction_start":
        raise CompactionEvidenceError("evidence requires an actual compaction_start event")
    if not isinstance(event.data, dict):
        raise CompactionEvidenceError("compaction start data must be an object")
    data = event.data
    reasons: list[str] = []
    used = _count(data, "current_tokens", reasons)
    target = _count(data, "token_limit", reasons, positive=True)
    prompt = (
        _count(data, "target_prompt_token_limit", reasons, positive=True)
        if "target_prompt_token_limit" in data
        else None
    )
    reasons.append("active-pre-capacity:missing-admitted-producer-contract")
    if target is not None:
        reasons.append("target-capacity:is-not-active-pre-capacity")
    details = {}
    for key in ("compaction_attempt_id", "model", "switch_target_model", "execution"):
        details[key] = _text(data.get(key), key, reasons)
    if details["switch_target_model"] is not None:
        reasons.append("model-switch:target-and-source-capacities-may-differ")
    details["trigger"] = _diagnostic(data["trigger"]) if "trigger" in data else None
    details["event"] = "compaction_start"
    context = source if source is not None else CompactionSource()
    represented_source: dict[str, Any] = {"kind": context.kind}
    if not isinstance(context.kind, str) or context.kind not in {
        "represented-sdk",
        "owned-acp",
        "unknown",
    }:
        raise CompactionEvidenceError("unsupported compaction evidence source kind")
    for key in (
        "bridge_session_id",
        "durable_session_id",
        "sdk_session_id",
        "sdk_event_id",
        "continuity_id",
        "context_epoch",
        "source_timestamp",
    ):
        represented_source[key] = _text(getattr(context, key), key, reasons)
    represented_source["bridge_event_id"] = (
        event.id if type(event.id) is int and 0 < event.id <= MAX_COUNT else None
    )
    if represented_source["bridge_event_id"] is None:
        reasons.append("bridge-event-id:invalid")
    represented_source["bridge_observed_timestamp"] = (
        event.timestamp
        if (
            (
                type(event.timestamp) is float
                and math.isfinite(event.timestamp)
                and event.timestamp >= 0
            )
            or (
                type(event.timestamp) is int
                and event.timestamp.bit_length() <= 63
                and event.timestamp >= 0
            )
        )
        else None
    )
    if represented_source["bridge_observed_timestamp"] is None:
        reasons.append("bridge-observed-timestamp:invalid")
    if (
        represented_source["durable_session_id"] is not None
        and represented_source["sdk_session_id"] is not None
        and represented_source["durable_session_id"] != represented_source["sdk_session_id"]
    ):
        reasons.append("source-session:identity-mismatch")
    for key in ("bridge_session_id", "durable_session_id", "sdk_event_id", "context_epoch"):
        if represented_source[key] is None:
            reasons.append(f"{key}:unavailable")
    sdk_total = used is not None and context.kind == "represented-sdk"
    if used is not None and not sdk_total:
        reasons.append("total-coverage:source-contract-unconfirmed")
    evidence = CompactionEvidence(
        "bridge-compaction-evidence-v1",
        1,
        represented_source,
        details,
        PreCompactionUsage(
            used,
            "total-system-conversation-tools-at-start" if sdk_total else "unknown",
            "same-start-event" if used is not None else "unknown",
            None,
            target,
            prompt,
            "unknown",
            reasons,
        ),
        {
            "start_fields": {key: _diagnostic(data[key]) for key in START_FIELDS if key in data},
            "source_fields": {key: _diagnostic(value) for key, value in asdict(context).items()},
            "bridge_event_id": _diagnostic(event.id),
            "bridge_observed_timestamp": _diagnostic(event.timestamp),
        },
    )
    evidence.as_dict()
    return evidence
