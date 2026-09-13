"""Tests for `_classify_records`' resident-daemon fast path and
`_classify_daemon_compute` (plugin-process-hygiene Phase 4d, #2323).

These exercise the actual production wiring -- a real `CoalescingServer`
running the real `_classify_daemon_compute`, reached through a real lock
file `_classify_records` reads -- but entirely against isolated, temporary
tracking/config directories. No test here ever starts `cmd_status_monitor`
or touches this host's own real resident status monitor, per this effort's
own validation-bar requirement.
"""

from __future__ import annotations

import threading
import types

import pytest

from agent_worktrees import classify_daemon, git_ops, locks as _locks, tracking


def _rec(path, *, worktree_id="wt1", status="active"):
    return tracking.WorktreeRecord(
        worktree_id=worktree_id, branch="worktree/" + worktree_id,
        worktree_path=str(path), repo="ext", machine="m", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status=status, completed_at=None,
        sessions=None, prs=[], git_state=None, session_turns=None,
    )


def _make_worktree(tmp_path, worktree_id, *, with_git=True):
    wt_path = tmp_path / worktree_id
    wt_path.mkdir(parents=True)
    if with_git:
        (wt_path / ".git").mkdir()
    return wt_path


def _wire_common_classify_internals(monkeypatch, m, *, state=git_ops.WorktreeState.DIRTY):
    """Stub the actual git-inspection layer so these tests exercise wiring,
    not real git subprocess calls (mirrors test_classify_lease.py's own
    convention)."""
    monkeypatch.setattr(m, "_build_active_paths", lambda *a, **k: set())
    monkeypatch.setattr(
        m.git_ops, "classify_worktree",
        lambda *a, **k: git_ops.WorktreeStateInfo(state=state),
    )
    monkeypatch.setattr(m, "_apply_tracking_override", lambda r, i: i)


def _fake_load_config(*, path=None, project=None, **_kw):
    return types.SimpleNamespace(
        default_repo=types.SimpleNamespace(remote="origin", default_branch="master"),
    )


class TestClassifyDaemonComputeIsolated:
    """`_classify_daemon_compute` resolves everything itself from `payload` --
    it must never depend on this process's own active-project global."""

    def test_resolves_named_project_records_and_classifies(self, monkeypatch, tmp_path):
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = _make_worktree(tmp_path, "wt1")
        tracking.save_record(_rec(wt_path, worktree_id="wt1"), tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        monkeypatch.setattr(m.cfg, "load_config", _fake_load_config)
        _wire_common_classify_internals(monkeypatch, m)

        raw = m._classify_daemon_compute(
            "classify",
            {"project": project, "status_filter": None, "platform_filter": None, "all": False},
        )

        assert set(raw) == {"wt1"}
        assert raw["wt1"]["state"] == git_ops.WorktreeState.DIRTY.value

    def test_excludes_worktrees_without_a_git_dir_unless_all(self, monkeypatch, tmp_path):
        """Mirrors `_list_records_for_args`' own existing-worktree filter --
        a daemon answer must match what the caller would have resolved
        itself."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        gone_path = tmp_path / "gone-wt"  # never created on disk
        tracking.save_record(
            _rec(gone_path, worktree_id="gone"), tracking_dir / "gone.yaml"
        )
        live_path = _make_worktree(tmp_path, "wt-live")
        tracking.save_record(
            _rec(live_path, worktree_id="wt-live"), tracking_dir / "wt-live.yaml"
        )

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        monkeypatch.setattr(m.cfg, "load_config", _fake_load_config)
        _wire_common_classify_internals(monkeypatch, m)

        raw = m._classify_daemon_compute(
            "classify",
            {"project": project, "status_filter": None, "platform_filter": None, "all": False},
        )
        assert set(raw) == {"wt-live"}

        raw_all = m._classify_daemon_compute(
            "classify",
            {"project": project, "status_filter": None, "platform_filter": None, "all": True},
        )
        assert set(raw_all) == {"gone", "wt-live"}

    def test_missing_project_raises_rather_than_guessing(self):
        from agent_worktrees import __main__ as m

        with pytest.raises(ValueError):
            m._classify_daemon_compute("classify", {"project": ""})

    def test_two_projects_resolved_concurrently_never_cross_contaminate(
        self, monkeypatch, tmp_path
    ):
        """Two concurrent compute calls for two different projects must
        never race on any shared/global project state -- everything is
        threaded through explicit `project`/`path` params."""
        from agent_worktrees import __main__ as m

        projects = {}
        for name, state in (("proj-a", git_ops.WorktreeState.DIRTY),
                             ("proj-b", git_ops.WorktreeState.WIP)):
            tracking_dir = tmp_path / name / "worktrees"
            tracking_dir.mkdir(parents=True)
            wt_path = _make_worktree(tmp_path, f"{name}-wt")
            tracking.save_record(
                _rec(wt_path, worktree_id=f"{name}-wt"), tracking_dir / f"{name}-wt.yaml"
            )
            projects[name] = state

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / name)
        monkeypatch.setattr(m.cfg, "load_config", _fake_load_config)
        monkeypatch.setattr(m, "_build_active_paths", lambda *a, **k: set())
        monkeypatch.setattr(m, "_apply_tracking_override", lambda r, i: i)

        def _classify_worktree(path, branch, **kwargs):
            # Return a state derived from the *actual path passed in* -- if
            # any cross-contamination occurred, this would answer for the
            # wrong project's expected state.
            name = next(n for n in projects if f"{n}-wt" in str(path))
            return git_ops.WorktreeStateInfo(state=projects[name])

        monkeypatch.setattr(m.git_ops, "classify_worktree", _classify_worktree)

        results: dict[str, dict] = {}
        barrier = threading.Barrier(2)

        def _run(name):
            barrier.wait(timeout=5)
            results[name] = m._classify_daemon_compute(
                "classify",
                {"project": name, "status_filter": None, "platform_filter": None, "all": False},
            )

        threads = [threading.Thread(target=_run, args=(n,)) for n in projects]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert results["proj-a"]["proj-a-wt"]["state"] == git_ops.WorktreeState.DIRTY.value
        assert results["proj-b"]["proj-b-wt"]["state"] == git_ops.WorktreeState.WIP.value


class TestClassifyRecordsDaemonFastPath:
    """`_classify_records`'s own daemon-first dispatch, end-to-end against a
    real (isolated) `CoalescingServer` running the real compute callback."""

    def test_reaches_a_live_resident_daemon_and_never_falls_back(
        self, monkeypatch, tmp_path
    ):
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = _make_worktree(tmp_path, "wt1")
        rec = _rec(wt_path, worktree_id="wt1")
        tracking.save_record(rec, tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_name", lambda: project)
        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        monkeypatch.setattr(m.cfg, "load_config", _fake_load_config)
        monkeypatch.setattr(m, "_ensure_status_monitor", lambda: False)
        _wire_common_classify_internals(monkeypatch, m)

        def _must_not_fall_back(*a, **k):
            raise AssertionError("resident daemon was reachable; must not fall back")

        monkeypatch.setattr(m, "_classify_records_lease_guarded", _must_not_fall_back)

        server = classify_daemon.start_server(m._classify_daemon_compute)
        server.start()
        try:
            lock_path = tmp_path / "status-monitor.lock"
            monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock_path)
            _locks.write_lock(lock_path, extra=classify_daemon.rendezvous_fields(server))

            out = m._classify_records(
                [rec], None,
                daemon_filters={
                    "status_filter": None, "platform_filter": None, "all": False,
                },
            )
        finally:
            server.close()

        assert out["wt1"].state == git_ops.WorktreeState.DIRTY

    def test_falls_back_cleanly_when_no_resident_daemon_is_running(
        self, monkeypatch, tmp_path
    ):
        """No lock file at all (the common case: no resident monitor running
        on this host) must degrade to the exact pre-#2323 lease-guarded
        answer -- never raise, never hang."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        monkeypatch.setattr(m.cfg, "project_name", lambda: project)
        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path)
        monkeypatch.setattr(m, "_monitor_lock_path", lambda: tmp_path / "no-such-monitor.lock")
        # No boot function -- `classify_with_boot` must skip the boot-wait
        # poll loop entirely (never spin for `BOOT_WAIT_S`) when there is
        # nothing to boot, and go straight to fallback.
        monkeypatch.setattr(m, "_ensure_status_monitor", None)
        _wire_common_classify_internals(monkeypatch, m, state=git_ops.WorktreeState.WIP)
        monkeypatch.setattr(m.cfg, "load_config", lambda: _fake_load_config())

        wt_path = _make_worktree(tmp_path, "wt1")
        rec = _rec(wt_path, worktree_id="wt1")

        out = m._classify_records(
            [rec], None,
            daemon_filters={"status_filter": None, "platform_filter": None, "all": False},
        )

        assert out["wt1"].state == git_ops.WorktreeState.WIP

    def test_boots_a_monitor_and_waits_when_none_is_currently_publishing(
        self, monkeypatch, tmp_path
    ):
        """No lock file exists *yet*, but a monitor starts (and publishes its
        classify rendezvous) partway through the boot-wait window -- the
        production caller must actually invoke `_ensure_status_monitor` and
        keep polling, not just take one dial attempt and give up."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = _make_worktree(tmp_path, "wt1")
        rec = _rec(wt_path, worktree_id="wt1")
        tracking.save_record(rec, tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_name", lambda: project)
        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        monkeypatch.setattr(m.cfg, "load_config", _fake_load_config)
        _wire_common_classify_internals(monkeypatch, m)

        def _must_not_fall_back(*a, **k):
            raise AssertionError("the booted daemon answered; must not fall back")

        monkeypatch.setattr(m, "_classify_records_lease_guarded", _must_not_fall_back)

        lock_path = tmp_path / "status-monitor.lock"
        monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock_path)

        server = classify_daemon.start_server(m._classify_daemon_compute)
        server.start()
        boot_calls = []

        def _boot():
            boot_calls.append(1)
            # Simulate the monitor finishing its own startup shortly after
            # being asked to boot -- publish its rendezvous now.
            _locks.write_lock(lock_path, extra=classify_daemon.rendezvous_fields(server))
            return True

        monkeypatch.setattr(m, "_ensure_status_monitor", _boot)
        try:
            out = m._classify_records(
                [rec], None,
                daemon_filters={
                    "status_filter": None, "platform_filter": None, "all": False,
                },
            )
        finally:
            server.close()

        assert boot_calls == [1]
        assert out["wt1"].state == git_ops.WorktreeState.DIRTY

    def test_id_set_mismatch_from_daemon_falls_back_rather_than_returns_partial(
        self, monkeypatch, tmp_path
    ):
        """A daemon that answers with the wrong worktree-id set (a stale or
        differently-scoped compute) must never be trusted as a partial
        answer -- it is exactly as untrustworthy as no answer at all."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        monkeypatch.setattr(m.cfg, "project_name", lambda: project)
        monkeypatch.setattr(m, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
        monkeypatch.setattr(m.cfg, "load_config", lambda: _fake_load_config())
        _wire_common_classify_internals(monkeypatch, m, state=git_ops.WorktreeState.WIP)

        wt_path = _make_worktree(tmp_path, "wt1")
        rec = _rec(wt_path, worktree_id="wt1")

        # A live daemon that answers, but with a completely different
        # worktree-id set than the caller asked about.
        server = classify_daemon.start_server(
            lambda kind, payload: {"some-other-wt": {"state": "dirty"}}
        )
        server.start()
        try:
            lock_path = tmp_path / "status-monitor.lock"
            _locks.write_lock(lock_path, extra=classify_daemon.rendezvous_fields(server))

            out = m._classify_records(
                [rec], None,
                daemon_filters={
                    "status_filter": None, "platform_filter": None, "all": False,
                },
            )
        finally:
            server.close()

        # Fell back to the real lease-guarded classification (wired to
        # return WIP above), not the bogus daemon payload.
        assert set(out) == {"wt1"}
        assert out["wt1"].state == git_ops.WorktreeState.WIP

    def test_malformed_daemon_entry_falls_back_rather_than_crashes(
        self, monkeypatch, tmp_path
    ):
        """A malformed per-entry shape (e.g. a non-dict value) must degrade
        to the lease-guarded fallback, never raise past `_classify_records`."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        monkeypatch.setattr(m.cfg, "project_name", lambda: project)
        monkeypatch.setattr(m, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
        monkeypatch.setattr(m.cfg, "load_config", lambda: _fake_load_config())
        _wire_common_classify_internals(monkeypatch, m, state=git_ops.WorktreeState.WIP)

        wt_path = _make_worktree(tmp_path, "wt1")
        rec = _rec(wt_path, worktree_id="wt1")

        server = classify_daemon.start_server(lambda kind, payload: {"wt1": "not-a-dict"})
        server.start()
        try:
            lock_path = tmp_path / "status-monitor.lock"
            _locks.write_lock(lock_path, extra=classify_daemon.rendezvous_fields(server))

            out = m._classify_records(
                [rec], None,
                daemon_filters={
                    "status_filter": None, "platform_filter": None, "all": False,
                },
            )
        finally:
            server.close()

        assert out["wt1"].state == git_ops.WorktreeState.WIP

    def test_daemon_filters_none_skips_daemon_entirely(self, monkeypatch, tmp_path):
        """Every caller that doesn't opt in (passes no `daemon_filters`) must
        see byte-identical behavior to before #2323 -- the daemon codepath
        must not even attempt to resolve a project."""
        from agent_worktrees import __main__ as m

        def _boom():
            raise AssertionError("daemon path must not run when daemon_filters is None")

        monkeypatch.setattr(m.cfg, "project_name", _boom)
        monkeypatch.setattr(m.cfg, "project_dir", lambda: tmp_path)
        monkeypatch.setattr(m.cfg, "load_config", lambda: _fake_load_config())
        _wire_common_classify_internals(monkeypatch, m, state=git_ops.WorktreeState.DIRTY)

        rec = _rec(tmp_path / "wt-plain", worktree_id="wt-plain")
        (tmp_path / "wt-plain").mkdir()

        out = m._classify_records([rec])

        assert out["wt-plain"].state == git_ops.WorktreeState.DIRTY
