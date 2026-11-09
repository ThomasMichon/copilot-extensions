"""Use real configuration and registry owners, independent of the caller CWD."""

import json
import inspect
import os
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
    monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("selected account before target gate"))
    monkeypatch.setattr(repos, "resolve_account", lambda *a, **k: pytest.fail("selected account before target gate"))
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
    monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("credential policy bypass"))
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
    monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("stale policy acquired account"))
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


@pytest.mark.parametrize("mapped", [True, False])
def test_checkout_free_selection_does_not_borrow_sibling_account(target, mapped):
    path, _, _, registry, write = target
    registry["repos"]["sibling"] = {
        "remote": "https://github.com/example/other.git", "account": "sibling-only",
    }
    if not mapped:
        registry["account_map"] = {}
    write()
    if mapped:
        assert select_report_target(path, "example/project")["account"] == "selected"
    else:
        with pytest.raises(ValueError, match="explicitly configured"):
            select_report_target(path, "example/project")


@pytest.mark.parametrize("recover", [True, False])
def test_invalid_utf8_committed_policy_refuses_on_both_read_paths(committed_target, monkeypatch, recover):
    from agent_worktrees import git_ops

    path, anchor, git = committed_target
    policy = anchor / ".agent-worktrees" / "config.yaml"
    policy.write_bytes(b"pr: {to\xffken_command: fixture-helper}\n")
    git("add", ".agent-worktrees/config.yaml")
    git("commit", "-m", "fixture invalid bytes")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    policy.write_text("{}\n", encoding="utf-8")
    original = git_ops.git
    def unavailable(*args, **kwargs):
        if recover and kwargs.get("cwd") == anchor and args[0] == "cat-file":
            raise subprocess.TimeoutExpired(args, 1)
        return original(*args, **kwargs)
    monkeypatch.setattr(git_ops, "git", unavailable)
    monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("corrupted policy acquired account"))
    with pytest.raises(ValueError, match="lossless UTF-8"):
        select_report_target(path, "example/project")


@pytest.mark.parametrize("recover", [True, False])
def test_valid_utf8_replacement_character_is_not_decoding_loss(committed_target, monkeypatch, recover):
    from agent_worktrees import git_ops

    path, anchor, git = committed_target
    policy = anchor / ".agent-worktrees" / "config.yaml"
    policy.write_text("marker: \ufffd\n", encoding="utf-8")
    git("add", ".agent-worktrees/config.yaml")
    git("commit", "-m", "fixture valid Unicode")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    policy.write_text("{}\n", encoding="utf-8")
    original = git_ops.git
    def unavailable(*args, **kwargs):
        if recover and kwargs.get("cwd") == anchor and args[0] == "cat-file":
            raise subprocess.TimeoutExpired(args, 1)
        return original(*args, **kwargs)
    monkeypatch.setattr(git_ops, "git", unavailable)
    assert select_report_target(path, "example/project")["account"] == "selected"


@pytest.mark.parametrize("source", ["explicit", "global", "dropin", "pointer"])
def test_duplicate_keys_in_other_config_sources_cannot_drop_credential_policy(target, monkeypatch, source):
    path, _, _, _, _ = target
    duplicate = 'repos:\n  target:\n    pr:\n      token_command: helper\n      token_command: ""\n'
    if source == "explicit":
        path.write_text(path.read_text().replace(
            '"provider": "github"',
            '"provider": "github", "token_command": "helper", "token_command": ""',
        ), encoding="utf-8")
    elif source == "global":
        (path.parent / "global.yaml").write_text(duplicate, encoding="utf-8")
    else:
        dropins = path.parent / "config.d"
        dropins.mkdir()
        if source == "dropin":
            (dropins / "policy.yaml").write_text(duplicate, encoding="utf-8")
        else:
            (dropins / "policy.json").write_text(
                '{"schema_version":1,"plugin":"first@market","plugin":"other@market",'
                '"plugin_root":"/fixture","target":"policy.yaml"}', encoding="utf-8",
            )
    monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("duplicate policy selected account"))
    with pytest.raises(ValueError):
        select_report_target(path, "example/project")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture; native Windows qualification is separate")
@pytest.mark.parametrize("source", ["global", "repository", "overlay"])
@pytest.mark.parametrize("kind", ["loop", "dangling", "directory"])
def test_unreadable_policy_link_is_not_confirmed_absence(target, tmp_path, monkeypatch, source, kind):
    path, _, document, _, write = target
    if source == "global":
        policy = path.parent / "global-link" / "config.yaml" if kind == "directory" else path.parent / "global-link.yaml"
        monkeypatch.setattr(config, "global_config_path", lambda: policy)
    else:
        anchor = tmp_path / "linked-target"
        document["repos"]["target"]["anchor"] = str(anchor)
        if source == "repository":
            policy = anchor / ".agent-worktrees" / "config.yaml"
        else:
            policy = anchor / config.MARKETPLACE_OVERLAYS_DIR / "fixture" / config.GLOBAL_CONFIG_FILENAME
            monkeypatch.setattr(config.registry_paths, "installation_context_call",
                                lambda callback: {"marketplaceId": "fixture"})
        write()
    if kind == "directory":
        policy.parent.parent.mkdir(parents=True, exist_ok=True)
        policy.parent.symlink_to("missing-policy-directory", target_is_directory=True)
    else:
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.symlink_to(policy.name if kind == "loop" else "missing-policy.yaml")
    monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("unreadable policy selected account"))
    with pytest.raises((OSError, ValueError)):
        select_report_target(path, "example/project")


@pytest.mark.parametrize("kind", ["missing", "directory", "outside", "root-mismatch", "ambiguous-plugin", "disabled"])
def test_enabled_dropin_policy_must_be_available_before_account_selection(target, tmp_path, monkeypatch, kind):
    from dropin_registry import EntryDecision, Finding, ScanAuthority
    from plugin_activation import ActivationReport
    from agent_worktrees import config_dropins
    from test_config_dropins import _active_report, _pointer, _target

    path, _, _, _, _ = target
    directory = path.parent / "config.d"
    directory.mkdir()
    root = tmp_path / "fixture-plugin"
    policy = _target(root, "repos:\n  target:\n    pr:\n      token_command: fixture-helper\n")
    if kind in ("missing", "disabled"):
        policy = root / "missing.yaml"
    elif kind == "directory":
        policy = root / "config"
    elif kind == "outside":
        policy = _target(tmp_path / "other-plugin")
    stored_root = tmp_path / "other-root" if kind == "root-mismatch" else root
    if kind == "root-mismatch":
        stored_root.mkdir()
    source = "fixture@example-marketplace"
    _pointer(directory, source, stored_root, policy)
    activation = (
        ActivationReport(authority=ScanAuthority.COMPLETE, decisions={})
        if kind == "disabled" else _active_report(source, root)
    )
    if kind == "ambiguous-plugin":
        activation = ActivationReport(authority=ScanAuthority.COMPLETE, decisions={
            source: EntryDecision.inactive(Finding(
                registry="plugin-activation", entry=source, status="inactive", reason="root-ambiguous",
            )),
        })
    monkeypatch.setattr(config_dropins, "resolve_active_plugins", lambda: activation)
    if kind == "disabled":
        assert select_report_target(path, "example/project")["account"] == "selected"
    else:
        monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("unavailable dropin selected account"))
        with pytest.raises(ValueError):
            select_report_target(path, "example/project")


@pytest.mark.parametrize("fail", [False, True])
def test_anchor_registry_failure_cannot_be_repaired_by_later_account_read(target, tmp_path, monkeypatch, fail):
    path, _, _, registry, write = target
    anchor = tmp_path / "registered-policy"
    policy = anchor / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("pr: {token_command: fixture-helper}\n", encoding="utf-8")
    registry["repos"]["target"] = {
        "remote": "https://github.com/example/project.git", "account": "selected",
        **{platform: str(anchor) for platform in ("linux", "wsl", "windows")},
    }
    write()
    original = repos.read_registry
    failed, anchor_reads = [False], []

    def read(**kwargs):
        if inspect.currentframe().f_back.f_code.co_name == "_resolve_anchor_from_registry":
            anchor_reads.append(kwargs)
            if fail and not failed[0]:
                failed[0] = True
                raise OSError("fixture-private-anchor-failure")
        return original(**kwargs)

    monkeypatch.setattr(repos, "read_registry", read)
    monkeypatch.setattr(repos, "resolve_account", lambda *a, **k: pytest.fail("unresolved anchor selected account"))
    with pytest.raises(ValueError, match="anchor registry" if fail else "credential selection is unsupported"):
        select_report_target(path, "example/project")
    assert failed[0] is fail and anchor_reads == [{"strict": True}]


@pytest.mark.parametrize("kind", ["ref", "tree", "blob", "direct-head", "declared-branch"])
def test_unresolved_committed_git_state_cannot_qualify_absent_policy(committed_target, monkeypatch, kind):
    path, anchor, git = committed_target
    if kind == "ref":
        git("update-ref", "-d", "refs/remotes/origin/main")
    elif kind in ("tree", "blob"):
        revision = "HEAD^{tree}" if kind == "tree" else "HEAD:.agent-worktrees/config.yaml"
        oid = git("rev-parse", revision).stdout.strip()
        (anchor / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    elif kind == "direct-head":
        git("update-ref", "--no-deref", "refs/remotes/origin/HEAD", "HEAD")
    else:
        policy = anchor / ".agent-worktrees" / "config.yaml"
        policy.write_text("default_branch: unavailable-policy\n", encoding="utf-8")
        git("add", ".agent-worktrees/config.yaml")
        git("commit", "-m", "fixture unavailable branch")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        policy.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(repos, "account_from_map", lambda *a, **k: pytest.fail("unresolved Git state selected account"))
    with pytest.raises(ValueError, match="committed repository policy"):
        select_report_target(path, "example/project")


def test_readable_committed_tree_can_confirm_policy_path_absence(committed_target):
    path, _, git = committed_target
    git("rm", "--cached", "--", ".agent-worktrees/config.yaml")
    git("commit", "-m", "fixture confirmed policy absence")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    assert select_report_target(path, "example/project")["account"] == "selected"
