"""Tests for the repo-scoped freshness ledger
(worktree-finality-and-obligations Phase 9): `tracking.record_repo_fetch_
confirmed` / `repo_fetch_confirmed_at` / `is_repo_fetch_fresh`."""

from __future__ import annotations

from agent_worktrees import tracking


def _use_tmp_ledger(monkeypatch, tmp_path):
    path = tmp_path / "repo-freshness.json"
    monkeypatch.setattr(tracking, "_repo_freshness_path", lambda: path)
    return path


class TestRepoFreshnessLedger:
    def test_unrecorded_repo_is_never_fresh(self, monkeypatch, tmp_path):
        _use_tmp_ledger(monkeypatch, tmp_path)
        assert tracking.repo_fetch_confirmed_at("owner/repo") is None
        assert tracking.is_repo_fetch_fresh("owner/repo") is False

    def test_recorded_fetch_is_fresh_immediately(self, monkeypatch, tmp_path):
        _use_tmp_ledger(monkeypatch, tmp_path)
        tracking.record_repo_fetch_confirmed("owner/repo", at="2026-09-15T21:00:00")
        assert tracking.repo_fetch_confirmed_at("owner/repo") == "2026-09-15T21:00:00"
        assert tracking.is_repo_fetch_fresh(
            "owner/repo", now="2026-09-15T21:00:05") is True

    def test_recorded_fetch_ages_out(self, monkeypatch, tmp_path):
        _use_tmp_ledger(monkeypatch, tmp_path)
        tracking.record_repo_fetch_confirmed("owner/repo", at="2026-09-15T21:00:00")
        assert tracking.is_repo_fetch_fresh(
            "owner/repo", max_age_seconds=60,
            now="2026-09-15T21:05:00") is False

    def test_entries_are_independent_per_repo(self, monkeypatch, tmp_path):
        _use_tmp_ledger(monkeypatch, tmp_path)
        tracking.record_repo_fetch_confirmed("owner/repo-a", at="2026-09-15T21:00:00")
        assert tracking.repo_fetch_confirmed_at("owner/repo-b") is None
        assert tracking.is_repo_fetch_fresh(
            "owner/repo-a", now="2026-09-15T21:00:05") is True

    def test_recording_one_repo_preserves_another(self, monkeypatch, tmp_path):
        _use_tmp_ledger(monkeypatch, tmp_path)
        tracking.record_repo_fetch_confirmed("owner/repo-a", at="2026-09-15T21:00:00")
        tracking.record_repo_fetch_confirmed("owner/repo-b", at="2026-09-15T21:01:00")
        assert tracking.repo_fetch_confirmed_at("owner/repo-a") == "2026-09-15T21:00:00"
        assert tracking.repo_fetch_confirmed_at("owner/repo-b") == "2026-09-15T21:01:00"

    def test_empty_repo_name_is_a_no_op(self, monkeypatch, tmp_path):
        path = _use_tmp_ledger(monkeypatch, tmp_path)
        tracking.record_repo_fetch_confirmed("")
        assert not path.exists()
        assert tracking.repo_fetch_confirmed_at("") is None
        assert tracking.is_repo_fetch_fresh("") is False

    def test_corrupt_ledger_file_degrades_to_not_fresh(self, monkeypatch, tmp_path):
        path = _use_tmp_ledger(monkeypatch, tmp_path)
        path.write_text("not json", encoding="utf-8")
        assert tracking.repo_fetch_confirmed_at("owner/repo") is None
        assert tracking.is_repo_fetch_fresh("owner/repo") is False

    def test_missing_lock_dir_does_not_raise(self, monkeypatch, tmp_path):
        # The parent directory doesn't exist yet -- record_repo_fetch_confirmed
        # must still succeed (mirrors _atomic_write's own mkdir(parents=True)).
        path = tmp_path / "nested" / "repo-freshness.json"
        monkeypatch.setattr(tracking, "_repo_freshness_path", lambda: path)
        tracking.record_repo_fetch_confirmed("owner/repo", at="2026-09-15T21:00:00")
        assert tracking.repo_fetch_confirmed_at("owner/repo") == "2026-09-15T21:00:00"
