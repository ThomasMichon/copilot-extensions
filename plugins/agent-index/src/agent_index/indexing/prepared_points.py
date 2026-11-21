"""Resumable named-target observations, subordinate to one retained whole claim."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from agent_index.indexing.change_hints import _identity, _keys
from agent_index.indexing.component_manifest import ComponentIdentity, ComponentVersion
from agent_index.indexing.manifest import CrawlManifest, CrawlStats
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import (
    CommentVersionTarget, PointTarget, WorkItemRevisionTarget, _target,
)
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile

_KIND = "named-point-acquisition/v1"
_APPLICATION_KIND = "named-point-application/v1"


def _json(value: Any) -> str:
    def validate(item: Any) -> None:
        if type(item) in (str, int, float, bool, type(None)):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise ValueError("Point acquisition requires JSON values and string object keys")

    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate point acquisition field")
        result[key] = value
    return result


@dataclass(frozen=True)
class PointAcquisition:
    """One adapter-qualified read, not current eligibility or application proof."""

    target: PointTarget
    component: ComponentVersion
    entry: FileEntry | None = None
    deletion: DeletedFile | None = None

    def to_dict(self) -> dict[str, Any]:
        """Validate and retain all content and metadata without type coercion."""
        if type(self.target) not in (WorkItemRevisionTarget, CommentVersionTarget):
            raise ValueError("Point acquisition requires an exact typed target")
        if type(self.component) is not ComponentVersion:
            raise ValueError("Point acquisition requires a typed component version")
        version = (
            self.target.revision if isinstance(self.target, WorkItemRevisionTarget)
            else self.target.version
        )
        if self.component.version != version:
            raise ValueError("Point acquisition component does not match the target version")
        identity = ComponentIdentity(
            self.target.kind, self.target.item_id,
            self.target.comment_id if isinstance(self.target, CommentVersionTarget) else None,
        )
        if self.component.identity is not None and self.component.identity != identity:
            raise ValueError("Point acquisition component identity does not match its target")
        if (self.entry is None) == (self.deletion is None):
            raise ValueError("Point acquisition requires exactly one upsert or deletion")
        mutation = self.entry if self.entry is not None else self.deletion
        if (
            self.entry is not None and type(self.entry) is not FileEntry
            or self.deletion is not None and type(self.deletion) is not DeletedFile
        ):
            raise ValueError("Invalid point acquisition mutation")
        assert mutation is not None
        if (mutation.source, mutation.path) != (self.component.source, self.component.path):
            raise ValueError("Point acquisition mutation does not match its component")
        if self.entry is not None:
            if not isinstance(self.entry.content, str) or type(self.entry.metadata) is not dict:
                raise ValueError("Point acquisition requires complete text and JSON metadata")
            _identity(self.entry.language, "language")
            _json(self.entry.metadata)
        data = {
            "version": 1, "target": self.target.to_dict(), "component": self.component.to_dict(),
            "entry": asdict(self.entry) if self.entry is not None else None,
            "deletion": asdict(self.deletion) if self.deletion is not None else None,
        }
        _json(data)
        return data

    @classmethod
    def from_bytes(cls, payload: bytes) -> PointAcquisition:
        """Refuse corrupt, unknown, ambiguous or coercible retained representations."""
        data = _keys(json.loads(payload, object_pairs_hook=_unique), {
            "version", "target", "component", "entry", "deletion",
        })
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("Unsupported point acquisition version")
        result = cls(
            _target(data["target"]), ComponentVersion.from_dict(data["component"]),
            FileEntry(**_keys(data["entry"], {"path", "content", "language", "source", "metadata"}))
            if data["entry"] is not None else None,
            DeletedFile(**_keys(data["deletion"], {"source", "path"}))
            if data["deletion"] is not None else None,
        )
        if _json(result.to_dict()) != _json(data):
            raise ValueError("Point acquisition changed during decoding")
        return result


class PreparedPoints:
    """Use existing immutable prepared rows; no queue, cursor or completion authority."""

    def __init__(self, journal: PreparedJournal) -> None:
        manifest, claim = PendingCrawls.decode_point_pending(journal.payload, journal.source)
        if journal.point_claim != claim:
            raise PendingCrawlConflict("Point acquisition requires its whole bound claim")
        self.journal, self.claim, self.manifest = journal, claim, manifest
        self._targets = frozenset(claim.hints.targets)

    def _key(self, target: PointTarget) -> str:
        if (
            type(target) not in (WorkItemRevisionTarget, CommentVersionTarget)
            or target not in self._targets
        ):
            raise ValueError("Point acquisition target does not belong to the whole claim")
        return _json(target.to_dict())

    def retain(self, observation: PointAcquisition, *, claim: PendingPointClaim) -> None:
        """Persist one complete read; identical retries cannot overwrite a conflict."""
        if type(claim) is not PendingPointClaim or claim != self.claim:
            raise PendingCrawlConflict("Point acquisition does not match the whole bound claim")
        if type(observation) is not PointAcquisition:
            raise ValueError("Point acquisition requires a typed observation")
        key = self._key(observation.target)
        payload = _json(observation.to_dict()).encode()
        if observation.component.source != self.claim.hints.canonical_family:
            raise ValueError("Point acquisition is outside its canonical component family")
        self.journal.put_immutable(_KIND, key, payload)

    def read(self, target: PointTarget) -> PointAcquisition | None:
        """Reopen only a positively retained target under the same parent generation."""
        key = self._key(target)
        stored = self.journal.get(_KIND, [key])
        if key not in stored:
            return None
        return self._decode(target, stored[key])

    def _decode(self, target: PointTarget, payload: bytes) -> PointAcquisition:
        result = PointAcquisition.from_bytes(payload)
        if (
            result.target != target
            or result.component.source != self.claim.hints.canonical_family
        ):
            raise ValueError("Retained point acquisition has a different target or family")
        return result

    def compose(self) -> CrawlManifest:
        """Build a whole-claim candidate, never publish, apply, retire or advance polling.

        Every named version and correlation needs its own retained observation
        before physical components can be deduplicated to their newest version.
        The candidate does not replace the exact manifest bound to this parent.
        """
        logical_to_component: dict[tuple[str, ...], tuple[str, str]] = {}
        component_to_logical: dict[tuple[str, str], tuple[str, ...]] = {}
        versions: dict[tuple[str, str, int], str] = {}
        selected: dict[tuple[str, str], PointAcquisition] = {}
        targets = {self._key(target): target for target in self.claim.hints.targets}
        seen = set()
        for key, payload in self.journal.iter_rows(_KIND, list(targets)):
            target = targets[key]
            seen.add(key)
            observed = self._decode(target, payload)
            component = observed.component
            logical = (target.kind, target.item_id) + (
                (target.comment_id,) if isinstance(target, CommentVersionTarget) else ()
            )
            physical = component.source, component.path
            if (
                logical_to_component.setdefault(logical, physical) != physical
                or component_to_logical.setdefault(physical, logical) != logical
            ):
                raise ValueError("Point acquisition logical/component identity changed")
            data = observed.to_dict()
            del data["target"]
            content = hashlib.sha256(_json(data).encode("utf-8")).hexdigest()
            version_key = (*physical, component.version)
            if versions.setdefault(version_key, content) != content:
                raise ValueError("Equal point versions have conflicting acquisition content")
            previous = selected.get(physical)
            if previous is not None and previous.component.semantics != component.semantics:
                raise ValueError("Point acquisition component semantics changed")
            if previous is None or previous.component.version < component.version:
                selected[physical] = observed
        if seen != targets.keys():
            raise ValueError("Whole point acquisition is incomplete")
        ordered = [selected[key] for key in sorted(selected)]
        upserts = [item.entry for item in ordered if item.entry is not None]
        deletions = [item.deletion for item in ordered if item.deletion is not None]
        result = CrawlManifest(
            source=self.claim.hints.source, accounting_family=self.claim.hints.canonical_family,
            stored_family=self.manifest.stored_family, commit=self.manifest.commit,
            upserts=upserts, deletions=deletions,
            stats=CrawlStats(files_changed=len(upserts), files_deleted=len(deletions)),
            component_versions=[item.component for item in ordered],
        )
        PendingCrawls._validate(result)
        return result

    def bind_application(self, manifest: CrawlManifest) -> None:
        """Retain the exact complete candidate, not applied-version or queue coverage."""
        candidate = self.compose()
        for entry in manifest.upserts:
            _json(entry.metadata)
        expected = _json(PendingCrawls.manifest_data(candidate)).encode()
        supplied = _json(PendingCrawls.manifest_data(manifest)).encode()
        if supplied != expected:
            raise PendingCrawlConflict("Point application manifest does not match every acquired target")
        self.journal.put_immutable(_APPLICATION_KIND, "manifest", expected)

    def read_application(self) -> CrawlManifest:
        """Requalify the complete retained application without creating a binding."""
        candidate = self.compose()
        expected = _json(PendingCrawls.manifest_data(candidate)).encode()
        if self.journal.get(_APPLICATION_KIND, ["manifest"]).get("manifest") != expected:
            raise PendingCrawlConflict("Whole point application binding is missing or changed")
        return candidate
