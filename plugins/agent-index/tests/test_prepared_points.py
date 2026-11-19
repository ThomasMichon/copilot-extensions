"""Every exact named target must survive restart before candidate composition."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_index.indexing.component_manifest import ComponentVersion
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import (
    CommentVersionTarget, PointBinding, PointChangeHints, PointTarget, WorkItemRevisionTarget,
)
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.prepared_points import PointAcquisition, PreparedPoints
from agent_index.indexing.task_store import TaskStatus, TaskStore
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile

_FAMILY = "sample:components"
_TARGETS = (
    WorkItemRevisionTarget("1", 7, ("event-a",)),
    WorkItemRevisionTarget("1", 8, ("event-b",)),
    WorkItemRevisionTarget("1", 8, ("event-c",)),
    CommentVersionTarget("1", "2", 4, ("event-d",)),
    CommentVersionTarget("3", "2", 4, ("event-e",)),
)


def _observed(target: PointTarget, *, deleted: bool = False) -> PointAcquisition:
    path = f"{target.item_id}/body.md"
    version = target.revision if isinstance(target, WorkItemRevisionTarget) else target.version
    if isinstance(target, CommentVersionTarget):
        path = f"{target.item_id}/comments/{target.comment_id}.md"
    return PointAcquisition(
        target, ComponentVersion(_FAMILY, path, version, target.kind + "/v1"),
        None if deleted else FileEntry(
            path, f"Version {version}", "markdown", _FAMILY,
            {"nested": [True, 1, 1.0, None, {"label": "text"}]},
        ),
        DeletedFile(_FAMILY, path) if deleted else None,
    )


@pytest.fixture
def retained(tmp_path: Path) -> tuple[PreparedPoints, TaskStore]:
    hints = PointChangeHints(
        source="selected", canonical_family=_FAMILY, account_id="account-a",
        binding=PointBinding("https://example.test", "project", "eligible-query/v1"),
        targets=_TARGETS,
    )
    tasks = TaskStore(tmp_path / "tasks.db")
    task = tasks.enqueue(hints.source, change_hints=hints)
    claim = PendingPointClaim.from_task(tasks.pin_point_claim(task.id, expected_hints=hints))
    paths = PathIndex(tmp_path / "paths.db")
    first = _observed(_TARGETS[0])
    assert first.entry is not None
    manifest = CrawlManifest(
        source=hints.source, accounting_family=_FAMILY, commit="unchanged-poll",
        upserts=[first.entry], component_versions=[first.component],
    )
    pending = PendingCrawls(paths)
    pending.save(manifest, scope="account/endpoint/query", full=False, point_claim=claim)
    payload = pending.read_payload(manifest.source, "account/endpoint/query")
    assert payload is not None
    return PreparedPoints(PreparedJournal(
        paths, manifest.source, "account/endpoint/query", payload, point_claim=claim,
    )), tasks


def _reopen(points: PreparedPoints) -> PreparedPoints:
    parent = points.journal
    return PreparedPoints(PreparedJournal(
        PathIndex(Path(parent.paths._db_path)), parent.source, parent.scope, parent.payload,
        point_claim=points.claim,
    ))


def test_restart_resumes_exact_targets_and_candidate_does_not_apply_or_acknowledge(
    retained: tuple[PreparedPoints, TaskStore],
) -> None:
    points, tasks = retained
    parent = points.journal
    for target in points.claim.hints.targets:
        assert points.read(target) is None
    with pytest.raises(ValueError, match="incomplete"):
        points.compose()
    acquired: list[PointTarget] = []
    for target in points.claim.hints.targets:
        if points.read(target) is None:
            points.retain(
                _observed(target, deleted=isinstance(target, CommentVersionTarget)),
                claim=points.claim,
            )
            acquired.append(target)
        points = _reopen(points)
        assert points.journal.generation == parent.generation
        assert points.read(target) is not None
    assert set(acquired) == set(_TARGETS)
    with closing(parent.paths._connect()) as conn:
        before = list(conn.iterdump())
    candidate = points.compose()
    assert candidate.source == "selected" and candidate.accounting_family == _FAMILY
    assert candidate.commit == "unchanged-poll" and candidate.poll_refresh is None
    assert candidate.upserts == [_observed(_TARGETS[1]).entry]
    assert candidate.deletions == [
        _observed(_TARGETS[3], deleted=True).deletion,
        _observed(_TARGETS[4], deleted=True).deletion,
    ]
    assert candidate.component_versions == [
        _observed(_TARGETS[1]).component, _observed(_TARGETS[3]).component,
        _observed(_TARGETS[4]).component,
    ]
    assert points.read(_TARGETS[0]) == _observed(_TARGETS[0])
    with closing(parent.paths._connect()) as conn:
        assert list(conn.iterdump()) == before
    pending = PendingCrawls(parent.paths)
    assert pending.read_payload(parent.source, parent.scope) == parent.payload
    task = tasks.get_task(points.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    with pytest.raises(ValueError, match="completion is not supported"):
        pending.clear(parent.source)
    # A candidate is not the manifest already bound to this exact parent.
    with pytest.raises(PendingCrawlConflict, match="manifest does not match"):
        PreparedJournal(
            parent.paths, parent.source, parent.scope, parent.payload,
            manifest=candidate, full=False, point_claim=points.claim,
        )


@pytest.mark.parametrize("missing", [_TARGETS[0], _TARGETS[2]])
def test_missing_old_version_or_correlation_is_not_covered_by_newest_manifest_point(
    retained: tuple[PreparedPoints, TaskStore], missing: PointTarget,
) -> None:
    points, _tasks = retained
    for target in _TARGETS:
        if target != missing:
            points.retain(_observed(target), claim=points.claim)
    with pytest.raises(ValueError, match="incomplete"):
        points.compose()
    assert points.read(missing) is None
    points.retain(_observed(missing), claim=points.claim)
    assert len(points.compose().upserts) == 3


@pytest.mark.parametrize("change", ["text", "metadata", "deleted", "semantics"])
def test_same_target_retry_is_immutable(
    retained: tuple[PreparedPoints, TaskStore], change: str,
) -> None:
    points, _tasks = retained
    original = _observed(_TARGETS[0])
    points.retain(original, claim=points.claim)
    points.retain(original, claim=points.claim)
    assert original.entry is not None
    if change == "deleted":
        changed = _observed(original.target, deleted=True)
    elif change == "semantics":
        changed = replace(original, component=replace(original.component, semantics="different"))
    elif change == "text":
        changed = replace(original, entry=replace(original.entry, content="different"))
    else:
        changed = replace(original, entry=replace(original.entry, metadata={"nested": [1]}))
    with pytest.raises(PendingCrawlConflict, match="intent changed"):
        points.retain(changed, claim=points.claim)
    assert _reopen(points).read(original.target) == original


@pytest.mark.parametrize("change", ["alias", "split", "semantics", "metadata", "deleted"])
def test_composition_refuses_identity_or_equal_version_content_collisions(
    retained: tuple[PreparedPoints, TaskStore], change: str,
) -> None:
    points, _tasks = retained
    for target in points.claim.hints.targets:
        observed = _observed(target)
        entry = observed.entry
        assert entry is not None
        if change == "alias" and target == _TARGETS[4]:
            path = _observed(_TARGETS[3]).component.path
            observed = replace(observed, component=replace(observed.component, path=path),
                               entry=replace(entry, path=path))
        if target == _TARGETS[2]:
            if change == "split":
                observed = replace(
                    observed, component=replace(observed.component, path="different.md"),
                    entry=replace(entry, path="different.md"),
                )
            elif change == "semantics":
                observed = replace(
                    observed, component=replace(observed.component, semantics="different"),
                )
            elif change == "metadata":
                observed = replace(observed, entry=replace(entry, metadata={"nested": [1]}))
            elif change == "deleted":
                observed = _observed(target, deleted=True)
        points.retain(observed, claim=points.claim)
    with pytest.raises(ValueError, match="identity changed|semantics changed|conflicting"):
        points.compose()


@pytest.mark.parametrize("value", [True, 1, 1.0])
def test_metadata_types_cannot_collapse_for_same_version(
    retained: tuple[PreparedPoints, TaskStore], value: Any,
) -> None:
    points, _tasks = retained
    baseline = 1 if type(value) is bool else (1.0 if type(value) is int else True)
    for target in points.claim.hints.targets:
        observed = _observed(target)
        assert observed.entry is not None
        observed = replace(observed, entry=replace(observed.entry, metadata={
            "qualifier": value if target == _TARGETS[2] else baseline,
        }))
        points.retain(observed, claim=points.claim)
    with pytest.raises(ValueError, match="conflicting"):
        points.compose()


@pytest.mark.parametrize("newest_deleted", [False, True])
def test_reverse_arrival_keeps_newest_upsert_or_tombstone(
    retained: tuple[PreparedPoints, TaskStore], newest_deleted: bool,
) -> None:
    points, _tasks = retained
    for target in reversed(points.claim.hints.targets):
        is_newest = isinstance(target, WorkItemRevisionTarget) and target.revision == 8
        points.retain(_observed(
            target, deleted=newest_deleted if is_newest else not newest_deleted,
        ), claim=points.claim)
    candidate = _reopen(points).compose()
    body = _observed(_TARGETS[1], deleted=newest_deleted)
    assert (body.deletion in candidate.deletions) if newest_deleted else (
        body.entry in candidate.upserts
    )
    assert candidate.component_versions is not None
    assert body.component in candidate.component_versions
    assert _observed(_TARGETS[0]).component not in candidate.component_versions


@pytest.mark.parametrize("invalid", [
    "extra-target", "wrong-version", "family", "mutation", "both", "neither",
    "metadata-key", "metadata-tuple", "nan", "language", "text",
])
def test_invalid_target_or_outcome_refuses_without_cache_mutation(
    retained: tuple[PreparedPoints, TaskStore], invalid: str,
) -> None:
    points, _tasks = retained
    observed = _observed(_TARGETS[0])
    entry = observed.entry
    assert entry is not None
    if invalid == "extra-target":
        observed = _observed(WorkItemRevisionTarget("1", 7, ("other-event",)))
    elif invalid == "wrong-version":
        observed = replace(observed, component=replace(observed.component, version=9))
    elif invalid == "family":
        observed = replace(
            observed, component=replace(observed.component, source="other"),
            entry=replace(entry, source="other"),
        )
    elif invalid == "mutation":
        observed = replace(observed, entry=replace(entry, path="other.md"))
    elif invalid == "both":
        observed = replace(observed, deletion=DeletedFile(_FAMILY, observed.component.path))
    elif invalid == "neither":
        observed = replace(observed, entry=None)
    else:
        changes: dict[str, Any] = {
            "metadata-key": {"metadata": {1: "not-string"}},
            "metadata-tuple": {"metadata": {"not-json": (1, 2)}},
            "nan": {"metadata": {"not-finite": float("nan")}},
            "language": {"language": ""},
            "text": {"content": None},
        }
        observed = replace(observed, entry=replace(entry, **changes[invalid]))
    with closing(points.journal.paths._connect()) as conn:
        before = list(conn.iterdump())
    with pytest.raises(ValueError):
        points.retain(observed, claim=points.claim)
    with closing(points.journal.paths._connect()) as conn:
        assert list(conn.iterdump()) == before


@pytest.mark.parametrize("invalid", ["unknown", "version", "duplicate", "nan", "events", "target"])
def test_strict_decoder_rejects_malformed_retained_bytes(invalid: str) -> None:
    data = _observed(_TARGETS[0]).to_dict()
    if invalid == "unknown":
        data["unexpected"] = True
    elif invalid == "version":
        data["version"] = True
    elif invalid == "nan":
        data["entry"]["metadata"]["number"] = float("nan")
    elif invalid == "events":
        data["target"]["event_ids"] *= 2
    elif invalid == "target":
        data["target"]["unknown"] = True
    raw = json.dumps(data)
    if invalid == "duplicate":
        raw = raw.replace('"version": 1,', '"version": 1, "version": 1,', 1)
    with pytest.raises(ValueError):
        PointAcquisition.from_bytes(raw.encode())


def test_stale_parent_refuses_reads_and_writes(retained: tuple[PreparedPoints, TaskStore]) -> None:
    points, _tasks = retained
    observed = _observed(_TARGETS[0])
    points.retain(observed, claim=points.claim)
    with closing(points.journal.paths._connect()) as conn, conn:
        conn.execute("UPDATE prepared_crawl_parents SET generation = 'different'")
    for operation in (lambda: points.read(observed.target),
                      lambda: points.retain(observed, claim=points.claim),
                      points.compose):
        with pytest.raises(PendingCrawlConflict, match="no longer matches"):
            operation()


@pytest.mark.parametrize("change", ["task", "pin", "account", "endpoint", "query", "subset"])
def test_observation_claim_binding_cannot_be_substituted(
    retained: tuple[PreparedPoints, TaskStore], change: str,
) -> None:
    points, _tasks = retained
    claim = points.claim
    if change == "task":
        claim = replace(claim, task_id="other-task")
    elif change == "pin":
        claim = replace(claim, point_claim_id="b" * 32)
    elif change == "account":
        claim = replace(claim, hints=replace(claim.hints, account_id="other-account"))
    elif change == "subset":
        claim = replace(claim, hints=replace(claim.hints, targets=(_TARGETS[0],)))
    else:
        binding = (
            replace(claim.hints.binding, endpoint="https://other.test") if change == "endpoint"
            else replace(claim.hints.binding, query_context="different")
        )
        claim = replace(claim, hints=replace(claim.hints, binding=binding))
    with pytest.raises(PendingCrawlConflict, match="whole bound claim"):
        points.retain(_observed(_TARGETS[0]), claim=claim)
    assert points.read(_TARGETS[0]) is None
