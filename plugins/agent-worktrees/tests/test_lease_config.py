from __future__ import annotations

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import git_ops
from agent_worktrees.lease_config import (
    ORIGIN_ENV,
    ConfigError,
    _resolve_store_target,
)


def _fake_config(*, knowledge_repo: str, platform: str = "windows") -> cfg.Config:
    """A minimal Config whose default repo is the current project's own repo."""
    repo = cfg.RepoConfig(
        anchor="/anchors/self",
        worktree_root="/anchors/self-worktrees",
        remote="origin",
    )
    return cfg.Config(
        srcroot="/anchors",
        machine="example-dev6",
        platform=platform,
        repo_name="self",
        repos={"self": repo},
        knowledge_repo=knowledge_repo,
    )


def test_override_argument_used_verbatim_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    url, remote, anchor = _resolve_store_target("https://example/x.git")
    assert url == "https://example/x.git"
    assert remote is None
    assert anchor is None


def test_override_env_used_verbatim_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ORIGIN_ENV, "https://env/y.git")
    url, remote, anchor = _resolve_store_target()
    assert url == "https://env/y.git"
    assert remote is None
    assert anchor is None


def test_knowledge_repo_redirects_before_current_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(
        cfg, "load_config", lambda: _fake_config(knowledge_repo="dotfiles")
    )
    monkeypatch.setattr(
        cfg,
        "_resolve_anchor_from_registry",
        lambda name, platform: "/anchors/dotfiles" if name == "dotfiles" else None,
    )

    def fake_remote_url(remote: str, *, cwd: str) -> str | None:
        # Only the knowledge checkout should be consulted -- not the self anchor.
        assert str(cwd) == "/anchors/dotfiles"
        assert remote == "origin"
        return "https://github.com/example-operator/dotfiles.git"

    monkeypatch.setattr(git_ops, "_remote_url", fake_remote_url)

    url, remote, anchor = _resolve_store_target()
    assert url == "https://github.com/example-operator/dotfiles.git"
    assert remote == "origin"
    assert anchor == "/anchors/dotfiles"


def test_knowledge_repo_resolution_failure_raises_not_fall_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bound knowledge repo is AUTHORITATIVE: if it cannot be resolved we must
    # NOT silently fall back to the launch/harness repo (that would pollute a
    # shared harness with per-user lease refs). Instead, raise.
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(
        cfg, "load_config", lambda: _fake_config(knowledge_repo="dotfiles")
    )
    # Registry cannot resolve the knowledge checkout on this machine.
    monkeypatch.setattr(
        cfg, "_resolve_anchor_from_registry", lambda name, platform: None
    )

    def fail_remote_url(remote: str, *, cwd: str) -> str | None:
        raise AssertionError(
            "must not fall back to the harness/default repo when a knowledge "
            "repo is bound"
        )

    monkeypatch.setattr(git_ops, "_remote_url", fail_remote_url)

    with pytest.raises(ConfigError, match="knowledge repo"):
        _resolve_store_target()


def test_knowledge_repo_no_origin_remote_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The knowledge checkout resolves but has no 'origin' remote URL -> raise
    # (still no fall-back to the harness repo).
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(
        cfg, "load_config", lambda: _fake_config(knowledge_repo="dotfiles")
    )
    monkeypatch.setattr(
        cfg,
        "_resolve_anchor_from_registry",
        lambda name, platform: "/anchors/dotfiles" if name == "dotfiles" else None,
    )
    monkeypatch.setattr(git_ops, "_remote_url", lambda remote, *, cwd: None)

    with pytest.raises(ConfigError, match="knowledge repo"):
        _resolve_store_target()


def test_no_knowledge_repo_uses_current_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(cfg, "load_config", lambda: _fake_config(knowledge_repo=""))
    monkeypatch.setattr(
        git_ops,
        "_remote_url",
        lambda remote, *, cwd: "https://github.com/owner/self.git",
    )

    url, remote, anchor = _resolve_store_target()
    assert url == "https://github.com/owner/self.git"
    assert remote == "origin"
    assert anchor == "/anchors/self"


def test_unresolvable_default_repo_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(cfg, "load_config", lambda: _fake_config(knowledge_repo=""))
    monkeypatch.setattr(git_ops, "_remote_url", lambda remote, *, cwd: None)

    with pytest.raises(ConfigError):
        _resolve_store_target()
