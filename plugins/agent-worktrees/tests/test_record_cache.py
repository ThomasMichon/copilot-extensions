"""Tests for :mod:`record_cache` -- the per-process memoization layer for
``tracking``'s record reads and writes (``agent-worktrees-authoritative-daemon``
effort: "whenever a write is posted to the daemon, ensure all subsequent
readers see that result without another direct read").

Covers the module directly (``store``/``cached_load``/``clear``) as well as
the ``tracking.save_record``/``tracking.load_record`` integration that
routes every write and every single-record read through it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import record_cache, tracking
from agent_worktrees.tracking_lifecycle import create_new_record


@pytest.fixture(autouse=True)
def _clear_cache():
    record_cache.clear()
    yield
    record_cache.clear()


class TestCachedLoadDirect:
    def test_miss_then_hit_returns_equal_but_independent_copies(self, tmp_path: Path):
        path = tmp_path / "wt-A.yaml"
        record = create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        record_cache.clear()  # start cold -- creation above already pushed a cache entry
        calls = []

        def _loader(p: Path) -> tracking.WorktreeRecord:
            calls.append(p)
            return tracking._load_record_uncached(p)

        first = record_cache.cached_load(path, _loader)
        second = record_cache.cached_load(path, _loader)
        assert first.worktree_id == record.worktree_id == second.worktree_id
        assert len(calls) == 1, "second call must be a cache hit, not a re-parse"
        second.title = "mutated only on this copy"
        assert first.title != "mutated only on this copy"

    def test_file_change_invalidates_the_cache(self, tmp_path: Path):
        # A change that does NOT go through record_cache.store at all (a
        # raw out-of-process rewrite of the file, unmediated by any
        # tracking.py function) must still be picked up on the very next
        # cached_load, via the (mtime_ns, size) stamp -- this is the
        # "never a TTL/blackout window" guarantee.
        path = tmp_path / "wt-A.yaml"
        create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        record_cache.clear()  # start cold -- creation above already pushed a cache entry
        calls = []

        def _loader(p: Path) -> tracking.WorktreeRecord:
            calls.append(p)
            return tracking._load_record_uncached(p)

        record_cache.cached_load(path, _loader)
        path.write_text(path.read_text(encoding="utf-8") + "\n# raw external append\n",
                         encoding="utf-8")
        record_cache.cached_load(path, _loader)
        assert len(calls) == 2, "the raw external rewrite's stat change must force a re-parse"

    def test_copy_result_false_returns_the_cache_s_own_object(self, tmp_path: Path):
        """picker-performance-and-responsiveness Phase 3: ``copy_result=False``
        is the explicit opt-out of the per-hit ``copy.deepcopy`` -- a cache
        HIT must hand back the identical cached object (``is``, not just
        ``==``), both across repeated calls and against the object a
        concurrent ``copy_result=True`` caller would otherwise independently
        copy from. The miss path must also skip the copy (the object just
        placed in the cache is returned directly), since a caller requesting
        ``copy_result=False`` makes the same no-mutate promise on a miss as
        on a hit."""
        path = tmp_path / "wt-A.yaml"
        create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        record_cache.clear()

        # Miss path: no copy.
        first = record_cache.cached_load(
            path, tracking._load_record_uncached, copy_result=False,
        )
        # Hit path: same object as the miss path cached, still no copy.
        second = record_cache.cached_load(
            path, tracking._load_record_uncached, copy_result=False,
        )
        assert first is second

        # The default (copy_result=True) path is untouched by the opt-out:
        # still an independent copy, every call.
        third = record_cache.cached_load(path, tracking._load_record_uncached)
        assert third is not first
        assert third.worktree_id == first.worktree_id


class TestStoreStampsLoadedFrom:
    def test_store_sets_loaded_from_to_the_given_path_not_the_records_own(
        self, tmp_path: Path,
    ):
        # tracking.save_record calls record_cache.store(path, record) BEFORE
        # it sets record._loaded_from = path on its own in-memory object
        # (still inside the record lock, to avoid a write/stat race) -- so
        # store() must stamp _loaded_from onto the copy it caches itself,
        # or a later cache-hit load_record() hands back a record whose
        # yaml_path resolves to the WRONG file.
        path = tmp_path / "wt-A.yaml"
        record = create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        record._loaded_from = None
        record_cache.store(path, record)
        cached = record_cache.cached_load(path, tracking._load_record_uncached)
        assert cached._loaded_from == path

    def test_store_strips_transient_projection_dirty_markers(self, tmp_path: Path):
        # _save_record_unlocked can populate _session_projection_dirty /
        # _session_projection_initial_registration / _controller_projection_
        # dirty while serializing; these are cleared on the CALLER's own
        # object only after the cache write returns
        # (_flush_session_projections, once the lock releases). A fresh
        # uncached parse never carries these attributes at all, so the
        # cached copy must not either -- otherwise a cache-hit load_record()
        # would replay already-flushed projection work.
        path = tmp_path / "wt-A.yaml"
        record = create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        record._session_projection_dirty = {"sess-1"}
        record._session_projection_initial_registration = {"sess-1"}
        record._controller_projection_dirty = {"ctrl-1"}
        record_cache.store(path, record)
        cached = record_cache.cached_load(path, tracking._load_record_uncached)
        assert not hasattr(cached, "_session_projection_dirty")
        assert not hasattr(cached, "_session_projection_initial_registration")
        assert not hasattr(cached, "_controller_projection_dirty")


class TestSaveRecordPushesCache:
    def test_load_record_after_save_never_reparses(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "wt-A.yaml"
        record = create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        record.title = "pushed on save"
        tracking.save_record(record, path)

        calls = []
        real_uncached = tracking._load_record_uncached

        def _spy(p: Path) -> tracking.WorktreeRecord:
            calls.append(p)
            return real_uncached(p)

        monkeypatch.setattr(tracking, "_load_record_uncached", _spy)
        reloaded = tracking.load_record(path)
        assert reloaded.title == "pushed on save"
        assert calls == [], "save_record's cache push must make this a hit, not a re-parse"

    def test_reloaded_record_can_be_resaved_without_an_explicit_path(
        self, tmp_path: Path,
    ):
        # Regression for the _loaded_from staleness this cache-push
        # introduced: a record handed back from a cache HIT must resolve
        # its own yaml_path correctly, so a caller that mutates it and
        # calls tracking.save_record(record) with no explicit path (very
        # common throughout this codebase) writes back to the SAME file,
        # not somewhere else.
        path = tmp_path / "wt-A.yaml"
        create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        reloaded = tracking.load_record(path)
        assert reloaded.yaml_path == path
        reloaded.title = "resaved with implicit path"
        tracking.save_record(reloaded)
        assert tracking.load_record(path).title == "resaved with implicit path"

    def test_a_direct_unlocked_save_caller_also_pushes_the_cache(
        self, tmp_path: Path, monkeypatch,
    ):
        # Several real callers (the execution-leg CLI, tracking_lifecycle's
        # create_new_record_if_absent) already hold their OWN record lock
        # and call tracking._save_record_unlocked directly, bypassing
        # save_record entirely, to avoid re-acquiring a lock they already
        # hold. The cache push must live in _save_record_unlocked itself
        # (the true universal write chokepoint) to reach these callers too
        # -- moving store() back into save_record alone would pass every
        # other test here while silently reintroducing that gap.
        path = tmp_path / "wt-A.yaml"
        record = create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )
        record.title = "pushed by a direct _save_record_unlocked caller"
        with tracking._RecordLock(path, require_sidecar=True):
            tracking._save_record_unlocked(record, path)

        calls = []
        real_uncached = tracking._load_record_uncached

        def _spy(p: Path) -> tracking.WorktreeRecord:
            calls.append(p)
            return real_uncached(p)

        monkeypatch.setattr(tracking, "_load_record_uncached", _spy)
        reloaded = tracking.load_record(path)
        assert reloaded.title == "pushed by a direct _save_record_unlocked caller"
        assert calls == [], (
            "a direct _save_record_unlocked caller's cache push must make this "
            "a hit, not a re-parse"
        )
