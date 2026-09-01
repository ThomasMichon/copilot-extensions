from __future__ import annotations

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import git_ops
from agent_worktrees import repos as repos_mod
from agent_worktrees import state_root
from agent_worktrees.lease_config import (
    ORIGIN_ENV,
    ConfigError,
    CoordinationReadinessError,
    _resolve_acquisition_store_target,
    _resolve_store_target,
)


def _fake_config(
    *,
    knowledge_repo: str,
    platform: str = "windows",
    stateless: bool = False,
    requires_external_state_root: bool = False,
) -> cfg.Config:
    """A minimal Config whose default repo is the current project's own repo."""
    repo = cfg.RepoConfig(
        anchor="/anchors/self",
        worktree_root="/anchors/self-worktrees",
        remote="origin",
        stateless=stateless,
        requires_external_state_root=requires_external_state_root,
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
        repos_mod,
        "resolve_path",
        lambda name: "/anchors/dotfiles" if name == "dotfiles" else None,
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
        repos_mod, "resolve_path", lambda name: None
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
        repos_mod,
        "resolve_path",
        lambda name: "/anchors/dotfiles" if name == "dotfiles" else None,
    )
    monkeypatch.setattr(git_ops, "_remote_url", lambda remote, *, cwd: None)

    with pytest.raises(ConfigError, match="knowledge repo"):
        _resolve_store_target()


def test_no_knowledge_repo_refuses_current_project_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(cfg, "load_config", lambda: _fake_config(knowledge_repo=""))

    def fail_remote_url(remote: str, *, cwd: str) -> str | None:
        raise AssertionError("must not inspect the current project's source remote")

    monkeypatch.setattr(git_ops, "_remote_url", fail_remote_url)

    with pytest.raises(ConfigError, match="Refusing to use.*source remote"):
        _resolve_store_target()


def test_acquisition_override_cannot_bypass_missing_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: _fake_config(
            knowledge_repo="",
            stateless=True,
            requires_external_state_root=True,
        ),
    )
    monkeypatch.setattr(
        git_ops,
        "_remote_url",
        lambda *args, **kwargs: pytest.fail(
            "unbound acquisition resolved a store origin"
        ),
    )

    with pytest.raises(CoordinationReadinessError) as caught:
        _resolve_acquisition_store_target("https://example.test/state.git")
    assert caught.value.readiness.code == "knowledge_binding_required"


def test_acquisition_distinguishes_unresolved_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: _fake_config(
            knowledge_repo="missing",
            stateless=True,
            requires_external_state_root=True,
        ),
    )
    monkeypatch.setattr(state_root, "_checkout_path", lambda name: None)

    with pytest.raises(CoordinationReadinessError) as caught:
        _resolve_acquisition_store_target("https://example.test/state.git")
    assert caught.value.readiness.code == "state_root_resolution_failed"


def test_acquisition_matching_override_keeps_bound_auth_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        ORIGIN_ENV, "https://github.com/example/wrong-environment.git"
    )
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: _fake_config(
            knowledge_repo="knowledge",
            stateless=True,
            requires_external_state_root=True,
        ),
    )
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        repos_mod,
        "resolve_path",
        lambda name: str(knowledge),
    )
    monkeypatch.setattr(
        git_ops,
        "_remote_url",
        lambda remote, *, cwd: "git@github.com:example/state.git",
    )

    target = _resolve_acquisition_store_target(
        "https://github.com/example/state.git"
    )

    assert target == (
        "https://github.com/example/state.git",
        "origin",
        str(knowledge),
    )


def test_acquisition_rejects_override_for_different_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: _fake_config(
            knowledge_repo="knowledge",
            stateless=True,
            requires_external_state_root=True,
        ),
    )
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        repos_mod,
        "resolve_path",
        lambda name: str(knowledge),
    )
    monkeypatch.setattr(
        git_ops,
        "_remote_url",
        lambda remote, *, cwd: "https://github.com/example/state.git",
    )

    with pytest.raises(ConfigError, match="must match the bound state"):
        _resolve_acquisition_store_target(
            "https://github.com/example/other.git"
        )


def test_acquisition_rejects_mismatched_environment_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        ORIGIN_ENV, "https://github.com/example/other.git"
    )
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: _fake_config(
            knowledge_repo="knowledge",
            stateless=True,
            requires_external_state_root=True,
        ),
    )
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        repos_mod, "resolve_path", lambda name: str(knowledge)
    )
    monkeypatch.setattr(
        git_ops,
        "_remote_url",
        lambda remote, *, cwd: "https://github.com/example/state.git",
    )

    with pytest.raises(ConfigError, match="must match the bound state"):
        _resolve_acquisition_store_target()


def test_acquisition_without_override_reuses_existing_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    config = _fake_config(
        knowledge_repo="knowledge",
        stateless=True,
        requires_external_state_root=True,
    )
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        repos_mod, "resolve_path", lambda name: str(knowledge)
    )
    monkeypatch.setattr(
        git_ops,
        "_remote_url",
        lambda remote, *, cwd: "https://github.com/example/state.git",
    )

    assert _resolve_acquisition_store_target() == _resolve_store_target()


def test_self_hosted_acquisition_keeps_explicit_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ORIGIN_ENV, raising=False)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: _fake_config(knowledge_repo=""),
    )

    assert _resolve_acquisition_store_target(
        "https://example.test/state.git"
    ) == ("https://example.test/state.git", None, None)
