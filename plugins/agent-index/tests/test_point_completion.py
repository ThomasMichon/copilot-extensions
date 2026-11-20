"""Explicit native completion qualifies the whole claim without enabling a worker."""

from __future__ import annotations

import sqlite3
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pytest

import test_point_component_application as native
import test_point_prepared_application as prepared

from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.component_application import qualify_applied_component
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_completion import complete_point_claim
from agent_index.indexing.point_hints import WorkItemRevisionTarget
from agent_index.indexing.task_store import TaskRecord, TaskStatus, TaskStore
from agent_index.store.component_ledger import ComponentLedger
from agent_index.store.source_layout import SourceLayoutError

point_preparation = native.point_preparation
application = native.application
SCOPE, FAMILY = native.SCOPE, native.FAMILY


def complete(f: prepared.Fixture, **kwargs: Any) -> TaskRecord:
    options: dict[str, Any] = {
        "claim": f.points.claim, "scope": SCOPE, "paths": f.paths, "tasks": f.tasks,
        "ledger": ComponentLedger(f.config, f.store), "max_rows": 8,
    }
    return complete_point_claim(**(options | kwargs))


def fences(f: prepared.Fixture) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    result = []
    for owner in (f.tasks, f.paths):
        with closing(owner._connect()) as conn:
            result.append([tuple(row) for row in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name",
            )])
    return result[0], result[1]


@pytest.mark.parametrize("point_preparation", [False, True], indirect=True)
def test_whole_completion_reopens_retains_trailing_and_preserves_polling(
    application: prepared.Fixture,
) -> None:
    f = application
    assert native.prepare(f).publish() == (2, 0)
    claim = f.points.claim
    domain = f.store._source_domain
    assert domain is not None
    trailing = f.tasks.enqueue("selected", change_hints=replace(
        claim.hints, targets=(WorkItemRevisionTarget("one", 4, ("trailing-event",)),),
    ))
    before = fences(f), f.config.state_file.read_bytes(), domain.receipt()
    result = complete(f)
    assert result.status == TaskStatus.COMPLETE
    assert result.point_claim_id == claim.point_claim_id and result.change_hints == claim.hints
    assert result.result_stats is not None
    assert len(result.result_stats["point_completion"]["components"]) == 2
    assert f.tasks.get_task(trailing.id) == trailing
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) is None
    with closing(f.paths._connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM prepared_crawl_rows").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM prepared_crawl_parents").fetchone()[0] == 0
    native.reopen(f)
    f.tasks = TaskStore(f.tasks._db_path)
    assert complete(f) == result
    assert not f.calls
    assert (fences(f), f.config.state_file.read_bytes(), domain.receipt()) == before
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        f.tasks.update_status(claim.task_id, TaskStatus.FAILED)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        f.tasks.set_result_stats(claim.task_id, {"forged": True})
    assert f.tasks.dequeue_next(accept_change_hints=True) is None
    with pytest.raises(sqlite3.IntegrityError, match="unsupported"):
        f.tasks.update_status(trailing.id, TaskStatus.COMPLETE)


@pytest.mark.parametrize("interruption", ["before-retire", "after-delete"])
def test_queue_commit_before_pending_retirement_has_a_requalifying_retry(
    application: prepared.Fixture, monkeypatch: pytest.MonkeyPatch, interruption: str,
) -> None:
    f = application
    assert native.prepare(f).publish() == (2, 0)
    parent = f.points.journal.payload
    before = fences(f)
    original = PendingCrawls._retire_point_claim

    def interrupt(*args: Any, **kwargs: Any) -> None:
        if interruption == "after-delete":
            original(*args, **kwargs)
        raise RuntimeError("fixture lost completion return")

    with monkeypatch.context() as patch:
        patch.setattr(PendingCrawls, "_retire_point_claim", staticmethod(interrupt))
        with pytest.raises(RuntimeError, match="lost completion return"):
            complete(f)
    result = f.tasks.get_task(f.points.claim.task_id)
    assert result is not None and result.status == TaskStatus.COMPLETE
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    assert fences(f) == before
    native.reopen(f)
    f.tasks = TaskStore(f.tasks._db_path)
    assert complete(f) == result
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) is None
    assert not f.calls and fences(f) == before


@pytest.mark.parametrize("status", [TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.INTERRUPTED])
def test_noncurrent_queue_outcome_cannot_retire_applied_work(
    application: prepared.Fixture, status: TaskStatus,
) -> None:
    f = application
    native.prepare(f).publish()
    f.tasks.update_status(f.points.claim.task_id, status)
    before = f.tasks.get_task(f.points.claim.task_id), f.cache(), fences(f)
    with pytest.raises(ValueError, match="no longer eligible"):
        complete(f)
    assert (f.tasks.get_task(f.points.claim.task_id), f.cache(), fences(f)) == before


@pytest.mark.parametrize("drift", [
    "unapplied", "incomplete", "binding", "checkpoint", "coverage", "policy", "claim", "scope",
    "policy-during-qualification",
])
def test_partial_or_stale_evidence_cannot_acknowledge_the_whole_claim(
    application: prepared.Fixture, drift: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    native.prepare(f).publish() if drift != "unapplied" else native.prepare(f)
    options: dict[str, Any] = {}
    if drift in ("incomplete", "binding", "coverage"):
        with closing(f.paths._connect()) as conn, conn:
            if drift == "coverage":
                conn.execute("DELETE FROM embedding_coverage")
            else:
                kind = "named-point-acquisition/v1" if drift == "incomplete" else "named-point-application/v1"
                conn.execute("DELETE FROM prepared_crawl_rows WHERE kind = ?", (kind,))
    elif drift == "checkpoint":
        f.paths.mark_indexed(FAMILY, "opaque-body", content_hash="stale", chunk_count=9)
    elif drift == "policy":
        f.config.model_profiles["first"] = replace(f.config.model_profiles["first"], revision="next")
    elif drift == "claim":
        options["claim"] = replace(f.points.claim, point_claim_id="b" * 32)
    elif drift == "scope":
        options["scope"] = "different-account"
    elif drift == "policy-during-qualification":
        def qualify(*args: Any, **kwargs: Any) -> Any:
            result = qualify_applied_component(*args, **kwargs)
            if args[2].content.component.path == "opaque-comment":
                f.config.model_profiles["first"] = replace(f.config.model_profiles["first"], revision="next")
            return result

        monkeypatch.setattr("agent_index.indexing.point_completion.qualify_applied_component", qualify)
    before = f.tasks.get_task(f.points.claim.task_id), f.cache(), fences(f)
    with pytest.raises((ValueError, PendingCrawlConflict, SourceLayoutError)):
        complete(f, **options)
    assert (f.tasks.get_task(f.points.claim.task_id), f.cache(), fences(f)) == before


@pytest.mark.parametrize("retire", [False, True])
def test_completed_retry_refuses_a_mixed_checkpoint_restore(
    application: prepared.Fixture, monkeypatch: pytest.MonkeyPatch, retire: bool,
) -> None:
    f = application
    native.prepare(f).publish()
    with monkeypatch.context() as patch:
        if not retire:
            patch.setattr(PendingCrawls, "_retire_point_claim", staticmethod(lambda *a, **k: None))
        result = complete(f)
    f.paths.mark_indexed(FAMILY, "opaque-body", content_hash="old-backup", chunk_count=1)
    native.reopen(f)
    before = f.cache(), fences(f), f.config.state_file.read_bytes()
    with pytest.raises(SourceLayoutError, match="checkpoint"):
        complete(f)
    assert f.tasks.get_task(result.id) == result
    assert (f.cache(), fences(f), f.config.state_file.read_bytes()) == before


def test_matching_higher_applied_versions_cover_each_acquired_target(
    application: prepared.Fixture,
) -> None:
    f = application
    native.prepare(f).publish()
    ledger = ComponentLedger(f.config, f.store)
    scope = f.points.claim.hints.scope
    with ledger._source_domain._write(f.store.content_store.db, point_scope=scope):
        for component in f.candidate.component_versions or []:
            previous = ledger.get(component.source, component.path, point_scope=scope)
            assert previous is not None
            higher = replace(
                previous, content=replace(
                    previous.content, component=replace(component, version=component.version + 1),
                ),
            )
            ledger.put(higher, expected=previous, max_rows=8, point_scope=scope)
    assert complete(f).status == TaskStatus.COMPLETE


def test_lost_return_retry_cannot_retire_a_newer_claim_parent(application: prepared.Fixture) -> None:
    f = application
    native.prepare(f).publish()
    completed = complete(f)
    hints = replace(f.points.claim.hints, targets=(WorkItemRevisionTarget("one", 4, ("new-event",)),))
    task = f.tasks.enqueue("selected", change_hints=hints)
    newer = PendingPointClaim.from_task(f.tasks.pin_point_claim(task.id, expected_hints=hints))
    PendingCrawls(f.paths).save(f.candidate, scope=SCOPE, full=False, point_claim=newer)
    before = f.cache(), f.tasks.get_task(newer.task_id), fences(f)
    assert complete(f) == completed
    assert (f.cache(), f.tasks.get_task(newer.task_id), fences(f)) == before


def test_native_directory_restore_cannot_discharge_a_completed_pending_parent(
    application: prepared.Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    f = application
    before_application = tmp_path / "before-application"
    shutil.copytree(f.config.lance_dir, before_application)
    native.prepare(f).publish()
    with monkeypatch.context() as patch:
        patch.setattr(PendingCrawls, "_retire_point_claim", staticmethod(lambda *a, **k: None))
        completed = complete(f)
    f.config.lance_dir.rename(tmp_path / "applied-native")
    shutil.copytree(before_application, f.config.lance_dir)
    native.reopen(f)
    before = f.cache(), fences(f), f.config.state_file.read_bytes()
    with pytest.raises(SourceLayoutError, match="not durably applied"):
        complete(f)
    assert f.tasks.get_task(completed.id) == completed
    assert (f.cache(), fences(f), f.config.state_file.read_bytes()) == before


def test_cancellation_cannot_cross_the_current_claim_completion_boundary(
    application: prepared.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    native.prepare(f).publish()
    reached, cancellation_started = Event(), Event()
    original = qualify_applied_component

    def qualify(*args: Any, **kwargs: Any) -> Any:
        reached.set()
        assert cancellation_started.wait(5)
        return original(*args, **kwargs)

    def cancel() -> bool:
        assert reached.wait(5)
        cancellation_started.set()
        return f.tasks.cancel(f.points.claim.task_id)

    monkeypatch.setattr("agent_index.indexing.point_completion.qualify_applied_component", qualify)
    with ThreadPoolExecutor(max_workers=2) as pool:
        completion = pool.submit(complete, f)
        cancellation = pool.submit(cancel)
        result = completion.result(timeout=15)
        assert cancellation.result(timeout=15) is False
    assert result.status == TaskStatus.COMPLETE
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) is None


@pytest.mark.parametrize("fence", [
    "tasks_point_execution_unsupported_update", "pending_point_claim_delete",
])
def test_abrupt_exit_cannot_commit_a_gap_in_the_legacy_fence(
    application: prepared.Fixture, fence: str,
) -> None:
    f = application
    queue = fence.startswith("tasks_")
    owner = f.tasks if queue else f.paths
    before = fences(f), f.tasks.get_task(f.points.claim.task_id), f.cache()
    code = """
import os, sqlite3, sys
from agent_index.indexing.point_claim import _point_completion_transition
conn = sqlite3.connect(sys.argv[1])
conn.execute('BEGIN IMMEDIATE')
with _point_completion_transition(conn, sys.argv[2]):
    conn.execute(sys.argv[3])
    os._exit(17)
"""
    statement = (
        "UPDATE tasks SET status = 'complete'" if queue
        else "DELETE FROM pending_crawls"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, owner._db_path, fence, statement],
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 17, result.stderr.decode(errors="replace")
    assert (fences(f), f.tasks.get_task(f.points.claim.task_id), f.cache()) == before
    with pytest.raises(sqlite3.IntegrityError, match="unsupported"):
        f.tasks.update_status(f.points.claim.task_id, TaskStatus.COMPLETE)
    with pytest.raises(ValueError, match="completion is not supported"):
        PendingCrawls(f.paths).clear("selected")
