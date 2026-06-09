"""Tests for agent_worktrees.tracking — YAML CRUD and session registry."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees.tracking import (
    SessionEntry,
    WorktreeRecord,
    _atomic_write,
    create_new_record,
    deregister_session,
    list_records,
    load_record,
    mark_resumed,
    register_session,
    save_record,
    update_status,
)

# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    """Verify YAML serialization round-trips correctly."""

    def _make_record(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt-001",
            branch="worktree/wt-001",
            worktree_path="/tmp/wt",
            repo="test-repo",
            machine="test-machine",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_basic_round_trip(self, tmp_path: Path):
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.worktree_id == rec.worktree_id
        assert loaded.branch == rec.branch
        assert loaded.worktree_path == rec.worktree_path
        assert loaded.repo == rec.repo
        assert loaded.status == rec.status
        assert loaded.resume_count == 0

    def test_title_with_special_chars(self, tmp_path: Path):
        rec = self._make_record(title="Fix: handle edge case #42 & more")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.title == "Fix: handle edge case #42 & more"

    def test_null_title(self, tmp_path: Path):
        rec = self._make_record(title=None)
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.title is None

    def test_completed_at(self, tmp_path: Path):
        rec = self._make_record(
            status="complete",
            completed_at="2026-06-01T12:00:00",
        )
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.completed_at == "2026-06-01T12:00:00"

    def test_handoff_prompt(self, tmp_path: Path):
        rec = self._make_record(handoff_prompt="/tmp/handoff.md")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.handoff_prompt == "/tmp/handoff.md"


# ---------------------------------------------------------------------------
# Session registry — three-state semantics
# ---------------------------------------------------------------------------

class TestSessionsField:
    """Verify None vs [] vs populated sessions semantics."""

    def _make_record(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt-sess",
            branch="worktree/wt-sess",
            worktree_path="/tmp/wt-sess",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_sessions_none_means_not_indexed(self, tmp_path: Path):
        """sessions=None (pre-registry) — YAML has no sessions key."""
        rec = self._make_record(sessions=None)
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        content = path.read_text()
        assert "sessions:" not in content

        loaded = load_record(path)
        assert loaded.sessions is None

    def test_sessions_empty_means_indexed(self, tmp_path: Path):
        """sessions=[] (indexed, no sessions) — YAML has sessions: []."""
        rec = self._make_record(sessions=[])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        content = path.read_text()
        assert "sessions: []" in content

        loaded = load_record(path)
        assert loaded.sessions == []
        assert loaded.sessions is not None

    def test_sessions_populated(self, tmp_path: Path):
        """sessions=[...] with entries."""
        entries = [
            SessionEntry(
                session_id="aaa-111",
                started_at="2026-06-01T10:00:00",
                pid=1234,
            ),
            SessionEntry(
                session_id="bbb-222",
                started_at="2026-06-01T11:00:00",
                ended_at="2026-06-01T11:30:00",
            ),
        ]
        rec = self._make_record(sessions=entries)
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        loaded = load_record(path)
        assert len(loaded.sessions) == 2
        assert loaded.sessions[0].session_id == "aaa-111"
        assert loaded.sessions[0].pid == 1234
        assert loaded.sessions[0].ended_at is None
        assert loaded.sessions[1].session_id == "bbb-222"
        assert loaded.sessions[1].ended_at == "2026-06-01T11:30:00"

    def test_session_entry_no_optional_fields(self, tmp_path: Path):
        """SessionEntry with only required fields."""
        rec = self._make_record(sessions=[
            SessionEntry(session_id="ccc-333", started_at="2026-06-01T12:00:00"),
        ])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        loaded = load_record(path)
        assert loaded.sessions[0].pid is None
        assert loaded.sessions[0].ended_at is None

    def test_backward_compat_no_sessions_key(self, tmp_path: Path):
        """Loading a YAML written before session registry (no sessions key)."""
        content = """\
worktree_id: old-wt
branch: worktree/old-wt
worktree_path: /tmp/old
repo: test
machine: test
platform: wsl
started_at: 2026-01-01T00:00:00
last_resumed_at: 2026-01-01T00:00:00
resume_count: 3
title: Old worktree
status: active
completed_at: null
"""
        path = tmp_path / "old.yaml"
        path.write_text(content)
        loaded = load_record(path)
        assert loaded.sessions is None
        assert loaded.worktree_id == "old-wt"
        assert loaded.resume_count == 3


# ---------------------------------------------------------------------------
# register_session / deregister_session
# ---------------------------------------------------------------------------

class TestSessionRegistration:
    """Test hook-invoked session registration."""

    def test_register_new_session(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = WorktreeRecord(
            worktree_id="reg-wt",
            branch="worktree/reg-wt",
            worktree_path="/tmp/reg",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=[],
        )
        save_record(rec, tmp_tracking_dir / "reg-wt.yaml")

        register_session("reg-wt", "session-aaa", pid=999)

        loaded = load_record(tmp_tracking_dir / "reg-wt.yaml")
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].session_id == "session-aaa"
        assert loaded.sessions[0].pid == 999

    def test_register_dedupes(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = WorktreeRecord(
            worktree_id="dup-wt",
            branch="worktree/dup-wt",
            worktree_path="/tmp/dup",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=[SessionEntry("existing", "2026-06-01T09:00:00", pid=100)],
        )
        save_record(rec, tmp_tracking_dir / "dup-wt.yaml")

        register_session("dup-wt", "existing", pid=200)

        loaded = load_record(tmp_tracking_dir / "dup-wt.yaml")
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].pid == 200  # updated, not duplicated

    def test_register_initializes_none_sessions(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Registering on a pre-registry record initializes the list."""
        rec = WorktreeRecord(
            worktree_id="pre-reg",
            branch="worktree/pre-reg",
            worktree_path="/tmp/pre",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=None,
        )
        save_record(rec, tmp_tracking_dir / "pre-reg.yaml")

        register_session("pre-reg", "first-session")

        loaded = load_record(tmp_tracking_dir / "pre-reg.yaml")
        assert loaded.sessions is not None
        assert len(loaded.sessions) == 1

    def test_deregister_stamps_ended_at(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = WorktreeRecord(
            worktree_id="end-wt",
            branch="worktree/end-wt",
            worktree_path="/tmp/end",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=[SessionEntry("sess-end", "2026-06-01T10:00:00")],
        )
        save_record(rec, tmp_tracking_dir / "end-wt.yaml")

        deregister_session("end-wt", "sess-end")

        loaded = load_record(tmp_tracking_dir / "end-wt.yaml")
        assert loaded.sessions[0].ended_at is not None

    def test_register_nonexistent_worktree(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Registering against a missing worktree is a no-op."""
        register_session("nonexistent", "some-session")
        # Should not raise

    def test_deregister_nonexistent_worktree(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Deregistering against a missing worktree is a no-op."""
        deregister_session("nonexistent", "some-session")
        # Should not raise

    def test_deregister_unknown_session(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Deregistering a session ID that doesn't exist is a no-op."""
        rec = WorktreeRecord(
            worktree_id="noop-wt",
            branch="worktree/noop-wt",
            worktree_path="/tmp/noop",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=[SessionEntry("other-sess", "2026-06-01T10:00:00")],
        )
        save_record(rec, tmp_tracking_dir / "noop-wt.yaml")

        deregister_session("noop-wt", "nonexistent-session")

        loaded = load_record(tmp_tracking_dir / "noop-wt.yaml")
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].ended_at is None


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------

class TestListRecords:
    """Test record listing and filtering."""

    def _save_records(self, tracking_dir: Path, records: list[WorktreeRecord]):
        for rec in records:
            save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")

    def _make(self, wt_id: str, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=f"/tmp/{wt_id}",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=[],
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_list_all(self, tmp_tracking_dir: Path):
        self._save_records(tmp_tracking_dir, [
            self._make("a"),
            self._make("b"),
            self._make("c"),
        ])
        records = list_records(tmp_tracking_dir)
        assert len(records) == 3

    def test_filter_by_status(self, tmp_tracking_dir: Path):
        self._save_records(tmp_tracking_dir, [
            self._make("active-1", status="active"),
            self._make("done-1", status="complete"),
            self._make("active-2", status="active"),
        ])
        active = list_records(tmp_tracking_dir, status_filter="active")
        assert len(active) == 2

    def test_filter_by_platform(self, tmp_tracking_dir: Path):
        self._save_records(tmp_tracking_dir, [
            self._make("wsl-1", platform="wsl"),
            self._make("win-1", platform="windows"),
        ])
        wsl = list_records(tmp_tracking_dir, platform_filter="wsl")
        assert len(wsl) == 1
        assert wsl[0].worktree_id == "wsl-1"

    def test_empty_dir(self, tmp_tracking_dir: Path):
        records = list_records(tmp_tracking_dir)
        assert records == []

    def test_nonexistent_dir(self, tmp_path: Path):
        records = list_records(tmp_path / "nonexistent")
        assert records == []


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    """Test update_status and mark_resumed."""

    def _make_and_save(
        self, tmp_tracking_dir: Path, monkeypatch_config, **overrides
    ) -> WorktreeRecord:
        defaults = dict(
            worktree_id="status-wt",
            branch="worktree/status-wt",
            worktree_path="/tmp/status",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=[],
        )
        defaults.update(overrides)
        rec = WorktreeRecord(**defaults)
        save_record(rec, tmp_tracking_dir / f"{rec.worktree_id}.yaml")
        return rec

    def test_update_to_complete(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        update_status(rec, "complete")
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.status == "complete"
        assert loaded.completed_at is not None

    def test_update_to_finalized(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        update_status(rec, "finalized")
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.status == "finalized"
        assert loaded.completed_at is not None

    def test_mark_resumed_increments(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        mark_resumed(rec)
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.resume_count == 1
        assert loaded.last_resumed_at != "2026-06-01T10:00:00"

    def test_mark_resumed_twice(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        mark_resumed(rec)
        mark_resumed(rec)
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.resume_count == 2


# ---------------------------------------------------------------------------
# create_new_record
# ---------------------------------------------------------------------------

class TestCreateNewRecord:
    """Test new record creation."""

    def test_creates_with_defaults(self, tmp_tracking_dir: Path):
        rec = create_new_record(
            worktree_id="new-001",
            branch="worktree/new-001",
            worktree_path="/tmp/new",
            repo="test-repo",
            machine="test",
            platform_name="wsl",
            tracking_path=tmp_tracking_dir,
        )
        assert rec.worktree_id == "new-001"
        assert rec.status == "active"
        assert rec.sessions == []  # indexed from creation
        assert rec.resume_count == 0
        assert rec.completed_at is None

        # Verify it was persisted
        loaded = load_record(tmp_tracking_dir / "new-001.yaml")
        assert loaded.sessions == []


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    """Test _atomic_write safety."""

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "file.yaml"
        _atomic_write(target, "content")
        assert target.read_text() == "content"

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "file.yaml"
        target.write_text("old")
        _atomic_write(target, "new")
        assert target.read_text() == "new"
