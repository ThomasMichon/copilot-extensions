"""Typed ordering evidence retained by the shared crawl-recovery owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.indexing.manifest import CrawlManifest


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

    def __post_init__(self) -> None:
        for name in ("source", "path", "semantics"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Component {name} must be a nonempty string")
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("Component version must be a positive integer")

    @classmethod
    def from_dict(cls, raw: object) -> ComponentVersion:
        """Decode only the complete versioned component record."""
        if not isinstance(raw, dict) or set(raw) != {"source", "path", "version", "semantics"}:
            raise ValueError("Invalid pending component version")
        return cls(**raw)


@dataclass(frozen=True)
class ComponentContent:
    """Canonical replacement/deletion identity, independent of arrival order."""

    component: ComponentVersion
    fingerprint: str
    deleted: bool

    def covered_by(self, previous: ComponentContent) -> bool:
        """Only matching identities can prove a duplicate or superseded version."""
        current, applied = self.component, previous.component
        if (current.source, current.path) != (applied.source, applied.path):
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
    for version in versions:
        if not isinstance(version, ComponentVersion):
            raise ValueError("Invalid component version record")
        identity = (version.source, version.path)
        if identity in identities:
            raise ValueError("Component versions repeat a mutation identity")
        identities.add(identity)
    mutations = {(entry.source, entry.path) for entry in manifest.upserts} | {
        (entry.source, entry.path) for entry in manifest.deletions
    }
    if identities != mutations:
        raise ValueError("Component versions must cover exactly every manifest mutation")


def require_unordered_application(manifest: CrawlManifest) -> None:
    """Retaining versions never authorizes an unordered store replacement."""
    if manifest.component_versions is not None:
        raise ValueError("Ordered component application is not supported")
