"""Provider uncertainty and native deletion reach their real consumers."""

from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from typing import Any, cast

import pytest

from agent_worktrees import config as cfg, git_ops, tracking
from agent_worktrees.providers.base import PRScope, PullResult
from test_parallel_publication import git, repository
from test_pr_creation_review import _attempt
from test_pr_foreign_create import _tracking_setup as _tracking_setup
from test_pr_source_binding import NativeOwners
from test_pr_transport_fence import native_source_owners as native_source_owners


@pytest.mark.parametrize("status", ["complete", "completed", "orphaned"])
def test_ordinary_creation_cannot_verify_a_rejected_claim(
    native_source_owners: NativeOwners, status: tracking.WorktreeStatus,
) -> None:
    from agent_worktrees import pr_publication_state, pr_unverified_creation

    config, caller, _owner, _workspace = native_source_owners
    caller.kind, caller.status = "bridge", status
    caller.prs = [tracking.PRRecord(
        pr_id="pending", repo="owner/repo", branch="topic", provider="github",
        state="creating", creation={"provider_outcome": "publishing"},
    )]
    tracking.save_record(caller)
    caller = tracking.load_record(caller.yaml_path)
    target = caller.prs[0]
    pull = PullResult(number=7, url="https://github.com/owner/repo/pull/7")
    before = caller.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="claim|responsibility"):
        pr_publication_state.persist_pull(
            config, caller, target, pull, head_sha="a" * 40, marker_published=False, session="",
        )
    assert caller.yaml_path.read_bytes() == before
    result: dict[str, Any] = {}
    assert pr_unverified_creation.retain(result, config, caller, target, ValueError("Claim refused"), pull=pull)
    fresh = tracking.load_record(caller.yaml_path)
    assert fresh.prs[0].creation["provider_outcome"] == "unknown"
    assert result["number"] == 7 and not result["claimed"]
    assert not fresh.live_resources


@pytest.mark.parametrize("race", [False, True])
@pytest.mark.parametrize("custom_mapping", [False, True])
def test_successful_branch_deletion_updates_only_the_unchanged_tracking_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: bool, custom_mapping: bool,
) -> None:
    root, remote = repository(tmp_path)
    namespace = "mirror" if custom_mapping else "origin"
    if custom_mapping:
        git(root, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/mirror/*")
    tracked = f"refs/remotes/{namespace}/topic"
    assert git_ops.push("origin", "HEAD:refs/heads/topic", cwd=root).ok
    previous = git(root, "rev-parse", tracked)
    replacement = git(root, "rev-parse", "HEAD^{tree}")
    original = git_ops.git
    if race:
        replacement = git(
            root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "commit-tree", replacement, "-p", previous, "-m", "Concurrent tracking update",
        )

        def run(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args[:2] == ("update-ref", "-d"):
                original("update-ref", tracked, replacement, cwd=root)
            return original(*args, **kwargs)

        monkeypatch.setattr(git_ops, "git", run)
    result = git_ops.push("origin", ":refs/heads/topic", cwd=root)
    assert result.ok, result.stderr
    assert git_ops.git("show-ref", "--verify", "refs/heads/topic", cwd=remote, check=False).returncode
    observed = git_ops.git("rev-parse", "--verify", tracked, cwd=root, check=False)
    if race:
        assert observed.stdout.strip() == replacement
        assert "refresh failed" in result.stderr
    else:
        assert observed.returncode


@pytest.mark.parametrize("provider_name", ["gitea", "azure-devops"])
@pytest.mark.parametrize("defect", ["shape", "number-type", "number-value"])
def test_actual_provider_response_parsing_retains_the_unknown_attempt(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
    provider_name: str, defect: str,
) -> None:
    from agent_worktrees import pr_unverified_creation, providers
    from agent_worktrees.providers import azure_devops, gitea

    config, caller, _owner, _workspace = native_source_owners
    target = tracking.PRRecord(
        pr_id="pending", branch="topic", repo="owner/repo", provider=provider_name, state="creating",
    )
    caller.prs = [target]
    tracking.save_record(caller)
    field = "number" if provider_name == "gitea" else "pullRequestId"
    payload: object = [] if defect == "shape" else {field: [] if defect == "number-type" else "invalid"}
    calls: list[str] = []
    if provider_name == "gitea":
        provider: Any = gitea.GiteaProvider()

        def curl(method: str, *_args: Any, **_kwargs: Any) -> tuple[int, str]:
            calls.append(method)
            return 201, json.dumps(payload)

        monkeypatch.setattr(provider, "_curl", curl)
    else:
        provider = azure_devops.AzureDevOpsProvider()

        def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(" ".join(argv[:4]))
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        monkeypatch.setattr(azure_devops, "run_cli", run)
    monkeypatch.setattr(providers, "get_provider", lambda *_: provider)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: "fixture-token")
    policy = replace(config.default_repo.pr, provider=provider_name, api_base="https://forge.example.test")
    scope = PRScope(repo="owner/repo", head="topic", base="main", title="Fix", api_base=policy.api_base)
    result: dict[str, Any] = {}
    assert pr_unverified_creation.create(result, config, caller, target, policy, scope) is None
    assert len(calls) == 1
    assert result["publication_state"] == "unknown" and not result["claimed"]
    assert "reconcile" in result["pr_open_error"].lower() or "reconcile" in result.get("error", "").lower()
    assert tracking.load_record(caller.yaml_path).prs[0].creation["provider_outcome"] == "unknown"


@pytest.mark.parametrize("state", [git_ops.WorktreeState.COMPLETED, git_ops.WorktreeState.GONE])
@pytest.mark.parametrize("consumer", ["prune", "cleanup"])
def test_foreign_pending_receipt_is_not_advertised_as_cleanable(
    _tracking_setup: Any, state: git_ops.WorktreeState, consumer: str,
) -> None:
    from agent_worktrees import prune
    from test_pr_foreign_create import _config

    ledger, wid = _tracking_setup
    record = tracking.load_record(ledger / f"{wid}.yaml")
    attempt = _attempt(cast(Callable[..., cfg.Config], _config)(), record)
    assert attempt.reserve("main", None) is None
    record.status = "finalized"
    tracking.save_record(record)
    before = record.yaml_path.read_bytes()
    info = git_ops.WorktreeStateInfo(state=state)
    if consumer == "prune":
        verdict = prune.assess(record, info)
        assert not verdict.safe and verdict.category == "unknown-publication"
    else:
        disposition = prune.cleanup_disposition(record, info)
        assert not disposition.cleanable and disposition.bucket == "unknown-publication"
    assert record.yaml_path.read_bytes() == before
