"""FTS rebuild must rely on LanceDB's incremental ``optimize()`` update for an
already-available index, and only pay for a full ``create_fts_index(...,
replace=True)`` on the first build (or a recovery from an index that was
never successfully created).

Per LanceDB's own docs (Reindexing / Incremental Reindexing), ``optimize()``
already performs compaction, retention pruning, AND an incremental update of
any existing vector/scalar/FTS index against newly-ingested rows. Forcing
``replace=True`` on every dirty-triggered rebuild discarded and rebuilt the
whole BM25 index over the entire content table regardless of how small the
actual delta was -- observed downstream as a sustained heavy read burst off
a handful of new commits.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager

from agent_index.store.content_store import ChunkRecord, ContentStore


def _chunk(i: int, content: str | None = None) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=f"c{i}",
        source="test:repo",
        file_path=f"f{i}.py",
        chunk_type="function",
        language="python",
        content=content if content is not None else f"def f{i}(): pass",
        content_hash=f"hash{i}",
        line_start=1,
        line_end=1,
    )


def _fake_run_factory(calls: list[str]):
    """Return a ``subprocess.run`` stand-in that records the generated code
    string (argv[3]) and reports success without touching a real subprocess."""

    def _fake_run(argv, **kwargs):
        calls.append(argv[3])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return _fake_run


def test_first_build_uses_full_replace(tmp_path, monkeypatch):
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0), _chunk(1)])

    calls: list[str] = []
    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _fake_run_factory(calls)
    )

    assert store.fts_available is False
    ok = store.ensure_fts_index()

    assert ok is True
    assert store.fts_available is True
    assert len(calls) == 1
    assert "t.optimize()" in calls[0]
    assert "t.create_fts_index('content', replace=True)" in calls[0], (
        "the first-ever build has no existing index for optimize() to "
        "incrementally update, so it must still do a full replace=True build"
    )


def test_dirty_rebuild_of_available_index_skips_full_replace(tmp_path, monkeypatch):
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])

    calls: list[str] = []
    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _fake_run_factory(calls)
    )

    # First build: establishes fts_available=True (full replace, as above).
    assert store.ensure_fts_index() is True
    assert store.fts_available is True

    # New content lands; the index is merely dirty, not unavailable.
    store.upsert([_chunk(1)])
    store.mark_fts_dirty()
    assert store.fts_dirty is True

    ok = store.ensure_fts_index()

    assert ok is True
    assert store.fts_dirty is False
    assert len(calls) == 2
    assert "t.optimize()" in calls[1]
    assert "create_fts_index" not in calls[1], (
        "a dirty rebuild of an already-available index must rely on "
        "optimize()'s incremental FTS update, not a full from-scratch rebuild "
        "over the entire content table"
    )


def test_run_fts_build_code_gen_full_vs_incremental(tmp_path, monkeypatch):
    """Direct unit test of _run_fts_build's generated subprocess code for both
    the ``full`` and incremental paths, independent of the dirty/available
    bookkeeping exercised above."""
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])

    calls: list[str] = []
    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _fake_run_factory(calls)
    )

    store._run_fts_build(full=True)
    store._run_fts_build(full=False)

    assert "create_fts_index" in calls[0]
    assert "create_fts_index" not in calls[1]
    assert "t.optimize()" in calls[0]
    assert "t.optimize()" in calls[1]
    assert "try:" in calls[0]
    assert "try:" not in calls[1]


def test_optimize_failure_on_incremental_path_is_not_swallowed(tmp_path, monkeypatch):
    """A failed optimize() on the full=False path is the ONLY thing that
    would have updated the index (no create_fts_index follows it), so it
    must propagate as a failure -- not be swallowed into a false 'success'
    that clears _fts_dirty while the on-disk index silently goes stale.

    Both the initial incremental attempt AND its full-rebuild escalation
    (#2951) fail here, so the overall rebuild is still a genuine failure.
    """
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])
    store._fts_available = True  # simulate an already-available index

    def _failing_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="Traceback...\nRuntimeError: optimize boom"
        )

    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _failing_run
    )

    store.mark_fts_dirty()
    ok = store.ensure_fts_index(max_retries=1)

    # `ok` mirrors `fts_available` ("is the store currently serving FTS
    # queries at all"), not "did this particular rebuild succeed" -- a
    # previously-available index is deliberately left usable on a failed
    # rebuild (see ensure_fts_index's docstring). The correctness bar here
    # is that the failure is NOT silently treated as a successful update:
    # dirty stays pending and the failure is recorded for backoff/retry.
    assert ok is True, "a prior index remains reported as available/usable"
    assert store.fts_dirty is True, "work must remain pending for the next retry"
    assert store.fts_consecutive_failures == 1, (
        "a swallowed optimize() failure would never increment this -- it "
        "must be recorded as a genuine failed rebuild attempt"
    )


def test_incremental_optimize_failure_recovers_via_full_rebuild_escalation(
    tmp_path, monkeypatch
):
    """A non-retryable optimize() failure on the incremental path (e.g. a
    lancedb/lance-index panic against the current on-disk index state, #2951)
    must escalate to one full replace=True rebuild in the same cycle, since
    retrying the same incremental call would fail forever. When that
    escalation succeeds, the overall rebuild must be reported as successful
    and the failure bookkeeping must be cleared -- not left recording a
    failure that a full rebuild just resolved."""
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])
    store._fts_available = True  # simulate an already-available (but broken) index

    calls: list[str] = []

    def _run(argv, **kwargs):
        code = argv[3]
        calls.append(code)
        if "create_fts_index" not in code:
            # The incremental optimize()-only call: simulate the panic.
            return subprocess.CompletedProcess(
                argv, 1, stdout="",
                stderr="pyo3_async_runtimes.RustPanic: rust future panicked",
            )
        # The escalated full rebuild: succeeds.
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_index.store.content_store.subprocess.run", _run)

    store.mark_fts_dirty()
    store._fts_consecutive_failures = 3  # simulate prior failed cycles
    ok = store.ensure_fts_index(max_retries=1)

    assert ok is True
    assert store.fts_available is True
    assert store.fts_dirty is False, "the escalated full rebuild resolved the pending work"
    assert store.fts_consecutive_failures == 0, (
        "a successful escalation is a genuine recovery, not a continued failure"
    )
    assert len(calls) == 2
    assert "create_fts_index" not in calls[0], "first attempt is the incremental path"
    assert "t.create_fts_index('content', replace=True)" in calls[1], (
        "second attempt must be the escalated full rebuild"
    )


def test_retryable_conflict_exhaustion_does_not_escalate_to_full_rebuild(
    tmp_path, monkeypatch
):
    """A commit conflict that is genuinely retryable (per #1818) but exhausts
    its retries must keep the existing capped-backoff behavior -- it must NOT
    trigger the #2951 full-rebuild escalation, which is reserved for a
    non-retryable failure. Escalating here would pay for an unneeded full
    rebuild (and could mask the conflict) on every ordinary contention spike."""
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])
    store._fts_available = True

    calls: list[str] = []

    def _run(argv, **kwargs):
        code = argv[3]
        calls.append(code)
        return subprocess.CompletedProcess(
            argv, 1, stdout="",
            stderr="Retryable commit conflict for version 1",
        )

    monkeypatch.setattr("agent_index.store.content_store.subprocess.run", _run)
    monkeypatch.setattr("agent_index.store.content_store.time.sleep", lambda _s: None)

    store.mark_fts_dirty()
    ok = store.ensure_fts_index(max_retries=2)

    assert ok is True, "a prior index remains reported as available/usable"
    assert store.fts_dirty is True, "work must remain pending for the next retry"
    assert store.fts_consecutive_failures == 1
    assert len(calls) == 2, "both attempts are the incremental path; no escalation call"
    assert all("create_fts_index" not in c for c in calls)


def test_optimize_failure_on_full_path_is_swallowed(tmp_path, monkeypatch):
    """On the full path, create_fts_index (not optimize) is the source of
    correctness -- a failed optimize() there is a missed compaction, not a
    missed index update, so the generated code must still swallow it."""
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])

    calls: list[str] = []
    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _fake_run_factory(calls)
    )

    store._run_fts_build(full=True)

    assert "try:" in calls[0] and "except Exception" in calls[0]
    assert "t.create_fts_index('content', replace=True)" in calls[0]


def test_restart_detects_durable_fts_index_and_skips_full_rebuild(tmp_path, monkeypatch):
    """A fresh ContentStore (simulating a process restart) must detect an
    already-durable FTS index via LanceDB's own metadata and take the
    incremental path, instead of assuming _fts_available=False and paying
    for another full-corpus rebuild."""
    db_path = str(tmp_path / "db")

    # Real (unmocked) first build in one ContentStore "process".
    first = ContentStore(db_path)
    first.upsert([_chunk(0)])
    assert first.ensure_fts_index() is True
    assert first.fts_available is True

    # A brand-new ContentStore against the SAME db_path -- fts_available
    # starts False here, as it would after a real process restart.
    second = ContentStore(db_path)
    assert second.fts_available is False

    calls: list[str] = []
    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _fake_run_factory(calls)
    )

    second.mark_fts_dirty()
    ok = second.ensure_fts_index()

    assert ok is True
    assert second.fts_available is True, (
        "must detect the durable on-disk FTS index left by the prior process"
    )
    assert len(calls) == 1
    assert "create_fts_index" not in calls[0], (
        "restart detection must find the durable index and take the "
        "incremental path, not re-pay for a full rebuild"
    )


def test_full_decision_rechecks_durable_index_after_file_lock(tmp_path, monkeypatch):
    """If another process creates the first index while this process waits for
    the cross-process lock, the waiter must re-check durable metadata under the
    lock before deciding whether to run a full replace=True rebuild."""
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])

    lock_entered = False

    @contextmanager
    def _lock():
        nonlocal lock_entered
        lock_entered = True
        yield True

    def _durable_exists(_table):
        return lock_entered

    calls: list[str] = []
    monkeypatch.setattr(store, "_fts_file_lock", _lock)
    monkeypatch.setattr(store, "_durable_fts_index_exists", _durable_exists)
    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _fake_run_factory(calls)
    )

    assert store.fts_available is False
    assert store.ensure_fts_index() is True

    assert store.fts_available is True
    assert len(calls) == 1
    assert "create_fts_index" not in calls[0], (
        "the full/incremental decision must use the durable-index state observed "
        "after acquiring the cross-process lock"
    )


def test_recovery_from_unavailable_index_does_full_replace(tmp_path, monkeypatch):
    """If the index was never successfully built (fts_available False), even
    a 'merely dirty' mark must still trigger the full replace=True path -- an
    incremental optimize() cannot recover a nonexistent FTS index."""
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0)])

    calls: list[str] = []
    monkeypatch.setattr(
        "agent_index.store.content_store.subprocess.run", _fake_run_factory(calls)
    )

    assert store.fts_available is False
    store.mark_fts_dirty()
    ok = store.ensure_fts_index()

    assert ok is True
    assert len(calls) == 1
    assert "t.create_fts_index('content', replace=True)" in calls[0]


def _fts_index_stats(store: ContentStore):
    """(indexed, unindexed) row counts for the content FTS index, read from a
    freshly-opened table handle so a cached one cannot hide a newer version."""
    store._table = None
    table = store._get_or_create_table()
    indices = table.list_indices()
    name = getattr(indices[0], "name", None) or indices[0]["name"]
    stats = table.index_stats(name)
    return stats.num_indexed_rows, stats.num_unindexed_rows


def test_incremental_optimize_indexes_and_finds_new_content(tmp_path):
    """End-to-end against a REAL LanceDB table with no mocked subprocess:
    content added after the first build must be folded into the existing FTS
    index by the incremental (optimize-only) path, and be searchable.

    The code-generation tests above only inspect the generated child source,
    so they would still pass if ``optimize()`` did not actually update the
    index. Searchability alone is not sufficient evidence either -- LanceDB
    also flat-scans rows that are not yet in the index -- so this asserts the
    index itself absorbed the new row (unindexed count drops to zero).
    """
    store = ContentStore(str(tmp_path / "db"))
    store.upsert([_chunk(0), _chunk(1)])

    assert store.ensure_fts_index() is True
    assert store.fts_available is True
    assert _fts_index_stats(store) == (2, 0)
    assert store.fts_search("quetzalcoatl") == []

    store.upsert([_chunk(2, content="quetzalcoatl migration helper")])
    store.mark_fts_dirty()
    assert _fts_index_stats(store) == (2, 1), "new row is not yet indexed"

    # Incremental path: an already-available index, so no create_fts_index.
    assert store.ensure_fts_index() is True
    assert store.fts_dirty is False

    assert _fts_index_stats(store) == (3, 0), (
        "optimize() must incrementally fold newly-ingested rows into the "
        "EXISTING FTS index, without a full from-scratch rebuild"
    )
    hits = [chunk_id for chunk_id, _score in store.fts_search("quetzalcoatl")]
    assert hits == ["c2"]
