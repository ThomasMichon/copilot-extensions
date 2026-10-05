"""A full reindex must never race or trail a now-redundant incremental one.

This file locks in the intended ``TaskStore`` sequencing contract end to end:

- ``enqueue(full=True, ...)`` cancels any *covered*, still-``queued``
  incremental task(s) -- ``source='all'`` covers every source; a specific
  source only covers its own exact match. An already-``processing``
  incremental is left to finish (there is no safe way to interrupt an
  in-flight worker process).
- ``enqueue(full=False, ...)`` for a source already covered by a
  still-``queued`` full task is itself redundant: it returns that covering
  full task instead of inserting a new row. A *processing* full task does
  NOT count as covering -- its crawl may have already passed this source, so
  coalescing into it could silently drop a change that lands afterward; the
  incremental must enqueue normally in that case.
- ``dequeue_next()`` serves queued full tasks ahead of incremental ones
  (FIFO within each tier), so a full queued *after* an incremental for the
  same scope still runs first rather than racing/trailing it.
"""

from __future__ import annotations

from agent_index.indexing.task_store import TaskStatus, TaskStore


def _store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_full_all_cancels_every_queued_incremental(tmp_path) -> None:
    store = _store(tmp_path)
    inc_a = store.enqueue(source="git:a", full=False)
    inc_b = store.enqueue(source="git:b", full=False)

    store.enqueue(source="all", full=True)

    assert store.get_task(inc_a.id).status == TaskStatus.CANCELLED.value
    assert store.get_task(inc_b.id).status == TaskStatus.CANCELLED.value


def test_full_specific_source_cancels_only_matching_incremental(tmp_path) -> None:
    store = _store(tmp_path)
    inc_a = store.enqueue(source="git:a", full=False)
    inc_b = store.enqueue(source="git:b", full=False)

    store.enqueue(source="git:a", full=True)

    assert store.get_task(inc_a.id).status == TaskStatus.CANCELLED.value
    # A full reindex scoped to one source must not touch an unrelated
    # source's still-pending incremental -- that one still needs to run.
    assert store.get_task(inc_b.id).status == TaskStatus.QUEUED.value


def test_full_does_not_cancel_already_processing_incremental(tmp_path) -> None:
    """Only *queued* incrementals are cancelled -- a running one is left to
    finish (there is no safe way to interrupt an in-flight worker process)."""
    store = _store(tmp_path)
    inc = store.enqueue(source="git:a", full=False)
    store.dequeue_next()  # claim it: queued -> processing

    store.enqueue(source="git:a", full=True)

    assert store.get_task(inc.id).status == TaskStatus.PROCESSING.value


def test_incremental_covered_by_queued_full_is_redundant(tmp_path) -> None:
    store = _store(tmp_path)
    full_task = store.enqueue(source="git:a", full=True)

    result = store.enqueue(source="git:a", full=False)

    # No new row inserted -- the caller gets back the covering full task.
    assert result.id == full_task.id
    assert result.full is True


def test_incremental_covered_by_queued_full_all_is_redundant(tmp_path) -> None:
    store = _store(tmp_path)
    full_task = store.enqueue(source="all", full=True)

    result = store.enqueue(source="git:any-source", full=False)

    assert result.id == full_task.id


def test_incremental_not_coalesced_behind_processing_full(tmp_path) -> None:
    """A *processing* full task must NOT be treated as covering: a full-'all'
    run crawls sources sequentially, so by the time this request arrives the
    processing task may have already crawled this source. Coalescing into it
    would silently drop a change that lands afterward -- nothing would be
    left queued to pick it up. The incremental must enqueue as a real row."""
    store = _store(tmp_path)
    full_task = store.enqueue(source="git:a", full=True)
    store.dequeue_next()  # queued -> processing

    result = store.enqueue(source="git:a", full=False)

    assert result.id != full_task.id
    assert result.full is False
    assert result.status == TaskStatus.QUEUED.value


def test_incremental_for_uncovered_source_still_enqueues_normally(tmp_path) -> None:
    """Regression: an incremental for a source with no covering full task
    must still insert a brand-new row exactly as before."""
    store = _store(tmp_path)
    store.enqueue(source="git:a", full=True)

    result = store.enqueue(source="git:b", full=False)

    assert result.source == "git:b"
    assert result.full is False
    assert result.status == TaskStatus.QUEUED.value


def test_dequeue_prioritizes_full_over_earlier_queued_incremental(tmp_path) -> None:
    """A full request made *after* an incremental for the same scope must
    still be served first -- it must never trail (or race) a now-redundant
    incremental."""
    store = _store(tmp_path)
    inc = store.enqueue(source="git:a", full=False)
    full_task = store.enqueue(source="git:b", full=True)

    claimed = store.dequeue_next()

    assert claimed.id == full_task.id
    # The incremental is still queued behind it, not skipped entirely.
    assert store.get_task(inc.id).status == TaskStatus.QUEUED.value


def test_dequeue_is_fifo_within_the_same_priority_tier(tmp_path) -> None:
    store = _store(tmp_path)
    first = store.enqueue(source="git:a", full=False)
    second = store.enqueue(source="git:b", full=False)

    claimed = store.dequeue_next()

    assert claimed.id == first.id
    claimed_next = store.dequeue_next()
    assert claimed_next.id == second.id


def test_full_reindex_can_target_a_single_source(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.enqueue(source="git:one-repo", full=True)

    assert task.source == "git:one-repo"
    assert task.full is True


def test_full_reindex_can_target_everything_everywhere_all_at_once(tmp_path) -> None:
    store = _store(tmp_path)
    task = store.enqueue(source="all", full=True)

    assert task.source == "all"
    assert task.full is True
