"""Typed native index responses; no transport, credential or authorization side effects."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .records import (
    MAX_QUERY_BYTES,
    MAX_RESPONSE_BYTES,
    ContractError,
    _installation_id,
    _integer,
    _object,
    _text,
    _tuple,
    decode_json,
)


class BackendResponseError(ContractError):
    """A backend explicitly failed or returned invalid identity/data."""


def _string(value: object, field: str, maximum: int, *, empty: bool = True) -> None:
    if not isinstance(value, str):
        raise BackendResponseError(f"{field} must be text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise BackendResponseError(f"{field} must be UTF-8") from exc
    if size > maximum or (not empty and not value):
        raise BackendResponseError(f"{field} exceeds its text bounds")


@dataclass(frozen=True)
class IndexHealth:
    installation_id: str
    runtime_version: str
    state: str
    can_accept_reads: bool

    def __post_init__(self) -> None:
        _installation_id(self.installation_id)
        _text(self.runtime_version, "runtime_version", 128)
        if self.state not in ("ok", "passive", "draining"):
            raise BackendResponseError("unsupported index process state")
        if type(self.can_accept_reads) is not bool or self.can_accept_reads != (self.state == "ok"):
            raise BackendResponseError("inconsistent index read-admission state")

    @classmethod
    def from_backend(cls, payload: bytes, *, expected_installation_id: str) -> IndexHealth:
        _installation_id(expected_installation_id)
        try:
            return cls._parse_backend(payload, expected_installation_id)
        except BackendResponseError:
            raise
        except ContractError:
            raise BackendResponseError("invalid backend health response") from None

    @classmethod
    def _parse_backend(cls, payload: bytes, expected_installation_id: str) -> IndexHealth:
        data = decode_json(payload, max_bytes=MAX_RESPONSE_BYTES)
        required = {"status", "plugin", "version", "passive", "promoted", "installationId"}
        if not required <= data.keys() or data["plugin"] != "agent-index":
            raise BackendResponseError("backend is not the expected index service")
        if data["installationId"] != expected_installation_id:
            raise BackendResponseError("backend installation identity mismatch")
        if type(data["passive"]) is not bool or type(data["promoted"]) is not bool:
            raise BackendResponseError("invalid backend promotion evidence")
        state = data["status"]
        if state not in ("ok", "passive", "draining"):
            raise BackendResponseError("unsupported backend process state")
        if (
            data["passive"] == data["promoted"]
            or (state == "ok" and not data["promoted"])
            or (state == "passive" and data["promoted"])
        ):
            raise BackendResponseError("inconsistent backend process/promotion evidence")
        return cls(data["installationId"], data["version"], state, state == "ok")

    def to_dict(self) -> dict:
        # Backend process tokens and PIDs are deliberately not part of the public health DTO.
        return asdict(self)


@dataclass(frozen=True)
class IndexHit:
    id: str
    chunk_id: str
    score: float
    file_path: str
    line_start: int | None
    line_end: int | None
    source: str
    chunk_type: str
    language: str | None
    content: str

    def __post_init__(self) -> None:
        _string(self.id, "id", 512, empty=False)
        _string(self.chunk_id, "chunk_id", 512, empty=False)
        if self.id != self.chunk_id:
            raise BackendResponseError("index hit identifiers disagree")
        try:
            finite_score = type(self.score) in (int, float) and math.isfinite(self.score)
        except OverflowError as exc:
            raise BackendResponseError("index score exceeds its numeric bounds") from exc
        if not finite_score:
            raise BackendResponseError("index score must be finite")
        for name in ("file_path", "source", "chunk_type"):
            _string(getattr(self, name), name, 4096)
        for name in ("line_start", "line_end"):
            line = getattr(self, name)
            if line is not None:
                _integer(line, name, minimum=0)
        if self.language is not None:
            _string(self.language, "language", 256)
        _string(self.content, "content", MAX_RESPONSE_BYTES)

    @classmethod
    def from_dict(cls, value: object) -> IndexHit:
        data = _object(value, {
            "id", "chunk_id", "score", "file_path", "line_start", "line_end",
            "source", "chunk_type", "language", "content",
        })
        return cls(**data)


@dataclass(frozen=True)
class IndexSearchResult:
    query: str
    available: bool
    hits: tuple[IndexHit, ...]

    def __post_init__(self) -> None:
        _text(self.query, "query", MAX_QUERY_BYTES)
        if self.available is not True:
            raise BackendResponseError("backend search is unavailable")
        if type(self.hits) is not tuple:
            raise BackendResponseError("hits must be an immutable collection")
        values = _tuple(self.hits, "hits", 100, empty=True)
        if any(not isinstance(hit, IndexHit) for hit in values):
            raise BackendResponseError("hits must contain validated index records")

    @classmethod
    def from_backend(
        cls, payload: bytes, *, expected_query: str, requested_limit: int,
    ) -> IndexSearchResult:
        _text(expected_query, "expected_query", MAX_QUERY_BYTES)
        _integer(requested_limit, "requested_limit", maximum=100)
        try:
            return cls._parse_backend(payload, expected_query, requested_limit)
        except BackendResponseError:
            raise
        except ContractError:
            raise BackendResponseError("invalid backend search response") from None

    @classmethod
    def _parse_backend(
        cls, payload: bytes, expected_query: str, requested_limit: int,
    ) -> IndexSearchResult:
        data = decode_json(payload, max_bytes=MAX_RESPONSE_BYTES)
        if data.get("available") is False:
            raise BackendResponseError("backend search explicitly reported unavailable")
        data = _object(data, {"query", "available", "hits"})
        if data["query"] != expected_query:
            raise BackendResponseError("backend query identity mismatch")
        raw_hits = _tuple(data["hits"], "hits", requested_limit, empty=True)
        return cls(data["query"], data["available"],
                   tuple(IndexHit.from_dict(hit) for hit in raw_hits))

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "available": True,
            "hits": [asdict(hit) for hit in self.hits],
        }
