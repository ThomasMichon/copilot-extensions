"""Explicit related bindings retain their own identity without a fake checkout."""

from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import config as cfg, pr_config, repos
from agent_worktrees.pr_related_binding import parse_repository, resolve_related_binding


def binding(**changes):
    return {
        "enabled": True, "required": True, "provider": "azure-devops",
        "repository": "Project/repository", "api_base": "https://dev.azure.com/example",
        "token_env": "TARGET_TOKEN", "notes": "Target guidance", **changes,
    }


def configuration():
    own = cfg.RepoConfig(anchor="caller", worktree_root="caller-worktrees",
                         pr=cfg.PRConfig(provider="github", token_env="CALLER_TOKEN"))
    return SimpleNamespace(repo_name="caller", default_repo=own, repos={"caller": own})


def test_related_only_target_resolves_without_inheriting_the_callers_binding(monkeypatch):
    config = configuration()
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {"target": binding()})
    monkeypatch.setattr(pr_config, "_resolve_repo_remote", lambda *args: "https://github.com/owner/caller")
    monkeypatch.setattr(repos, "list_repos", lambda: [])
    result = pr_config.resolve_repo_config_for_slug(config, "Project/repository")
    assert result.resolved and result.repo_name == "target" and not result.same_as_active
    assert result.repo_config.pr.token_env == "TARGET_TOKEN"
    assert result.repo_config.pr.provider == "azure-devops"
    assert result.repo_config.anchor == "" and result.repo_config.default_branch == ""


def test_ambiguous_related_targets_are_not_selected_by_file_order(monkeypatch):
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map",
                        lambda: {"first": binding(), "second": binding()})
    resolved, _, error = resolve_related_binding(configuration(), "Project/repository")
    assert resolved is None and "Multiple" in error


def test_unknown_target_does_not_use_a_related_names_coincidental_match(monkeypatch):
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map",
                        lambda: {"Project/repository": binding(repository="Different/repository")})
    resolved, _, error = resolve_related_binding(configuration(), "Project/repository")
    assert resolved is None and not error


@pytest.mark.parametrize("changes", [{"provider": ""}, {"api_base": ""}])
def test_incomplete_foreign_binding_has_an_explicit_error(monkeypatch, changes):
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {"target": binding(**changes)})
    resolved, _, error = resolve_related_binding(configuration(), "Project/repository")
    assert resolved is None and error


def test_machine_layer_already_resolved_for_that_target_keeps_precedence(monkeypatch):
    config = configuration()
    config.repos["target"] = cfg.RepoConfig(
        anchor="", worktree_root="",
        pr=cfg.PRConfig(provider="azure-devops", repository="Project/repository",
                        api_base="https://dev.azure.com/example",
                        token_env="MACHINE_TARGET_TOKEN"),
    )
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {"target": binding()})
    resolved, _, _ = resolve_related_binding(config, "Project/repository")
    assert resolved.pr.token_env == "MACHINE_TARGET_TOKEN"


def test_explicit_machine_target_does_not_require_an_anchor_or_a_related_copy(monkeypatch):
    config = configuration()
    config.repos["target"] = cfg.RepoConfig(
        anchor="", worktree_root="", default_branch="",
        pr=cfg.PRConfig(provider="azure-devops", repository="Project/repository",
                        api_base="https://dev.azure.com/example"),
    )
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {})
    resolved, _, _ = resolve_related_binding(config, "Project/repository")
    assert resolved is config.repos["target"]


def test_explicit_machine_retarget_does_not_reactivate_the_old_related_binding(monkeypatch):
    config = configuration()
    config.repos["target"] = cfg.RepoConfig(
        anchor="", worktree_root="",
        pr=cfg.PRConfig(provider="azure-devops", repository="Other/repository",
                        api_base="https://dev.azure.com/other"),
    )
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {"target": binding()})
    resolved, _, _ = resolve_related_binding(config, "Project/repository")
    assert resolved is None


@pytest.mark.parametrize("related_target", [False, True])
@pytest.mark.parametrize("repository", ["Project/repository", "Other/repository"])
def test_machine_without_checkout_reaches_resolution_through_real_loader(
    monkeypatch, tmp_path, related_target, repository,
):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "repo_name": "caller", "platform": "linux",
        "repos": {
            "caller": {"anchor": str(tmp_path / "caller")},
            "target": {"pr": {
                "provider": "azure-devops", "repository": repository,
                "api_base": "https://dev.azure.com/machine",
                "token_env": "MACHINE_TARGET_TOKEN",
                "observation": {"requirements": {"minimum_approvals": 2}},
            }},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "absent.yaml")
    monkeypatch.setattr(cfg, "_resolve_anchor_from_registry", lambda *args: "")
    monkeypatch.setattr(cfg, "_resolve_adoption_defaults_from_registry", lambda *args: {})
    monkeypatch.setattr(cfg, "_load_knowledge_overlay_config", lambda *args: {})
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map",
                        lambda: {"target": binding(observation={
                            "requirements": {"minimum_approvals": 1, "resolved_comments": False},
                        })} if related_target else {})
    loaded = cfg.load_config(path)
    monkeypatch.setattr(pr_config, "_resolve_repo_remote",
                        lambda *args: "https://github.com/owner/caller")
    monkeypatch.setattr(repos, "list_repos", lambda: [repos.RepoEntry(
        name="target", remote="https://dev.azure.com/example/Project/_git/repository",
    )])
    monkeypatch.setattr(cfg, "load_project_config", lambda name: loaded)
    result = pr_config.resolve_repo_config_for_slug(loaded, repository)
    resolved = result.repo_config
    assert not result.error and result.repo_name == "target" and resolved is not None
    assert resolved.pr.api_base == "https://dev.azure.com/machine"
    assert resolved.pr.token_env == "MACHINE_TARGET_TOKEN"
    assert resolved.pr.observation.minimum_approvals == 2
    assert resolved.pr.observation.resolved_comments is not related_target
    assert resolved.anchor == "" and resolved.default_branch == ""
    assert "target" not in loaded.repos
    if repository != "Project/repository":
        assert not pr_config.resolve_repo_config_for_slug(loaded, "Project/repository").resolved


@pytest.fixture
def registered_checkout_free_target(monkeypatch):
    config = configuration()
    config.checkout_free_pr = {"target": cfg.PRConfig(**binding())}
    monkeypatch.setattr(pr_config, "_resolve_repo_remote",
                        lambda *args: "https://github.com/owner/caller")
    monkeypatch.setattr(repos, "list_repos", lambda: [repos.RepoEntry(
        name="target", remote="https://dev.azure.com/example/Project/_git/repository",
    )])
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {"target": binding()})
    return config


@pytest.mark.parametrize("provider", ["azure-devops", "github", "gitea"])
def test_registered_target_without_local_project_uses_explicit_checkout_free_binding(
    monkeypatch, registered_checkout_free_target, provider,
):
    config = registered_checkout_free_target
    config.checkout_free_pr["target"] = cfg.PRConfig(**binding(
        provider=provider, token_env="MACHINE_TARGET_TOKEN",
    ))

    def unavailable(name):
        raise cfg.ProjectNotRegisteredError("No local repository resolved")

    monkeypatch.setattr(cfg, "load_project_config", unavailable)
    result = pr_config.resolve_repo_config_for_slug(config, "Project/repository")
    assert result.resolved and result.repo_name == "target" and not result.error
    assert result.repo_config.pr is config.checkout_free_pr["target"]
    assert result.repo_config.anchor == "" and result.repo_config.default_branch == ""


def test_registered_project_config_precedes_explicit_checkout_free_binding(
    monkeypatch, registered_checkout_free_target,
):
    config = registered_checkout_free_target
    target = cfg.RepoConfig(anchor="target", worktree_root="target-worktrees",
                            pr=cfg.PRConfig(**binding(token_env="REGISTERED_TOKEN")))
    monkeypatch.setattr(cfg, "load_project_config",
                        lambda name: SimpleNamespace(repos={"target": target}))
    result = pr_config.resolve_repo_config_for_slug(config, "Project/repository")
    assert result.repo_config is target and not result.error


@pytest.mark.parametrize("error", [
    ValueError("Malformed target PR configuration"),
    yaml.YAMLError("Malformed target YAML"),
    PermissionError("Target configuration access denied"),
    FileNotFoundError("Unexpected missing configuration dependency"),
])
def test_registered_project_errors_do_not_fall_back_to_checkout_free_binding(
    monkeypatch, registered_checkout_free_target, error,
):
    def broken(name):
        raise error

    monkeypatch.setattr(cfg, "load_project_config", broken)
    result = pr_config.resolve_repo_config_for_slug(
        registered_checkout_free_target, "Project/repository",
    )
    assert not result.resolved and result.error


@pytest.mark.parametrize("changes", [{"provider": "unsupported"}, {"api_base": ""}])
def test_registered_missing_target_does_not_accept_incomplete_checkout_free_binding(
    monkeypatch, registered_checkout_free_target, changes,
):
    config = registered_checkout_free_target
    config.checkout_free_pr["target"] = cfg.PRConfig(**binding(**changes))
    monkeypatch.setattr(cfg, "load_project_config", lambda name: SimpleNamespace(repos={}))
    result = pr_config.resolve_repo_config_for_slug(config, "Project/repository")
    assert not result.resolved and result.error


@pytest.mark.parametrize("value", [True, "repository", "../repository", "Project/..", "P/r/more", "P/r\nx"])
def test_malformed_repository_binding_is_not_a_guess(value):
    with pytest.raises(ValueError):
        parse_repository(value)
