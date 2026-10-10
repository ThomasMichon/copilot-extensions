"""Tests for finalize's content-on-upstream check validating the worktree's
ACTUAL current checkout, not a possibly-stale tracked branch name (#7723).

Before the fix, ``validate_and_finalize`` resolved the branch to check via
``_worktree_branch(record, worktree_id)`` -- the originally-tracked
``record.branch`` from the worktree's creation-time tracking record -- and
used it unconditionally, even when the worktree's checkout had since moved to
a differently-named branch (e.g. a ``-journal`` suffix variant) or ended up
detached. That produced both a false "Unmerged work detected" verdict (when
the stale tracked name was actually behind but the real checkout was safe)
and, in the opposite direction, a silent false-pass (when the tracked name
happened to be safe but the real checkout held something genuinely different).

These exercise the new ``_resolve_finalize_ref`` helper directly against real
temporary git repos -- the same style as ``test_finalize_precondition.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_worktrees import finalize


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_identity(repo: Path) -> None:
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", f"add {name}", cwd=repo)


def _make_worktree(tmp_path: Path) -> Path:
    repo = tmp_path / "wt"
    repo.mkdir()
    _git("init", "-q", "-b", "worktree/anomalous-potato", cwd=repo)
    _init_identity(repo)
    _commit(repo, "base.txt", "base\n")
    return repo


def test_no_divergence_when_checkout_matches_tracked_branch(tmp_path: Path):
    repo = _make_worktree(tmp_path)

    effective_ref, current_ref, diverged = finalize._resolve_finalize_ref(
        "worktree/anomalous-potato", str(repo),
    )

    assert current_ref == "worktree/anomalous-potato"
    assert effective_ref == "worktree/anomalous-potato"
    assert diverged is False


def test_diverged_when_checkout_renamed_to_journal_suffix(tmp_path: Path):
    repo = _make_worktree(tmp_path)
    # The checkout later moved to a renamed branch, but the tracking record
    # still names the original -- exactly the #7723 repro pattern.
    _git("checkout", "-q", "-b", "worktree/anomalous-potato-journal", cwd=repo)
    _commit(repo, "journal.txt", "notes\n")

    effective_ref, current_ref, diverged = finalize._resolve_finalize_ref(
        "worktree/anomalous-potato", str(repo),
    )

    assert current_ref == "worktree/anomalous-potato-journal"
    assert effective_ref == "worktree/anomalous-potato-journal"
    assert diverged is True


def test_diverged_true_and_effective_ref_is_head_when_detached(tmp_path: Path):
    repo = _make_worktree(tmp_path)
    _git("checkout", "-q", "--detach", cwd=repo)

    effective_ref, current_ref, diverged = finalize._resolve_finalize_ref(
        "worktree/anomalous-potato", str(repo),
    )

    assert current_ref is None
    assert effective_ref == "HEAD"
    # A detached checkout is NOT sitting on the tracked branch either -- it
    # must count as diverged too, or a detached worktree whose tracked
    # branch still holds orphaned work would never get the warning/
    # preservation treatment below (#7723 review follow-up).
    assert diverged is True


def test_warn_if_diverged_fires_for_a_detached_checkout_with_orphaned_tracked_branch(
    tmp_path: Path, monkeypatch,
):
    from agent_worktrees import finalize_ref

    repo = _make_worktree(tmp_path)
    # The tracked branch itself never lands anywhere else in this repo, so
    # it always reads as "not on upstream" against a bare empty ref name --
    # use the repo's own initial commit as a stand-in "upstream" that does
    # NOT include the tracked branch's later commit.
    initial = _git("rev-parse", "HEAD", cwd=repo)
    _commit(repo, "unlanded.txt", "never landed\n")
    _git("update-ref", "refs/heads/upstream-stand-in", initial, cwd=repo)
    _git("checkout", "-q", "--detach", cwd=repo)

    warnings: list[str] = []
    monkeypatch.setattr(finalize_ref.output, "warn", lambda msg: warnings.append(msg))

    flagged = finalize_ref.warn_if_tracked_branch_diverged(
        "wt-1", "worktree/anomalous-potato", None,
        "upstream-stand-in", str(repo),
    )

    assert flagged is True
    assert any("detached HEAD" in w for w in warnings)


def test_validate_and_finalize_does_not_false_block_on_renamed_checkout(
    tmp_path: Path, monkeypatch,
):
    """End-to-end repro: the worktree's actual checkout is safe (its content
    is on upstream), but the STALE tracked branch name is behind. Before the
    fix, checking the stale name would report a false "Unmerged work
    detected" here."""
    from agent_worktrees import config as cfg
    from agent_worktrees import tracking

    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tracking_d)

    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "base", str(origin), cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "base", cwd=seed)
    _init_identity(seed)
    _commit(seed, "base.txt", "base\n")
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "-q", "origin", "base", cwd=seed)

    wt_id = "anomalous-potato-test"
    tracked_branch = f"worktree/{wt_id}"
    repo = tmp_path / wt_id
    _git("clone", "-q", str(origin), str(repo), cwd=tmp_path)
    _init_identity(repo)
    _git("checkout", "-q", "-b", tracked_branch, cwd=repo)
    # The originally-tracked branch stalls here -- one commit ahead, never
    # landed. The checkout then moves on to a renamed branch (below) without
    # ever finalizing or abandoning this one.
    _commit(repo, "stale.txt", "stale, never landed\n")

    _git("checkout", "-q", "-b", f"{tracked_branch}-journal", cwd=repo)
    _git("reset", "-q", "--hard", "origin/base", cwd=repo)
    # The actual checkout's own content: already safely on upstream.
    landed_sha = _git("rev-parse", "HEAD", cwd=repo)
    assert landed_sha == _git("rev-parse", "origin/base", cwd=repo)

    tracking.save_record(
        tracking.WorktreeRecord(
            worktree_id=wt_id, branch=tracked_branch, worktree_path=str(repo),
            repo="owner/repo", machine="test", platform="linux",
            started_at="2026-09-01T00:00:00", last_resumed_at="2026-09-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
        ),
        tracking_d / f"{wt_id}.yaml",
    )

    repo_cfg = cfg.RepoConfig(
        anchor=str(seed), worktree_root=str(tmp_path),
        default_branch="base", remote="origin",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="repo", repos={"repo": repo_cfg},
    )

    warnings: list[str] = []
    monkeypatch.setattr(finalize.output, "warn", lambda msg: warnings.append(msg))

    ok = finalize.validate_and_finalize(wt_id, config)

    assert ok is True
    # And the stale tracked branch's orphaned content was surfaced, not
    # silently discarded without a trace.
    assert any("diverged" in w for w in warnings)
    assert any(tracked_branch in w for w in warnings)


def test_successful_finalize_stays_final_on_fetch_free_status(
    tmp_path: Path, monkeypatch,
):
    from agent_worktrees import __main__ as cli
    from agent_worktrees import config as cfg
    from agent_worktrees import tracking

    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_d)
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "base", str(origin), cwd=tmp_path)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    _git("init", "-q", "-b", "base", cwd=anchor)
    _init_identity(anchor)
    _commit(anchor, "base.txt", "base\n")
    _git("remote", "add", "origin", str(origin), cwd=anchor)
    _git("push", "-q", "origin", "base", cwd=anchor)
    wt_id = "final-status"
    checkout = tmp_path / wt_id
    _git("worktree", "add", "-b", f"worktree/{wt_id}", str(checkout), "base", cwd=anchor)
    record = tracking.WorktreeRecord(
        worktree_id=wt_id, branch=f"worktree/{wt_id}", worktree_path=str(checkout),
        repo="repo", machine="test", platform="linux",
        started_at="2026-10-10T00:00:00", last_resumed_at="2026-10-10T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
    )
    tracking.save_record(record, tracking_d / f"{wt_id}.yaml")
    repo_cfg = cfg.RepoConfig(
        anchor=str(anchor), worktree_root=str(tmp_path), default_branch="base", remote="origin",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="repo", repos={"repo": repo_cfg},
    )
    monkeypatch.chdir(checkout)
    assert finalize.validate_and_finalize(wt_id, config)
    finalized = tracking.load_record(tracking_d / f"{wt_id}.yaml")
    assert finalized.status == "finalized"
    monkeypatch.setattr(cli, "_find_record_for_path", lambda path: finalized)
    monkeypatch.setattr(cli, "_detect_upstream_branch", lambda *args: "base")
    monkeypatch.setattr(cfg, "load_config", lambda **kwargs: config)
    for _ in range(3):
        assert cli._render_status_segment(
            str(checkout), fetch=False, plain=True, no_title=True,
        ).strip() == "[FINAL]"


def test_cleanup_deletes_renamed_branch_and_preserves_orphaned_tracked_branch(
    tmp_path: Path, monkeypatch,
):
    """Real ``git worktree add`` setup (refs shared with the anchor, matching
    production) covering the full cleanup path: the checkout renamed to a
    safe branch must actually get deleted (not leaked), while the orphaned
    original tracked branch must survive cleanup for a later rescue."""
    from agent_worktrees import config as cfg
    from agent_worktrees import tracking

    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tracking_d)

    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "base", str(origin), cwd=tmp_path)

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    _git("init", "-q", "-b", "base", cwd=anchor)
    _init_identity(anchor)
    _commit(anchor, "base.txt", "base\n")
    _git("remote", "add", "origin", str(origin), cwd=anchor)
    _git("push", "-q", "origin", "base", cwd=anchor)

    wt_id = "anomalous-potato-cleanup"
    tracked_branch = f"worktree/{wt_id}"
    journal_branch = f"{tracked_branch}-journal"
    worktree_path = tmp_path / wt_id
    _git(
        "worktree", "add", "-b", tracked_branch, str(worktree_path), "base",
        cwd=anchor,
    )
    _init_identity(worktree_path)
    # The originally-tracked branch stalls here -- one commit ahead, never
    # landed anywhere else.
    _commit(worktree_path, "stale.txt", "stale, never landed\n")

    # The checkout then moves on to a renamed branch reset to the safe,
    # already-upstream state, without ever finalizing/abandoning the
    # tracked branch above.
    _git("checkout", "-q", "-b", journal_branch, cwd=worktree_path)
    _git("reset", "-q", "--hard", "origin/base", cwd=worktree_path)

    tracking.save_record(
        tracking.WorktreeRecord(
            worktree_id=wt_id, branch=tracked_branch, worktree_path=str(worktree_path),
            repo="owner/repo", machine="test", platform="linux",
            started_at="2026-09-01T00:00:00", last_resumed_at="2026-09-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
        ),
        tracking_d / f"{wt_id}.yaml",
    )

    repo_cfg = cfg.RepoConfig(
        anchor=str(anchor), worktree_root=str(tmp_path),
        default_branch="base", remote="origin",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="repo", repos={"repo": repo_cfg},
    )

    ok = finalize.validate_and_finalize(wt_id, config)

    assert ok is True
    remaining = _git("branch", "--list", cwd=anchor)
    # The renamed (safe, actually-checked-out) branch is cleaned up --
    # leaving it behind would leak a dangling ref forever.
    assert journal_branch not in remaining
    # The orphaned tracked branch survives cleanup -- it was flagged as
    # possibly holding never-landed work, so it must stay reachable for a
    # rescue rather than being silently discarded right after the warning.
    assert tracked_branch in remaining
