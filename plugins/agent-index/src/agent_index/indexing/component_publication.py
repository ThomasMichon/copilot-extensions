"""Reconcile sealed native publication through the existing pending/source owners."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_index.indexing.embedding_coverage import EmbeddingCoverage
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.store.component_ledger import ComponentEvidence, ComponentLedger, _decode, _encode
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.source_layout import SourceLayoutError

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.component_application import PreparedComponentApplication
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.store.multi_model_store import MultiModelStore

OWNER = "component-publication-v1"
KEY = "publication"
Point = tuple[str, str]
Checkpoint = tuple[str | None, int, float] | None
Completion = tuple[str, int] | None


@dataclass(frozen=True)
class _Point:
    target: ComponentEvidence
    previous: ComponentEvidence | None
    before: Checkpoint
    after: Completion
    selected: bool

    @property
    def identity(self) -> Point:
        component = self.target.content.component
        return component.source, component.path


def _checkpoint(value: object) -> Checkpoint:
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 3
            or value[0] is not None and not isinstance(value[0], str)
            or type(value[1]) is not int or value[1] < 0
            or type(value[2]) not in (int, float) or not math.isfinite(value[2])):
        raise ValueError("Invalid component publication checkpoint")
    return value[0], value[1], value[2]


def _completion(value: object) -> Completion:
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 2
            or not isinstance(value[0], str) or not value[0]
            or type(value[1]) is not int or value[1] < 0):
        raise ValueError("Invalid component publication completion")
    return value[0], value[1]


def _completed(current: Checkpoint, after: Completion) -> bool:
    return current is None if after is None else current is not None and current[:2] == after


class ComponentPublication:
    """A sealed commit suffix, not an acquisition, write replay or claim receipt."""

    def __init__(self, journal: PreparedJournal, payload: bytes, points: list[_Point]):
        self.journal, self.payload, self.points = journal, payload, points
        self.token = hashlib.sha256(payload).hexdigest()

    def finish(
        self, config: IndexConfig, store: MultiModelStore, ledger: ComponentLedger, *,
        max_rows: int,
    ) -> tuple[int, int]:
        from .component_application import _qualified_chunks

        domain = ledger._source_domain
        point_scope = (
            self.journal.point_claim.hints.scope if self.journal.point_claim is not None else None
        )
        if not domain.writing:
            raise SourceLayoutError("Component publication requires the owning source writer")
        if self.journal.get(OWNER, [KEY]).get(KEY) != self.payload:
            raise PendingCrawlConflict("Component publication intent changed")
        paths = self.journal.paths
        current = paths.get_checkpoint_rows([point.identity for point in self.points])
        retained = {}
        coverage = EmbeddingCoverage(paths)
        # Validate the whole sealed set before repairing any ordering/checkpoint suffix.
        for point in self.points:
            identity, target = point.identity, point.target
            previous = ledger.get(*identity, point_scope=point_scope)
            if previous != target and (not point.selected or previous != point.previous):
                raise SourceLayoutError("Component publication ordering evidence changed")
            retained[identity] = previous
            projection = capture_file_projection(
                config, store, *identity, max_rows=max_rows,
                retained_chunk_ids=target.projection.vector_chunk_ids,
                point_scope=point_scope,
            )
            if projection != target.projection:
                raise SourceLayoutError("Component publication native projection changed")
            _qualified_chunks(projection, config, store, coverage, point_scope=point_scope)
            if (current.get(identity) != point.before
                    and (not point.selected or not _completed(current.get(identity), point.after))):
                raise PendingCrawlConflict("Component publication checkpoint changed")
        for point in self.points:
            if not point.selected:
                continue
            identity = point.identity
            if retained[identity] != point.target:
                ledger.put(
                    point.target, expected=point.previous, max_rows=max_rows, point_scope=point_scope,
                )
                domain.seal_reconciliation(OWNER, self.token)
            if not _completed(current.get(identity), point.after):
                if point.after is None:
                    paths.remove(*identity)
                else:
                    paths.mark_indexed_batch([(*identity, *point.after)])
        selected = sum(point.selected for point in self.points)
        return selected, len(self.points) - selected


def retain_component_publication(
    application: PreparedComponentApplication, targets: dict[Point, ComponentEvidence],
) -> ComponentPublication:
    """Seal only after native input, vector, cleanup and index qualification."""
    points = []
    for identity in sorted(application.contents):
        selected = identity in application.selected
        previous = application.previous[identity]
        target = targets[identity] if selected else previous
        assert target is not None
        checkpoint = application.checkpoints.get(identity)
        before = (None, *checkpoint[1:]) if selected and checkpoint is not None else checkpoint
        after = (
            None if target.content.deleted else application.checkpoint_rows[identity][2:]
        ) if selected else None
        points.append(_Point(target, previous, before, after, selected))
    journal = application.prepared.journal
    payload = json.dumps({
        "version": 1, "generation": journal.generation, "scope": journal.scope,
        "parent": hashlib.sha256(journal.payload.encode()).hexdigest(),
        "family": application.family, "policy": application.policy,
        "max_rows": application.max_rows,
        "points": [{
            "target": _encode(point.target).decode(),
            "previous": _encode(point.previous).decode() if point.previous else None,
            "before": point.before, "after": point.after, "selected": point.selected,
        } for point in points],
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    publication = ComponentPublication(journal, payload, points)
    journal.put_immutable(OWNER, KEY, payload)
    application.domain.seal_reconciliation(OWNER, publication.token)
    return publication


def reconcile_component_publication(
    manifest: CrawlManifest, *, scope: str, family: str, max_rows: int,
    config: IndexConfig, paths: PathIndex, store: MultiModelStore,
    point_claim: PendingPointClaim | None = None,
) -> tuple[int, int]:
    """Finish an exact sealed suffix without engines, native data replay or claim completion.

    Interruptions before native qualification, or inside a later native effect,
    still require owner repair. Cached bytes never clear an ordinary dirty latch.
    """
    from .component_application import _component_contents, _policy

    if (Path(paths._db_path).resolve() != (config.data_dir / "path_index.db").resolve()
            or not family or manifest.accounting_family != family
            or manifest.stored_family not in (None, family)
            or type(max_rows) is not int or max_rows < 1):
        raise ValueError("Component reconciliation requires configured path/family owners")
    PendingCrawls._validate(manifest)
    if manifest.component_versions is None:
        raise ValueError("Component reconciliation requires ordered acquisition")
    if point_claim is not None and type(point_claim) is not PendingPointClaim:
        raise ValueError("Point reconciliation requires a whole typed claim")
    point_scope = point_claim.hints.scope if point_claim is not None else None
    ledger = ComponentLedger(config, store)
    ledger._source_domain._require_read_access(point_scope)
    payload = PendingCrawls(paths).read_payload(manifest.source, scope)
    if payload is None:
        raise PendingCrawlConflict("Component reconciliation requires its pending crawl")
    journal = PreparedJournal(
        paths, manifest.source, scope, payload, manifest=manifest, full=False,
    ) if point_claim is None else PreparedJournal.for_point_application(
        paths, manifest.source, scope, payload, manifest=manifest, point_claim=point_claim,
    )
    stored = journal.get(OWNER, [KEY]).get(KEY)
    if stored is None:
        raise SourceLayoutError("Component publication intent is missing")
    try:
        data = json.loads(stored)
        expected = {
            "version": 1, "generation": journal.generation, "scope": scope,
            "parent": hashlib.sha256(payload.encode()).hexdigest(),
            "family": family, "policy": _policy(config), "max_rows": max_rows,
        }
        if (not isinstance(data, dict) or set(data) != {*expected, "points"}
                or {key: data[key] for key in expected} != expected
                or type(data["version"]) is not int or type(data["max_rows"]) is not int
                or not isinstance(data["points"], list)):
            raise ValueError("Invalid component publication binding")
        points = []
        for row in data["points"]:
            if (not isinstance(row, dict)
                    or set(row) != {"target", "previous", "before", "after", "selected"}
                    or type(row["selected"]) is not bool):
                raise ValueError("Invalid component publication point")
            points.append(_Point(
                _decode(row["target"].encode()),
                _decode(row["previous"].encode()) if row["previous"] is not None else None,
                _checkpoint(row["before"]), _completion(row["after"]), row["selected"],
            ))
        contents = _component_contents(manifest, config)
        if (len(points) != len(contents) or {point.identity for point in points} != contents.keys()
                or not any(point.selected for point in points)
                or any(
                    (point.target.scope, point.target.policy) != (scope, expected["policy"])
                    or (point.selected and point.target.content != contents[point.identity])
                    or (not point.selected and (
                        point.target != point.previous
                        or not contents[point.identity].covered_by(point.target.content)
                    ))
                    for point in points
                )):
            raise ValueError("Invalid component publication target set")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SourceLayoutError("Invalid retained component publication") from exc
    publication = ComponentPublication(journal, stored, points)
    domain = ledger._source_domain
    with domain.reconcile(
        store.content_store.db, owner=OWNER, token=publication.token,
        point_scope=point_scope,
    ):
        if point_claim is not None:
            from .embedding_batch import _point_pair_store

            store = _point_pair_store(store, config.model_profiles, point_scope=point_claim.hints.scope)
            ledger = ComponentLedger(config, store)
        return publication.finish(config, store, ledger, max_rows=max_rows)
