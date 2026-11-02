"""Optional committed agenda observation wire contract; no planning authority."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta

CAPABILITY = "worktree-agendas-v1"
V2_CAPABILITY = "worktree-agendas-v2"
CONTRACT_VERSIONS = (1, 2)
BODY_BYTES = 32 * 1024
FRAME_BYTES = 35 * 1024
MAX_SEQUENCE = (1 << 63) - 1
SNAPSHOT_FIELDS = frozenset(
    """
worktree revision status root stack next_action exit_criteria declared_outcomes
queued parked notes cursor_version deferred due_rechecks next_revisit_at
""".split()
)
REPLY_FIELDS = frozenset(
    """
ok contract_version generation sequence project worktree_id session_id availability observation
""".split()
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z")
_HEX = re.compile(r"[0-9a-f]{32}\Z")


class AgendaError(ValueError):
    def __init__(self, message: str, code: str = "invalid-request") -> None:
        super().__init__(message)
        self.code = code


def contract_version(value) -> int:
    if type(value) is not int or value not in CONTRACT_VERSIONS:
        raise AgendaError("unsupported agenda contract version")
    return value


def error_version(payload) -> int:
    value = payload.get("contract_version") if isinstance(payload, dict) else None
    return value if type(value) is int and value in CONTRACT_VERSIONS else 1


def capability(version: int) -> str:
    return CAPABILITY if contract_version(version) == 1 else V2_CAPABILITY


def failure(code: str, message: str, *, version: int = 1) -> dict:
    return {
        "ok": False,
        "contract_version": contract_version(version),
        "error": {"code": code, "message": message[:1024] or code},
    }


def identity(value, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None or value in {".", ".."}:
        raise AgendaError(f"invalid {name}")
    return value


def hex_id(value, name: str) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise AgendaError(f"invalid {name}")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AgendaError("duplicate JSON field")
        result[key] = value
    return result


def _nonfinite(_value):
    raise AgendaError("non-finite JSON number")


def bounded(value, depth=0) -> None:
    if depth > 16:
        raise AgendaError("agenda exceeds nesting bound")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -MAX_SEQUENCE <= value <= MAX_SEQUENCE:
            raise AgendaError("agenda exceeds integer bound")
    elif type(value) is float:
        if not math.isfinite(value):
            raise AgendaError("non-finite agenda number")
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > 4096 or "\x00" in value:
            raise AgendaError("agenda exceeds text bound or contains NUL")
    elif isinstance(value, (dict, list)):
        if len(value) > 128:
            raise AgendaError("agenda exceeds collection bound")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise AgendaError("agenda field is not text")
                bounded(key, depth + 1)
                bounded(child, depth + 1)
        else:
            for child in value:
                bounded(child, depth + 1)
    else:
        raise AgendaError("agenda contains a non-JSON value")


def encode(value, limit=FRAME_BYTES) -> str:
    bounded(value)
    result = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    if len(result.encode("utf-8")) + 1 > limit:
        raise AgendaError("agenda exceeds byte bound")
    return result


def decode(raw: bytes, limit=FRAME_BYTES):
    if len(raw) > limit:
        raise AgendaError("agenda exceeds byte bound")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_nonfinite
        )
        bounded(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AgendaError("malformed agenda JSON") from exc


def exact(value, fields) -> None:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise AgendaError("invalid agenda fields")


def selection(payload: dict) -> tuple[str, str, str | None]:
    from . import config as cfg

    exact(payload, {"contract_version", "project", "worktree_id", "session_id"})
    contract_version(payload["contract_version"])
    project = identity(payload["project"], "project")
    if cfg._PROJECT_NAME_RE.fullmatch(project) is None:
        raise AgendaError("invalid project")
    wid = identity(payload["worktree_id"], "worktree id")
    session = payload["session_id"]
    if session is not None:
        identity(session, "session id")
    return project, wid, session


def _utc(value, name: str) -> None:
    if not isinstance(value, str) or "T" not in value or len(value) > 64:
        raise AgendaError(f"invalid {name} UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgendaError(f"invalid {name} UTC timestamp") from exc
    if parsed.utcoffset() != timedelta(0):
        raise AgendaError(f"{name} timestamp must be UTC")


def _revisit_timestamp(value) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise AgendaError("invalid next revisit timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgendaError("invalid next revisit timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AgendaError("next revisit timestamp must include a timezone")


def observation(body: dict, *, version: int | None = None) -> str:
    bounded(body, depth=4)
    exact(body, {"schema", "version", "source", "revision", "committed_at", "snapshot"})
    if body["schema"] != "efforts.agenda-observation" or (
        type(body["version"]) is not int or body["version"] not in (1, 2)
    ):
        raise AgendaError("unsupported committed agenda schema")
    source = body["source"]
    exact(source, {"worktree", "session", "store_id"})
    wid = identity(source["worktree"], "source worktree")
    if source["session"] is not None:
        identity(source["session"], "source session")
    hex_id(source["store_id"], "source store id")
    revision = body["revision"]
    if type(revision) is not int or not 1 <= revision <= MAX_SEQUENCE:
        raise AgendaError("invalid committed revision")
    _utc(body["committed_at"], "committed")
    snapshot = body["snapshot"]
    exact(snapshot, SNAPSHOT_FIELDS)
    if snapshot["worktree"] != wid or (
        type(snapshot["revision"]) is not int or snapshot["revision"] != revision
    ):
        raise AgendaError("snapshot identity or revision mismatch")
    statuses = {"working", "waiting", "complete"}
    statuses.add("steering" if body["version"] == 1 else "replanning")
    if not isinstance(snapshot["status"], str) or snapshot["status"] not in statuses:
        raise AgendaError("invalid snapshot status")
    cursors = (1, 2) if body["version"] == 1 else (4,)
    if type(snapshot["cursor_version"]) is not int or snapshot["cursor_version"] not in cursors:
        raise AgendaError("invalid cursor version")
    for field in ("stack", "exit_criteria", "queued", "notes", "deferred", "due_rechecks"):
        if not isinstance(snapshot[field], list):
            raise AgendaError(f"invalid snapshot {field}")
    if not isinstance(snapshot["root"], str) or not isinstance(
        snapshot["declared_outcomes"], dict
    ):
        raise AgendaError("invalid snapshot root or declared outcomes")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in snapshot["declared_outcomes"].items()
    ):
        raise AgendaError("invalid declared outcome text")
    if type(snapshot["parked"]) is not int or not 0 <= snapshot["parked"] <= MAX_SEQUENCE:
        raise AgendaError("invalid parked count")
    for field in ("stack", "exit_criteria", "queued", "notes", "due_rechecks"):
        if any(not isinstance(item, str) for item in snapshot[field]):
            raise AgendaError(f"invalid snapshot {field} text")
    for field in ("next_action", "next_revisit_at"):
        if snapshot[field] is not None and not isinstance(snapshot[field], str):
            raise AgendaError(f"invalid snapshot {field}")
    if snapshot["next_revisit_at"] is not None:
        _revisit_timestamp(snapshot["next_revisit_at"])
    encoded = encode(body, BODY_BYTES)
    if version is not None and contract_version(version) == 1 and body["version"] != 1:
        raise AgendaError("cursor-4 observation requires worktree-agendas-v2", "unsupported")
    return encoded


def validate_reply(result: dict, request: dict, generation: str) -> dict:
    exact(result, REPLY_FIELDS)
    selection(request)
    version = contract_version(request["contract_version"])
    hex_id(result["generation"], "response generation")
    if (
        result["ok"] is not True
        or type(result["contract_version"]) is not int
        or (
            result["contract_version"] != version
            or result["generation"] != generation
            or any(result[key] != request[key] for key in ("project", "worktree_id", "session_id"))
            or type(result["sequence"]) is not int
            or not 0 <= result["sequence"] <= MAX_SEQUENCE
        )
    ):
        raise AgendaError(
            "agenda reply identity, generation or sequence mismatch", "invalid-response"
        )
    availability, body = result["availability"], result["observation"]
    if not isinstance(availability, str) or availability not in {
        "observed",
        "not-published",
        "head-changed",
    }:
        raise AgendaError("invalid agenda availability", "invalid-response")
    if availability == "observed":
        observation(body, version=version)
        if body["source"]["worktree"] != request["worktree_id"] or (
            body["source"]["session"] != request["session_id"] or request["session_id"] is None
        ):
            raise AgendaError("agenda observation source mismatch", "invalid-response")
    elif body is not None:
        raise AgendaError("unqualified agenda must not expose an observation", "invalid-response")
    encode(result)
    return result
