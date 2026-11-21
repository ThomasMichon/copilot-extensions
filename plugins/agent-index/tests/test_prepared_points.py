"""Every exact named target must survive restart before candidate composition."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_index.indexing.component_manifest import ComponentIdentity, ComponentVersion
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
def retained(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[PreparedPoints, TaskStore]:
    parameter = getattr(request, "param", None)
    count, correlations = parameter if parameter is not None else (0, False)
    targets = _TARGETS if parameter is None else tuple(
        WorkItemRevisionTarget("shared" if correlations else str(index), 1, (f"event-{index}",))
        for index in range(count)
    )
    hints = PointChangeHints(
        source="selected", canonical_family=_FAMILY, account_id="account-a",
        binding=PointBinding("https://example.test", "project", "eligible-query/v1"),
        targets=targets,
    )
    tasks = TaskStore(tmp_path / "tasks.db")
    task = tasks.enqueue(hints.source, change_hints=hints)
    claim = PendingPointClaim.from_task(tasks.pin_point_claim(task.id, expected_hints=hints))
    paths = PathIndex(tmp_path / "paths.db")
    first = _observed(targets[0])
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


@pytest.mark.parametrize("retained", [(1024, False), (1024, True)], indirect=True)
def test_whole_claim_composition_uses_one_snapshot_and_batched_point_reads(
    retained: tuple[PreparedPoints, TaskStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, _tasks = retained
    targets = points.claim.hints.targets
    for target in targets:
        points.retain(_observed(target), claim=points.claim)
    connect = points.journal.paths._connect
    counts = {"connections": 0, "batch_reads": 0}
    memory = {"live": 0, "peak": 0, "largest": 0}

    class Payload(bytes):
        def __new__(cls, data: bytes) -> Payload:
            result = super().__new__(cls, data)
            memory["live"] += len(result)
            memory["peak"] = max(memory["peak"], memory["live"])
            memory["largest"] = max(memory["largest"], len(result))
            return result

        def __del__(self) -> None:
            memory["live"] -= len(self)

    def row_factory(cursor: Any, row: tuple[Any, ...]) -> dict[str, Any]:
        values = dict(zip((column[0] for column in cursor.description), row, strict=True))
        if isinstance(values.get("payload"), bytes):
            values["payload"] = Payload(values["payload"])
        return values

    def trace(sql: str) -> None:
        if sql.startswith("SELECT key, payload, digest"):
            counts["batch_reads"] += 1

    def observed_connect() -> Any:
        counts["connections"] += 1
        conn = connect()
        conn.row_factory = row_factory
        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(points.journal.paths, "_connect", observed_connect)
    candidate = points.compose()
    assert counts == {"connections": 1, "batch_reads": 6}
    assert 0 < memory["peak"] <= memory["largest"] * 4
    expected = {f"{t.item_id}/body.md" for t in targets}
    assert len(candidate.upserts) == len(expected)
    assert {entry.path for entry in candidate.upserts} == expected


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


def test_typed_identity_survives_candidate_binding_without_path_or_metadata_inference(
    retained: tuple[PreparedPoints, TaskStore],
) -> None:
    points, _tasks = retained
    for target in _TARGETS:
        observation = _observed(target, deleted=isinstance(target, CommentVersionTarget))
        identity = ComponentIdentity(
            target.kind, target.item_id,
            target.comment_id if isinstance(target, CommentVersionTarget) else None,
        )
        path = "opaque-" + target.item_id + "-" + (identity.comment_id or "body")
        observed = replace(
            observation, component=replace(observation.component, path=path, identity=identity),
            entry=replace(observation.entry, path=path, metadata={"item_id": "not-the-target"})
            if observation.entry is not None else None,
            deletion=replace(observation.deletion, path=path) if observation.deletion else None,
        )
        points.retain(observed, claim=points.claim)
        assert _reopen(points).read(target) == observed
    candidate = _reopen(points).compose()
    assert candidate.component_versions is not None
    assert {item.identity for item in candidate.component_versions} == {
        ComponentIdentity("work_item_revision", "1"),
        ComponentIdentity("comment_version", "1", "2"),
        ComponentIdentity("comment_version", "3", "2"),
    }
    bound = _bind(points, candidate)
    retained_manifest = json.loads(bound.get("named-point-application/v1", ["manifest"])["manifest"])
    assert PendingCrawls.decode_manifest(retained_manifest, "selected", components=True) == candidate
    assert _bind(_reopen(points), candidate).generation == bound.generation


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
    "metadata-key", "metadata-tuple", "metadata-dataclass", "metadata-dataclass-list",
    "nan", "language", "text", "identity",
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
    elif invalid == "identity":
        observed = replace(observed, component=replace(
            observed.component, identity=ComponentIdentity("work_item_revision", "different"),
        ))
    else:
        changes: dict[str, Any] = {
            "metadata-key": {"metadata": {1: "not-string"}},
            "metadata-tuple": {"metadata": {"not-json": (1, 2)}},
            "metadata-dataclass": {"metadata": {"not-json": observed.component}},
            "metadata-dataclass-list": {"metadata": {"not-json": [observed.component]}},
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


def _bind(points: PreparedPoints, manifest: CrawlManifest) -> PreparedJournal:
    parent = points.journal
    return PreparedJournal.for_point_application(
        parent.paths, parent.source, parent.scope, parent.payload,
        manifest=manifest, point_claim=points.claim,
    )


def test_complete_point_application_binding_survives_restart_without_parent_or_queue_effects(
    retained: tuple[PreparedPoints, TaskStore],
) -> None:
    points, tasks = retained
    parent = points.journal
    for target in _TARGETS:
        points.retain(_observed(target, deleted=isinstance(target, CommentVersionTarget)),
                      claim=points.claim)
    candidate = points.compose()
    bound = _bind(points, candidate)
    with closing(parent.paths._connect()) as conn:
        after = list(conn.iterdump())
    reopened = _bind(_reopen(points), candidate)
    assert (bound.payload, bound.point_claim, bound.generation) == (
        parent.payload, points.claim, parent.generation,
    )
    assert json.loads(bound.get("named-point-application/v1", ["manifest"])["manifest"]) == (
        PendingCrawls.manifest_data(candidate)
    )
    assert reopened.generation == parent.generation
    with closing(parent.paths._connect()) as conn:
        assert list(conn.iterdump()) == after
    for target in _TARGETS:
        assert PreparedPoints(reopened).read(target) == points.read(target)
    pending = PendingCrawls(parent.paths)
    assert pending.read_payload(parent.source, parent.scope) == parent.payload
    task = tasks.get_task(points.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    with pytest.raises(PendingCrawlConflict, match="manifest does not match"):
        PreparedJournal(
            parent.paths, parent.source, parent.scope, parent.payload,
            manifest=candidate, full=False, point_claim=points.claim,
        )
    with pytest.raises(ValueError, match="explicit claim binding"):
        pending.load(parent.source, parent.scope)
    with pytest.raises(ValueError, match="completion is not supported"):
        pending.clear(parent.source)


@pytest.mark.parametrize("missing", [_TARGETS[0], _TARGETS[2]])
def test_point_application_binding_requires_old_versions_and_correlations(
    retained: tuple[PreparedPoints, TaskStore], missing: PointTarget,
) -> None:
    points, _tasks = retained
    for target in _TARGETS:
        if target != missing:
            points.retain(_observed(target), claim=points.claim)
    with closing(points.journal.paths._connect()) as conn:
        before = list(conn.iterdump())
    with pytest.raises(ValueError, match="incomplete"):
        _bind(points, points.manifest)
    with closing(points.journal.paths._connect()) as conn:
        assert list(conn.iterdump()) == before


@pytest.mark.parametrize("change", ["text", "metadata-type", "subset", "poll", "stats", "metadata-dataclass"])
def test_point_application_binding_refuses_changed_candidate_without_writing(
    retained: tuple[PreparedPoints, TaskStore], change: str,
) -> None:
    points, _tasks = retained
    for target in _TARGETS:
        observed = _observed(target)
        assert observed.entry is not None
        points.retain(replace(observed, entry=replace(observed.entry, metadata={
            "qualifier": True, "component": {
                "source": _FAMILY, "path": "example.md", "version": 1, "semantics": "text/v1",
            },
        })), claim=points.claim)
    candidate = points.compose()
    if change == "subset":
        candidate.upserts.pop()
        assert candidate.component_versions is not None
        candidate.component_versions.pop()
    elif change == "poll":
        candidate.commit = "newer-poll"
    elif change == "stats":
        candidate.stats.files_scanned = 1
    elif change == "text":
        candidate.upserts[0] = replace(candidate.upserts[0], content="different")
    elif change == "metadata-type":
        candidate.upserts[0].metadata["qualifier"] = 1
    else:
        candidate.upserts[0].metadata["component"] = ComponentVersion(
            _FAMILY, "example.md", 1, "text/v1",
        )
    with closing(points.journal.paths._connect()) as conn:
        before = list(conn.iterdump())
    with pytest.raises((PendingCrawlConflict, ValueError), match="does not match|JSON values"):
        _bind(points, candidate)
    with closing(points.journal.paths._connect()) as conn:
        assert list(conn.iterdump()) == before


def test_lost_point_binding_return_reuses_exact_durable_binding(
    retained: tuple[PreparedPoints, TaskStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, _tasks = retained
    for target in _TARGETS:
        points.retain(_observed(target), claim=points.claim)
    candidate = points.compose()
    original = PreparedJournal.put_immutable

    def lose_return(self: PreparedJournal, kind: str, key: str, payload: bytes) -> None:
        original(self, kind, key, payload)
        raise RuntimeError("binding return lost")

    with monkeypatch.context() as patch:
        patch.setattr(PreparedJournal, "put_immutable", lose_return)
        with pytest.raises(RuntimeError, match="return lost"):
            _bind(points, candidate)
    assert _bind(_reopen(points), candidate).generation == points.journal.generation


def test_point_binding_refuses_conflicting_retained_binding(
    retained: tuple[PreparedPoints, TaskStore],
) -> None:
    points, _tasks = retained
    for target in _TARGETS:
        points.retain(_observed(target), claim=points.claim)
    candidate = points.compose()
    bound = _bind(points, candidate)
    bound.put("named-point-application/v1", [("manifest", b"conflicting binding")])
    with pytest.raises(PendingCrawlConflict, match="intent changed"):
        _bind(_reopen(points), candidate)
    assert bound.get("named-point-application/v1", ["manifest"]) == {
        "manifest": b"conflicting binding",
    }


def test_point_binding_rechecks_incarnation_after_composing(
    retained: tuple[PreparedPoints, TaskStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, _tasks = retained
    for target in _TARGETS:
        points.retain(_observed(target), claim=points.claim)
    candidate = points.compose()
    original = PreparedPoints.compose

    def replace_parent(self: PreparedPoints) -> CrawlManifest:
        manifest = original(self)
        with closing(self.journal.paths._connect()) as conn, conn:
            conn.execute("UPDATE prepared_crawl_parents SET generation = 'different'")
        return manifest

    monkeypatch.setattr(PreparedPoints, "compose", replace_parent)
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        _bind(points, candidate)
    assert _reopen(points).journal.get("named-point-application/v1", ["manifest"]) == {}
