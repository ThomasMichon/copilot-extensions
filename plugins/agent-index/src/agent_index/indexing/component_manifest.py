"""Typed ordering evidence retained by the shared crawl-recovery owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agent_index.indexing.manifest import CrawlManifest


@dataclass(frozen=True)
class ComponentIdentity:
    """Version-independent logical identity supplied by the qualified adapter."""

    kind: Literal["work_item_revision", "comment_version"]
    item_id: str
    comment_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("work_item_revision", "comment_version"):
            raise ValueError("Unsupported component identity kind")
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("Component item_id must be a nonempty string")
        if self.kind == "comment_version":
            if not isinstance(self.comment_id, str) or not self.comment_id.strip():
                raise ValueError("Component comment_id must be a nonempty string")
        elif self.comment_id is not None:
            raise ValueError("Work-item component identity cannot include a comment_id")

    def to_dict(self) -> dict[str, str]:
        """Retain the complete kind-specific identity, without parsing a path."""
        result = {"kind": self.kind, "item_id": self.item_id}
        if self.comment_id is not None:
            result["comment_id"] = self.comment_id
        return result

    @classmethod
    def from_dict(cls, raw: object) -> ComponentIdentity:
        """Decode only an exact kind-specific identity."""
        if not isinstance(raw, dict):
            raise ValueError("Invalid component identity")
        fields = {"kind", "item_id"}
        if raw.get("kind") == "comment_version":
            fields.add("comment_id")
        if set(raw) != fields:
            raise ValueError("Invalid component identity fields")
        return cls(**raw)


@dataclass(frozen=True)
class ComponentVersion:
    """A provider version for one scoped component, not application proof.

    ``semantics`` identifies the selected representation/rendering contract.
    Account, endpoint and query bindings remain in the pending recovery scope.
    """

    source: str
    path: str
    version: int
    semantics: str
    identity: ComponentIdentity | None = None

    def __post_init__(self) -> None:
        for name in ("source", "path", "semantics"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Component {name} must be a nonempty string")
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("Component version must be a positive integer")
        if self.identity is not None and type(self.identity) is not ComponentIdentity:
            raise ValueError("Component identity must be a typed record")

    def to_dict(self) -> dict[str, Any]:
        """Preserve the legacy encoding when no typed identity was acquired."""
        result: dict[str, Any] = {
            "source": self.source, "path": self.path,
            "version": self.version, "semantics": self.semantics,
        }
        if self.identity is not None:
            result["identity"] = self.identity.to_dict()
        return result

    @classmethod
    def from_dict(cls, raw: object) -> ComponentVersion:
        """Decode only the complete versioned component record."""
        fields = {"source", "path", "version", "semantics"}
        if not isinstance(raw, dict) or set(raw) not in (fields, fields | {"identity"}):
            raise ValueError("Invalid pending component version")
        return cls(
            raw["source"], raw["path"], raw["version"], raw["semantics"],
            ComponentIdentity.from_dict(raw["identity"]) if "identity" in raw else None,
        )


@dataclass(frozen=True)
class ComponentContent:
    """Canonical replacement/deletion identity, independent of arrival order."""

    component: ComponentVersion
    fingerprint: str
    deleted: bool

    def covered_by(self, previous: ComponentContent) -> bool:
        """Only matching identities can prove a duplicate or superseded version."""
        current, applied = self.component, previous.component
        if (
            (current.source, current.path) != (applied.source, applied.path)
            or current.identity != applied.identity
        ):
            raise ValueError("Component application identity does not match")
        if current.version > applied.version:
            return False
        if current.version == applied.version and self != previous:
            raise ValueError("Equal component versions have conflicting content or semantics")
        return True


def validate_components(manifest: CrawlManifest) -> None:
    """Every mutation needs exactly one ordering record; legacy work needs none."""
    versions = manifest.component_versions
    if versions is None:
        return
    if not isinstance(versions, list) or not versions:
        raise ValueError("Component versions must be a nonempty list")
    if manifest.poll_refresh is not None:
        raise ValueError("Component versions cannot certify polling refresh")
    identities: set[tuple[str, str]] = set()
    logical: set[tuple[str, ComponentIdentity]] = set()
    for version in versions:
        if not isinstance(version, ComponentVersion):
            raise ValueError("Invalid component version record")
        identity = (version.source, version.path)
        if identity in identities:
            raise ValueError("Component versions repeat a mutation identity")
        identities.add(identity)
        if version.identity is not None:
            scoped_identity = version.source, version.identity
            if scoped_identity in logical:
                raise ValueError("Component versions repeat a logical identity")
            logical.add(scoped_identity)
    mutations = {(entry.source, entry.path) for entry in manifest.upserts} | {
        (entry.source, entry.path) for entry in manifest.deletions
    }
    if identities != mutations:
        raise ValueError("Component versions must cover exactly every manifest mutation")


def require_unordered_application(manifest: CrawlManifest) -> None:
    """Retaining versions never authorizes an unordered store replacement."""
    if manifest.component_versions is not None:
        raise ValueError("Ordered component application is not supported")
