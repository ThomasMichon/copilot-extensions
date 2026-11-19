"""Pinned whole claims are inert, exact-parent acquisition evidence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from agent_index.indexing.component_manifest import ComponentVersion
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls, recover_source
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import (
    CommentVersionTarget, PointBinding, PointChangeHints, WorkItemRevisionTarget,
)
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.state import IndexState
from agent_index.indexing.task_store import TaskStatus, TaskStore
from agent_index.sources.base import FileEntry


@pytest.fixture
def bound_work(
    tmp_path: Path,
) -> tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore]:
    hints = PointChangeHints(
        source="selected", canonical_family="sample:components", account_id="account-a",
        binding=PointBinding("https://example.test", "project", "eligible-query/v1"),
        targets=(
            WorkItemRevisionTarget("1", 7, ("event-a",)),
            WorkItemRevisionTarget("1", 8, ("event-b",)),
            CommentVersionTarget("1", "2", 4, ("event-c",)),
            CommentVersionTarget("3", "2", 4, ("event-d",)),
        ),
    )
    tasks = TaskStore(tmp_path / "tasks.db")
    task = tasks.enqueue(hints.source, change_hints=hints)
    with pytest.raises(ValueError, match="immutable parked"):
        PendingPointClaim.from_task(task)
    claim = PendingPointClaim.from_task(tasks.pin_point_claim(task.id, expected_hints=hints))
    paths = PathIndex(tmp_path / "paths.db")
    pending = PendingCrawls(paths)
    # Ordering a manifest point does not prove acquisition of every named target.
    manifest = CrawlManifest(
        source=hints.source, accounting_family=hints.canonical_family, commit="unchanged-poll",
        upserts=[FileEntry("body.md", "Fields", "markdown", hints.canonical_family)],
        component_versions=[ComponentVersion(hints.canonical_family, "body.md", 8, "body/v1")],
    )
    return paths, pending, manifest, claim, tasks


def _save(
    pending: PendingCrawls, manifest: CrawlManifest, claim: PendingPointClaim,
) -> str:
    pending.save(manifest, scope="recovery-scope", full=False, point_claim=claim)
    payload = pending.read_payload(manifest.source, "recovery-scope")
    assert payload is not None
    return payload


def test_whole_claim_roundtrip_reopen_and_trailing_arrivals(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
) -> None:
    paths, pending, manifest, claim, tasks = bound_work
    payload = _save(pending, manifest, claim)
    assert json.loads(payload)["version"] == 9
    reopened = PendingCrawls(PathIndex(Path(paths._db_path)))
    assert reopened.decode_point_pending(payload, manifest.source) == (manifest, claim)
    cache = PreparedJournal(
        paths, manifest.source, "recovery-scope", payload,
        manifest=manifest, full=False, point_claim=claim,
    )
    cache.put("vector", [("pair", b"retained")])
    assert PreparedJournal(
        paths, manifest.source, "recovery-scope", payload, point_claim=claim,
    ).get("vector", ["pair"]) == {"pair": b"retained"}
    trailing = tasks.enqueue(claim.hints.source, change_hints=claim.hints)
    assert trailing.id != claim.task_id and trailing.point_claim_id is None
    pinned = tasks.get_task(claim.task_id)
    assert pinned is not None and PendingPointClaim.from_task(pinned) == claim
    assert pinned.status == TaskStatus.QUEUED_HINTS_V2
    assert reopened.read_payload(manifest.source, "recovery-scope") == payload


@pytest.mark.parametrize(
    "mismatch", ["task", "pin", "account", "endpoint", "query", "query-scope", "subset",
                 "revision", "parent", "events", "scope", "manifest", "mode", "payload"],
)
def test_wrong_whole_claim_or_parent_refuses_without_prepared_writes(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
    mismatch: str,
) -> None:
    paths, pending, manifest, claim, _tasks = bound_work
    payload = _save(pending, manifest, claim)
    supplied, scope, full, application = claim, "recovery-scope", False, manifest
    if mismatch == "task":
        supplied = replace(claim, task_id="other-task")
    elif mismatch == "pin":
        supplied = replace(claim, point_claim_id="f" * 32)
    elif mismatch == "account":
        supplied = replace(claim, hints=replace(claim.hints, account_id="account-b"))
    elif mismatch in ("endpoint", "query", "query-scope"):
        binding = claim.hints.binding
        if mismatch == "endpoint":
            binding = replace(binding, endpoint="different")
        elif mismatch == "query":
            binding = replace(binding, query_context="different")
        else:
            binding = replace(binding, query_scope="different")
        supplied = replace(claim, hints=replace(
            claim.hints, binding=binding,
        ))
    elif mismatch in ("subset", "revision", "parent", "events"):
        targets = claim.hints.targets
        replacements = {
            "subset": targets[:1],
            "revision": (WorkItemRevisionTarget("1", 9, ("event-a",)),),
            "parent": (CommentVersionTarget("other", "2", 4, ("event-c",)),),
            "events": (WorkItemRevisionTarget("1", 7, ("different",)),),
        }
        supplied = replace(claim, hints=replace(claim.hints, targets=replacements[mismatch]))
    elif mismatch == "scope":
        scope = "other-scope"
    elif mismatch == "manifest":
        application = replace(manifest, commit="newer-poll")
    elif mismatch == "mode":
        full = True
    else:
        payload += " "
    with closing(paths._connect()) as conn:
        before = list(conn.iterdump())
    with pytest.raises(PendingCrawlConflict):
        PreparedJournal(
            paths, manifest.source, scope, payload, manifest=application, full=full,
            point_claim=supplied,
        )
    with closing(paths._connect()) as conn:
        assert list(conn.iterdump()) == before


@pytest.mark.parametrize(
    "invalid", ["version", "full", "unknown", "missing", "pin", "claim-version", "family",
                "source", "legacy", "representation", "duplicate"],
)
def test_invalid_bound_envelope_is_refused(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
    invalid: str,
) -> None:
    _paths, pending, manifest, claim, _tasks = bound_work
    payload = _save(pending, manifest, claim)
    raw: dict[str, Any] = json.loads(payload)
    if invalid == "version":
        raw["version"] = True
    elif invalid == "full":
        raw["full"] = True
    elif invalid == "unknown":
        raw["extra"] = True
    elif invalid == "missing":
        del raw["point_claim"]["hints"]["targets"]
    elif invalid == "pin":
        raw["point_claim"]["point_claim_id"] = "not-a-pin"
    elif invalid == "claim-version":
        raw["point_claim"]["version"] = True
    elif invalid in ("family", "source"):
        raw["point_claim"]["hints"][
            "canonical_family" if invalid == "family" else "source"
        ] = "different"
    elif invalid == "legacy":
        del raw["manifest"]["component_versions"]
    elif invalid == "representation":
        raw["point_claim"]["hints"]["binding"]["representation_version"] = 2
    else:
        payload = payload.replace('"revision": 7', '"revision": 7, "revision": 8')
        assert payload != json.dumps(raw)
    with pytest.raises(ValueError):
        pending.decode_point_pending(payload if invalid == "duplicate" else json.dumps(raw),
                                     manifest.source)


def test_bound_work_cannot_enter_ordinary_recovery_or_retirement(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
    tmp_path: Path,
) -> None:
    paths, pending, manifest, claim, tasks = bound_work
    payload = _save(pending, manifest, claim)
    state = IndexState()
    state.mark_source_indexed(manifest.source, "old-poll", 3)
    state_file = tmp_path / "state.json"
    state.save(state_file)
    before = state_file.read_bytes()
    untouched = Mock()
    with pytest.raises(ValueError, match="explicit claim binding"):
        pending.load(manifest.source, "recovery-scope")
    with pytest.raises(ValueError, match="explicit claim binding"):
        PreparedJournal(paths, manifest.source, "recovery-scope", payload)
    with pytest.raises(ValueError, match="explicit claim binding"):
        recover_source(
            manifest.source, scope="recovery-scope", state=state, state_file=state_file,
            paths=paths, full=False, acquire=untouched, apply=untouched,
            prepare=untouched, publication=untouched, family=claim.hints.canonical_family,
        )
    with pytest.raises(ValueError, match="completion is not supported"):
        pending.clear(manifest.source)
    with pytest.raises(ValueError, match="mandatory replay"):
        pending.discard_acquisition(
            manifest.source, scope="recovery-scope", expected_payload=payload,
            family=claim.hints.canonical_family,
        )
    assert untouched.mock_calls == [] and state_file.read_bytes() == before
    pinned = tasks.get_task(claim.task_id)
    assert pinned is not None and pinned.status == TaskStatus.QUEUED_HINTS_V2
    assert pending.read_payload(manifest.source, "recovery-scope") == payload


@pytest.mark.parametrize("invalid", ["source", "family", "legacy", "full", "refresh"])
def test_invalid_claim_publication_does_not_create_a_pending_parent(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
    invalid: str,
) -> None:
    from agent_index.indexing.manifest import PollRefresh

    paths, pending, manifest, claim, _tasks = bound_work
    if invalid == "source":
        claim = replace(claim, hints=replace(claim.hints, source="other"))
    elif invalid == "family":
        claim = replace(claim, hints=replace(claim.hints, canonical_family="other"))
    elif invalid == "legacy":
        manifest = replace(manifest, component_versions=None)
    elif invalid == "refresh":
        manifest = replace(manifest, poll_refresh=PollRefresh(retain=True, paths={}, history={}))
    with closing(paths._connect()) as conn:
        before = list(conn.iterdump())
    with pytest.raises(ValueError):
        pending.save(manifest, scope="recovery-scope", full=invalid == "full", point_claim=claim)
    with closing(paths._connect()) as conn:
        assert list(conn.iterdump()) == before


def test_retained_binding_does_not_turn_cancellation_into_success(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
) -> None:
    _paths, pending, manifest, claim, tasks = bound_work
    payload = _save(pending, manifest, claim)
    assert tasks.cancel(claim.task_id)
    cancelled = tasks.get_task(claim.task_id)
    assert cancelled is not None and cancelled.point_claim_id == claim.point_claim_id
    with pytest.raises(ValueError, match="immutable parked"):
        PendingPointClaim.from_task(cancelled)
    with pytest.raises(sqlite3.IntegrityError):
        tasks.update_status(claim.task_id, TaskStatus.COMPLETE)
    assert pending.decode_point_pending(payload, manifest.source) == (manifest, claim)
    with pytest.raises(ValueError, match="completion is not supported"):
        pending.clear(manifest.source)


@pytest.mark.parametrize(
    "statement", [
        "DELETE FROM pending_crawls",
        "UPDATE pending_crawls SET payload = ?",
        "UPDATE pending_crawls SET scope = 'other'",
        "UPDATE pending_crawls SET source = 'other'",
        "INSERT OR REPLACE INTO pending_crawls VALUES ('selected', 'recovery-scope', ?)",
    ],
)
def test_older_sql_writers_cannot_remove_or_downgrade_bound_work(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
    statement: str,
) -> None:
    paths, pending, manifest, claim, _tasks = bound_work
    payload = _save(pending, manifest, claim)
    legacy = json.dumps({
        "version": 1, "full": False,
        "manifest": pending.manifest_data(replace(manifest, component_versions=None)),
    })
    with closing(paths._connect()) as conn, conn:
        with pytest.raises(sqlite3.IntegrityError, match="completion is not supported"):
            conn.execute(statement, (legacy,) if "?" in statement else ())
    assert pending.read_payload(manifest.source, "recovery-scope") == payload


def test_restored_identical_payload_does_not_revive_a_stale_prepared_incarnation(
    bound_work: tuple[PathIndex, PendingCrawls, CrawlManifest, PendingPointClaim, TaskStore],
) -> None:
    paths, pending, manifest, claim, _tasks = bound_work
    payload = _save(pending, manifest, claim)
    with closing(sqlite3.connect(":memory:")) as saved:
        with closing(paths._connect()) as conn:
            conn.backup(saved)
        first = PreparedJournal(
            paths, manifest.source, "recovery-scope", payload, point_claim=claim,
        )
        first.put("vector", [("pair", b"old-incarnation")])
        with closing(paths._connect()) as conn:
            saved.backup(conn)
    reopened = PreparedJournal(
        paths, manifest.source, "recovery-scope", payload, point_claim=claim,
    )
    assert reopened.generation != first.generation
    assert reopened.get("vector", ["pair"]) == {}
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        first.put("vector", [("pair", b"stale")])
