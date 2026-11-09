"""Use real configuration and registry owners, independent of the caller CWD."""

import json
import subprocess

import pytest

from agent_worktrees import config, repos
from agent_worktrees.pr_report_target import select_report_target


@pytest.fixture
def target(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / ".agent-worktrees").mkdir()
    (caller / ".agent-worktrees" / "config.yaml").write_text(
        "pr:\n  provider: gitea\n  api_base: https://unrelated.example\n", encoding="utf-8",
    )
    global_path = root / "global.yaml"
    global_path.write_text("{}\n", encoding="utf-8")
    registry_path = root / "repos.yaml"
    registry = {"account_map": {"example": "selected"}, "repos": {}}
    path = root / "read.yaml"
    document = {
        "repo_name": "caller",
        "repos": {
            "caller": {"anchor": str(caller)},
            "target": {"pr": {"repository": "example/project", "provider": "github"}},
        },
    }
    monkeypatch.setattr(config, "global_config_path", lambda: global_path)
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry_path)
    monkeypatch.setattr(config, "_home", lambda: root)
    config.set_active_project(None)
    monkeypatch.chdir(tmp_path)

    def write():
        path.write_text(json.dumps(document), encoding="utf-8")
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

    write()
    return path, registry_path, document, registry, write


def test_actual_checkout_free_selection_does_not_inherit_callers_provider(target):
    path, _, _, _, _ = target
    assert select_report_target(path, "example/project") == {
        "target": "target", "provider": "github", "endpoint": "github.com",
        "account": "selected", "repo": "example/project",
    }


def test_account_map_change_is_observed_without_config_cache(target):
    path, _, _, registry, write = target
    assert select_report_target(path, "example/project")["account"] == "selected"
    registry["account_map"]["example"] = "replacement"
    write()
    assert select_report_target(path, "example/project")["account"] == "replacement"


def test_explicit_repository_account_precedes_owner_map(target):
    path, _, _, registry, write = target
    registry["repos"]["registered"] = {
        "remote": "https://github.com/example/project.git", "account": "override",
    }
    write()
    assert select_report_target(path, "example/project")["account"] == "override"


@pytest.mark.parametrize("change", [
    lambda d: d["repos"].update(other={"pr": dict(d["repos"]["target"]["pr"])}),
    lambda d: d["repos"]["target"]["pr"].update(provider="gitea"),
    lambda d: d["repos"]["target"]["pr"].update(provider="azure-devops"),
    lambda d: d["repos"]["target"]["pr"].update(token_env="TOKEN"),
    lambda d: d["repos"]["target"]["pr"].update(token_command="helper"),
    lambda d: d["repos"]["target"]["pr"].update(api_base="https://github.com/other"),
    lambda d: d["repos"]["target"]["pr"].update(api_base="http://github.com"),
    lambda d: d["repos"]["target"]["pr"].update(api_base="https://github.com:444"),
    lambda d: d["repos"]["target"]["pr"].update(api_base="https://github.com/?q=1"),
    lambda d: d["repos"]["target"]["pr"].update(api_base="https://other.example"),
    lambda d: d["repos"]["target"]["pr"].update(api_base="https://user@github.com"),
    lambda d: d["repos"]["target"]["pr"].update(repository="other/project"),
])
def test_wrong_or_ambiguous_binding_refuses_before_account_acquisition(target, change, monkeypatch):
    path, _, document, _, write = target
    change(document)
    write()
    monkeypatch.setattr(repos, "account_for_github_slug", lambda *a, **k: pytest.fail("selected account before target gate"))
    with pytest.raises(ValueError):
        select_report_target(path, "example/project")


def test_derived_owner_is_not_a_configured_account(target):
    path, _, _, registry, write = target
    registry["account_map"] = {}
    write()
    with pytest.raises(ValueError, match="explicitly configured"):
        select_report_target(path, "example/project")


def test_strict_registry_and_duplicate_slug_refuse(target):
    path, registry_path, _, registry, write = target
    registry_path.write_text("account_map: [bad]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        select_report_target(path, "example/project")
    registry["repos"] = {
        key: {"remote": "https://github.com/example/project", "account": key}
        for key in ("first", "second")
    }
    write()
    with pytest.raises(ValueError, match="ambiguous"):
        select_report_target(path, "example/project")


@pytest.mark.parametrize("content", ["pr: [", "pr: {}\npr: {token_command: helper}\n", "[]"])
def test_invalid_target_policy_cannot_disappear_into_explicit_binding(target, tmp_path, content):
    path, _, document, _, write = target
    anchor = tmp_path / "target"
    policy = anchor / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(content, encoding="utf-8")
    document["repos"]["target"]["anchor"] = str(anchor)
    write()
    with pytest.raises(ValueError):
        select_report_target(path, "example/project")


def test_unreadable_target_policy_refuses_before_account_acquisition(target, tmp_path, monkeypatch):
    from agent_worktrees import repos_configuration_sources

    path, _, document, _, write = target
    anchor = tmp_path / "target"
    policy = anchor / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("pr: {token_command: helper}\n", encoding="utf-8")
    document["repos"]["target"]["anchor"] = str(anchor)
    write()
    original = repos_configuration_sources.read_bytes

    def unavailable(source):
        if source == policy:
            raise PermissionError("fixture-private-diagnostic")
        return original(source)

    monkeypatch.setattr(repos_configuration_sources, "read_bytes", unavailable)
    monkeypatch.setattr(repos, "account_for_github_slug", lambda *a, **k: pytest.fail("credential policy bypass"))
    with pytest.raises(PermissionError):
        select_report_target(path, "example/project")


def test_failed_config_dropin_is_not_an_absent_policy(target):
    path, _, _, _, _ = target
    dropins = path.parent / "config.d"
    dropins.mkdir()
    (dropins / "policy.yaml").write_text("repos: [", encoding="utf-8")
    with pytest.raises(ValueError, match="acquisition"):
        select_report_target(path, "example/project")


@pytest.fixture
def committed_target(target, tmp_path):
    from agent_worktrees import git_ops

    path, _, document, _, write = target
    anchor = tmp_path / "committed-target"
    anchor.mkdir()
    def git(*args):
        return git_ops.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           *args, cwd=anchor)
    git("init", "--template=", "-b", "main")
    policy = anchor / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir()
    policy.write_text("pr: {token_command: fixture-helper}\n", encoding="utf-8")
    git("add", ".agent-worktrees/config.yaml")
    git("commit", "-m", "fixture policy")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    policy.write_text("{}\n", encoding="utf-8")
    document["repos"]["target"]["anchor"] = str(anchor)
    write()
    return path, anchor, git


@pytest.mark.parametrize("failure", ["head", "blob", "presence"])
def test_unresolved_committed_policy_never_uses_divergent_checkout(committed_target, monkeypatch, failure):
    from agent_worktrees import git_ops

    path, anchor, _ = committed_target
    original = git_ops.git
    def unavailable(*args, **kwargs):
        if kwargs.get("cwd") == anchor and (
            (failure == "head" and args[0] == "symbolic-ref")
            or (failure != "head" and args[0] == "cat-file")
            or (failure == "blob" and args[0] == "show")
            or (failure == "presence" and "ls-tree" in args)
        ):
            raise subprocess.TimeoutExpired(args, 1)
        return original(*args, **kwargs)
    monkeypatch.setattr(git_ops, "git", unavailable)
    monkeypatch.setattr(repos, "account_for_github_slug", lambda *a, **k: pytest.fail("stale policy acquired account"))
    with pytest.raises(ValueError, match="committed repository policy"):
        select_report_target(path, "example/project")


def test_same_committed_source_recovery_retains_its_credential_policy(committed_target, monkeypatch):
    from agent_worktrees import git_ops

    path, anchor, _ = committed_target
    original = git_ops.git
    def unavailable(*args, **kwargs):
        if kwargs.get("cwd") == anchor and args[0] == "cat-file":
            raise subprocess.TimeoutExpired(args, 1)
        return original(*args, **kwargs)
    monkeypatch.setattr(git_ops, "git", unavailable)
    with pytest.raises(ValueError, match="credential selection is unsupported"):
        select_report_target(path, "example/project")


def test_confirmed_absent_committed_source_keeps_checkout_selection(committed_target):
    path, _, git = committed_target
    git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    assert select_report_target(path, "example/project")["account"] == "selected"
