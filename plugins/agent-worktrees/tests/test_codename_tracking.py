"""Tests for agent_worktrees.codename_tracking: wiring the Phase 1 codename
generator to the local tracking store -- assignment at create time, lazy
backfill, and codename -> worktree-id lookup (effort:
``pr-attribution-codenames`` Phase 2, issue #2838)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_worktrees.codename import DEFAULT_WORDLIST, Wordlist
from agent_worktrees.codename_tracking import (
    assign_new_codename,
    ensure_codename,
    existing_codenames,
    find_record_by_codename,
    wordlist_for_repo,
)
from agent_worktrees.tracking import create_new_record, load_record_by_id


def _make_config(wordlist_path: str = ""):
    """A minimal stand-in for ``agent_worktrees.config.Config`` carrying just
    the ``default_repo.codename.wordlist_path`` attribute path
    ``wordlist_for_repo`` reads.
    """
    return SimpleNamespace(
        default_repo=SimpleNamespace(
            codename=SimpleNamespace(wordlist_path=wordlist_path)
        )
    )


class TestWordlistForRepo:
    def test_no_wordlist_path_uses_default(self) -> None:
        assert wordlist_for_repo(_make_config()) is DEFAULT_WORDLIST

    def test_missing_file_falls_back_to_default(self, tmp_path: Path) -> None:
        config = _make_config(str(tmp_path / "nope.yaml"))
        assert wordlist_for_repo(config) is DEFAULT_WORDLIST

    def test_config_missing_expected_shape_falls_back_to_default(self) -> None:
        # A caller that doesn't otherwise need a real Config (e.g. some
        # existing tests stub `cfg.load_config` with a bare `object()`) must
        # not crash a codename-assigning call path that now reads
        # `config.default_repo.codename.wordlist_path`.
        assert wordlist_for_repo(object()) is DEFAULT_WORDLIST


class TestExistingCodenames:
    def test_empty_tracking_dir(self, tmp_path: Path) -> None:
        assert existing_codenames(tmp_path) == set()

    def test_collects_only_assigned_codenames(self, tmp_path: Path) -> None:
        create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        create_new_record(
            "wt-b", "worktree/wt-b", "/tmp/wt-b", "repo", "machine", "wsl",
            tmp_path,
        )
        assert existing_codenames(tmp_path) == {"rusty-gizmo"}


class TestAssignNewCodename:
    def test_avoids_existing_local_codenames(self, tmp_path: Path) -> None:
        wordlist = Wordlist(
            nouns=(), adjectives=(),
            pairs=(("only", "gizmo"), ("other", "widget")),
        )
        create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="only-gizmo",
        )
        # The only remaining unused pair must be the one picked, every time.
        for _ in range(10):
            assert assign_new_codename(tmp_path, wordlist) == "other-widget"


class TestEnsureCodename:
    def test_leaves_existing_codename_untouched(self, tmp_path: Path) -> None:
        rec = create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        result = ensure_codename(rec, tmp_path)
        assert result.codename == "rusty-gizmo"

    def test_backfills_and_persists_missing_codename(self, tmp_path: Path) -> None:
        # Simulates a pre-Phase-2 record: created with no codename at all.
        rec = create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl", tmp_path,
        )
        assert rec.codename is None
        result = ensure_codename(rec, tmp_path)
        assert result.codename
        # Persisted, not just set in memory -- a fresh load sees the same value.
        reloaded = load_record_by_id("wt-a", tracking_path=tmp_path)
        assert reloaded is not None
        assert reloaded.codename == result.codename

    def test_does_not_resurrect_a_reaped_record(self, tmp_path: Path) -> None:
        # If the record vanished (e.g. reaped by retire_record) between the
        # caller's read and this call, ensure_codename must not write a new
        # tracking file back into existence for it.
        rec = create_new_record(
            "wt-gone", "worktree/wt-gone", "/tmp/wt-gone", "repo", "machine", "wsl", tmp_path,
        )
        (tmp_path / "wt-gone.yaml").unlink()

        result = ensure_codename(rec, tmp_path)

        assert result.codename is None  # returned as-is, still codename-less
        assert not (tmp_path / "wt-gone.yaml").exists()  # never resurrected


class TestFindRecordByCodename:
    def test_empty_codename_returns_none(self, tmp_path: Path) -> None:
        assert find_record_by_codename(tmp_path, "") is None

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        assert find_record_by_codename(tmp_path, "humming-widget") is None

    def test_resolves_matching_record(self, tmp_path: Path) -> None:
        create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        found = find_record_by_codename(tmp_path, "rusty-gizmo")
        assert found is not None
        assert found.worktree_id == "wt-a"
