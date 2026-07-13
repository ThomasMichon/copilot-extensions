"""Tests for agent_worktrees.tracking — YAML CRUD and session registry."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees.tracking import (
    SessionEntry,
    WorktreeRecord,
    _atomic_write,
    create_new_record,
    deregister_session,
    find_worktree_id_by_cwd,
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

    def test_parent_session_round_trip(self, tmp_path: Path):
        # #1029: the originating-session pointer survives a save/load cycle.
        rec = self._make_record(parent_session="63903896")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "parent_session: 63903896" in path.read_text()
        loaded = load_record(path)
        assert loaded.parent_session == "63903896"

    def test_parent_session_absent_omitted(self, tmp_path: Path):
        # No pointer -> the key is omitted so common-case YAML stays lean.
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "parent_session" not in path.read_text()
        loaded = load_record(path)
        assert loaded.parent_session is None

    def test_caller_worktree_round_trip(self, tmp_path: Path):
        # #2178: the bridge caller-worktree pointer survives save/load and is
        # omitted when unset.
        rec = self._make_record(caller_worktree="lambda-core-win-20260101-abcd")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "caller_worktree: lambda-core-win-20260101-abcd" in path.read_text()
        assert load_record(path).caller_worktree == "lambda-core-win-20260101-abcd"
        rec2 = self._make_record()
        path2 = tmp_path / "wt2.yaml"
        save_record(rec2, path2)
        assert "caller_worktree" not in path2.read_text()
        assert load_record(path2).caller_worktree is None

    def test_pr_absent_round_trips_as_none(self, tmp_path: Path):
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "pr:" not in path.read_text()
        loaded = load_record(path)
        assert loaded.pr is None

    def test_pr_record_round_trip(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(
            prs=[PRRecord(
                state="open",
                branch="feature/fix-auth-abc123",
                base_sha="abc123",
                head_sha="def456",
                url="https://example/pulls/42",
                number=42,
                provider="gitea",
            )]
        )
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.pr is not None
        assert loaded.pr.state == "open"
        assert loaded.pr.branch == "feature/fix-auth-abc123"
        assert loaded.pr.base_sha == "abc123"
        assert loaded.pr.head_sha == "def456"
        assert loaded.pr.url == "https://example/pulls/42"
        assert loaded.pr.number == 42
        assert loaded.pr.provider == "gitea"

    def test_pr_record_number_optional(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[PRRecord(state="creating", branch="feature/x")])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.pr is not None
        assert loaded.pr.state == "creating"
        assert loaded.pr.number is None

    # --- multi-PR schema (#1107) --------------------------------------------

    def test_legacy_pr_block_loads_as_one_element_list(self, tmp_path: Path):
        # A record written by an older tool (single `pr:` block, no `prs:`)
        # must load as a one-element prs list, with repo defaulted to the
        # worktree repo.
        path = tmp_path / "legacy.yaml"
        path.write_text(
            "worktree_id: wt-001\n"
            "branch: worktree/wt-001\n"
            "worktree_path: /tmp/wt\n"
            "repo: owner/thing\n"
            "machine: m\n"
            "platform: wsl\n"
            "started_at: 2026-06-01T10:00:00\n"
            "last_resumed_at: 2026-06-01T10:00:00\n"
            "resume_count: 0\n"
            "title: null\n"
            "status: active\n"
            "completed_at: null\n"
            "handoff_prompt: null\n"
            "pr:\n"
            "  state: open\n"
            "  branch: feature/legacy-abc\n"
            "  number: 7\n"
            "  provider: gitea\n",
            encoding="utf-8",
        )
        loaded = load_record(path)
        assert len(loaded.prs) == 1
        assert loaded.prs[0].branch == "feature/legacy-abc"
        assert loaded.prs[0].number == 7
        assert loaded.prs[0].repo == "owner/thing"  # defaulted from worktree repo
        assert loaded.pr is loaded.prs[0]

    def test_multi_pr_round_trip(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[
            PRRecord(state="merged", branch="feature/one-abc", number=10,
                     provider="gitea", repo="owner/a",
                     opened_at="2026-06-01T10:00:00",
                     closed_at="2026-06-01T11:00:00"),
            PRRecord(state="open", branch="feature/two-abc", number=11,
                     provider="github", repo="owner/b",
                     opened_at="2026-06-01T12:00:00"),
        ])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert [p.number for p in loaded.prs] == [10, 11]
        assert loaded.prs[0].repo == "owner/a"
        assert loaded.prs[1].provider == "github"
        # active = most recent non-terminal -> the open one (#11)
        assert loaded.pr.number == 11

    def test_active_pr_rule(self):
        from agent_worktrees.tracking import PRRecord

        # No live PR -> most recent overall (last by opened_at).
        rec = self._make_record(prs=[
            PRRecord(state="merged", branch="a", opened_at="2026-06-01T10:00:00"),
            PRRecord(state="closed", branch="b", opened_at="2026-06-01T12:00:00"),
        ])
        assert rec.active_pr().branch == "b"
        # A live PR wins over a more-recent terminal one.
        rec2 = self._make_record(prs=[
            PRRecord(state="open", branch="live", opened_at="2026-06-01T10:00:00"),
            PRRecord(state="merged", branch="done", opened_at="2026-06-01T12:00:00"),
        ])
        assert rec2.active_pr().branch == "live"
        # Empty -> None.
        assert self._make_record(prs=[]).active_pr() is None

    def test_has_live_pr(self):
        from agent_worktrees.tracking import PRRecord
        assert self._make_record(prs=[]).has_live_pr() is False
        assert self._make_record(prs=[
            PRRecord(state="merged", branch="a"),
            PRRecord(state="closed", branch="b"),
        ]).has_live_pr() is False
        assert self._make_record(prs=[
            PRRecord(state="merged", branch="a"),
            PRRecord(state="open", branch="b"),
        ]).has_live_pr() is True

    def test_pr_setter_replaces_active(self):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[PRRecord(state="creating", branch="feature/x")])
        rec.pr = PRRecord(state="open", branch="feature/x", number=5)
        assert len(rec.prs) == 1
        assert rec.prs[0].state == "open"
        assert rec.prs[0].number == 5

    def test_pr_setter_appends_when_empty_and_clears(self):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[])
        rec.pr = PRRecord(state="open", branch="feature/x")
        assert len(rec.prs) == 1
        rec.pr = None
        assert rec.prs == []

    def test_save_mirrors_active_to_legacy_pr_block(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[
            PRRecord(state="merged", branch="a", number=1),
            PRRecord(state="open", branch="b", number=2),
        ])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        text = path.read_text(encoding="utf-8")
        assert "prs:" in text
        # Mirrored legacy pr: block points at the active PR (#2).
        import yaml as _yaml
        data = _yaml.safe_load(text)
        assert data["pr"]["number"] == 2
        assert [p["number"] for p in data["prs"]] == [1, 2]

    def test_zero_pr_emits_neither_block(self, tmp_path: Path):
        rec = self._make_record(prs=[])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        text = path.read_text(encoding="utf-8")
        assert "\npr:" not in text
        assert "prs:" not in text


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

    def test_seeds_parent_session(self, tmp_tracking_dir: Path):
        # #1029: an explicit parent-session pointer is recorded at creation.
        rec = create_new_record(
            worktree_id="new-002",
            branch="worktree/new-002",
            worktree_path="/tmp/new2",
            repo="test-repo",
            machine="test",
            platform_name="wsl",
            tracking_path=tmp_tracking_dir,
            parent_session="deadbeef",
        )
        assert rec.parent_session == "deadbeef"
        loaded = load_record(tmp_tracking_dir / "new-002.yaml")
        assert loaded.parent_session == "deadbeef"


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


class TestFindWorktreeIdByCwd:
    """find_worktree_id_by_cwd -- resolve a worktree from a session cwd."""

    def _save(self, tracking_dir: Path, wt_id: str, wt_path: str) -> None:
        rec = WorktreeRecord(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=wt_path,
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        save_record(rec, tracking_dir / f"{wt_id}.yaml")

    def test_exact_match(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "wt-a", "/tmp/src/wt-a")
        assert find_worktree_id_by_cwd("/tmp/src/wt-a") == "wt-a"

    def test_subdirectory_match(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "wt-a", "/tmp/src/wt-a")
        assert find_worktree_id_by_cwd("/tmp/src/wt-a/sub/dir") == "wt-a"

    def test_deepest_match_wins(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "outer", "/tmp/src")
        self._save(tmp_tracking_dir, "inner", "/tmp/src/inner")
        assert find_worktree_id_by_cwd("/tmp/src/inner/x") == "inner"

    def test_no_match_returns_none(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "wt-a", "/tmp/src/wt-a")
        assert find_worktree_id_by_cwd("/tmp/elsewhere") is None

    def test_empty_cwd_returns_none(self, tmp_tracking_dir: Path, monkeypatch_config):
        assert find_worktree_id_by_cwd("") is None


# ---------------------------------------------------------------------------
# System worktrees -- kind annotation, back-compat, and filtering
# ---------------------------------------------------------------------------

class TestSystemWorktreeKind:
    """The `kind` field marks daemon-owned worktrees (hidden from the Picker)."""

    def _base(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt-k",
            branch="worktree/wt-k",
            worktree_path="/tmp/wt-k",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_default_kind_is_session(self, tmp_path: Path):
        rec = self._base()
        assert rec.kind == "session"

    def test_system_kind_round_trip(self, tmp_path: Path):
        rec = self._base(kind="system", owner="config-reflect")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.kind == "system"
        assert loaded.owner == "config-reflect"

    def test_bridge_kind_round_trip(self, tmp_path: Path):
        rec = self._base(kind="bridge")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "kind: bridge\n" in path.read_text(encoding="utf-8")
        loaded = load_record(path)
        assert loaded.kind == "bridge"

    def test_unknown_kind_degrades_to_session(self, tmp_path: Path):
        path = tmp_path / "weird.yaml"
        path.write_text(
            "worktree_id: w\nbranch: worktree/w\nworktree_path: /tmp/w\n"
            "repo: test-repo\nmachine: test\nplatform: wsl\n"
            "started_at: 2026-06-01T10:00:00\nlast_resumed_at: 2026-06-01T10:00:00\n"
            "resume_count: 0\ntitle: null\nstatus: active\ncompleted_at: null\n"
            "handoff_prompt: null\nkind: gremlin\n",
            encoding="utf-8",
        )
        assert load_record(path).kind == "session"

    def test_legacy_record_without_kind_loads_as_session(self, tmp_path: Path):
        # A pre-feature YAML has no `kind:` line.
        path = tmp_path / "legacy.yaml"
        path.write_text(
            "worktree_id: old\n"
            "branch: worktree/old\n"
            "worktree_path: /tmp/old\n"
            "repo: test-repo\n"
            "machine: test\n"
            "platform: wsl\n"
            "started_at: 2026-06-01T10:00:00\n"
            "last_resumed_at: 2026-06-01T10:00:00\n"
            "resume_count: 0\n"
            "title: null\n"
            "status: active\n"
            "completed_at: null\n"
            "handoff_prompt: null\n",
            encoding="utf-8",
        )
        loaded = load_record(path)
        assert loaded.kind == "session"
        assert loaded.owner is None

    def test_session_record_yaml_has_no_kind_line(self, tmp_path: Path):
        # Back-compat: session records must not gain a `kind:` line (no churn).
        rec = self._base(kind="session")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "kind:" not in path.read_text(encoding="utf-8")

    def test_list_records_kind_filter(self, tmp_path: Path):
        save_record(self._base(worktree_id="s1", kind="session"), tmp_path / "s1.yaml")
        save_record(
            self._base(worktree_id="d1", kind="system", owner="config-reflect"),
            tmp_path / "d1.yaml",
        )
        system = list_records(tmp_path, kind_filter="system")
        assert [r.worktree_id for r in system] == ["d1"]
        sessions_only = list_records(tmp_path, kind_filter="session")
        assert [r.worktree_id for r in sessions_only] == ["s1"]
        assert len(list_records(tmp_path)) == 2

    def test_create_new_record_system(self, tmp_path: Path):
        rec = create_new_record(
            "sys-x", "worktree/sys-x", "/tmp/sys-x", "test-repo", "test", "wsl",
            tmp_path, kind="system", owner="session-sync",
        )
        assert rec.kind == "system"
        assert rec.owner == "session-sync"
        loaded = load_record(tmp_path / "sys-x.yaml")
        assert loaded.kind == "system"
        assert loaded.owner == "session-sync"


# ---------------------------------------------------------------------------
# #2668 -- two-axis taxonomy (interface x origin) + Picker visibility
# ---------------------------------------------------------------------------

class TestOriginInterfaceTaxonomy:
    """The interface/origin marks derive from kind (+ caller) when unstamped,
    an explicit stamp always wins, and visibility keys on origin (not kind)."""

    def _base(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt", branch="b", worktree_path="/tmp/wt",
            repo="r", machine="m", platform="wsl",
            started_at="t", last_resumed_at="t", resume_count=0,
            title=None, status="active", completed_at=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    # -- derivation from kind -------------------------------------------------

    def test_session_derives_cli_user_shown(self):
        r = self._base(kind="session")
        assert r.resolved_interface == "cli"
        assert r.resolved_origin == "user"
        assert r.is_picker_hidden is False

    def test_system_derives_system_hidden(self):
        r = self._base(kind="system")
        assert r.resolved_origin == "system"
        assert r.is_picker_hidden is True

    def test_bridge_without_caller_is_user_acp_shown(self):
        # An operator/NF-launched ACP session: no spawning caller -> user, shown.
        r = self._base(kind="bridge")
        assert r.resolved_interface == "acp"
        assert r.resolved_origin == "user"
        assert r.is_picker_hidden is False

    def test_bridge_with_caller_is_delegate_hidden(self):
        # An agent-spawned ACP session carries its caller worktree -> delegate.
        r = self._base(kind="bridge", caller_worktree="wt-parent")
        assert r.resolved_interface == "acp"
        assert r.resolved_origin == "delegate"
        assert r.is_picker_hidden is True

    # -- explicit stamp overrides derivation ----------------------------------

    def test_explicit_origin_overrides_caller_heuristic(self):
        # agent-bridge (Phase 2) stamps the authoritative origin: a bridge
        # worktree with a caller but an explicit origin=user stays shown.
        r = self._base(kind="bridge", caller_worktree="wt-parent", origin="user")
        assert r.resolved_origin == "user"
        assert r.is_picker_hidden is False

    def test_explicit_delegate_on_session_hides_it(self):
        r = self._base(kind="session", origin="delegate")
        assert r.resolved_origin == "delegate"
        assert r.is_picker_hidden is True

    def test_explicit_interface_overrides_kind(self):
        r = self._base(kind="session", interface="acp")
        assert r.resolved_interface == "acp"

    def test_invalid_stamps_fall_back_to_derivation(self):
        r = self._base(kind="session", interface="bogus", origin="bogus")  # type: ignore[arg-type]
        # Raw invalid values still derive cleanly.
        assert r.resolved_interface == "cli"
        assert r.resolved_origin == "user"

    # -- persistence ----------------------------------------------------------

    def test_stamped_marks_round_trip(self, tmp_path: Path):
        create_new_record(
            "b1", "worktree/b1", "/tmp/b1", "r", "m", "wsl", tmp_path,
            kind="bridge", interface="acp", origin="user",
        )
        loaded = load_record(tmp_path / "b1.yaml")
        assert loaded.interface == "acp"
        assert loaded.origin == "user"
        assert loaded.resolved_origin == "user"
        assert loaded.is_picker_hidden is False

    def test_unstamped_session_yaml_omits_marks(self, tmp_path: Path):
        # A plain session record stays lean: no interface/origin keys emitted
        # (values derive), so legacy YAMLs are byte-stable.
        create_new_record(
            "s1", "worktree/s1", "/tmp/s1", "r", "m", "wsl", tmp_path,
        )
        text = (tmp_path / "s1.yaml").read_text()
        assert "interface:" not in text
        assert "origin:" not in text
        # ...yet they still resolve.
        loaded = load_record(tmp_path / "s1.yaml")
        assert loaded.resolved_interface == "cli"
        assert loaded.resolved_origin == "user"
