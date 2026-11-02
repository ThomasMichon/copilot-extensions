"""Versioned acquisition hints, never snapshot-completeness or delete authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _identity(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _keys(value: object, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"Expected exactly these hint fields: {sorted(expected)}")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


@dataclass(frozen=True, order=True)
class RefRange:
    """One distinct base/target pair; no merging of unrelated ref histories."""

    base: str
    target: str

    def __post_init__(self) -> None:
        _identity(self.base, "base")
        _identity(self.target, "target")

    def to_dict(self) -> dict[str, str]:
        return {"base": self.base, "target": self.target}


@dataclass(frozen=True, order=True)
class MetadataRevision:
    """An opaque revision bound to an item, retaining conflicting deliveries."""

    item_id: str
    revision: str

    def __post_init__(self) -> None:
        _identity(self.item_id, "item_id")
        _identity(self.revision, "revision")

    def to_dict(self) -> dict[str, str]:
        return {"item_id": self.item_id, "revision": self.revision}


@dataclass(frozen=True, kw_only=True)
class ChangeHints:
    """Immutable v1 hints fenced by exact source, family and account identity.

    Adapters supply the canonical family and a stable nonsecret account key;
    these are not credentials and the queue does not infer or normalize them.
    No completeness, tombstone or checkpoint authority is represented here.
    """

    source: str
    canonical_family: str
    account_id: str
    item_ids: tuple[str, ...] = ()
    ref_ranges: tuple[RefRange, ...] = ()
    event_ids: tuple[str, ...] = ()
    metadata_revisions: tuple[MetadataRevision, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Unsupported change-hints version (expected 1)")
        for name in ("source", "canonical_family", "account_id"):
            _identity(getattr(self, name), name)
        if self.source == "all":
            raise ValueError("Change hints require an exact configured source")
        for name, cls in (
            ("item_ids", str),
            ("ref_ranges", RefRange),
            ("event_ids", str),
            ("metadata_revisions", MetadataRevision),
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(type(v) is not cls for v in values):
                raise ValueError(f"{name} must be an immutable tuple of {cls.__name__}")
            if cls is str:
                for value in values:
                    _identity(value, name)
            object.__setattr__(self, name, tuple(sorted(set(values))))

    @property
    def scope(self) -> tuple[str, str, str]:
        """The exact union boundary; aliases and accounts never collapse."""
        return self.source, self.canonical_family, self.account_id

    def union(self, other: ChangeHints) -> ChangeHints:
        """Losslessly union acquisition identities within one exact scope."""
        if not isinstance(other, ChangeHints) or self.scope != other.scope:
            raise ValueError("Cannot union change hints from different scopes")
        return ChangeHints(
            source=self.source,
            canonical_family=self.canonical_family,
            account_id=self.account_id,
            item_ids=self.item_ids + other.item_ids,
            ref_ranges=self.ref_ranges + other.ref_ranges,
            event_ids=self.event_ids + other.event_ids,
            metadata_revisions=self.metadata_revisions + other.metadata_revisions,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete versioned representation."""
        return {
            "version": self.version,
            "source": self.source,
            "canonical_family": self.canonical_family,
            "account_id": self.account_id,
            "item_ids": list(self.item_ids),
            "ref_ranges": [value.to_dict() for value in self.ref_ranges],
            "event_ids": list(self.event_ids),
            "metadata_revisions": [value.to_dict() for value in self.metadata_revisions],
        }

    def to_json(self) -> str:
        """Serialize deterministically without discarding any fields."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: object) -> ChangeHints:
        """Strictly decode v1; unknown or missing fields are not downgraded."""
        data = _keys(value, {
            "version", "source", "canonical_family", "account_id", "item_ids",
            "ref_ranges", "event_ids", "metadata_revisions",
        })
        refs = [
            RefRange(**_keys(item, {"base", "target"}))
            for item in _array(data["ref_ranges"], "ref_ranges")
        ]
        revisions = [
            MetadataRevision(**_keys(item, {"item_id", "revision"}))
            for item in _array(data["metadata_revisions"], "metadata_revisions")
        ]
        return cls(
            version=data["version"],
            source=data["source"],
            canonical_family=data["canonical_family"],
            account_id=data["account_id"],
            item_ids=tuple(_array(data["item_ids"], "item_ids")),
            ref_ranges=tuple(refs),
            event_ids=tuple(_array(data["event_ids"], "event_ids")),
            metadata_revisions=tuple(revisions),
        )

    @classmethod
    def from_json(cls, value: str) -> ChangeHints:
        """Decode durable data visibly; corruption never becomes empty hints."""
        def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(f"Duplicate hint field: {key}")
                result[key] = item
            return result

        return cls.from_dict(json.loads(value, object_pairs_hook=unique_fields))
