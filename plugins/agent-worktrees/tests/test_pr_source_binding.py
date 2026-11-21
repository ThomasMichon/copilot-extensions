"""Publication keeps its original source, destination and ownership binding."""

import argparse
from collections.abc import Iterator
from dataclasses import replace
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_worktrees import config as cfg, git_ops, pr_publish, pr_transport_fence, tracking
from agent_worktrees.providers.base import PRScope, ProviderError, PullResult
from agent_worktrees.worktree_identity import _infer_worktree_id_from_cwd
from test_parallel_publication import git, repository
from test_pr_transport_fence import native_source_owners as native_source_owners

NativeOwners = tuple[cfg.Config, tracking.WorktreeRecord, tracking.WorktreeRecord, Path]


def test_nonbranch_push_retains_the_original_native_hook_identity(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    git(root, "tag", "-a", "release", "-m", "Release")
    marker = tmp_path / "hook-input"
    hook = root / ".git" / "hooks" / "pre-push"
    hook.write_text(f'#!/bin/sh\ncat > "{marker.as_posix()}"\n', encoding="utf-8")
    hook.chmod(0o700)
    git(root, "push", "origin", "refs/tags/release:refs/tags/native")
    native = marker.read_text(encoding="utf-8").split()[:2]
    result = git_ops.push("origin", "refs/tags/release:refs/tags/pinned", cwd=root)
    assert result.ok, result.stderr
    assert marker.read_text(encoding="utf-8").split()[:2] == native
    assert git(remote, "rev-parse", "refs/tags/pinned") == native[1] == result.published_head_sha


@pytest.mark.parametrize("filename", [
    "owner.yaml",
    pytest.param("OWNER.YAML", marks=pytest.mark.skipif(
        os.name != "nt", reason="Windows ledger globbing is case insensitive",
    )),
])
def test_cached_legacy_source_paths_resolve_in_the_current_callers_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    from agent_worktrees import source_owner_lookup

    ledger = tmp_path / "ledger"
    ledger.mkdir()
    owner = ledger / filename
    owner.write_text("worktree_id: owner\nbranch: topic\nworktree_path: external\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        workspace = root / "external"
        workspace.mkdir()
        monkeypatch.chdir(root)
        assert source_owner_lookup.matching((ledger,), workspace) == {owner}


@pytest.mark.parametrize("movement", ["reset", "checkout"])
def test_source_and_implicit_destination_are_bound_before_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, movement: str,
) -> None:
    root, remote = repository(tmp_path)
    initial = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-qc", "topic")
    git(root, "commit", "--allow-empty", "-qm", "remote head")
    expected = git(root, "rev-parse", "HEAD")
    git(root, "push", "origin", "topic")
    git(root, "commit", "--allow-empty", "-qm", "qualified candidate")
    candidate = git(root, "rev-parse", "HEAD")
    git(root, "branch", "other", initial)
    marker = tmp_path / "hook-input"
    hook = root / ".git" / "hooks" / "pre-push"
    hook.write_text(f'#!/bin/sh\ncat > "{marker.as_posix()}"\n', encoding="utf-8")
    hook.chmod(0o700)
    original = pr_transport_fence.Head.guard
    moved = False

    @contextmanager
    def move(self: pr_transport_fence.Head, **kwargs: Any) -> Iterator[None]:
        nonlocal moved
        with original(self, **kwargs):
            if not moved:
                moved = True
                if movement == "reset":
                    git(root, "reset", "--hard", initial)
                else:
                    git(root, "switch", "other")
            yield

    monkeypatch.setattr(pr_transport_fence.Head, "guard", move)
    result = git_ops.push(
        "origin", "HEAD", cwd=root, force_with_lease_expect=expected,
    )
    assert result.ok, result.stderr
    assert moved
    assert result.published_head_sha == candidate
    assert git(remote, "rev-parse", "refs/heads/topic") == candidate
    assert marker.read_text(encoding="utf-8").split()[:3] == [
        "HEAD", candidate, "refs/heads/topic",
    ]
    assert git_ops.git("show-ref", "--verify", "refs/heads/other", cwd=remote, check=False).returncode


@pytest.mark.parametrize("url,identity", [
    ("ssh://git@ssh.github.com:443/alice/source.git", "github.com/alice/source"),
    ("ssh://git@ssh.github.com:22/alice/source.git", "ssh.github.com/alice/source"),
    ("ssh://git@ssh.github.com:444/alice/source.git", "ssh.github.com:444/alice/source"),
    ("ssh://git@elsewhere.test:443/alice/source.git", "elsewhere.test:443/alice/source"),
])
@pytest.mark.parametrize("endpoint", ["github.com", "github.com:443"])
def test_public_github_ssh_alias_shares_only_the_supported_head_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, identity: str,
    endpoint: str,
) -> None:
    from agent_worktrees.pr_provider_admission import creation_guard

    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    assert pr_publish._identity(url, str(tmp_path)) == identity
    provider = type("Provider", (), {"authority_endpoint": lambda self, base: endpoint})()
    scope = PRScope(repo="upstream/repo", head="alice:topic", base="main", title="Change",
                    head_identity=url.removeprefix("ssh://git@").removesuffix(".git"))
    if identity.startswith("github.com/"):
        with creation_guard(provider, scope, token=None):
            with pytest.raises(TimeoutError), pr_transport_fence.Head(identity, "topic").guard():
                pytest.fail("The supported SSH alias escaped the provider's source fence")
    else:
        with pytest.raises(ProviderError):
            with creation_guard(provider, scope, token=None):
                pytest.fail("An unrelated SSH destination was admitted")


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("subdirectory", [False, True])
def test_mark_complete_missing_owner_refuses_nonblocking_during_source_publication(
    native_source_owners: NativeOwners, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    explicit: bool, subdirectory: bool,
) -> None:
    from agent_worktrees import finalize_cli, pr_authority, worktree_identity

    config, caller, _owner, _workspace = native_source_owners
    workspace = tmp_path / "external"
    git_ops.git("worktree", "add", "-b", "completion-admission", str(workspace), "HEAD",
                cwd=caller.worktree_path)
    directory = workspace / "nested" if subdirectory else workspace
    directory.mkdir(exist_ok=True)
    monkeypatch.chdir(directory)
    monkeypatch.setattr(cfg, "active_project", lambda: config.repo_name)
    monkeypatch.setattr(cfg, "project_name", lambda: config.repo_name)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id_from_cwd", _infer_worktree_id_from_cwd)
    args = argparse.Namespace(worktree_id="external" if explicit else None, title="Complete", title_only=False)
    with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace) as recheck:
        recheck()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(finalize_cli.cmd_mark_complete, args).result(timeout=2) == 1
        assert not (cfg.tracking_dir() / "external.yaml").exists()
    assert finalize_cli.cmd_mark_complete(args) == 0
    record = tracking.load_record(cfg.tracking_dir() / "external.yaml")
    assert record.status == "complete" and record.title == "Complete"
    assert Path(record.worktree_path) == workspace.resolve()


def test_complete_owner_lookup_scales_past_retained_history_and_invalidates_changes(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_authority

    config, caller, owner, workspace = native_source_owners
    for index in range(4097):
        (cfg.tracking_dir() / f"retained-{index}.yaml").write_text(
            f"worktree_id: retained-{index}\nbranch: old\nrepo: ext\nstatus: archived\n"
            f"worktree_path: {workspace.parent.as_posix()}/old-{index}\n",
            encoding="utf-8",
        )
    original = tracking.load_record
    loaded: list[Path] = []

    def load(path: Path, **kwargs: Any) -> tracking.WorktreeRecord:
        loaded.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(tracking, "load_record", load)
    for _ in range(2):
        with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace) as recheck:
            recheck()
    assert not any(path.name.startswith("retained-") for path in loaded)
    # Model a legacy writer that does not maintain the disposable routing cache.
    duplicate = yaml.safe_load(owner.yaml_path.read_text(encoding="utf-8"))
    duplicate.update(worktree_id="retained-4096", status="finalized")
    (cfg.tracking_dir() / "retained-4096.yaml").write_text(yaml.safe_dump(duplicate), encoding="utf-8")
    with pytest.raises(ValueError, match="closing or finalized|receiving"):
        with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace):
            pytest.fail("A changed retained entry hid another source owner")


@pytest.mark.parametrize("workspace", [None, "reachable-source"])
@pytest.mark.parametrize("explicit_source", [False, True])
def test_github_foreign_fork_creation_uses_explicit_actual_source_repository(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, workspace: str | None,
    explicit_source: bool,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_checks, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from agent_worktrees.providers.github import GitHubProvider

    config, caller, _owner, _workspace = native_source_owners
    repo = replace(config.default_repo, pr=cfg.PRConfig(enabled=True, provider="github"))
    resolution = pr_config.ForeignRepoResolution(repo, "owner/other-repo")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    seen: list[tuple[str, str]] = []
    created: list[PRScope] = []
    expected = git(_workspace, "rev-parse", "HEAD")
    if workspace:
        workspace = str(_workspace)
        git(_workspace, "switch", "-c", "topic")
        git(_workspace, "remote", "set-url", "--push", "origin",
            "https://github.com/contributor/renamed-source.git")

    class GitHub(GitHubProvider):
        def resolve_head_repository(self, repo: str, owner: str, *, api_base: str = "",
                                    token: str | None = None) -> str:
            assert (repo, owner) == ("owner/other-repo", "contributor")
            return "contributor/renamed-source"

        def find_pull_by_source(self, repo: str, source: str, branch: str, *, api_base: str = "",
                                token: str | None = None) -> PullResult | None:
            assert (repo, source, branch) == ("owner/other-repo", "contributor/renamed-source", "topic")
            return None

        def observe_branch(self, repo: str, branch: str, *, api_base: str = "",
                           token: str | None = None) -> dict[str, str]:
            seen.append((repo, branch))
            return {"repository": repo, "branch": branch, "head_sha": expected}

        @pr_transport_fence.provider_creation
        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            assert scope.head == "contributor:topic"
            assert scope.head_identity == "github.com/contributor/renamed-source"
            created.append(scope)
            return PullResult(url="https://github.com/owner/other-repo/pull/7", number=7)

    backend = GitHub()
    monkeypatch.setattr(providers, "get_provider", lambda *_: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)

    real_qualify = pr_foreign_checks.qualify

    def qualify(root: str, repository: str, branch: str, expected_head: str,
                *args: Any, **kwargs: Any) -> dict[str, Any]:
        assert branch == "topic"
        assert kwargs["source_repository"] == "contributor/renamed-source"
        return real_qualify(root, repository, branch, expected_head, *args, **kwargs)

    monkeypatch.setattr(pr_foreign_checks, "qualify", qualify)
    result = pr_foreign_create.create_foreign_pr_from_branch(
        caller.worktree_id, config, target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/renamed-source" if explicit_source else None, title="Fix",
        source_workspace=workspace, expected_head=expected if workspace else None,
    )
    assert result.get("pr_opened") and result["claimed"], result
    assert len(created) == 1
    if workspace:
        source, target = ("contributor/renamed-source", "topic"), ("owner/other-repo", repo.default_branch)
        assert seen == [source, target, source, target, source, source, target]
        assert created[0].expected_head_sha == expected
        assert result["publication_checks"]["outcome"] == "verified-after-create"


@pytest.mark.parametrize("url,accepted", [
    ("ssh://git@ssh.github.com:443/contributor/renamed-source.git", True),
    ("https://github.com/contributor/renamed-source.git", True),
    ("ssh://git@ssh.github.com:444/contributor/renamed-source.git", False),
    ("https://elsewhere.test/contributor/renamed-source.git", False),
])
def test_foreign_fork_workspace_resolves_only_its_actual_source(
    tmp_path: Path, url: str, accepted: bool,
) -> None:
    from agent_worktrees.pr_foreign_checks import source_binding
    from agent_worktrees.providers.github import GitHubProvider

    root, _remote = repository(tmp_path)
    git(root, "remote", "set-url", "origin", url)
    repo = cfg.RepoConfig(anchor=str(root), worktree_root=str(tmp_path), remote="origin",
                         pr=cfg.PRConfig(provider="github"))
    if accepted:
        assert source_binding(
            "upstream/different-name", "contributor:topic", None, str(root), repo, GitHubProvider(),
        ) == ("contributor/renamed-source", "topic")
    else:
        with pytest.raises(ValueError, match="actual fork|exact fork"):
            source_binding(
                "upstream/different-name", "contributor:topic", None, str(root), repo, GitHubProvider(),
            )
