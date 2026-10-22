"""Record-only fixture construction, not publication evidence."""

from pathlib import Path

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, tracking


def test_record_repo_creates_private_persisted_state_without_git(request, monkeypatch):
    def unexpected_git(*args, **kwargs):
        raise AssertionError("record-only fixture must not invoke Git")

    monkeypatch.setattr(git_ops, "git", unexpected_git)
    config, wid, worktree, remote = request.getfixturevalue("record_repo")
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")

    assert cfg.load_config() is config
    assert record.worktree_id == wid
    assert record.worktree_path == str(worktree)
    assert record.branch == f"worktree/{wid}"
    assert record.prs == []
    assert worktree.is_dir()
    assert not (worktree / ".git").exists()
    assert not remote.exists()
    assert not Path(config.default_repo.anchor).exists()
