"""Bounded admission of display-only agenda publications across the CLI seam."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

MAX_FRAME_BYTES = 35 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_INTEGER = 2**63 - 1
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_SNAPSHOT_FIELDS = {
    "worktree", "revision", "status", "root", "stack", "next_action",
    "exit_criteria", "declared_outcomes", "queued", "parked", "notes",
    "cursor_version", "deferred", "due_rechecks", "next_revisit_at",
}
_REPLY_FIELDS = {
    "ok", "contract_version", "generation", "sequence", "project",
    "worktree_id", "session_id", "availability", "observation",
}


class AgendaUnavailable(ValueError):
    def __init__(self, message: str, *, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry


def bounded_json(value, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("agenda data exceeds its nesting bound")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 4096 or "\x00" in value:
            raise ValueError("agenda string exceeds its bound")
    elif isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("agenda object exceeds its member bound")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("agenda object has a non-string key")
            bounded_json(key, depth + 1)
            bounded_json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ValueError("agenda array exceeds its member bound")
        for item in value:
            bounded_json(item, depth + 1)
    elif value is not None and type(value) not in {int, bool, float}:
        raise ValueError("agenda data is not JSON")
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("agenda data contains a non-finite number")
    elif type(value) is int and not -MAX_INTEGER <= value <= MAX_INTEGER:
        raise ValueError("agenda data exceeds its integer bound")


def _positive(value) -> bool:
    return type(value) is int and 0 < value <= MAX_INTEGER


def _utc(value) -> bool:
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    except ValueError:
        return False


def validate_snapshot(frame: dict, project: str, worktree: str, session: str) -> None:
    """Validate read and feed snapshots without importing the domain owner."""
    bounded_json(frame)
    if not isinstance(frame, dict):
        raise ValueError("agenda publication is not an object")
    fields = _REPLY_FIELDS | ({"type"} if "type" in frame else set())
    if set(frame) != fields:
        raise ValueError("agenda publication has invalid fields")
    if len(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1 > MAX_FRAME_BYTES:
        raise ValueError("agenda frame exceeds its bound")
    if (
        frame.get("ok") is not True or type(frame.get("contract_version")) is not int
        or frame["contract_version"] != 1
        or (frame.get("project"), frame.get("worktree_id"), frame.get("session_id"))
        != (project, worktree, session)
        or not isinstance(frame.get("generation"), str) or not _HEX.fullmatch(frame["generation"])
        or type(frame.get("sequence")) is not int or not 0 <= frame["sequence"] <= 2**63 - 1
    ):
        raise ValueError("agenda publication has an invalid contract or selected source")
    availability, observation = frame.get("availability"), frame.get("observation")
    if not isinstance(availability, str):
        raise ValueError("agenda publication has non-string availability")
    if availability in {"not-published", "head-changed"}:
        if observation is not None or "observation" not in frame:
            raise ValueError("unavailable agenda publication retained active data")
        return
    if availability != "observed" or not isinstance(observation, dict):
        raise ValueError("agenda publication has invalid availability or observation")
    bounded_json(observation, depth=4)
    if len(json.dumps(observation, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1 > MAX_BODY_BYTES:
        raise ValueError("agenda observation exceeds its body bound")
    source, snapshot = observation.get("source"), observation.get("snapshot")
    if (
        set(observation) != {"schema", "version", "source", "revision", "committed_at", "snapshot"}
        or
        observation.get("schema") != "efforts.agenda-observation"
        or type(observation.get("version")) is not int or observation["version"] != 1
        or not isinstance(source, dict)
        or set(source) != {"worktree", "session", "store_id"}
        or (source.get("worktree"), source.get("session")) != (worktree, session)
        or not isinstance(source.get("store_id"), str) or not _HEX.fullmatch(source["store_id"])
        or not _positive(observation.get("revision"))
        or not _utc(observation.get("committed_at"))
        or not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS
        or snapshot["worktree"] != worktree or type(snapshot["revision"]) is not int
        or snapshot["revision"] != observation["revision"]
        or not isinstance(snapshot["status"], str) or not snapshot["status"]
        or type(snapshot["cursor_version"]) is not int or snapshot["cursor_version"] not in {1, 2}
    ):
        raise ValueError("agenda observation has an invalid identity, revision or snapshot")
    for field in (
        "stack", "exit_criteria", "queued", "notes", "deferred", "due_rechecks",
    ):
        if not isinstance(snapshot[field], list):
            raise ValueError(f"agenda snapshot has invalid {field}")
    for field in ("stack", "exit_criteria", "queued", "notes", "due_rechecks"):
        if any(not isinstance(item, str) for item in snapshot[field]):
            raise ValueError(f"agenda snapshot has invalid {field} text")
    if (
        not isinstance(snapshot["root"], str)
        or not isinstance(snapshot["declared_outcomes"], dict)
        or any(not isinstance(value, str) for value in snapshot["declared_outcomes"].values())
        or type(snapshot["parked"]) is not int or not 0 <= snapshot["parked"] <= MAX_INTEGER
    ):
        raise ValueError("agenda snapshot has invalid root, outcomes or parked count")
    for field in ("next_action", "next_revisit_at"):
        if snapshot[field] is not None and not isinstance(snapshot[field], str):
            raise ValueError(f"agenda snapshot has invalid {field}")


class AgendaFeed:
    """One connection, one atomic first snapshot, one resident generation."""

    def __init__(self, project: str, worktree: str, session: str) -> None:
        self.identity = (project, worktree, session)
        self.current: dict | None = None
        self.closed = False

    def receive(self, frame: dict) -> dict | None:
        bounded_json(frame)
        if not isinstance(frame, dict) or self.closed:
            raise ValueError("agenda feed emitted an invalid or post-terminal frame")
        kind = frame.get("type")
        if not isinstance(kind, str):
            raise ValueError("agenda feed emitted a non-string frame type")
        if kind == "error":
            error = frame.get("error")
            if (
                set(frame) != {"type", "ok", "contract_version", "error"}
                or frame.get("ok") is not False or type(frame.get("contract_version")) is not int
                or frame["contract_version"] != 1 or not isinstance(error, dict)
                or set(error) != {"code", "message"}
                or not isinstance(error.get("code"), str) or not error["code"]
                or not isinstance(error.get("message"), str) or not error["message"]
            ):
                raise ValueError("agenda feed omitted its structured error")
            raise AgendaUnavailable(f"{error['code']}: {error['message']}")
        if kind in {"heartbeat", "going-away"}:
            if (
                set(frame) != {"type", "generation", "sequence"} | ({"reason"} if kind == "going-away" else set())
                or self.current is None
                or frame.get("generation") != self.current["generation"]
                or type(frame.get("sequence")) is not int
                or frame["sequence"] != self.current["sequence"]
            ):
                raise ValueError("agenda control frame changed generation or sequence")
            if kind == "going-away":
                if not isinstance(frame.get("reason"), str) or not frame["reason"]:
                    raise ValueError("agenda going-away omitted its reason")
                self.closed = True
                raise AgendaUnavailable(f"provider going away: {frame['reason']}", retry=True)
            return None
        if kind not in {"snapshot", "update"}:
            raise ValueError("agenda feed emitted an unknown frame type")
        validate_snapshot(frame, *self.identity)
        if self.current is None:
            if kind != "snapshot":
                raise ValueError("agenda feed did not start with an atomic snapshot")
        else:
            previous = self.current
            if (
                kind != "update" or frame["generation"] != previous["generation"]
                or frame["sequence"] <= previous["sequence"]
                or (frame["availability"], frame["observation"])
                == (previous["availability"], previous["observation"])
            ):
                raise ValueError("agenda feed repeated or regressed its publication")
            old, new = previous["observation"], frame["observation"]
            if old and new and old["source"]["store_id"] == new["source"]["store_id"]:
                if new["revision"] <= old["revision"]:
                    raise ValueError("agenda observation regressed its store revision")
        self.current = frame
        if frame["availability"] == "head-changed":
            self.closed = True
        return frame
