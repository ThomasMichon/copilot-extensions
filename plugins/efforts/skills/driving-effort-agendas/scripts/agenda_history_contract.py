"""Passive agenda history positions; a store identity is not a restore epoch."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any

from agenda import AgendaError, _text, decode
from agenda_store import MAX_REVISION, encode

CAPABILITY = "agenda-history-page-v1"
VERSION = 1
BODY_BYTES = 32 * 1024
FRAME_BYTES = 35 * 1024
PAGE_ENTRIES = 64
_STORE_ID = re.compile(r"[0-9a-f]{32}\Z")


class HistoryError(AgendaError):
    def __init__(self, message: str, code: str = "invalid-request") -> None:
        super().__init__(message)
        self.code = code


def version(value: Any) -> None:
    if type(value) is not int:
        raise HistoryError("history contract version must be an integer")
    if value != VERSION:
        raise HistoryError("unsupported agenda history contract", "unsupported")


def text(value: Any, label: str) -> str:
    try:
        result = _text(value, label)
        if len(result.encode("utf-8")) > 1024:
            raise HistoryError(f"{label} exceeds 1 KiB")
    except (AgendaError, UnicodeError) as error:
        raise HistoryError(f"invalid {label}: {error}") from error
    return result


def store_id(value: Any) -> str:
    if not isinstance(value, str) or _STORE_ID.fullmatch(value) is None:
        raise HistoryError("invalid agenda store identity")
    return value


@dataclass(frozen=True)
class Source:
    worktree: str
    store_id: str
    store_version: int

    def __post_init__(self) -> None:
        text(self.worktree, "selected worktree")
        store_id(self.store_id)
        if type(self.store_version) is not int or self.store_version not in (1, 2):
            raise HistoryError("unsupported agenda store version", "unsupported")

    def body(self) -> dict[str, Any]:
        return {"provider": "agenda", **asdict(self)}


def position(source: Source, revision: int) -> str:
    if type(revision) is not int or not 0 <= revision <= MAX_REVISION:
        raise HistoryError("invalid agenda history revision")
    namespace = hashlib.sha256(encode(source.body()).encode("utf-8")).hexdigest()
    body = encode({"namespace": namespace, "revision": revision}).encode("utf-8")
    return "ag1." + base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def revision(token: str, source: Source) -> int:
    text(token, "agenda history cursor")
    if not token.startswith("ag1."):
        raise HistoryError("invalid agenda history cursor")
    try:
        raw = base64.b64decode(token[4:] + "=" * (-len(token[4:]) % 4),
                              altchars=b"-_", validate=True)
        value = decode(raw, "agenda history cursor", limit=512)
    except (ValueError, binascii.Error) as error:
        raise HistoryError("invalid agenda history cursor") from error
    if (
        not isinstance(value, dict) or set(value) != {"namespace", "revision"}
        or type(value["revision"]) is not int
        or not 0 <= value["revision"] <= MAX_REVISION
        or token != position(source, value["revision"])
    ):
        raise HistoryError("agenda cursor belongs to another source", "source-replaced")
    return value["revision"]


def encoded(value: Any, limit: int = FRAME_BYTES) -> bytes:
    raw = encode(value).encode("utf-8") + b"\n"
    if len(raw) > limit:
        raise HistoryError("agenda history frame exceeds its byte bound", "oversized")
    return raw


def frame(kind: str, source: Source, head: int, body: dict[str, Any]) -> dict[str, Any]:
    encoded(body, BODY_BYTES)
    result = {"schema": "efforts.agenda-history", "contract_version": VERSION,
              "type": kind, "source": source.body(), "cursor": position(source, head), **body}
    encoded(result)
    return result


def failure(error: AgendaError) -> dict[str, Any]:
    return {"schema": "efforts.agenda-history", "contract_version": VERSION,
            "type": "error", "error": {
                "code": error.code if isinstance(error, HistoryError) else "unavailable",
                "message": str(error).encode("utf-8", "backslashreplace").decode("utf-8")[:1024]
                or "agenda history unavailable"}}
