"""Tests wiring `tracking.record_repo_fetch_confirmed` into operations that
already perform a real git fetch (worktree-finality-and-obligations Phase 9,
slice 4): `finalize.push_changes`/`validate_and_finalize`,
`pr_ops.create_pr`/`_pull_forward_recommendation`, and `cmd_sync`.

Each op's own fetch is expected to record the ledger entry immediately,
instead of only benefiting from whichever surface happens to read
`is_repo_fetch_fresh` later or waiting on the periodic sweep."""

from __future__ import annotations

from pathlib import Path

from test_squash_abort import _git, _make_repo

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, tracking
from agent_worktrees.git_ops import PushResult


def _write_record(tracking_dir: Path, wt_id: str, worktree_path: Path, repo: str):
    rec = tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=str(worktree_path),
        repo=repo,
        machine="test",
        platform="linux",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )
    tracking.save_record(rec, tracking_dir / f"{wt_id}.yaml")
    return rec


def _make_pushable_repo_with_record(
    tmp_path: Path, n_commits: int, monkeypatch, *, repo_name: str = "owner/repo",
):
    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tracking_d)

    repo = _make_repo(tmp_path)
    base_sha = git_ops.git(
        "rev-parse", "HEAD", cwd=str(repo), check=False).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/base", base_sha)
    _git(repo, "checkout", "-q", "-b", "worktree/repo")
    for i in range(n_commits):
        (repo / "f.txt").write_text(f"{i + 1}\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", f"c{i + 1}")

    _write_record(tracking_d, "repo", repo, repo_name)

    repo_cfg = cfg.RepoConfig(
        anchor=str(repo), worktree_root=str(tmp_path),
        default_branch="base", remote="origin",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="repo", repos={"repo": repo_cfg},
    )
    return repo, "repo", config


class TestPushChangesRecordsLedger:
    def test_first_fetch_records_ledger_even_when_squash_later_aborts(
        self, tmp_path, monkeypatch,
    ):
        from agent_worktrees import finalize

        _repo, wt_id, config = _make_pushable_repo_with_record(
            tmp_path, 3, monkeypatch)
        monkeypatch.setattr(finalize.git_ops, "fetch", lambda *a, **k: None)
        monkeypatch.setattr(
            finalize.git_ops, "squash_branch",
            lambda *a, **k: (False, "boom"))
        recorded: list[str] = []
        monkeypatch.setattr(
            tracking, "record_repo_fetch_confirmed", lambda repo: recorded.append(repo))

        ok = finalize.push_changes(wt_id, config, allow_unsquashed=False)

        assert ok is False  # aborted at squash, as before -- unrelated to this
        assert recorded == ["owner/repo"]

    def test_retry_fetch_also_records_ledger(self, tmp_path, monkeypatch):
        from agent_worktrees import finalize

        _repo, wt_id, config = _make_pushable_repo_with_record(
            tmp_path, 2, monkeypatch)
        push_calls = {"n": 0}

        def _race_then_ok(*a, **k):
            push_calls["n"] += 1
            if push_calls["n"] == 1:
                return PushResult(
                    ok=False,
                    stderr=" ! [rejected]  base -> base (fetch first)")
            return PushResult(ok=True)

        monkeypatch.setattr(finalize.git_ops, "push", _race_then_ok)
        monkeypatch.setattr(finalize.git_ops, "fetch", lambda *a, **k: None)
        monkeypatch.setattr(finalize.git_ops, "rebase", lambda *a, **k: True)
        recorded: list[str] = []
        monkeypatch.setattr(
            tracking, "record_repo_fetch_confirmed", lambda repo: recorded.append(repo))

        ok = finalize.push_changes(wt_id, config)

        assert ok is True
        # The initial fetch (1) + the retry fetch (1) = 2 ledger writes.
        assert recorded == ["owner/repo", "owner/repo"]

    def test_no_tracking_record_does_not_touch_ledger(self, tmp_path, monkeypatch):
        """The existing squash-abort tests build a repo with NO tracking
        record at all -- push_changes must keep tolerating that (record is
        None) rather than raising when trying to read `.repo`."""
        from agent_worktrees import finalize

        tracking_d = tmp_path / "tracking"
        tracking_d.mkdir()
        monkeypatch.setattr(
            "agent_worktrees.config.tracking_dir", lambda: tracking_d)
        repo = _make_repo(tmp_path)
        base_sha = git_ops.git(
            "rev-parse", "HEAD", cwd=str(repo), check=False).stdout.strip()
        _git(repo, "update-ref", "refs/remotes/origin/base", base_sha)
        _git(repo, "checkout", "-q", "-b", "worktree/repo")
        (repo / "f.txt").write_text("1\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "c1")
        repo_cfg = cfg.RepoConfig(
            anchor=str(repo), worktree_root=str(tmp_path),
            default_branch="base", remote="origin",
        )
        config = cfg.Config(
            srcroot=str(tmp_path), machine="test", platform="linux",
            repo_name="repo", repos={"repo": repo_cfg},
        )
        monkeypatch.setattr(finalize.git_ops, "fetch", lambda *a, **k: None)
        monkeypatch.setattr(
            finalize.git_ops, "squash_branch", lambda *a, **k: (False, "boom"))
        recorded: list[str] = []
        monkeypatch.setattr(
            tracking, "record_repo_fetch_confirmed", lambda repo: recorded.append(repo))

        ok = finalize.push_changes("repo", config, allow_unsquashed=False)

        assert ok is False
        assert recorded == []


class TestSyncRecordsLedger:
    def test_sync_records_ledger_for_each_distinct_repo(self, tmp_path, monkeypatch):
        from agent_worktrees import __main__ as cli

        wt_a = tmp_path / "a"
        wt_b = tmp_path / "b"
        wt_a.mkdir()
        wt_b.mkdir()
        rec_a = tracking.WorktreeRecord(
            worktree_id="a", branch="worktree/a", worktree_path=str(wt_a),
            repo="owner/repo", machine="m", platform=cfg.detect_platform(),
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
        )
        rec_b = tracking.WorktreeRecord(
            worktree_id="b", branch="worktree/b", worktree_path=str(wt_b),
            repo="owner/repo", machine="m", platform=cfg.detect_platform(),
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
        )
        recorded: list[str] = []
        monkeypatch.setattr(
            tracking, "record_repo_fetch_confirmed", lambda repo: recorded.append(repo))
        monkeypatch.setattr(cli.git_ops, "has_remote", lambda *a, **k: True)
        monkeypatch.setattr(cli.git_ops, "fetch", lambda *a, **k: None)

        repo_cfg = cfg.RepoConfig(
            anchor=str(tmp_path / "anchor"), worktree_root=str(tmp_path),
            remote="origin", default_branch="base",
        )
        config = cfg.Config(
            srcroot=str(tmp_path), machine="test", platform=cfg.detect_platform(),
            repo_name="repo", repos={"repo": repo_cfg},
        )
        monkeypatch.setattr(cli.cfg, "load_config", lambda: config)
        monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: tmp_path / "tracking")
        monkeypatch.setattr(
            cli.tracking, "list_records", lambda *a, **k: [rec_a, rec_b])

        def _fake_sync_one(rec, *a, **k):
            return {"worktree_id": rec.worktree_id, "updated": False, "reason": "test"}

        monkeypatch.setattr(cli, "_sync_one_record", _fake_sync_one)
        monkeypatch.setattr(
            cli.sessions, "scan_sessions_fast", lambda *a, **k: object())
        monkeypatch.setattr(cli, "_build_active_paths", lambda *a, **k: set())

        args = cli.build_parser().parse_args(["sync"])
        cli.cmd_sync(args)

        # Two records, same repo -> exactly one ledger write for that repo.
        assert recorded == ["owner/repo"]


class TestPrOpsRecordsLedger:
    def test_pull_forward_recommendation_records_ledger_on_merge(
        self, tmp_path, monkeypatch,
    ):
        from agent_worktrees import pr_ops

        repo = _make_repo(tmp_path)
        rec = tracking.WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path=str(repo),
            repo="owner/repo", machine="m", platform="linux",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
        )
        active = tracking.PRRecord(
            state="merged", branch="feature/x", number=1,
            provider="gitea", repo="owner/repo",
        )
        repo_cfg = cfg.RepoConfig(
            anchor=str(repo), worktree_root=str(tmp_path),
            default_branch="base", remote="origin",
        )
        config = cfg.Config(
            srcroot=str(tmp_path), machine="test", platform="linux",
            repo_name="repo", repos={"repo": repo_cfg},
        )
        monkeypatch.setattr(pr_ops.git_ops, "has_remote", lambda *a, **k: True)
        monkeypatch.setattr(pr_ops.git_ops, "fetch", lambda *a, **k: None)
        recorded: list[str] = []
        monkeypatch.setattr(
            tracking, "record_repo_fetch_confirmed", lambda repo: recorded.append(repo))

        pr_ops._pull_forward_recommendation(rec, active, config)

        assert recorded == ["owner/repo"]

    def test_pull_forward_recommendation_does_not_record_on_fetch_failure(
        self, tmp_path, monkeypatch,
    ):
        from agent_worktrees import pr_ops

        repo = _make_repo(tmp_path)
        rec = tracking.WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path=str(repo),
            repo="owner/repo", machine="m", platform="linux",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
        )
        active = tracking.PRRecord(
            state="merged", branch="feature/x", number=1,
            provider="gitea", repo="owner/repo",
        )
        repo_cfg = cfg.RepoConfig(
            anchor=str(repo), worktree_root=str(tmp_path),
            default_branch="base", remote="origin",
        )
        config = cfg.Config(
            srcroot=str(tmp_path), machine="test", platform="linux",
            repo_name="repo", repos={"repo": repo_cfg},
        )
        monkeypatch.setattr(pr_ops.git_ops, "has_remote", lambda *a, **k: True)

        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(pr_ops.git_ops, "fetch", _boom)
        recorded: list[str] = []
        monkeypatch.setattr(
            tracking, "record_repo_fetch_confirmed", lambda repo: recorded.append(repo))

        pr_ops._pull_forward_recommendation(rec, active, config)

        assert recorded == []
