"""Bounded transcript accounting reads without retaining conversation content."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_logger.sync.publication_directory import publication_directory
from agent_logger.usage_ledger import MAX_RECORDS, UsageEvidenceError, validate_values

MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 200000
FIELDS = {
    "model": "model", "inputTokens": "input_tokens", "outputTokens": "output_tokens",
    "cacheReadTokens": "cache_read_tokens", "cacheWriteTokens": "cache_write_tokens",
    "reasoningTokens": "reasoning_tokens", "duration": "duration_ms",
    "cost": "request_multiplier",
    "apiCallId": "api_call_id", "parentToolCallId": "parent_tool_call_id",
    "agentId": "agent_id",
}


def _invalid_constant(value: str):
    raise ValueError("nonfinite JSON constant")


def project_usage_line(line: bytes, session_id: str) -> dict | None:
    if not line.endswith(b"\n"):
        raise UsageEvidenceError("transcript has an unstable partial tail")
    try:
        event = json.loads(line, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise UsageEvidenceError("transcript contains invalid JSON") from exc
    if not isinstance(event, dict):
        raise UsageEvidenceError("transcript event is not an object")
    if event.get("type") != "session.usage_record":
        return None
    data = event.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("usage"), dict):
        raise UsageEvidenceError("transcript usage record is malformed")
    usage = data["usage"]
    values = {target: usage[name] for name, target in FIELDS.items() if name in usage}
    values["created_at"] = event.get("timestamp")
    accounting = usage.get("accounting")
    if isinstance(accounting, dict):
        for original, target in (
            ("sourceSessionId", "source_session_id"), ("sequence", "sequence"),
            ("usageId", "usage_id"),
        ):
            if original in accounting:
                values[target] = accounting[original]
    meter = usage.get("copilotUsage")
    if isinstance(meter, dict) and "totalNanoAiu" in meter:
        values["total_nano_aiu"] = meter["totalNanoAiu"]
    if event.get("id") is not None:
        values["event_id"] = event["id"]
    return {
        "source": "transcript", "session_id": session_id,
        "values": validate_values(values),
    }


def read_transcript_usage(session: Path, session_id: str) -> list[dict]:
    """Read modern per-call meters only; checkpoints/shutdown totals stay separate."""
    records: list[dict] = []
    with publication_directory(session, create=False) as directory:
        with directory.open("events.jsonl") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_TRANSCRIPT_BYTES:
                raise UsageEvidenceError("transcript byte limit exceeded")
            count = consumed = 0
            while True:
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                consumed += len(line)
                count += 1
                if len(line) > MAX_LINE_BYTES or consumed > MAX_TRANSCRIPT_BYTES or count > MAX_EVENTS:
                    raise UsageEvidenceError("transcript read limit exceeded")
                record = project_usage_line(line, session_id)
                if record is not None:
                    if len(records) >= MAX_RECORDS:
                        raise UsageEvidenceError("transcript accounting record limit exceeded")
                    records.append(record)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise UsageEvidenceError("transcript changed during accounting capture")
    return records
