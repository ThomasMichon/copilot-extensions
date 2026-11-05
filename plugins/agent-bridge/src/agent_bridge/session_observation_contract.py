"""Strict, bounded read contract for admitted Bridge session observations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

OBSERVATIONS = "bridge-session-observations-v1"
HISTORY = "bridge-session-observation-history-v1"
CURRENT_REQUESTS = "bridge-session-current-requests-v1"
VERSION = 1
MAX_BODY = 32 * 1024
MAX_FRAME = 35 * 1024
MAX_PAGE = 64
_PREFIX = "abso1."


class ObservationError(ValueError):
    """A read cannot meet its identity, continuity or bounded-data contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError("invalid-json", "duplicate object field")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ObservationError("invalid-json", "nonfinite JSON value")


def encode(body: dict[str, Any]) -> bytes:
    """Encode a finite UTF-8 body within the negotiated bound."""
    try:
        raw = json.dumps(
            body,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ObservationError("invalid-source", "source is not bounded finite JSON") from exc
    if len(raw) > MAX_BODY:
        raise ObservationError("body-too-large", "observation body exceeds 32 KiB")
    return raw


def decode(raw: bytes) -> dict[str, Any]:
    """Decode bounded JSON without duplicate keys or nonfinite constants."""
    if len(raw) > MAX_BODY:
        raise ObservationError("body-too-large", "observation body exceeds 32 KiB")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ObservationError("invalid-json", "invalid observation JSON") from exc
    if not isinstance(body, dict):
        raise ObservationError("invalid-json", "observation body must be an object")
    return body


def text(value: Any, name: str, *, maximum: int = 256) -> str:
    """Validate an exact identifier, never normalize it into another identity."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise ObservationError("invalid-request", f"{name} must be bounded exact ASCII text")
    return value


@dataclass(frozen=True)
class Selection:
    """Requested binding; it is not authentication or resource authority."""

    session_id: str
    project: str | None
    worktree_id: str
    head_session_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project": self.project,
            "worktree_id": self.worktree_id,
            "head_session_id": self.head_session_id,
        }


@dataclass(frozen=True)
class ReadRequest:
    selection: Selection
    history: bool
    follow: bool
    position: str | None
    limit: int
    current_requests: bool = False

    @classmethod
    def parse(cls, session_id: str, pairs: list[tuple[str, str]], *, history: bool) -> ReadRequest:
        """Validate the actual HTTP query, including repeated/foreign fields."""
        query = _unique(pairs)
        allowed = {"contract_version", "project", "worktree_id", "head_session_id", "limit"}
        allowed |= {"before"} if history else {"after", "follow", "current_requests"}
        if set(query) - allowed or "contract_version" not in query or "worktree_id" not in query:
            raise ObservationError("invalid-request", "unknown or missing observation fields")
        if query["contract_version"] != "1":
            raise ObservationError("unsupported-version", "observation contract_version must be 1")
        limit_text = query.get("limit", "64")
        if (
            len(limit_text) > 2
            or not limit_text.isascii()
            or not limit_text.isdecimal()
            or str(int(limit_text)) != limit_text
        ):
            raise ObservationError("invalid-request", "limit must be an exact decimal integer")
        limit = int(limit_text)
        if not 1 <= limit <= MAX_PAGE:
            raise ObservationError("invalid-request", "limit must be between 1 and 64")
        follow = query.get("follow", "false")
        if follow not in {"true", "false"}:
            raise ObservationError("invalid-request", "follow must be true or false")
        current_requests = query.get("current_requests", "false")
        if current_requests not in {"true", "false"}:
            raise ObservationError("invalid-request", "current_requests must be true or false")
        position = query.get("before" if history else "after")
        if position is not None:
            text(position, "position", maximum=4096)
        return cls(
            Selection(
                text(session_id, "session_id"),
                text(query["project"], "project") if "project" in query else None,
                text(query["worktree_id"], "worktree_id"),
                text(query["head_session_id"], "head_session_id")
                if "head_session_id" in query
                else None,
            ),
            history,
            follow == "true",
            position,
            limit,
            current_requests == "true",
        )


def position(source: dict[str, Any], event_id: int) -> str:
    """Scope a native event offset to its actual source and incarnation."""
    payload = {"version": VERSION, "source": source, "event_id": event_id}
    return _PREFIX + base64.urlsafe_b64encode(encode(payload)).decode("ascii").rstrip("=")


def read_position(token: str, source: dict[str, Any]) -> int:
    """Reject foreign positions rather than translating them into a new source."""
    if not isinstance(token, str) or not token.startswith(_PREFIX) or len(token) > 4096:
        raise ObservationError("invalid-position", "invalid observation position")
    encoded = token[len(_PREFIX) :]
    try:
        body = decode(
            base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
        )
    except (ValueError, binascii.Error) as exc:
        raise ObservationError("invalid-position", "invalid observation position") from exc
    if (
        set(body) != {"version", "source", "event_id"}
        or type(body["version"]) is not int
        or body["version"] != VERSION
        or type(body["event_id"]) is not int
        or body["event_id"] < 0
    ):
        raise ObservationError("invalid-position", "invalid observation position fields")
    if body["source"] != source:
        raise ObservationError(
            "source-changed", "position belongs to a different source incarnation"
        )
    return body["event_id"]


def sse(body: dict[str, Any]) -> bytes:
    """Frame one bounded semantic body, never a raw event stream fallback."""
    raw = b"event: " + body["kind"].encode("ascii") + b"\ndata: " + encode(body) + b"\n\n"
    if len(raw) > MAX_FRAME:
        raise ObservationError("frame-too-large", "observation frame exceeds 35 KiB")
    return raw


def validate_frame(body: dict[str, Any], request: ReadRequest) -> None:
    """Validate action, exact binding, bounded page and native-position integrity."""
    expected = HISTORY if request.history else OBSERVATIONS
    allowed = (
        {"history-page", "error", "gap"}
        if request.history
        else {
            "snapshot",
            "delta",
            "heartbeat",
            "going-away",
            "error",
            "gap",
        }
    )
    if (
        body.get("schema") != expected
        or type(body.get("version")) is not int
        or body["version"] != VERSION
        or body.get("kind") not in allowed
    ):
        raise ObservationError("invalid-frame", "wrong observation action or version")
    selection = body.get("selection", body.get("source"))
    if not isinstance(selection, dict) or any(
        selection.get(key) != value for key, value in request.selection.as_dict().items()
    ):
        raise ObservationError("invalid-frame", "response selection mismatch")
    if body["kind"] in {"error", "gap", "going-away"}:
        return
    source = body.get("source")
    if not isinstance(source, dict) or source.get("provider") != "bridge":
        raise ObservationError("invalid-frame", "missing Bridge source")
    text(source.get("hosting_generation"), "hosting_generation")
    if source.get("journal_incarnation") is not None:
        text(source["journal_incarnation"], "journal_incarnation")
    entries = body.get("entries", [])
    if not isinstance(entries, list) or len(entries) > MAX_PAGE:
        raise ObservationError("invalid-frame", "invalid observation page size")
    prior = 0
    for item in entries:
        if not isinstance(item, dict) or type(item.get("native_event_id")) is not int:
            raise ObservationError("invalid-frame", "invalid native event record")
        event_id = item["native_event_id"]
        if event_id <= prior or read_position(item.get("id", ""), source) != event_id:
            raise ObservationError("invalid-frame", "duplicate or foreign native event identity")
        digest = hashlib.sha256(
            encode({key: value for key, value in item.items() if key != "content_sha256"})
        ).hexdigest()
        if item.get("content_sha256") != digest:
            raise ObservationError("invalid-frame", "conflicting native event content")
        prior = event_id
    for key in ("position", "next_before"):
        if body.get(key) is not None:
            read_position(body[key], source)
    if request.current_requests and body["kind"] != "heartbeat":
        from .session_current_requests import validate

        validate(body.get("current_request_state"))
    elif "current_request_state" in body:
        raise ObservationError("invalid-frame", "unexpected live current request state")
    encode(body)
