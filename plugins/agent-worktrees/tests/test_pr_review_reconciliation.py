"""Creation retains exact source ownership and uncertain provider receipts."""

import argparse
from collections.abc import Callable
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from agent_worktrees import config as cfg, tracking, tracking_lifecycle
from agent_worktrees.providers import github
from agent_worktrees.providers.base import PRScope, ProviderError, PullResult
from test_parallel_publication import git
from test_pr_fork_source import _pull
from test_pr_foreign_create import _tracking_setup as _tracking_setup
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
@pytest.mark.parametrize("fork_origin", [False, True])
def test_foreign_reuse_qualifies_the_real_workspace_before_claiming(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, drift: str, fork_origin: bool,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers

    config, caller, _owner, workspace = native_source_owners
    git(workspace, "switch", "-c", "topic")
    if fork_origin:
        git(workspace, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "commit", "--allow-empty", "-m", "New target candidate")
    expected = git(workspace, "rev-parse", "HEAD")
    git(workspace, "remote", "set-url", "origin", "https://github.com/" + (
        "contributor/source.git" if fork_origin else "upstream/target.git"
    ))
    if fork_origin:
        git(workspace, "remote", "add", "upstream", "https://github.com/upstream/target.git")
        git(workspace, "update-ref", f"refs/remotes/upstream/{config.default_repo.default_branch}", expected)
    git(workspace, "remote", "set-url", "--push", "origin",
        "https://github.com/contributor/" + ("another-copy" if drift == "destination" else "source") + ".git")
    if drift == "branch":
        git(workspace, "switch", "-c", "elsewhere")
    elif drift == "head":
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            monkeypatch.delenv(name, raising=False)
        git(workspace, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "commit", "--allow-empty", "-m", "Unreviewed candidate")
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


@pytest.mark.parametrize("redirect", [False, True])
@pytest.mark.parametrize("trailing_space", [False, True])
def test_missing_completion_owner_uses_the_cwd_repository_not_inherited_git_context(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
    redirect: bool, trailing_space: bool,
) -> None:
    from agent_worktrees import completion_tracking

    config, caller, _owner, workspace = native_source_owners
    monkeypatch.setattr(cfg, "project_name", lambda: config.repo_name)
    if trailing_space:
        if os.name == "nt":
            pytest.skip("Win32 paths do not preserve a trailing-space directory component")
        destination = workspace.with_name(workspace.name + " ")
        workspace.rename(destination)
        workspace = destination
    git(workspace, "switch", "-c", "completion-topic")
    monkeypatch.chdir(workspace)
    if redirect:
        monkeypatch.setenv("GIT_DIR", git(Path(caller.worktree_path), "rev-parse", "--absolute-git-dir"))
        monkeypatch.setenv("GIT_WORK_TREE", caller.worktree_path)
    destination = caller.yaml_path.parent / "completion-owner.yaml"
    completion_tracking.update(destination, "completion-owner", title=None, title_only=False)
    loaded = tracking.load_record(destination)
    assert Path(loaded.worktree_path).resolve() == workspace.resolve()
    assert loaded.branch == "completion-topic" and loaded.status == "complete"


def test_numberless_healing_never_replaces_a_retained_unverified_provider_identity(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_reconcile, providers

    config, caller, _owner, _workspace = native_source_owners
    caller.prs = [tracking.PRRecord(
        pr_id="uncertain", branch="pr/topic", state="creating", repo="upstream/target",
        creation={"provider_outcome": "unknown", "provider_number": "7",
                  "provider_url": "https://github.com/upstream/target/pull/7"},
    )]
    tracking.save_record(caller)
    before = caller.yaml_path.read_bytes()

    def find(*_args: Any, **_kwargs: Any) -> PullResult:
        pytest.fail("A branch lookup cannot reconcile a retained, unverified provider receipt")

    monkeypatch.setattr(providers, "get_provider", lambda *_: SimpleNamespace(find_pull_by_head=find))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    assert not pr_reconcile.heal_numberless_active_pr(caller, config)
    assert caller.yaml_path.read_bytes() == before and caller.prs[0].number is None


@pytest.mark.parametrize("binding", ["unrelated", "stale", "excluded", "mapped"])
def test_target_fetch_binding_is_not_inferred_from_an_equal_object_or_source_push(
    native_source_owners: NativeOwners, binding: str,
) -> None:
    from agent_worktrees import pr_foreign_checks, pr_publication_checks

    config, _caller, _owner, workspace = native_source_owners
    repo = replace(config.default_repo, pr=cfg.PRConfig(enabled=True, provider="github"))
    expected = git(workspace, "rev-parse", "HEAD")
    git(workspace, "remote", "set-url", "origin", "https://github.com/" + (
        "unrelated/target.git" if binding == "unrelated" else "upstream/target.git"
    ))
    git(workspace, "remote", "set-url", "--push", "origin", "https://github.com/contributor/source.git")
    branch = config.default_repo.default_branch
    if binding == "excluded":
        git(workspace, "config", "--add", "remote.origin.fetch", "^refs/heads/" + branch)
    elif binding == "mapped":
        git(workspace, "config", "--replace-all", "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/base-cache/*")
        git(workspace, "update-ref", "refs/remotes/base-cache/" + branch, expected)
    if binding == "mapped":
        result = pr_foreign_checks.target_ref(workspace, "upstream/target", branch, repo, expected)
        assert result["ref"] == "refs/remotes/base-cache/" + branch
    else:
        with pytest.raises(pr_publication_checks.CheckError, match="fetched ref"):
            pr_foreign_checks.target_ref(
                workspace, "upstream/target", branch, repo,
                "b" * 40 if binding == "stale" else expected,
            )


@pytest.mark.parametrize("retry_new", [False, True])
def test_foreign_unverified_creation_is_durable_unclaimed_and_blocks_retry(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, retry_new: bool,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from agent_worktrees.providers.github_pull_source import CreatedSourceMismatch
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    path = ledger / f"{wid}.yaml"
    before = path.read_bytes()
    resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)(provider="github")
    config = cast(Callable[..., cfg.Config], _config)()
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    calls: list[PRScope] = []

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            calls.append(scope)
            raise CreatedSourceMismatch(PullResult(
                number=7, url="https://github.com/owner/other-repo/pull/7",
            ))

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    options: dict[str, Any] = dict(
        target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/source", title="Fix",
    )
    first = pr_foreign_create.create_foreign_pr_from_branch(wid, config, **options)
    second = pr_foreign_create.create_foreign_pr_from_branch(wid, config, new=retry_new, **options)
    for result in (first, second):
        assert result["publication_state"] == "unknown" and not result.get("claimed")
        assert result["number"] == 7 and result["url"].endswith("/pull/7")
        assert "#7" in result["error"] and "unknown" in result["error"].lower()
    assert len(calls) == 1 and path.read_bytes() == before
    receipts = list(ledger.glob("*.foreign-pr-receipts/*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["provider_number"] == 7 and receipt["phase"] == "unknown"
    assert receipt["binding"]["source"] == "contributor/source"


@pytest.mark.parametrize("conflict", ["owner", "receipt-revision", "receipt-write", "transport"])
def test_foreign_attempt_retains_uncertainty_when_the_owner_or_receipt_changes(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, conflict: str,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    path = ledger / f"{wid}.yaml"
    resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)(provider="github")
    config = cast(Callable[..., cfg.Config], _config)()
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    calls: list[PRScope] = []
    unchanged: list[bytes] = []
    atomic_write = tracking._atomic_write

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            calls.append(scope)
            receipt = next(ledger.glob("*.foreign-pr-receipts/*.json"))
            if conflict == "owner":
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                raw["creation_nonce"] = "replacement-incarnation"
                path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            elif conflict == "receipt-revision":
                value = json.loads(receipt.read_text(encoding="utf-8"))
                value["revision"] += 1
                receipt.write_text(json.dumps(value), encoding="utf-8")
            elif conflict == "receipt-write":
                def refuse(destination: Path, content: str | bytes) -> None:
                    if destination == receipt:
                        raise OSError("Fixture receipt persistence unavailable")
                    atomic_write(destination, content)
                monkeypatch.setattr(tracking, "_atomic_write", refuse)
            unchanged.append(path.read_bytes())
            if conflict == "transport":
                raise ProviderError("Fixture remote outcome unavailable")
            return PullResult(number=7, url="https://github.com/owner/other-repo/pull/7", state="open")

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    options: dict[str, Any] = dict(
        target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/source", title="Fix",
    )
    first = pr_foreign_create.create_foreign_pr_from_branch(wid, config, **options)
    second = pr_foreign_create.create_foreign_pr_from_branch(wid, config, new=True, **options)
    assert first["publication_state"] == second["publication_state"] == "unknown"
    assert not first.get("claimed") and not second.get("claimed") and len(calls) == 1
    assert path.read_bytes() == unchanged[0] and not tracking.load_record(path).resources
    if conflict != "transport":
        assert first.get("pr_receipt_tracking_error") and first["number"] == 7
    receipt = json.loads(next(ledger.glob("*.foreign-pr-receipts/*.json")).read_text(encoding="utf-8"))
    assert receipt["phase"] in ("publishing", "unknown")


@pytest.mark.parametrize("terminal", ["closed", "merged"])
def test_foreign_new_work_ignores_the_expected_head_of_a_terminal_historical_pr(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, terminal: str,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_checks, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    _ledger, wid = _tracking_setup
    resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)(provider="github")
    config = cast(Callable[..., cfg.Config], _config)()
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    monkeypatch.setattr(pr_foreign_checks, "qualify", lambda *a, **k: {"head": "b" * 40})
    monkeypatch.setattr(pr_foreign_checks, "after_create", lambda *a, **k: {"ok": True})
    calls: list[PRScope] = []

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> PullResult:
            return PullResult(
                number=3, state="closed", merged=terminal == "merged", head_sha="a" * 40,
                head_ref="topic", head_url="https://github.com/contributor/source.git",
            )

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            calls.append(scope)
            return PullResult(number=7, url="https://github.com/owner/other-repo/pull/7", state="open")

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    result = pr_foreign_create.create_foreign_pr_from_branch(
        wid, config, target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/source", source_workspace="reachable-source",
        expected_head="b" * 40, title="Fix",
    )
    assert result.get("number") == 7 and result.get("claimed") and len(calls) == 1, result


@pytest.mark.parametrize("use_json", [False, True])
@pytest.mark.parametrize("unverified", [False, True])
def test_create_cli_distinguishes_unverified_receipts_from_created_pr_authority(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], use_json: bool, unverified: bool,
) -> None:
    from agent_worktrees import __main__ as main
    from agent_worktrees import finalize_cli, pr_ops

    config, caller, _owner, _workspace = native_source_owners
    result: dict[str, Any] = {
        "success": not unverified, "pr_opened": True, "number": 7,
        "url": "https://forge.example.test/upstream/target/pull/7",
        "repo": "upstream/target", "provider": config.default_repo.pr.provider,
        "branch": "pr/topic", "remote": "origin", "base_sha": "b" * 40, "head_sha": "a" * 40,
        "state": "unknown" if unverified else "open",
    }
    if unverified:
        result.update(
            publication_state="unknown", pr_identity_verified=False,
            error="Created PR identity is unverified; reconcile the known receipt before retrying.",
            pr_open_error="Created PR identity is unverified.",
        )
    monkeypatch.setattr(pr_ops, "create_pr", lambda *a, **k: dict(result))
    args = main.build_parser().parse_args(
        ["create-pr", caller.worktree_id, "--title", "Fix"] + (["--json"] if use_json else []),
    )
    assert finalize_cli.cmd_create_pr(args) == (1 if unverified else 0)
    captured = capfd.readouterr()
    if use_json:
        data = json.loads(captured.out)
        assert data["number"] == 7
        if unverified:
            assert "pr" not in data and data["publication_state"] == "unknown"
        else:
            assert data["pr"]["number"] == 7
    elif unverified:
        text = (captured.out + captured.err).lower()
        assert "unverified" in text and "unknown" in text and "#7" in text
    else:
        assert "Opened PR #7" in captured.out


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


@pytest.mark.parametrize("owner_changed", [False, True])
def test_ordinary_creation_retains_unknown_receipt_without_claiming_or_recreating(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, owner_changed: bool,
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
    replacement: bytes | None = None

    def create(scope: PRScope, **kwargs: Any) -> PullResult:
        nonlocal replacement
        calls.append(scope)
        if owner_changed:
            raw = yaml.safe_load(caller.yaml_path.read_text(encoding="utf-8"))
            raw["creation_nonce"] = "replacement-incarnation"
            caller.yaml_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            replacement = caller.yaml_path.read_bytes()
        raise CreatedSourceMismatch(PullResult(number=7, url="https://forge.example.test/upstream/target/pull/7"))

    monkeypatch.setattr(providers, "get_provider", lambda *_: SimpleNamespace(create_pull=create))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    for _ in range(1 if owner_changed else 2):
        result: dict[str, Any] = {"success": True, "repo": target.repo, "branch": target.branch,
                                  "remote": "origin", "base_sha": "b" * 40}
        pr_ops._open_via_provider(
            result, config, caller, target, "Fix", "", caller.worktree_id, "a" * 40,
            prcfg=config.default_repo.pr, draft=True,
        )
        assert result["pr_opened"] and result["number"] == 7, result
        assert result["publication_state"] == "unknown" and not result.get("claimed")
        assert not result["success"] and result["error"] and result["state"] == "unknown"
        current = tracking.load_record(caller.yaml_path)
        if owner_changed:
            assert result.get("pr_receipt_tracking_error"), result
            assert replacement is not None and caller.yaml_path.read_bytes() == replacement
            assert current.creation_nonce == "replacement-incarnation"
        else:
            assert current.prs[0].creation["provider_number"] == "7"
        assert current.prs[0].number is None and current.prs[0].state == "creating"
        assert current.resources == before
    assert len(calls) == 1


@pytest.mark.parametrize("other_project", [False, True])
@pytest.mark.parametrize("retry_new", [False, True])
def test_foreign_pending_source_excludes_other_owners_and_bases(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, tmp_path: Path,
    other_project: bool, retry_new: bool,
) -> None:
    from agent_worktrees import installer, pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    other = tmp_path / "other-tracking" if other_project else ledger
    other.mkdir(exist_ok=True)
    second_id = "another-owner"
    tracking_lifecycle.create_new_record(
        second_id, "worktree/another", str(tmp_path / "another"),
        "another-project" if other_project else "caller-repo", "test", "linux", other,
    )
    project = "another-project" if other_project else "caller-repo"
    registry = tmp_path / "projects.yaml"
    registry.write_text(yaml.safe_dump({"projects": {"caller-repo": {}, project: {}}}), encoding="utf-8")
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: registry)
    active_project = "caller-repo"
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: (
        other if (name or active_project) == "another-project" else ledger
    ))
    resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)(provider="github")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    calls: list[PRScope] = []

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            calls.append(scope)
            raise ProviderError("Transport ended without a provider result")

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    config = cast(Callable[..., cfg.Config], _config)()
    options: dict[str, Any] = dict(
        target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/source", title="Fix",
    )
    first = pr_foreign_create.create_foreign_pr_from_branch(wid, config, **options)
    active_project = project
    second = pr_foreign_create.create_foreign_pr_from_branch(
        second_id, cast(Callable[..., cfg.Config], _config)(project),
        base="different-base", new=retry_new, **options,
    )
    assert first["publication_state"] == second["publication_state"] == "unknown"
    assert not second.get("claimed") and len(calls) == 1
    assert not list(other.glob(f"{second_id}.foreign-pr-receipts/*.json"))


def test_foreign_pending_default_port_alias_blocks_a_legacy_receipt_key(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution
    import hashlib

    ledger, wid = _tracking_setup
    resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)(provider="github")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    calls: list[PRScope] = []

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            calls.append(scope)
            raise ProviderError("Unknown provider outcome")

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    config = cast(Callable[..., cfg.Config], _config)()
    options: dict[str, Any] = dict(target_repo="owner/other-repo", from_branch="contributor:topic",
                                   source_repository="contributor/source", title="Fix")
    pr_foreign_create.create_foreign_pr_from_branch(wid, config, **options)
    path = next(ledger.glob("*.foreign-pr-receipts/*.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["binding"]["endpoint"] = "github.com:443"
    legacy_key = hashlib.sha256(json.dumps(value["binding"], sort_keys=True).encode()).hexdigest()
    legacy_path = path.with_name(legacy_key + ".json")
    path.rename(legacy_path)
    legacy_path.write_text(json.dumps(value), encoding="utf-8")
    before = legacy_path.read_bytes()
    result = pr_foreign_create.create_foreign_pr_from_branch(wid, config, new=True, **options)
    assert result["publication_state"] == "unknown" and len(calls) == 1, result
    assert legacy_path.read_bytes() == before and not path.exists()


@pytest.mark.parametrize("verified", [False, True])
def test_foreign_receipts_protect_owner_retirement_and_retained_metadata(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, verified: bool,
) -> None:
    from agent_worktrees import pr_foreign_receipts, retirement_safety
    from test_pr_foreign_create import _config

    ledger, wid = _tracking_setup
    record = tracking.load_record(ledger / f"{wid}.yaml")
    attempt = pr_foreign_receipts.Attempt(
        cast(Callable[..., cfg.Config], _config)(), record.yaml_path, record,
        provider="github", endpoint="github.com", target="owner/other-repo",
        source="contributor/source", branch="topic",
    )
    attempt.reserve("dev", None)
    for finish in (False, True):
        if finish:
            attempt.finish(PullResult(number=7, url="https://github.com/owner/other-repo/pull/7"),
                           verified=verified)
        fresh = tracking.load_record(record.yaml_path)
        assert retirement_safety.retains_artifact_metadata(fresh)
        blocker = retirement_safety.retirement_blocker(fresh)
        if verified and finish:
            assert blocker is None
            assert tracking.retire_record(fresh, ledger)
            assert record.yaml_path.is_file() and attempt.path.is_file()
        else:
            assert blocker is not None and blocker.bucket == "unknown-publication"
            before = record.yaml_path.read_bytes()
            with pytest.raises(ValueError, match="Foreign PR"):
                tracking.retire_record(fresh, ledger)
            assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("stage", ["reservation", "unverified", "verified", "transport"])
def test_ordinary_prepost_hold_survives_failed_provider_receipt_persistence(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    from agent_worktrees import pr_ops, providers
    from agent_worktrees.providers.github_pull_source import CreatedSourceMismatch

    config, caller, _owner, _workspace = native_source_owners
    target = tracking.PRRecord(pr_id="pending", branch="pr/topic", repo="upstream/target",
                               provider=config.default_repo.pr.provider, state="creating")
    caller.prs = [target]
    tracking.save_record(caller)
    atomic_write = tracking._atomic_write
    unavailable = stage == "reservation"
    calls: list[PRScope] = []

    def write(path: Path, content: str | bytes) -> None:
        if path == caller.yaml_path and unavailable:
            raise OSError("Fixture owner receipt persistence unavailable")
        atomic_write(path, content)

    def create(scope: PRScope, **_kwargs: Any) -> PullResult:
        nonlocal unavailable
        calls.append(scope)
        unavailable = True
        pull = PullResult(number=7, url="https://forge.example.test/upstream/target/pull/7", state="open")
        if stage == "unverified":
            raise CreatedSourceMismatch(pull)
        if stage == "transport":
            raise ProviderError("Unknown transport result")
        return pull

    monkeypatch.setattr(tracking, "_atomic_write", write)
    monkeypatch.setattr(providers, "get_provider", lambda *_: SimpleNamespace(create_pull=create))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    for _ in range(2):
        loaded = tracking.load_record(caller.yaml_path)
        result: dict[str, Any] = {"success": True, "repo": target.repo, "branch": target.branch,
                                  "remote": "origin", "base_sha": "b" * 40}
        pr_ops._open_via_provider(
            result, config, loaded, loaded.prs[0], "Fix", "", caller.worktree_id, "a" * 40,
            prcfg=config.default_repo.pr, draft=True,
        )
        assert not result.get("claimed") and result.get("pr_open_error"), result
    assert len(calls) == (0 if stage == "reservation" else 1)
    if stage != "reservation":
        retained = tracking.load_record(caller.yaml_path).prs[0]
        assert retained.number is None and retained.creation["provider_outcome"] in ("publishing", "unknown")


@pytest.mark.parametrize("managed", [False, True])
def test_ordinary_success_settles_the_hold_and_reuses_the_selected_provider_for_review(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, managed: bool,
) -> None:
    from agent_worktrees import pr_ops, providers

    config, caller, _owner, _workspace = native_source_owners
    target = tracking.PRRecord(pr_id="pending", branch="pr/topic", repo="upstream/target",
                               provider=config.default_repo.pr.provider, state="creating")
    caller.prs = [target]
    tracking.save_record(caller)
    review_calls: list[bool] = []

    def create(scope: PRScope, **kwargs: Any) -> PullResult:
        assert kwargs["token"] == "selected-credential"
        if managed:
            assert tracking.load_record(caller.yaml_path).prs[0].creation["provider_outcome"] == "publishing"
        return PullResult(number=7, url="https://forge.example.test/upstream/target/pull/7",
                          state="open", head_sha="a" * 40)

    backend = SimpleNamespace(create_pull=create)

    def review(flow: Any, selected: Any, repo: str, number: int | None, **kwargs: Any) -> str:
        assert selected is backend and repo == "upstream/target" and number == 7
        assert kwargs["token"] == "selected-credential" and kwargs["expected_head_sha"] == "a" * 40
        review_calls.append(True)
        return "Review eligibility inspected"

    monkeypatch.setattr(providers, "get_provider", lambda *_: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: "selected-credential")
    monkeypatch.setattr(pr_ops, "self_merge_bypass_note", review)
    result: dict[str, Any] = {"success": True, "repo": target.repo, "branch": target.branch,
                              "remote": "origin", "base_sha": "b" * 40}
    pr_ops._open_via_provider(
        result, config, caller if managed else None, target, "Fix", "", caller.worktree_id,
        "a" * 40, prcfg=config.default_repo.pr,
    )
    assert result["success"] and result["pr_opened"] and result["number"] == 7, result
    assert result["self_merge_note"] == "Review eligibility inspected" and review_calls == [True]
    if managed:
        fresh = tracking.load_record(caller.yaml_path)
        assert fresh.prs[0].number == 7 and fresh.prs[0].creation["provider_outcome"] == "verified"
        assert any(claim.kind == "pr" and claim.is_live for claim in fresh.resources)


def test_foreign_reservation_is_atomic_across_real_project_processes(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import sys
    import time
    from agent_procutil.owned_process import run_caller_owned

    for project in ("first", "second"):
        ledger = tmp_path / project / "worktrees"
        ledger.mkdir(parents=True)
        tracking_lifecycle.create_new_record(
            project, "worktree/" + project, str(tmp_path / project / "checkout"),
            project, "test", "linux", ledger,
        )
    (tmp_path / "projects.yaml").write_text(
        yaml.safe_dump({"projects": {"first": {}, "second": {}}}), encoding="utf-8",
    )
    code = """
import sys, time
from pathlib import Path
from machine_transport import detect_platform
from agent_worktrees import config as cfg, installer, pr_foreign_receipts, tracking
root, project = Path(sys.argv[1]), sys.argv[2]
cfg.install_dir = lambda: root / 'installation'
cfg.tracking_dir = lambda name=None: root / (name or project) / 'worktrees'
installer.projects_yaml_path = lambda: root / 'projects.yaml'
config = cfg.Config(srcroot=str(root), machine='test', platform=detect_platform(),
repo_name=project, repos={project: cfg.RepoConfig(
    anchor=str(root / project / 'anchor'), worktree_root=str(root / project / 'worktrees'),
)})
record = tracking.load_record(cfg.tracking_dir() / (project + '.yaml'))
attempt = pr_foreign_receipts.Attempt(
    config, record.yaml_path, record, provider='github', endpoint='github.com',
    target='upstream/target', source='contributor/source', branch='topic',
)
assert attempt.pending() is None
(root / (project + '.ready')).write_text('ready')
deadline = time.monotonic() + 10
while not (root / 'go').exists():
    assert time.monotonic() < deadline
    time.sleep(0.01)
print('reserved' if attempt.reserve(project + '-base', None) is None else 'blocked')
"""

    def run(project: str) -> subprocess.CompletedProcess[str]:
        return run_caller_owned(
            [sys.executable, "-c", code, str(tmp_path), project], timeout=15,
            max_stdout_bytes=4096, max_stderr_bytes=4096,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run, project) for project in ("first", "second")]
        try:
            deadline = time.monotonic() + 10
            while not all((tmp_path / (project + ".ready")).exists() for project in ("first", "second")):
                assert time.monotonic() < deadline, "Both real publishers must reach pre-reservation admission"
                time.sleep(0.01)
            (tmp_path / "go").write_text("go", encoding="utf-8")
        finally:
            results = [job.result(timeout=20) for job in jobs]
    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    assert sorted(result.stdout.strip() for result in results) == ["blocked", "reserved"]
    assert len(list(tmp_path.glob("*/worktrees/*.foreign-pr-receipts/*.json"))) == 1
