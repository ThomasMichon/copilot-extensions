"""Strict, point-only v2 acquisition claims; no component execution authority."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from .change_hints import ChangeHints, _array, _identity, _keys

MAX_POINT_HINT_BYTES = 1_048_576
MAX_POINT_TARGETS = 1024


def _positive(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _events(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError("event_ids must be an immutable tuple")
    for event in value:
        _identity(event, "event_id")
    return tuple(sorted(set(value)))


@dataclass(frozen=True)
class PointBinding:
    """Exact nonsecret representation, endpoint and query recovery boundary.

    The adapter must supply canonical effective values and later qualify them;
    accepting this binding does not establish query eligibility or coverage.
    """

    endpoint: str
    query_scope: str
    query_context: str
    representation: str = "point-components"
    representation_version: int = 1

    def __post_init__(self) -> None:
        for name in ("endpoint", "query_scope", "query_context"):
            _identity(getattr(self, name), name)
        if self.representation != "point-components":
            raise ValueError("Unsupported point representation")
        if type(self.representation_version) is not int or self.representation_version != 1:
            raise ValueError("Unsupported point representation version")

    def to_dict(self) -> dict[str, Any]:
        """Return the complete binding, without normalization or inference."""
        return {
            "endpoint": self.endpoint, "query_scope": self.query_scope,
            "query_context": self.query_context, "representation": self.representation,
            "representation_version": self.representation_version,
        }


@dataclass(frozen=True)
class WorkItemRevisionTarget:
    """One exact item's fields, never its discussion or polling interval."""

    item_id: str
    revision: int
    event_ids: tuple[str, ...] = ()
    kind: Literal["work_item_revision"] = field(default="work_item_revision", init=False)

    def __post_init__(self) -> None:
        _identity(self.item_id, "item_id")
        _positive(self.revision, "revision")
        object.__setattr__(self, "event_ids", _events(self.event_ids))

    def to_dict(self) -> dict[str, Any]:
        """Retain target version and its own event correlations."""
        return {
            "kind": self.kind, "item_id": self.item_id,
            "revision": self.revision, "event_ids": list(self.event_ids),
        }


@dataclass(frozen=True)
class CommentVersionTarget:
    """One named comment version bound to its parent item."""

    item_id: str
    comment_id: str
    version: int
    event_ids: tuple[str, ...] = ()
    kind: Literal["comment_version"] = field(default="comment_version", init=False)

    def __post_init__(self) -> None:
        _identity(self.item_id, "item_id")
        _identity(self.comment_id, "comment_id")
        _positive(self.version, "version")
        object.__setattr__(self, "event_ids", _events(self.event_ids))

    def to_dict(self) -> dict[str, Any]:
        """Retain parent, target version and its own event correlations."""
        return {
            "kind": self.kind, "item_id": self.item_id, "comment_id": self.comment_id,
            "version": self.version, "event_ids": list(self.event_ids),
        }


PointTarget = WorkItemRevisionTarget | CommentVersionTarget


def _target(value: object) -> PointTarget:
    if not isinstance(value, dict):
        raise ValueError("Point target must be an object")
    kind = value.get("kind")
    if kind == "work_item_revision":
        data = _keys(value, {"kind", "item_id", "revision", "event_ids"})
        return WorkItemRevisionTarget(
            data["item_id"], data["revision"], tuple(_array(data["event_ids"], "event_ids")),
        )
    if kind == "comment_version":
        data = _keys(value, {"kind", "item_id", "comment_id", "version", "event_ids"})
        return CommentVersionTarget(
            data["item_id"], data["comment_id"], data["version"],
            tuple(_array(data["event_ids"], "event_ids")),
        )
    raise ValueError("Unsupported point target kind")


@dataclass(frozen=True, kw_only=True)
class PointChangeHints:
    """Immutable v2 claim; union preserves every named version and correlation."""

    source: str
    canonical_family: str
    account_id: str
    binding: PointBinding
    targets: tuple[PointTarget, ...]
    version: int = 2

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 2:
            raise ValueError("Unsupported point change-hints version (expected 2)")
        for name in ("source", "canonical_family", "account_id"):
            _identity(getattr(self, name), name)
        if self.source == "all" or type(self.binding) is not PointBinding:
            raise ValueError("Point hints require an exact source and typed binding")
        if (
            not isinstance(self.targets, tuple) or not self.targets
            or len(self.targets) > MAX_POINT_TARGETS
            or any(type(t) not in (WorkItemRevisionTarget, CommentVersionTarget) for t in self.targets)
        ):
            raise ValueError("Point hints require a bounded nonempty immutable target tuple")
        targets = tuple(sorted(set(self.targets), key=lambda t: json.dumps(t.to_dict(), sort_keys=True)))
        object.__setattr__(self, "targets", targets)
        if len(self.to_json().encode("utf-8")) > MAX_POINT_HINT_BYTES:
            raise ValueError("Point change-hints payload too large")

    @property
    def scope(self) -> tuple[str, str, str, PointBinding]:
        """Exact union boundary, distinct from every v1 scope."""
        return self.source, self.canonical_family, self.account_id, self.binding

    def union(self, other: PointChangeHints) -> PointChangeHints:
        """Union only equally bound point claims, without widening coverage."""
        if type(other) is not PointChangeHints or self.scope != other.scope:
            raise ValueError("Cannot union point change hints from different scopes")
        return PointChangeHints(
            source=self.source, canonical_family=self.canonical_family,
            account_id=self.account_id, binding=self.binding,
            targets=tuple(set(self.targets + other.targets)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete v2 envelope; broad v1 hints have no place here."""
        return {
            "version": self.version, "source": self.source,
            "canonical_family": self.canonical_family, "account_id": self.account_id,
            "binding": self.binding.to_dict(), "targets": [t.to_dict() for t in self.targets],
        }

    def to_json(self) -> str:
        """Serialize deterministically for durable retention."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: object) -> PointChangeHints:
        """Strictly decode point targets and their entire recovery binding."""
        data = _keys(value, {
            "version", "source", "canonical_family", "account_id", "binding", "targets",
        })
        binding = PointBinding(**_keys(data["binding"], {
            "endpoint", "query_scope", "query_context", "representation", "representation_version",
        }))
        return cls(
            version=data["version"], source=data["source"], canonical_family=data["canonical_family"],
            account_id=data["account_id"], binding=binding,
            targets=tuple(_target(t) for t in _array(data["targets"], "targets")),
        )


AcquisitionHints = ChangeHints | PointChangeHints


def union_change_hints(first: AcquisitionHints, other: AcquisitionHints) -> AcquisitionHints:
    """Keep differently versioned claims out of each other's union operation."""
    if isinstance(first, ChangeHints) and isinstance(other, ChangeHints):
        return first.union(other)
    if isinstance(first, PointChangeHints) and isinstance(other, PointChangeHints):
        return first.union(other)
    raise ValueError("Cannot union different change-hints versions")


def decode_change_hints(value: object) -> AcquisitionHints:
    """Select a supported decoder explicitly; keep the v1 decoder v1-only."""
    if isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 2:
        return PointChangeHints.from_dict(value)
    return ChangeHints.from_dict(value)


def read_change_hints(value: str) -> AcquisitionHints:
    """Decode retained JSON without accepting duplicate fields at any depth."""
    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"Duplicate hint field: {key}")
            result[key] = item
        return result

    return decode_change_hints(json.loads(value, object_pairs_hook=unique_fields))
