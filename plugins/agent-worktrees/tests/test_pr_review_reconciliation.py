"""Creation retains exact source ownership and uncertain provider receipts."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import config as cfg, tracking
from agent_worktrees.providers import github
from agent_worktrees.providers.base import PRScope, ProviderError, PullResult
from test_parallel_publication import git
from test_pr_fork_source import _pull
from test_pr_source_binding import NativeOwners
from test_pr_transport_fence import native_source_owners as native_source_owners
from test_status_write import status_env as status_env


@pytest.mark.parametrize("title_only", [False, True])
def test_completion_explicit_empty_title_clears_the_assertion_and_records_history(
    status_env: Path, title_only: bool,
) -> None:
    from agent_worktrees import disposition_history, finalize_cli

    record = tracking.load_record(status_env)
    tracking.set_disposition(record, title="Previous asserted title", tracking_path=status_env.parent)
    assert tracking.load_record(status_env).title == "Previous asserted title"
    assert finalize_cli.cmd_mark_complete(argparse.Namespace(
        worktree_id=None, title=" \t\n", title_only=title_only,
    )) == 0
    loaded = tracking.load_record(status_env)
    assert loaded.title is None and not loaded.title_asserted
    assert loaded.status == ("finalized" if title_only else "complete")
    history = disposition_history.read(loaded.worktree_id, tracking_path=status_env.parent)
    assert len(history) == 2 and history[-1]["title"] is None


@pytest.mark.parametrize("drift", ["none", "destination", "branch", "head"])
def test_foreign_reuse_qualifies_the_real_workspace_before_claiming(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers

    config, caller, _owner, workspace = native_source_owners
    git(workspace, "switch", "-c", "topic")
    expected = git(workspace, "rev-parse", "HEAD")
    git(workspace, "remote", "set-url", "--push", "origin",
        "https://github.com/contributor/" + ("another-copy" if drift == "destination" else "source") + ".git")
    if drift == "branch":
        git(workspace, "switch", "-c", "elsewhere")
    elif drift == "head":
        git(workspace, "commit", "--allow-empty", "-m", "Unreviewed candidate")
    repo = replace(config.default_repo, pr=cfg.PRConfig(enabled=True, provider="github"))
    resolution = pr_config.ForeignRepoResolution(repo, "upstream/target")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    found: list[str] = []

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, repository: str, source: str, branch: str, *,
                                api_base: str = "", token: str | None = None) -> PullResult:
            found.append(source)
            return PullResult(number=7, url="https://github.com/upstream/target/pull/7",
                              state="open", base_ref=repo.default_branch, head_ref="topic",
                              head_url="https://github.com/contributor/source.git", head_sha=expected)

        def observe_branch(self, repository: str, branch: str, *,
                           api_base: str = "", token: str | None = None) -> dict[str, str]:
            return {"repository": repository, "branch": branch, "head_sha": expected}

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            pytest.fail("A reused PR must never be recreated")

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    before = caller.yaml_path.read_bytes()
    result = pr_foreign_create.create_foreign_pr_from_branch(
        caller.worktree_id, config, target_repo="upstream/target",
        from_branch="contributor:topic", source_repository="contributor/source", title="Fix",
        source_workspace=str(workspace), expected_head=expected,
    )
    if drift == "none":
        assert result.get("claimed") and result.get("reused"), result
        assert found == ["contributor/source"]
    else:
        assert result.get("error") and not result.get("claimed"), result
        assert caller.yaml_path.read_bytes() == before and not found


@pytest.mark.parametrize("ambiguous", [False, True])
def test_target_owner_is_not_assumed_to_have_only_the_target_in_its_fork_network(
    monkeypatch: pytest.MonkeyPatch, ambiguous: bool,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        data: object = {"full_name": "upstream/target", "fork": False}
        if "--paginate" in argv:
            data = [[{"full_name": "upstream/another-copy", "fork": True,
                      "owner": {"login": "upstream"}}]] if ambiguous else [[]]
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(github, "run_cli", run)
    if ambiguous:
        with pytest.raises(ProviderError, match="ambiguous"):
            github.GitHubProvider().resolve_head_repository("upstream/target", "upstream")
    else:
        assert github.GitHubProvider().resolve_head_repository("upstream/target", "upstream") == "upstream/target"
    assert len(calls) == 2


@pytest.mark.parametrize("write", [False, True])
def test_publication_labels_pin_the_endpoint_and_every_selected_credential_alias(
    monkeypatch: pytest.MonkeyPatch, write: bool,
) -> None:
    aliases = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    for alias in aliases:
        monkeypatch.setenv(alias, "unrelated-credential")
    monkeypatch.setenv("GH_API_HOST", "unrelated.invalid")
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv[argv.index("--hostname") + 1] == "forge.example.test"
        assert any(value.startswith("https://forge.example.test:8443/")
                   and value.endswith("/repos/upstream/target/issues/7/labels") for value in argv)
        assert all(kwargs["env"].get(alias) == "selected-credential" for alias in aliases)
        return subprocess.CompletedProcess(argv, 0, json.dumps([[{"name": "review-needed"}]]), "")

    monkeypatch.setattr(github, "run_cli", run)
    backend = github.GitHubProvider()
    options = {"api_base": "https://forge.example.test:8443", "token": "selected-credential"}
    if write:
        backend.add_publication_labels("upstream/target", 7, ("review-needed",), **options)
    else:
        assert backend.get_publication_labels("upstream/target", 7, **options) == ("review-needed",)
    assert len(calls) == 1


@pytest.mark.parametrize("state", ["open", "closed"])
def test_deleted_historical_fork_does_not_hide_a_valid_open_source(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    deleted = _pull(3)
    deleted["state"], deleted["head"]["repo"] = state, None
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps([[deleted], [_pull()]]), "")

    monkeypatch.setattr(github, "run_cli", run)
    if state == "closed":
        result = github.GitHubProvider().find_pull_by_source(
            "upstream/target", "contributor/renamed-source", "topic", api_base="https://forge.example.test",
        )
        assert result and result.number == 7
    else:
        with pytest.raises(ProviderError):
            github.GitHubProvider().find_pull_by_source(
                "upstream/target", "contributor/renamed-source", "topic", api_base="https://forge.example.test",
            )
    assert len(calls) == 1


def test_ordinary_creation_retains_unknown_receipt_without_claiming_or_recreating(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_ops, providers
    from agent_worktrees.providers.github_pull_source import CreatedSourceMismatch

    config, caller, _owner, _workspace = native_source_owners
    target = tracking.PRRecord(pr_id="pending", branch="pr/topic", repo="upstream/target",
                               provider=config.default_repo.pr.provider, state="creating")
    caller.prs = [target]
    tracking.save_record(caller)
    caller = tracking.load_record(caller.yaml_path)
    target = caller.prs[0]
    before = list(caller.resources)
    calls: list[PRScope] = []

    def create(scope: PRScope, **kwargs: Any) -> PullResult:
        calls.append(scope)
        raise CreatedSourceMismatch(PullResult(number=7, url="https://forge.example.test/upstream/target/pull/7"))

    monkeypatch.setattr(providers, "get_provider", lambda *_: SimpleNamespace(create_pull=create))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    for _ in range(2):
        result: dict[str, Any] = {"success": True, "repo": target.repo, "branch": target.branch,
                                  "remote": "origin", "base_sha": "b" * 40}
        pr_ops._open_via_provider(
            result, config, caller, target, "Fix", "", caller.worktree_id, "a" * 40,
            prcfg=config.default_repo.pr, draft=True,
        )
        assert result["pr_opened"] and result["number"] == 7, result
        assert result["publication_state"] == "unknown" and not result.get("claimed")
        current = tracking.load_record(caller.yaml_path)
        assert current.prs[0].creation["provider_number"] == "7"
        assert current.prs[0].number is None and current.prs[0].state == "creating"
        assert current.resources == before
    assert len(calls) == 1
