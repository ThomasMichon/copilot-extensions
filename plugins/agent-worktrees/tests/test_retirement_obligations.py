"""Open PRs and owned resources survive every archival shortcut."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import prune, tracking

from _cleanup_revalidation_helpers import (
    CleanupHarness,
    S,
    info,
    make_record,
    mutate_record,
)


def _add_obligation(record: tracking.WorktreeRecord, kind: str) -> None:
    if kind == "pr":
        record.prs.append(tracking.PRRecord(number=42, state="open"))
    else:
        record.resources.append(
            tracking.ResourceClaim(
                kind="worktree", ref="m/child-project/child", state="active"
            )
        )


@pytest.mark.parametrize("state", ["", "creating", "open", "active", "unknown"])
@pytest.mark.parametrize("status", ["active", "finalized"])
def test_git_completed_never_overrides_nonterminal_pr(
    tmp_path: Path, state: str, status: str
) -> None:
    record = make_record(tmp_path, status=status)
    record.prs = [tracking.PRRecord(number=42, state=state)]
    result = prune.cleanup_disposition(record, info(S.COMPLETED))
    assert not result.cleanable
    assert result.bucket == "open-pr"
    assert "#42" in result.reason


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("kind", ["pr", "child"])
def test_cleanup_rechecks_obligations_before_force_or_missing_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    force: bool,
    exists: bool,
    kind: str,
) -> None:
    harness = CleanupHarness(monkeypatch, tmp_path)
    record = make_record(
        tmp_path, status="finalized" if exists else "active", exists=exists
    )
    path = harness.seed(record)
    harness.set_classifier(info(S.COMPLETED), info(S.COMPLETED))
    harness.set_branch_merged_sequence([True, True])
    harness.set_finalize_lock(
        lambda: mutate_record(path, lambda latest: _add_obligation(latest, kind))
    )

    result = harness.run_reap_one(force=force)

    assert not result["removed"]
    assert result["bucket"] == ("open-pr" if kind == "pr" else "held-claims")
    assert harness.reaped == []
    assert path.exists()
    assert tracking.load_record(path).reaped_at is None


@pytest.mark.parametrize("kind", ["pr", "child"])
def test_batch_report_holds_missing_checkout_with_obligations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    kind: str,
) -> None:
    harness = CleanupHarness(monkeypatch, tmp_path)
    record = make_record(tmp_path, status="active", exists=False)
    _add_obligation(record, kind)
    harness.seed(record)
    harness.set_branch_merged_sequence([True])

    assert harness.run_batch() == 0

    captured = capsys.readouterr()
    assert "Skipping wt1" in captured.out + captured.err
    assert "eligible for cleanup" not in captured.out
    assert harness.reaped == []


@pytest.mark.parametrize("kind", ["pr", "child"])
def test_record_retirement_reloads_obligations_before_archiving(
    tmp_path: Path, kind: str
) -> None:
    record = make_record(tmp_path)
    path = tmp_path / "tracking" / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    mutate_record(path, lambda latest: _add_obligation(latest, kind))
    original = path.read_bytes()

    with pytest.raises(ValueError, match="Cannot retire"):
        tracking.retire_record(record, path.parent)

    assert path.read_bytes() == original
    assert record.reaped_at is None


@pytest.mark.parametrize("state", ["merged", "closed"])
def test_terminal_pr_still_allows_archival(tmp_path: Path, state: str) -> None:
    record = make_record(tmp_path)
    record.prs = [tracking.PRRecord(number=42, state=state)]
    record.resources = [
        tracking.ResourceClaim(kind="worktree", ref="child", state="released")
    ]
    path = tmp_path / "tracking" / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)

    assert prune.cleanup_disposition(record, info(S.COMPLETED)).cleanable
    assert tracking.retire_record(record, path.parent)
    assert tracking.load_record(path).status == "archived"


@pytest.mark.parametrize("state", ["active", "at-rest", "unknown"])
def test_held_obligation_must_be_released_before_retirement(
    tmp_path: Path, state: str
) -> None:
    record = make_record(tmp_path)
    record.resources = [
        tracking.ResourceClaim(kind="pr", ref="https://example.com/repo/pull/42", state=state)
    ]
    path = tmp_path / "tracking" / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)

    disposition = prune.cleanup_disposition(record, info(S.COMPLETED))
    assert not disposition.cleanable
    assert disposition.bucket == "held-claims"
    with pytest.raises(ValueError, match="held resource claim"):
        tracking.retire_record(record, path.parent)

    assert tracking.load_record(path).reaped_at is None


def test_pair_retirement_preserves_sibling_with_reopened_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        tracking.cfg, "project_dir", lambda name: tmp_path / str(name)
    )
    record = make_record(tmp_path, wt_id="parent")
    sibling = make_record(tmp_path, wt_id="sibling")
    record.repo = "parent"
    sibling.repo = "child"
    record.pair_id = sibling.pair_id = "pair"
    record.pair_role = "harness"
    sibling.pair_role = "knowledge"
    record.pair_ref = "m/child/sibling"
    sibling.pair_ref = "m/parent/parent"
    record.pair_kind = sibling.pair_kind = "worktree"
    sibling.reaped_at = "2026-01-01T00:00:00"
    _add_obligation(sibling, "pr")
    path = tmp_path / "parent" / "worktrees" / "parent.yaml"
    sibling_path = tmp_path / "child" / "worktrees" / "sibling.yaml"
    tracking.save_record(record, path)
    tracking.save_record(sibling, sibling_path)
    original_sibling = sibling_path.read_bytes()

    assert tracking.retire_record(record, path.parent)

    assert path.exists()
    assert sibling_path.read_bytes() == original_sibling


@pytest.mark.parametrize("kind", ["pr", "child"])
@pytest.mark.parametrize("stale", [False, True])
def test_direct_reaper_refuses_before_any_destructive_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, stale: bool
) -> None:
    from agent_worktrees import __main__ as cli

    reap = cli._reap_worktree
    harness = CleanupHarness(monkeypatch, tmp_path)
    record = make_record(tmp_path)
    if not stale:
        _add_obligation(record, kind)
    path = harness.seed(record)
    if stale:
        mutate_record(path, lambda latest: _add_obligation(latest, kind))
    original = path.read_bytes()
    actions: list[str] = []
    monkeypatch.setattr(
        cli.sessions, "kill_tmux_session", lambda *_a: actions.append("kill")
    )
    monkeypatch.setattr(
        cli.procs, "terminate_processes_under", lambda *_a: actions.append("terminate")
    )
    monkeypatch.setattr(
        cli.git_ops, "remove_worktree", lambda *_a: actions.append("remove")
    )
    monkeypatch.setattr(
        cli.git_ops, "delete_branch", lambda *_a, **_k: actions.append("delete")
    )
    monkeypatch.setattr(
        cli.tracking, "retire_record", lambda *_a: actions.append("retire")
    )

    failures, warnings = reap(
        record, info(S.COMPLETED), harness.repo, harness.tracking_dir
    )

    assert failures == 1
    expected = "#42" if kind == "pr" else "held resource claim"
    assert any(expected in warning for warning in warnings)
    assert actions == []
    assert Path(record.worktree_path).exists()
    assert path.read_bytes() == original


@pytest.mark.parametrize("checkout_managed", [False, True])
def test_reaper_returns_structured_retirement_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, checkout_managed: bool
) -> None:
    from agent_worktrees import __main__ as cli

    reap = cli._reap_worktree
    harness = CleanupHarness(monkeypatch, tmp_path)
    record = make_record(tmp_path, exists=False)
    record.worktree_path = ""
    record.branch = ""
    record.checkout_managed = checkout_managed
    path = harness.seed(record)
    original = path.read_bytes()

    def refuse(*_args: object) -> bool:
        raise ValueError("Cannot retire wt1: PR #42 still open")

    monkeypatch.setattr(tracking, "retire_record", refuse)

    failures, warnings = reap(
        record, info(S.COMPLETED), harness.repo, harness.tracking_dir
    )

    assert failures == 1
    assert warnings == ["Cannot retire wt1: PR #42 still open"]
    assert path.read_bytes() == original


def test_changed_pair_defers_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tracking.cfg, "project_dir", lambda name: tmp_path / str(name)
    )
    record = make_record(tmp_path)
    path = tmp_path / "tracking" / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)

    def change_pair(latest: tracking.WorktreeRecord) -> None:
        latest.pair_id = "new-pair"
        latest.pair_role = "harness"
        latest.pair_ref = "m/other/peer"
        latest.pair_kind = "worktree"

    mutate_record(path, change_pair)
    original = path.read_bytes()

    assert tracking.retire_record(record, path.parent) is False
    assert path.read_bytes() == original


def test_generic_blocker_does_not_echo_claim_references(tmp_path: Path) -> None:
    from agent_worktrees.retirement_safety import retirement_blocker

    record = make_record(tmp_path)
    record.resources = [
        tracking.ResourceClaim(
            kind="pr", ref="https://example.com/private-reference", state="active"
        )
    ]
    blocker = retirement_blocker(record)
    assert blocker is not None
    assert "1 held resource claim" in blocker.reason
    assert record.resources[0].ref not in blocker.reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pair_id", "new-pair"),
        ("pair_kind", "anchor"),
        ("pair_role", "knowledge"),
        ("pair_ref", "m/other/another-peer"),
    ],
)
def test_each_pair_identity_change_defers_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    monkeypatch.setattr(
        tracking.cfg, "project_dir", lambda name: tmp_path / str(name)
    )
    record = make_record(tmp_path)
    record.pair_id = "original-pair"
    record.pair_kind = "worktree"
    record.pair_role = "harness"
    record.pair_ref = "m/other/peer"
    path = tmp_path / "tracking" / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    original = path.read_bytes()
    # A stale caller disagrees with the authoritative pair under the retirement lock.
    setattr(record, field, value)

    assert tracking.retire_record(record, path.parent) is False
    assert path.read_bytes() == original


def test_pr_blocker_names_numbered_and_unnumbered_entries(tmp_path: Path) -> None:
    from agent_worktrees.retirement_safety import retirement_blocker

    record = make_record(tmp_path)
    record.prs = [
        tracking.PRRecord(number=42, state="open"),
        tracking.PRRecord(state="creating"),
    ]
    blocker = retirement_blocker(record)
    assert blocker is not None
    assert "PR #42, ?" in blocker.reason


def test_direct_reaper_reports_lock_contention_without_destructive_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_worktrees import cleanup_reap

    harness = CleanupHarness(monkeypatch, tmp_path)
    record = make_record(tmp_path)
    path = harness.seed(record)
    original = path.read_bytes()

    def locked(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("record lock contended")

    monkeypatch.setattr(tracking, "_RecordLock", locked)
    monkeypatch.setattr(
        cleanup_reap,
        "_reap_locked",
        lambda *_args: pytest.fail("destructive reaper must not run on contention"),
    )

    failures, warnings = cleanup_reap.reap_worktree(
        record, info(S.COMPLETED), harness.repo, path.parent
    )

    assert failures == 1
    assert warnings == ["Cannot retire wt1: record lock contended"]
    assert path.read_bytes() == original
    assert Path(record.worktree_path).exists()
