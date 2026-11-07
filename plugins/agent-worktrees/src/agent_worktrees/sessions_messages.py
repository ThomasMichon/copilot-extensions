"""Incremental, metadata-only message activity for registered sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import jsonl_cache


def timestamp(value: object) -> datetime | None:
    """Parse an event timestamp; historical naive values mean local time."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def newest(*values: object) -> str | None:
    """Select the latest valid timestamp by instant, not lexical offset."""
    valid = [(parsed, value) for value in values if (parsed := timestamp(value)) is not None]
    return max(valid, key=lambda item: item[0])[1] if valid else None


def read(entry: Path) -> tuple[int, str | None]:
    """Read only appended complete lines, retaining no message bodies.

    The existing turns sidecar remains readable by older counters. Missing
    message metadata triggers one rebuild, not an inference from file mtime.
    """
    events, sidecar = entry / "events.jsonl", entry / ".aw-turns.json"
    previous: dict = {}
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            previous = value
    except (OSError, ValueError):
        pass
    turns, last, offset = 0, None, 0
    try:
        with events.open("rb") as handle:
            device, inode, modified, size = jsonl_cache._stamp(handle)
            identity = [device, inode]
            old_size = previous.get("size")
            reusable = (
                type(previous.get("message_activity_version")) is int
                and previous["message_activity_version"] == 1
                and previous.get("identity") == identity
                and type(old_size) is int and 0 <= old_size <= size
                and type(previous.get("turns")) is int and previous["turns"] >= 0
                and (
                    old_size < size
                    or previous.get("mtime_ns") == modified
                )
            )
            if reusable:
                offset, turns = old_size, previous["turns"]
                last = newest(previous.get("last_message_at"))
            handle.seek(offset)
            remaining = size - offset
            while remaining:
                line = handle.readline(remaining)
                if not line or not line.endswith(b"\n"):
                    break
                remaining -= len(line)
                offset += len(line)
                if b'"user.message"' not in line and b'"assistant.message"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind not in {"user.message", "assistant.message"}:
                    continue
                turns += int(kind == "user.message")
                if not event.get("agentId"):
                    last = newest(last, event.get("timestamp"))
            value = {
                "size": offset, "turns": turns, "message_activity_version": 1,
                "last_message_at": last, "identity": identity, "mtime_ns": modified,
            }
        if value != previous:
            try:
                sidecar.write_text(json.dumps(value), encoding="utf-8")
            except OSError:
                pass
    except OSError:
        turns = previous.get("turns", 0)
        turns = turns if type(turns) is int and turns >= 0 else 0
        last = None
    try:
        pulse = json.loads((entry / "substatus.json").read_text(encoding="utf-8"))
        if (
            isinstance(pulse, dict) and type(pulse.get("messageActivityVersion")) is int
            and pulse["messageActivityVersion"] == 1
        ):
            last = newest(last, pulse.get("lastMessageAt"))
    except (OSError, ValueError):
        pass
    return turns, last
