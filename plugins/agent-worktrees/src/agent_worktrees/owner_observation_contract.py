"""Strict passive owner-observation frames; declarations are not running support."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

from .owner_observation_schema import ACTOR_KINDS as ACTOR_KINDS
from .owner_observation_schema import AVAILABILITY as AVAILABILITY
from .owner_observation_schema import BODY_BYTES as BODY_BYTES
from .owner_observation_schema import BODY_FIELDS as BODY_FIELDS
from .owner_observation_schema import COMPLETENESS as COMPLETENESS
from .owner_observation_schema import CORRELATION_FIELDS as CORRELATION_FIELDS
from .owner_observation_schema import DURABILITY as DURABILITY
from .owner_observation_schema import ENTRY_FIELDS as ENTRY_FIELDS
from .owner_observation_schema import EVIDENCE_KINDS as EVIDENCE_KINDS
from .owner_observation_schema import FEED_CAPABILITY as FEED_CAPABILITY
from .owner_observation_schema import FRAME_BYTES as FRAME_BYTES
from .owner_observation_schema import FRAME_KINDS as FRAME_KINDS
from .owner_observation_schema import GAP_REASONS as GAP_REASONS
from .owner_observation_schema import HEADER_FIELDS as HEADER_FIELDS
from .owner_observation_schema import HISTORY_CAPABILITY as HISTORY_CAPABILITY
from .owner_observation_schema import KINDS as KINDS
from .owner_observation_schema import MAX_POSITION as MAX_POSITION
from .owner_observation_schema import PAGE_ENTRIES as PAGE_ENTRIES
from .owner_observation_schema import QUALIFICATIONS as QUALIFICATIONS
from .owner_observation_schema import RELATION_KINDS as RELATION_KINDS
from .owner_observation_schema import VERSION as VERSION

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z")
_HEX = re.compile(r"[0-9a-f]{32}\Z")


class ObservationError(ValueError):
    def __init__(self, message: str, code: str = "invalid-request") -> None:
        super().__init__(message)
        self.code = code


def exact(value: Any, fields: set | frozenset) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ObservationError("invalid owner observation fields")


def enum(value: Any, values: frozenset, name: str) -> str:
    if not isinstance(value, str) or value not in values:
        raise ObservationError(f"invalid {name}")
    return value


def identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None or value in {".", ".."}:
        raise ObservationError(f"invalid {name}")
    return value


def utf8(value: str) -> bytes:
    if not isinstance(value, str):
        raise ObservationError("owner observation text must be a string")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ObservationError("malformed Unicode in owner observation") from error


def reference(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(utf8(value)) > 512
        or any(char.isspace() or ord(char) < 32 or char in "\x7f[]<>" for char in value)
    ):
        raise ObservationError(f"invalid {name}")
    return value


def generation(value: Any) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ObservationError("invalid owner generation")
    return value


def timestamp(value: Any, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ObservationError("invalid observation timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ObservationError("invalid observation timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ObservationError("observation timestamp must include a timezone")


def bounded(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ObservationError("owner observation exceeds nesting bound")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -MAX_POSITION <= value <= MAX_POSITION:
            raise ObservationError("owner observation exceeds integer bound")
    elif type(value) is float:
        if not math.isfinite(value):
            raise ObservationError("owner observation contains a nonfinite number")
    elif isinstance(value, str):
        if len(utf8(value)) > 4096 or "\x00" in value:
            raise ObservationError("owner observation exceeds text bound")
    elif isinstance(value, (dict, list)):
        if len(value) > 128:
            raise ObservationError("owner observation exceeds member bound")
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ObservationError("owner observation key must be text")
                bounded(key, depth + 1)
                bounded(item, depth + 1)
        else:
            for item in value:
                bounded(item, depth + 1)
    else:
        raise ObservationError("owner observation is not finite JSON")


def encode(value: Any, limit: int = FRAME_BYTES) -> str:
    bounded(value)
    text = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    if len(utf8(text)) + 1 > limit:
        raise ObservationError("owner observation exceeds byte bound", "oversized")
    return text


def _object(pairs: list) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ObservationError("duplicate owner observation JSON key")
        value[key] = item
    return value


def _nonfinite(_value: str) -> None:
    raise ObservationError("nonfinite owner observation JSON")


def decode(raw: bytes, limit: int = FRAME_BYTES) -> Any:
    if not isinstance(raw, bytes):
        raise ObservationError("owner observation wire input must be bytes")
    if len(raw) > limit:
        raise ObservationError("owner observation exceeds byte bound", "oversized")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_nonfinite
        )
        bounded(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ObservationError("malformed owner observation JSON") from error


@dataclass(frozen=True)
class Selection:
    project: str
    worktree_id: str
    expected_head_session_id: str | None

    def __post_init__(self) -> None:
        identity(self.project, "project")
        identity(self.worktree_id, "worktree")
        if self.expected_head_session_id is not None:
            identity(self.expected_head_session_id, "selected head")


@dataclass(frozen=True)
class Source:
    project: str
    worktree_id: str
    source: str
    source_generation: str
    resident_generation: str
    head_session_id: str | None

    def __post_init__(self) -> None:
        Selection(self.project, self.worktree_id, self.head_session_id)
        reference(self.source, "source")
        generation(self.source_generation)
        generation(self.resident_generation)

    def namespace(self) -> str:
        value = utf8(encode([self.project, self.worktree_id]))
        return hashlib.sha256(value).hexdigest()[:32]


def position(source: Source, sequence: int) -> str:
    if type(sequence) is not int or not 0 <= sequence <= MAX_POSITION:
        raise ObservationError("invalid owner position")
    raw = utf8(encode([source.namespace(), source.source_generation, sequence]))
    return "ow1." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def sequence(token: str, source: Source) -> int:
    reference(token, "owner position")
    if not token.startswith("ow1."):
        raise ObservationError("invalid owner position")
    try:
        text = token[4:]
        raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
        values = decode(raw, 512)
    except (ValueError, binascii.Error) as error:
        raise ObservationError("invalid owner position") from error
    if not isinstance(values, list) or len(values) != 3 or values[0] != source.namespace():
        raise ObservationError("owner position belongs to another selected source")
    if values[1] != source.source_generation:
        raise ObservationError("owner position generation replaced", "source-replaced")
    result = values[2]
    if (
        type(result) is not int
        or not 0 <= result <= MAX_POSITION
        or position(source, result) != token
    ):
        raise ObservationError("invalid owner position")
    return result


def request(value: dict, *, history: bool = False) -> tuple[Selection, dict]:
    fields = {"contract_version", "project", "worktree_id", "expected_head_session_id"}
    fields |= {"source_generation", "before_cursor", "limit"} if history else {"after_cursor"}
    exact(value, fields)
    if type(value["contract_version"]) is not int or value["contract_version"] != VERSION:
        raise ObservationError("unsupported owner observation contract", "unsupported")
    selected = Selection(value["project"], value["worktree_id"], value["expected_head_session_id"])
    if history:
        generation(value["source_generation"])
        if type(value["limit"]) is not int or not 1 <= value["limit"] <= PAGE_ENTRIES:
            raise ObservationError("invalid owner history page limit")
        reference(value["before_cursor"], "before position", nullable=True)
    else:
        reference(value["after_cursor"], "after position", nullable=True)
    return selected, value


def entry(value: dict, *, stored: bool = False, post_save: bool = False) -> None:
    exact(value, ENTRY_FIELDS | ({"event_id", "cursor", "head_session_id"} if stored else set()))
    bounded(value, 4)
    enum(value["kind"], KINDS, "event kind")
    evidence = enum(value["evidence_kind"], EVIDENCE_KINDS, "evidence kind")
    reference(value["native_kind"], "native event kind", nullable=True)
    timestamp(value["occurred_at"], nullable=True)
    timestamp(value["observed_at"])
    exact(value["actor"], {"kind", "id", "qualification", "session_id"})
    enum(value["actor"]["kind"], ACTOR_KINDS, "actor kind")
    enum(value["actor"]["qualification"], QUALIFICATIONS, "actor qualification")
    reference(value["actor"]["id"], "actor identity", nullable=True)
    reference(value["actor"]["session_id"], "actor session", nullable=True)
    if value["actor"]["qualification"] == "authoritative" and value["actor"]["id"] is None:
        raise ObservationError("unknown actor identity cannot be authoritative")
    exact(value["correlation"], CORRELATION_FIELDS)
    for item in value["correlation"].values():
        reference(item, "correlation identity", nullable=True)
    if not isinstance(value["relations"], list):
        raise ObservationError("relations must be a list")
    for relation in value["relations"]:
        exact(relation, {"kind", "ref", "qualification"})
        enum(relation["kind"], RELATION_KINDS, "relation kind")
        reference(relation["ref"], "relation reference")
        enum(relation["qualification"], QUALIFICATIONS, "relation qualification")
    if not isinstance(value["record_fence"], dict) or not isinstance(value["payload"], dict):
        raise ObservationError("record fence and payload must be objects")
    exact(value["source_ref"], {"source", "generation", "record_id", "payload_ref"})
    reference(value["source_ref"]["source"], "record source")
    reference(value["source_ref"]["generation"], "record incarnation")
    reference(value["source_ref"]["record_id"], "source record", nullable=True)
    reference(value["source_ref"]["payload_ref"], "payload reference", nullable=True)
    for name in ("summary", "details", "reason", "phase"):
        if value[name] is not None and not isinstance(value[name], str):
            raise ObservationError(f"invalid literal {name}")
    if evidence == "persisted-mutation" and (not post_save or not value["record_fence"]):
        raise ObservationError("persisted mutation requires explicit post-save record evidence")
    if evidence == "retained-event" and value["source_ref"]["record_id"] is None:
        raise ObservationError("retained events require an identified source record")
    if evidence == "observed-change" and value["kind"] not in {"observed-change", "source-event"}:
        raise ObservationError("an observed difference is not a canonical mutation event")
    if evidence == "reported-lifecycle" and (
        value["kind"] not in {"contribution-reported", "source-event"}
        or value["actor"]["qualification"] == "authoritative"
    ):
        raise ObservationError("reported lifecycle cannot claim canonical mutation or execution")
    if stored:
        reference(value["event_id"], "event identity")
        reference(value["cursor"], "event position")
        reference(value["head_session_id"], "event head", nullable=True)
    encode(value, BODY_BYTES - 1024)


def frame(kind: str, source: Source, cursor: str, body: dict) -> dict:
    enum(kind, FRAME_KINDS - {"error"}, "frame kind")
    exact(body, BODY_FIELDS[kind])
    encode(body, BODY_BYTES)
    value = {
        "type": kind,
        "contract_version": VERSION,
        "source": source.source,
        "source_generation": source.source_generation,
        "resident_generation": source.resident_generation,
        "project": source.project,
        "worktree_id": source.worktree_id,
        "head_session_id": source.head_session_id,
        "cursor": cursor,
        **body,
    }
    validate_frame(value)
    return value


def error(code: str, message: str) -> dict:
    reference(code, "error code")
    value = {
        "type": "error",
        "contract_version": VERSION,
        "error": {"code": code, "message": message[:1024] or code},
    }
    encode(value)
    return value


def validate_frame(value: dict) -> None:
    if not isinstance(value, dict):
        raise ObservationError("owner frame must be an object")
    kind = enum(value.get("type"), FRAME_KINDS, "frame kind")
    if type(value.get("contract_version")) is not int or value["contract_version"] != VERSION:
        raise ObservationError("unsupported owner frame contract")
    if kind == "error":
        exact(value, {"type", "contract_version", "error"})
        exact(value["error"], {"code", "message"})
        reference(value["error"]["code"], "error code")
        if not isinstance(value["error"]["message"], str) or not value["error"]["message"]:
            raise ObservationError("owner error needs a message")
    else:
        exact(value, HEADER_FIELDS | BODY_FIELDS[kind])
        source = Source(
            *(
                value[key]
                for key in (
                    "project",
                    "worktree_id",
                    "source",
                    "source_generation",
                    "resident_generation",
                    "head_session_id",
                )
            )
        )
        sequence(value["cursor"], source)
        if kind in {"snapshot", "delta", "history-page", "gap"}:
            enum(value["availability"], AVAILABILITY, "availability")
            enum(value["completeness"], COMPLETENESS, "completeness")
            enum(value["durability"], DURABILITY, "durability")
            if value["history_scope"] != "retained-range":
                raise ObservationError("history coverage is not lifetime history")
            timestamp(value["observed_at"])
            sequence(value["history_floor"], source)
        if kind in {"snapshot", "delta", "history-page"}:
            if not isinstance(value["entries"], list) or len(value["entries"]) > PAGE_ENTRIES:
                raise ObservationError("owner entries exceed page bound")
            seen = set()
            positions = []
            for item in value["entries"]:
                entry(item, stored=True, post_save=True)
                if item["event_id"] in seen:
                    raise ObservationError("duplicate owner event identity")
                seen.add(item["event_id"])
                positions.append(sequence(item["cursor"], source))
            if any(left >= right for left, right in pairwise(positions)):
                raise ObservationError("owner entry positions did not advance")
            if positions and (
                positions[-1] > sequence(value["cursor"], source)
                or positions[0] <= sequence(value["history_floor"], source)
            ):
                raise ObservationError("owner entries violate their retained range")
        if kind in {"snapshot", "delta"}:
            if value["facts"] is not None and not isinstance(value["facts"], dict):
                raise ObservationError("owner facts must be an object or unknown")
            if value["availability"] == "observed" and source.head_session_id is None:
                raise ObservationError("unknown canonical head cannot qualify active facts")
            if value["availability"] != "observed" and value["facts"] is not None:
                raise ObservationError("unqualified facts must not remain active")
            reference(value["base_cursor"], "base position", nullable=kind == "snapshot")
            if kind == "delta" and sequence(value["base_cursor"], source) >= sequence(
                value["cursor"], source
            ):
                raise ObservationError("owner delta position did not advance")
            if kind == "delta":
                base, tip = (
                    sequence(value["base_cursor"], source),
                    sequence(value["cursor"], source),
                )
                if (
                    len(positions) != tip - base
                    or positions[0] != base + 1
                    or positions[-1] != tip
                ):
                    raise ObservationError("owner delta is not complete indexed replay")
        if kind == "snapshot":
            if (
                type(value["replay_complete"]) is not bool
                or value["replay_barrier"] != value["cursor"]
            ):
                raise ObservationError("invalid atomic snapshot replay barrier")
            if value["base_cursor"] is not None and value["replay_complete"]:
                base, tip = (
                    sequence(value["base_cursor"], source),
                    sequence(value["cursor"], source),
                )
                if base > tip:
                    raise ObservationError("snapshot base position is ahead of its barrier")
                if len(positions) != tip - base or (
                    positions and (positions[0] != base + 1 or positions[-1] != tip)
                ):
                    raise ObservationError("snapshot claimed incomplete replay as complete")
        if kind == "history-page":
            reference(value["before_cursor"], "before position", nullable=True)
            reference(value["next_before"], "next-before position", nullable=True)
            if type(value["has_more"]) is not bool:
                raise ObservationError("invalid owner page completeness")
            if value["before_cursor"] is not None:
                before = sequence(value["before_cursor"], source)
                if positions and positions[-1] >= before:
                    raise ObservationError("page violated its before boundary")
            if value["has_more"]:
                if not positions or value["next_before"] != value["entries"][0]["cursor"]:
                    raise ObservationError("page lost its next-before continuation")
            elif value["next_before"] is not None:
                raise ObservationError("complete page unexpectedly has a continuation")
        if kind == "gap":
            enum(value["reason"], GAP_REASONS, "gap reason")
            if value["reconcile"] != "snapshot-and-history" or not isinstance(
                value["details"], str
            ):
                raise ObservationError("gap needs an explicit reconciliation path")
            reference(value["base_cursor"], "gap base position", nullable=True)
        if kind == "going-away":
            reference(value["reason"], "retirement reason")
        encode({key: item for key, item in value.items() if key not in HEADER_FIELDS}, BODY_BYTES)
    encode(value)
