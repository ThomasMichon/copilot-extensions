"""Tests for agent_worktrees.codename_tracking: wiring the Phase 1 codename
generator to the local tracking store -- assignment at create time, lazy
backfill, and codename -> worktree-id lookup (effort:
``pr-attribution-codenames`` Phase 2, issue #2838)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_worktrees.codename import DEFAULT_WORDLIST, Wordlist
from agent_worktrees.codename_config import CodenameConfig
from agent_worktrees.codename_tracking import (
    CodenameAttributionPolicyError,
    allocation_policy_kwargs_for_repo,
    assign_new_codename,
    check_allocation_policy,
    classify_codename_source,
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


class TestClassifyCodenameSource:
    def test_no_custom_wordlist_classifies_built_in(self) -> None:
        assert classify_codename_source(CodenameConfig()) == "built-in"

    def test_custom_wordlist_classifies_custom(self) -> None:
        cfg = CodenameConfig(
            wordlist_path="config/codenames.yaml", wordlist_path_configured=True,
        )
        assert classify_codename_source(cfg) == "custom"

    def test_malformed_value_still_classifies_custom(self) -> None:
        # round-13 finding: a malformed wordlist_path normalizes to the
        # empty string, indistinguishable from "no custom wordlist" by
        # wordlist_path alone -- classification must use
        # wordlist_path_configured, not wordlist_path's own truthiness.
        cfg = CodenameConfig(wordlist_path="", wordlist_path_configured=True)
        assert classify_codename_source(cfg) == "custom"

    def test_none_codename_config_classifies_built_in(self) -> None:
        assert classify_codename_source(None) == "built-in"


class TestCheckAllocationPolicy:
    def test_pr_active_custom_wordlist_unconfigured_raises(self) -> None:
        try:
            check_allocation_policy(
                pr_enabled=True, codename_source="custom",
                source_attribution_configured=False,
            )
        except CodenameAttributionPolicyError:
            pass
        else:
            raise AssertionError("expected CodenameAttributionPolicyError")

    def test_pr_active_custom_wordlist_explicitly_configured_ok(self) -> None:
        check_allocation_policy(
            pr_enabled=True, codename_source="custom",
            source_attribution_configured=True,
        )

    def test_pr_active_built_in_wordlist_unconfigured_ok(self) -> None:
        check_allocation_policy(
            pr_enabled=True, codename_source="built-in",
            source_attribution_configured=False,
        )

    def test_pr_inactive_custom_wordlist_unconfigured_ok(self) -> None:
        # round-22 finding: the gate is scoped to PR-active repos only --
        # ordinary local-only Picker usage never regresses.
        check_allocation_policy(
            pr_enabled=False, codename_source="custom",
            source_attribution_configured=False,
        )


class TestAllocationPolicyKwargsForRepo:
    def test_full_config_resolves_all_three(self) -> None:
        config = SimpleNamespace(
            default_repo=SimpleNamespace(
                codename=CodenameConfig(
                    wordlist_path="config/codenames.yaml",
                    wordlist_path_configured=True,
                ),
                pr=SimpleNamespace(
                    enabled=True, source_attribution_configured=True,
                ),
            )
        )
        assert allocation_policy_kwargs_for_repo(config) == {
            "codename_source": "custom",
            "pr_enabled": True,
            "source_attribution_configured": True,
        }

    def test_incomplete_config_degrades_to_safe_defaults(self) -> None:
        # Mirrors wordlist_for_repo's own fail-soft posture: a minimal
        # test stand-in missing default_repo/codename/pr entirely must
        # never raise here, just resolve to the safe "no PR mode,
        # built-in wordlist" defaults.
        assert allocation_policy_kwargs_for_repo(object()) == {
            "codename_source": "built-in",
            "pr_enabled": False,
            "source_attribution_configured": False,
        }


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

    def test_backfill_stamps_codename_source(self, tmp_path: Path) -> None:
        rec = create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl", tmp_path,
        )
        result = ensure_codename(rec, tmp_path, codename_source="custom")
        assert result.codename_source == "custom"
        reloaded = load_record_by_id("wt-a", tracking_path=tmp_path)
        assert reloaded is not None
        assert reloaded.codename_source == "custom"

    def test_concurrent_writer_provenance_is_copied_not_dropped(
        self, tmp_path: Path,
    ) -> None:
        # round-12 finding: when ensure_codename discovers a concurrent
        # writer already assigned a codename, it must copy BOTH the
        # codename AND its codename_source -- not just the codename,
        # silently dropping the provenance a concurrent writer already
        # recorded.
        rec = create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl", tmp_path,
        )
        # Simulate the concurrent writer: assign directly on-disk with a
        # KNOWN codename_source, bypassing this call's own in-memory `rec`.
        winner = ensure_codename(
            create_new_record(
                "wt-b", "worktree/wt-b", "/tmp/wt-b", "repo", "machine", "wsl",
                tmp_path,
            ),
            tmp_path, codename_source="custom",
        )
        from agent_worktrees import tracking as tracking_mod
        on_disk = tracking_mod.load_record(tmp_path / "wt-a.yaml")
        on_disk.codename = winner.codename
        on_disk.codename_source = "custom"
        tracking_mod.save_record(on_disk)

        # The losing caller's in-memory `rec` never saw the winner's write.
        result = ensure_codename(rec, tmp_path, codename_source="built-in")
        assert result.codename == winner.codename
        assert result.codename_source == "custom"

    def test_new_allocation_enforces_policy(self, tmp_path: Path) -> None:
        rec = create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl", tmp_path,
        )
        try:
            ensure_codename(
                rec, tmp_path, codename_source="custom", pr_enabled=True,
                source_attribution_configured=False,
            )
        except CodenameAttributionPolicyError:
            pass
        else:
            raise AssertionError("expected CodenameAttributionPolicyError")
        # The record must NOT have been allocated a codename by the failed
        # attempt.
        assert load_record_by_id("wt-a", tracking_path=tmp_path).codename is None

    def test_concurrent_writer_already_assigned_branch_never_reraises_policy(
        self, tmp_path: Path,
    ) -> None:
        # The policy check must run ONLY on the actual-new-allocation
        # branch, never on the "a concurrent writer already assigned one"
        # branch -- an already-decided codename is never re-validated.
        create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="already-assigned",
        )
        from agent_worktrees import tracking as tracking_mod
        on_disk = tracking_mod.load_record(tmp_path / "wt-a.yaml")
        on_disk.codename_source = "custom"
        tracking_mod.save_record(on_disk)
        # A fresh in-memory copy with no codename yet (simulating a stale
        # caller that hasn't seen the assignment).
        stale_copy = tracking_mod.load_record(tmp_path / "wt-a.yaml")
        stale_copy.codename = None
        result = ensure_codename(
            stale_copy, tmp_path, codename_source="custom", pr_enabled=True,
            source_attribution_configured=False,
        )
        assert result.codename == "already-assigned"
        assert result.codename_source == "custom"

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

    def test_archived_tombstone_survives_concurrent_codename_backfill(
        self, tmp_path: Path,
    ) -> None:
        # Real concurrency (not just "already gone before we started"):
        # ensure_codename and tracking.retire_record race against the SAME
        # record from separate threads. Both serialize through the same
        # per-record `_RecordLock`, so retire_record's archive-tombstone
        # write (unpaired records) and ensure_codename's existence-check +
        # re-read + save can never interleave -- whichever runs last
        # determines the final content, but `ensure_codename` always
        # re-reads the CURRENT on-disk record before saving (see
        # codename_tracking.ensure_codename), so it can never clobber an
        # archived tombstone back to a live status regardless of which
        # thread wins the race.
        import threading

        from agent_worktrees import tracking

        yaml_path = tmp_path / "wt-race.yaml"
        errors: list[BaseException] = []

        for _ in range(20):
            rec = create_new_record(
                "wt-race", "worktree/wt-race", "/tmp/wt-race", "repo", "machine", "wsl",
                tmp_path,
            )
            assert yaml_path.exists()

            barrier = threading.Barrier(2)

            def _retire(barrier=barrier, rec=rec) -> None:
                try:
                    barrier.wait(timeout=5)
                    tracking.retire_record(rec, tmp_path)
                except BaseException as exc:
                    errors.append(exc)

            def _backfill(barrier=barrier, rec=rec) -> None:
                try:
                    barrier.wait(timeout=5)
                    ensure_codename(rec, tmp_path)
                except BaseException as exc:
                    errors.append(exc)

            t_retire = threading.Thread(target=_retire)
            t_backfill = threading.Thread(target=_backfill)
            t_retire.start()
            t_backfill.start()
            t_retire.join(timeout=5)
            t_backfill.join(timeout=5)

            assert not errors, f"race produced exception(s): {errors}"
            # retire_record archives (never deletes) an unpaired record --
            # the file must still exist, and its status must be the
            # archived tombstone regardless of which thread won the race.
            assert yaml_path.exists()
            final = tracking.load_record(yaml_path)
            assert final.status == "archived"


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
