"""``pr_config.resolve_repo_config_for_slug`` -- foreign-repo PR-binding
resolution, independent of the caller's active project (the
``foreign-repo-pr-operations`` Feature, ``pull-requests`` vision)."""

from __future__ import annotations

import pytest
import yaml

from agent_worktrees import config as cfg
from agent_worktrees import pr_config
from agent_worktrees import repos as repos_mod


def _repo(anchor="/tmp/anchor", **pr_kwargs) -> cfg.RepoConfig:
    return cfg.RepoConfig(
        anchor=anchor,
        worktree_root="/tmp/wt",
        pr=cfg.PRConfig(**pr_kwargs),
    )


def _config(repo_name: str, remote: str, **pr_kwargs) -> cfg.Config:
    return cfg.Config(
        srcroot="/tmp/src",
        machine="test-machine",
        platform="linux",
        repo_name=repo_name,
        repos={repo_name: _repo(**pr_kwargs)},
    )


def _entry(name: str, remote: str) -> repos_mod.RepoEntry:
    return repos_mod.RepoEntry(name=name, remote=remote)


def test_fast_path_matches_the_active_project_without_extra_config_load(monkeypatch):
    config = _config("demo", "https://github.com/owner/demo.git", provider="github")
    monkeypatch.setattr(
        pr_config, "_resolve_repo_remote",
        lambda cfg_, repo: "https://github.com/owner/demo.git",
    )

    def _boom(*args, **kwargs):
        raise AssertionError("fast path must not list the registry at all")

    monkeypatch.setattr(repos_mod, "list_repos", _boom)

    resolution = pr_config.resolve_repo_config_for_slug(config, "owner/demo")
    assert resolution.resolved
    assert resolution.same_as_active is True
    assert resolution.repo_config is config.repos["demo"]
    assert resolution.repo_name == "demo"


def test_foreign_registered_repo_loads_its_own_config_not_the_callers(monkeypatch):
    config = _config("demo", "https://github.com/owner/demo.git", provider="github")
    monkeypatch.setattr(
        pr_config, "_resolve_repo_remote",
        lambda cfg_, repo: "https://github.com/owner/demo.git",
    )
    monkeypatch.setattr(
        repos_mod, "list_repos",
        lambda: [_entry("other", "https://github.com/owner/other.git")],
    )
    other_repo_cfg = _repo(provider="azure-devops")
    other_config = cfg.Config(
        srcroot="/tmp/src", machine="test-machine", platform="linux",
        repo_name="other", repos={"other": other_repo_cfg},
    )
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda name, **kwargs: other_config if name == "other" else (_ for _ in ()).throw(
            AssertionError(f"unexpected project {name!r}")
        ),
    )

    resolution = pr_config.resolve_repo_config_for_slug(config, "owner/other")
    assert resolution.resolved
    assert resolution.same_as_active is False
    assert resolution.repo_name == "other"
    assert resolution.repo_config is other_repo_cfg
    assert resolution.repo_config.pr.provider == "azure-devops"
    # Never the caller's own binding.
    assert resolution.repo_config is not config.repos["demo"]


def test_unregistered_repo_resolves_honestly_as_unresolved(monkeypatch):
    config = _config("demo", "https://github.com/owner/demo.git", provider="github")
    monkeypatch.setattr(
        pr_config, "_resolve_repo_remote",
        lambda cfg_, repo: "https://github.com/owner/demo.git",
    )
    monkeypatch.setattr(repos_mod, "list_repos", lambda: [])

    resolution = pr_config.resolve_repo_config_for_slug(config, "someone/unregistered")
    assert resolution.resolved is False
    assert resolution.repo_config is None
    assert resolution.repo_name == ""


def test_registered_but_unloadable_foreign_repo_resolves_honestly(monkeypatch):
    """A registry hit whose own config can't actually be loaded (e.g. its
    anchor is gone) must never silently fall back to the caller's binding."""
    config = _config("demo", "https://github.com/owner/demo.git", provider="github")
    monkeypatch.setattr(
        pr_config, "_resolve_repo_remote",
        lambda cfg_, repo: "https://github.com/owner/demo.git",
    )
    monkeypatch.setattr(
        repos_mod, "list_repos",
        lambda: [_entry("broken", "https://github.com/owner/broken.git")],
    )

    def _raise(name, **kwargs):
        raise ValueError("anchor missing")

    monkeypatch.setattr(cfg, "load_project_config", _raise)

    resolution = pr_config.resolve_repo_config_for_slug(config, "owner/broken")
    assert resolution.resolved is False


def test_registry_entry_without_remote_is_skipped(monkeypatch):
    config = _config("demo", "https://github.com/owner/demo.git", provider="github")
    monkeypatch.setattr(
        pr_config, "_resolve_repo_remote",
        lambda cfg_, repo: "https://github.com/owner/demo.git",
    )
    monkeypatch.setattr(
        repos_mod, "list_repos",
        lambda: [_entry("no-remote", "")],
    )
    resolution = pr_config.resolve_repo_config_for_slug(config, "owner/no-remote")
    assert resolution.resolved is False


@pytest.mark.parametrize("allow_git", [True, False])
@pytest.mark.parametrize("remote", [
    "https://dev.azure.com/example/Example%20Project/_git/WidgetRepo",
    "https://example.visualstudio.com/Example%20Project/_git/WidgetRepo",
    "git@ssh.dev.azure.com:v3/example/Example%20Project/WidgetRepo",
])
def test_encoded_singleton_loads_target_policy_and_base(monkeypatch, tmp_path, remote, allow_git):
    from agent_worktrees import pr_related_binding, pr_source_publication

    anchor = tmp_path / "widget"
    anchor.mkdir()
    local = tmp_path / "config"
    local.mkdir()
    target_pr = {
        "enabled": True, "provider": "azure-devops",
        "repository": "Example Project/WidgetRepo", "api_base": "https://dev.azure.com/example",
        "branch_update_strategy": "merge",
    }
    (local / "config.yaml").write_text(yaml.safe_dump({
        "repo_name": "widget", "machine": "test-machine",
        "repos": {"widget": {"default_branch": "main", "pr": target_pr}},
    }), encoding="utf-8")
    entry = repos_mod.RepoEntry(
        name="widget", remote=remote, repo_class="singleton", default_branch="main",
        paths={platform: str(anchor) for platform in ("windows", "linux", "wsl")},
    )
    registry = repos_mod.ReposRegistry(repos={"widget": entry})
    monkeypatch.setattr(repos_mod, "read_registry", lambda **kwargs: registry)
    monkeypatch.setattr(repos_mod, "list_repos", lambda **kwargs: [entry])
    monkeypatch.setattr(cfg, "project_dir", lambda *args: local)
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "absent-global.yaml")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(pr_related_binding, "control_plane_pr_map", lambda: {})
    monkeypatch.setattr(pr_config, "_resolve_repo_remote", lambda *args: "https://github.com/owner/demo")
    caller = _config("demo", "", provider="github")
    caller.checkout_free_pr["widget"] = cfg._parse_pr(target_pr)

    resolution = pr_config.resolve_repo_config_for_slug(
        caller, "Example Project/WidgetRepo", allow_git=allow_git,
    )

    assert resolution.resolved and not resolution.same_as_active
    assert resolution.repo_name == "widget"
    target = resolution.repo_config
    assert target.anchor == str(anchor)
    assert target.default_branch == "main"
    assert target.pr.provider == "azure-devops"
    assert target.pr.branch_update_strategy == "merge"
    assert target.pr.api_base == target_pr["api_base"]
    assert pr_source_publication._canonical_destination(
        target, "Example Project/WidgetRepo", str(anchor),
    ) == remote
    assert caller.repo_name == "demo" and caller.default_repo.pr.provider == "github"
    assert not list(anchor.iterdir())
