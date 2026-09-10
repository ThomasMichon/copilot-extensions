"""Tests for the per-project single-instance lease around ``_classify_records``
(plugin-process-hygiene Phase 4c, #2315) -- a losing caller must wait for the
winner then answer from the repaired cache, never race it or invent a worse
answer than what was already cached."""

from __future__ import annotations

import types

from single_instance_lease import SingleInstance

from agent_worktrees import git_ops, tracking


def _rec(path, *, worktree_id="wt-lease", status="active", git_state=None,
         session_turns=None):
    return tracking.WorktreeRecord(
        worktree_id=worktree_id, branch="worktree/" + worktree_id,
        worktree_path=str(path), repo="ext", machine="m", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status=status, completed_at=None,
        sessions=None, prs=[], git_state=git_state, session_turns=session_turns,
    )


def _write(rec, tracking_dir):
    """Persist ``rec`` as a real, fully-valid tracking YAML via the actual
    save path -- never a hand-written partial YAML, which ``load_record``
    can reject for missing fields it doesn't default (masking test bugs as
    the exception-fallback path rather than a genuine disk read)."""
    tracking.save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")


class TestClassifyRecordsLeaseWiring:
    """The normal (uncontended) path is unchanged: acquire, classify live,
    release -- and the lease file actually exists afterward, proving the
    real lease code path ran rather than being skipped."""

    def _wire_live(self, monkeypatch, *, raw_state=git_ops.WorktreeState.DIRTY):
        from agent_worktrees import __main__ as m

        monkeypatch.setattr(
            m.cfg, "load_config",
            lambda: types.SimpleNamespace(
                default_repo=types.SimpleNamespace(
                    remote="origin", default_branch="master",
                ),
            ),
        )
        monkeypatch.setattr(m, "_build_active_paths", lambda *a, **k: set())
        monkeypatch.setattr(
            m.git_ops, "classify_worktree",
            lambda *a, **k: git_ops.WorktreeStateInfo(state=raw_state),
        )
        monkeypatch.setattr(m, "_apply_tracking_override", lambda r, i: i)
        return m

    def test_uncontended_call_classifies_live_and_releases(
        self, monkeypatch, tmp_path
    ):
        m = self._wire_live(monkeypatch)
        monkeypatch.setattr(m.cfg, "project_dir", lambda: tmp_path)
        rec = _rec(tmp_path)

        out = m._classify_records([rec])

        assert out["wt-lease"].state == git_ops.WorktreeState.DIRTY
        # The lease file exists (proves the real code path ran) and is not
        # left held (proves release() ran, so a next caller can acquire it).
        lock_path = tmp_path / "classify.lock"
        assert lock_path.exists()
        SingleInstance(tmp_path, service="classify").acquire()  # must not raise

    def test_lease_layer_failure_degrades_to_live_classify(
        self, monkeypatch, tmp_path
    ):
        """An unexpected OSError acquiring the lease (not contention) must
        never block correctness -- classify live exactly as before this
        guard existed."""
        m = self._wire_live(monkeypatch)
        monkeypatch.setattr(m.cfg, "project_dir", lambda: tmp_path)

        class _BoomLease:
            def acquire(self):
                raise OSError("disk full")

            def release(self):
                pass

        monkeypatch.setattr(
            "single_instance_lease.SingleInstance", lambda *a, **k: _BoomLease()
        )
        rec = _rec(tmp_path)

        out = m._classify_records([rec])
        assert out["wt-lease"].state == git_ops.WorktreeState.DIRTY

    def test_no_resolvable_project_dir_degrades_to_live_classify(
        self, monkeypatch, tmp_path
    ):
        """cfg.project_dir() raising (no active project resolved) must not
        block correctness either."""
        m = self._wire_live(monkeypatch)

        def _boom():
            raise RuntimeError("no active project")

        monkeypatch.setattr(m.cfg, "project_dir", _boom)
        rec = _rec(tmp_path)

        out = m._classify_records([rec])
        assert out["wt-lease"].state == git_ops.WorktreeState.DIRTY


class TestClassifyRecordsLeaseContention:
    """A losing caller must never run its own live classification, and must
    never invent a state the record hasn't actually observed."""

    def test_losing_caller_never_classifies_live(self, monkeypatch, tmp_path):
        from agent_worktrees import __main__ as m

        m.cfg.set_active_project("test-project")
        monkeypatch.setattr(m.cfg, "project_dir", lambda: tmp_path)
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        # Simulate a winner already holding the lease.
        winner = SingleInstance(tmp_path, service="classify")
        winner.acquire()
        try:
            live_called = []
            monkeypatch.setattr(
                m, "_classify_records_live",
                lambda *a, **k: live_called.append(1) or {}
            )
            # Make the wait loop resolve immediately rather than really
            # sleeping/polling for the full timeout.
            monkeypatch.setattr(
                m, "_classify_records_wait_then_cache",
                lambda records, ctx, lease: m._classify_from_cache(records, ctx),
            )
            rec = _rec(tmp_path, git_state="convo")
            _write(rec, tmp_path)

            out = m._classify_records([rec])

            assert not live_called
            assert out["wt-lease"].state == git_ops.WorktreeState.CONVO
        finally:
            winner.release()

    def test_wait_then_cache_polls_until_winner_releases(self, monkeypatch, tmp_path):
        from agent_worktrees import __main__ as m

        m.cfg.set_active_project("test-project")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        winner = SingleInstance(tmp_path, service="classify")
        winner.acquire()
        rec = _rec(tmp_path, git_state="dirty")
        _write(rec, tmp_path)

        # Release the "winner" lease from a short-lived timer so the poll
        # loop observes contention at least once, then succeeds.
        import threading

        timer = threading.Timer(0.05, winner.release)
        timer.start()
        try:
            loser_lease = SingleInstance(tmp_path, service="classify")
            out = m._classify_records_wait_then_cache(
                [rec], None, loser_lease, timeout=5.0, poll=0.02,
            )
        finally:
            timer.cancel()
            winner.release()

        assert out["wt-lease"].state == git_ops.WorktreeState.DIRTY

    def test_wait_then_cache_times_out_and_still_answers_from_cache(
        self, monkeypatch, tmp_path
    ):
        """A winner that never releases within the budget must not wedge the
        loser forever -- it degrades to whatever is currently cached."""
        from agent_worktrees import __main__ as m

        m.cfg.set_active_project("test-project")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        winner = SingleInstance(tmp_path, service="classify")
        winner.acquire()
        try:
            rec = _rec(tmp_path, git_state="wip")
            _write(rec, tmp_path)
            loser_lease = SingleInstance(tmp_path, service="classify")

            out = m._classify_records_wait_then_cache(
                [rec], None, loser_lease, timeout=0.1, poll=0.02,
            )
            assert out["wt-lease"].state == git_ops.WorktreeState.WIP
        finally:
            winner.release()


class TestClassifyFromCache:
    """The cache-derived answer never invents a state; it degrades honestly."""

    def test_reads_fresh_git_state_off_disk(self, monkeypatch, tmp_path):
        from agent_worktrees import __main__ as m

        m.cfg.set_active_project("test-project")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        stale_in_memory = _rec(tmp_path, git_state="dirty")
        fresh_on_disk = _rec(tmp_path, git_state="convo")
        _write(fresh_on_disk, tmp_path)  # disk says convo -- must win over
                                         # the in-memory rec's stale "dirty"

        out = m._classify_from_cache([stale_in_memory])
        assert out["wt-lease"].state == git_ops.WorktreeState.CONVO

    def test_unclassified_active_record_is_unknown_not_wip(
        self, monkeypatch, tmp_path
    ):
        """The exact bug this whole fix chases: an absent/never-classified
        state must render as UNKNOWN, never a guessed WIP."""
        from agent_worktrees import __main__ as m

        m.cfg.set_active_project("test-project")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        rec = _rec(tmp_path, status="active", git_state=None)
        _write(rec, tmp_path)

        out = m._classify_from_cache([rec])
        assert out["wt-lease"].state == git_ops.WorktreeState.UNKNOWN

    def test_finalized_without_cached_state_is_completed(
        self, monkeypatch, tmp_path
    ):
        from agent_worktrees import __main__ as m

        m.cfg.set_active_project("test-project")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        rec = _rec(tmp_path, status="finalized", git_state=None)
        _write(rec, tmp_path)

        out = m._classify_from_cache([rec])
        assert out["wt-lease"].state == git_ops.WorktreeState.COMPLETED

    def test_session_ctx_refines_unused_with_turns_to_convo(
        self, monkeypatch, tmp_path
    ):
        from agent_worktrees import __main__ as m
        from agent_worktrees import sessions

        m.cfg.set_active_project("test-project")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        rec = _rec(tmp_path, git_state="unused", session_turns=3)
        _write(rec, tmp_path)

        out = m._classify_from_cache([rec], sessions.SessionContext())
        assert out["wt-lease"].state == git_ops.WorktreeState.CONVO

    def test_unreadable_record_falls_back_to_in_memory_copy(
        self, monkeypatch, tmp_path
    ):
        """A disk read hiccup degrades to the in-memory record rather than
        crashing the whole classify pass for every other row."""
        from agent_worktrees import __main__ as m

        rec = _rec(tmp_path, git_state="dirty")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        # No YAML file on disk at all -> load_record raises -> use `rec`.

        out = m._classify_from_cache([rec])
        assert out["wt-lease"].state == git_ops.WorktreeState.DIRTY
