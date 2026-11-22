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


@pytest.mark.parametrize("source", ["topic", "refs/heads/topic", "refs/tags/topic"])
@pytest.mark.parametrize("warn_ambiguous", [False, True])
def test_colliding_branch_and_tag_require_an_explicit_source(
    tmp_path: Path, source: str, warn_ambiguous: bool,
) -> None:
    root, remote = repository(tmp_path)
    git(root, "config", "core.warnAmbiguousRefs", str(warn_ambiguous).lower())
    tagged = git(root, "rev-parse", "HEAD")
    git(root, "tag", "topic")
    git(root, "switch", "-qc", "topic")
    git(root, "commit", "--allow-empty", "-qm", "branch differs from tag")
    branched = git(root, "rev-parse", "HEAD")
    native = git_ops.git(
        "push", "origin", f"{source}:refs/heads/native", cwd=root, check=False,
    )
    result = git_ops.push("origin", f"{source}:refs/heads/managed", cwd=root)
    if source == "topic":
        assert native.returncode
        assert not result.ok, result
        assert git_ops.git("show-ref", "--verify", "refs/heads/managed", cwd=remote, check=False).returncode
    else:
        assert not native.returncode, native.stderr
        assert result.ok, result.stderr
        expected = branched if source.startswith("refs/heads/") else tagged
        assert git(remote, "rev-parse", "refs/heads/managed") == expected


@pytest.mark.parametrize("custom_mapping", [False, True])
@pytest.mark.parametrize("initially_present", [False, True])
def test_deletion_retains_tracking_updates_completed_during_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    custom_mapping: bool, initially_present: bool,
) -> None:
    root, remote = repository(tmp_path)
    namespace = "mirror" if custom_mapping else "origin"
    if custom_mapping:
        git(root, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/mirror/*")
    tracked = f"refs/remotes/{namespace}/topic"
    assert git_ops.push("origin", "HEAD:refs/heads/topic", cwd=root).ok
    previous = git(root, "rev-parse", tracked)
    replacement = git(
        root, "commit-tree", git(root, "rev-parse", "HEAD^{tree}"),
        "-p", previous, "-m", "Newer tracking observation",
    )
    if not initially_present:
        git(root, "update-ref", "-d", tracked, previous)
    original = git_ops.git
    transported = False

    def run(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal transported
        result = original(*args, **kwargs)
        if "push" in args and ":refs/heads/topic" in args and result.returncode == 0:
            transported = True
            original("update-ref", tracked, replacement, cwd=root)
        return result

    monkeypatch.setattr(git_ops, "git", run)
    result = git_ops.push("origin", ":refs/heads/topic", cwd=root)
    assert transported and result.ok, result.stderr
    assert git_ops.git("show-ref", "--verify", "refs/heads/topic", cwd=remote, check=False).returncode
    assert git(root, "rev-parse", tracked) == replacement


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
@pytest.mark.parametrize("defect", [
    "shape", "number-type", "number-value", "boolean", "fractional", "numeric-string", "zero", "negative",
])
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
    numbers: dict[str, object] = {
        "number-type": [], "number-value": "invalid", "boolean": True, "fractional": 7.5,
        "numeric-string": "7", "zero": 0, "negative": -7,
    }
    payload: object = [] if defect == "shape" else {
        field: numbers[defect], "html_url": "https://forge.example.test/owner/repo/pulls/7",
    }
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


@pytest.mark.parametrize("url", [
    "not-a-url", "https://other.example.test/owner/repo/pulls/7",
    "https://forge.example.test/elsewhere/repo/pulls/7",
    "https://forge.example.test/owner/repo/pulls/8",
    "https://forge.example.test/owner/repo/pulls/7?unrelated=true",
])
def test_gitea_returned_url_is_verified_before_followup_mutations_or_claims(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, url: str,
) -> None:
    from agent_worktrees import pr_unverified_creation, providers
    from agent_worktrees.providers.gitea import GiteaProvider

    config, caller, _owner, _workspace = native_source_owners
    target = tracking.PRRecord(
        pr_id="pending", branch="topic", repo="owner/repo", provider="gitea", state="creating",
    )
    caller.prs = [target]
    tracking.save_record(caller)
    provider = GiteaProvider()
    calls: list[str] = []

    def curl(method: str, *_args: Any, **_kwargs: Any) -> tuple[int, str]:
        calls.append(method)
        return 201, json.dumps({"number": 7, "html_url": url, "state": "open"})

    monkeypatch.setattr(provider, "_curl", curl)
    monkeypatch.setattr(provider, "_apply_labels", lambda *_: pytest.fail("Unverified creation applied labels"))
    monkeypatch.setattr(providers, "get_provider", lambda *_: provider)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: "fixture-token")
    policy = replace(config.default_repo.pr, provider="gitea", api_base="https://forge.example.test")
    scope = PRScope(repo=target.repo, head="topic", base="main", title="Fix",
                    api_base=policy.api_base, labels=("review-needed",))
    result: dict[str, Any] = {}
    assert pr_unverified_creation.create(result, config, caller, target, policy, scope) is None
    assert calls == ["POST"]
    assert result["publication_state"] == "unknown" and not result["claimed"]
    assert result["number"] == 7 and result["url"] == url
    assert tracking.load_record(caller.yaml_path).prs[0].creation["provider_outcome"] == "unknown"


@pytest.mark.parametrize("provider_name", ["gitea", "azure-devops"])
def test_settlement_does_not_verify_a_returned_url_for_another_repository(
    native_source_owners: NativeOwners, provider_name: str,
) -> None:
    from agent_worktrees import pr_publication_state

    config, caller, _owner, _workspace = native_source_owners
    config = replace(config, repos={
        name: replace(repo, pr=replace(repo.pr, provider=provider_name, api_base="https://forge.example.test"))
        for name, repo in config.repos.items()
    })
    target = tracking.PRRecord(
        pr_id="pending", branch="topic", repo="owner/repo", provider=provider_name, state="creating",
        creation={"provider_outcome": "publishing"},
    )
    caller.prs = [target]
    tracking.save_record(caller)
    suffix = "pulls/7" if provider_name == "gitea" else "_git/repo/pullrequest/7"
    pull = PullResult(number=7, url="https://forge.example.test/unrelated/" + suffix)
    before = caller.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="identity|URL"):
        pr_publication_state.persist_pull(
            config, caller, target, pull, head_sha="a" * 40, marker_published=False, session="",
        )
    assert caller.yaml_path.read_bytes() == before


def test_advisory_foreign_receipt_summary_reuses_history_and_invalidates_updates(
    _tracking_setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_foreign_receipts, prune
    from test_pr_foreign_create import _config

    ledger, wid = _tracking_setup
    record = tracking.load_record(ledger / f"{wid}.yaml")
    attempt = _attempt(cast(Callable[..., cfg.Config], _config)(), record)
    assert attempt.reserve("main", None) is None
    attempt.finish(PullResult(number=7, url="https://github.com/owner/other-repo/pull/7"), verified=True)
    folder = attempt.path.parent
    raw = attempt.path.read_text(encoding="utf-8")
    for index in range(128):
        (folder / f"retained-{index}.json").write_text(raw, encoding="utf-8")
    read_receipt, iterdir = pr_foreign_receipts._read_receipt, Path.iterdir
    reads = scans = 0

    def read(path: Path) -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return read_receipt(path)

    def children(path: Path) -> Any:
        nonlocal scans
        if path == folder:
            scans += 1
        return iterdir(path)

    monkeypatch.setattr(pr_foreign_receipts, "_read_receipt", read)
    monkeypatch.setattr(Path, "iterdir", children)
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    for _ in range(3):
        assert prune.assess(record, info).safe
    assert reads == 129 and scans == 1
    pending = json.loads(raw)
    pending["phase"] = "unknown"
    tracking._atomic_write(attempt.path, json.dumps(pending))
    assert prune.assess(record, info).category == "unknown-publication"


def test_cleanup_rechecks_receipts_even_when_the_advisory_summary_is_stale(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import cleanup_gc_cli, pr_foreign_receipts, prune

    config, caller, _owner, _workspace = native_source_owners
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    assert prune.assess(caller, info).safe
    attempt = _attempt(config, caller)
    assert attempt.reserve("main", None) is None
    monkeypatch.setattr(pr_foreign_receipts, "advisory_owner_receipts", lambda *_: (False, False), raising=False)
    result = cleanup_gc_cli._revalidate_cleanup_safety(
        caller.worktree_id, repo=config.default_repo, tracking_path=caller.yaml_path.parent,
        reap=lambda *_: pytest.fail("A cached display admitted cleanup of a pending receipt"),
    )
    assert not result.cleanable and "foreign" in result.reason.lower()


@pytest.mark.parametrize("consumer", ["cleanup-report", "closure"])
def test_unknown_foreign_publication_is_reported_by_cleanup_and_closure_consumers(
    _tracking_setup: Any, consumer: str,
) -> None:
    from agent_worktrees import cleanup_gc_cli, closure_descriptor, prune
    from test_pr_foreign_create import _config

    ledger, wid = _tracking_setup
    record = tracking.load_record(ledger / f"{wid}.yaml")
    assert _attempt(cast(Callable[..., cfg.Config], _config)(), record).reserve("main", None) is None
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    disposition = prune.cleanup_disposition(record, info)
    assert not disposition.cleanable and disposition.bucket == "unknown-publication"
    if consumer == "cleanup-report":
        assert cleanup_gc_cli._cleanup_per_item_skip_reason(disposition) == disposition.reason
    else:
        descriptor = closure_descriptor.assemble_closure_descriptor(
            record, info, disposition, held_claims=0, open_follow_ups=0,
        )
        assert not descriptor.final and descriptor.action_disposition == "blocked"
        assert any(blocker["code"] == "incomplete-evidence" for blocker in descriptor.blockers)


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


@pytest.mark.parametrize("outcome", ["valid", "mismatched-url", "timeout", "malformed-id"])
def test_foreign_gitea_reserves_before_transport_and_preserves_uncertainty(
    _tracking_setup: Any, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create, pr_foreign_receipts, prune
    from agent_worktrees.providers import base as providers
    from agent_worktrees.providers.gitea import GiteaProvider
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    config = cast(Callable[..., cfg.Config], _config)()
    resolution = cast(Callable[..., Any], _foreign_resolution)()
    resolution = replace(resolution, repo_config=replace(
        resolution.repo_config, pr=replace(
            resolution.repo_config.pr, api_base="https://forge.example.test/gitea",
        ),
    ))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    provider = GiteaProvider()
    calls: list[str] = []
    reserved: list[dict[str, Any]] = []
    url = "https://forge.example.test/gitea/owner/other-repo/pulls/7"
    if outcome == "mismatched-url":
        url = url.replace("/gitea/", "/GITEA/")

    def curl(method: str, *_args: Any, **_kwargs: Any) -> tuple[int, str]:
        calls.append(method)
        paths = list(ledger.glob("*.foreign-pr-receipts/*.json"))
        reserved.extend(json.loads(path.read_text(encoding="utf-8")) for path in paths)
        if outcome == "timeout":
            raise providers.ProviderError("Fixture transport timeout")
        return 201, json.dumps({
            "number": "7" if outcome == "malformed-id" else 7,
            "html_url": url, "state": "open",
        })

    monkeypatch.setattr(provider, "_curl", curl)
    monkeypatch.setattr(providers, "get_provider", lambda *_: provider)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: "fixture-token")

    def create() -> dict[str, Any]:
        return pr_foreign_create.create_foreign_pr_from_branch(
            wid, config, target_repo="owner/other-repo", from_branch="topic", title="Fix", new=True,
        )

    result = create()
    assert calls == ["POST"]
    assert len(reserved) == 1 and reserved[0]["phase"] == "publishing"
    assert reserved[0]["binding"]["endpoint"] == "https://forge.example.test/gitea"
    record = tracking.load_record(ledger / f"{wid}.yaml")
    receipt = json.loads(next(ledger.glob("*.foreign-pr-receipts/*.json")).read_text(encoding="utf-8"))
    assert receipt["owner"]["nonce"] == record.creation_nonce
    if outcome == "valid":
        assert result["claimed"] and receipt["phase"] == "verified"
        assert any(claim.ref == url and claim.state == "active" for claim in record.resources)
        assert not pr_foreign_receipts.owner_receipts(record)[1]
    else:
        assert result["publication_state"] == "unknown" and not result["claimed"]
        assert receipt["phase"] == "unknown" and not record.live_resources
        if outcome == "mismatched-url":
            assert receipt["provider_number"] == result["number"] == 7
            assert receipt["provider_url"] == result["url"] == url
        assert not prune.assess(
            record, git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED),
        ).safe
        before = json.dumps(receipt, sort_keys=True)
        retry = create()
        assert retry["publication_state"] == "unknown" and calls == ["POST"]
        assert json.dumps(json.loads(next(ledger.glob(
            "*.foreign-pr-receipts/*.json",
        )).read_text(encoding="utf-8")), sort_keys=True) == before


@pytest.mark.parametrize("consumer", ["provider", "settlement"])
def test_gitea_mount_prefix_is_case_sensitive_at_creation_consumers(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, consumer: str,
) -> None:
    from agent_worktrees import pr_publication_state
    from agent_worktrees.providers import base as providers
    from agent_worktrees.providers.gitea import GiteaProvider

    config, caller, _owner, _workspace = native_source_owners
    endpoint = "https://forge.example.test/gitea"
    pull = PullResult(number=7, url="https://forge.example.test/GITEA/owner/repo/pulls/7")
    if consumer == "provider":
        provider = GiteaProvider()
        monkeypatch.setattr(provider, "_curl", lambda *_args, **_kwargs: (
            201, json.dumps({"number": pull.number, "html_url": pull.url}),
        ))
        monkeypatch.setattr(provider, "_apply_labels", lambda *_: pytest.fail("Wrong mount received labels"))
        with pytest.raises(providers.ProviderError):
            provider.create_pull(PRScope(
                repo="owner/repo", head="topic", base="main", title="Fix",
                api_base=endpoint, labels=("review-needed",),
            ), token="fixture-token")
    else:
        config = replace(config, repos={
            name: replace(repo, pr=replace(repo.pr, provider="gitea", api_base=endpoint))
            for name, repo in config.repos.items()
        })
        target = tracking.PRRecord(
            pr_id="pending", branch="topic", repo="owner/repo", provider="gitea", state="creating",
            creation={"provider_outcome": "publishing"},
        )
        caller.prs = [target]
        tracking.save_record(caller)
        before = caller.yaml_path.read_bytes()
        with pytest.raises(ValueError, match="identity|URL"):
            pr_publication_state.persist_pull(
                config, caller, target, pull, head_sha="a" * 40, marker_published=False, session="",
            )
        assert caller.yaml_path.read_bytes() == before


@pytest.mark.parametrize("number", [None, 7])
@pytest.mark.parametrize("field", ["error", "pr_open_error"])
def test_human_create_reports_the_unverified_cause_without_inventing_a_number(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], number: int | None, field: str,
) -> None:
    from agent_worktrees import __main__ as cli
    from agent_worktrees import finalize_cli, pr_ops

    config, caller, _owner, _workspace = native_source_owners
    monkeypatch.setattr(cfg, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(pr_ops, "create_pr", lambda *_args, **_kwargs: {
        "success": False, "pr_identity_verified": False, "publication_state": "unknown",
        "number": number, "url": "https://forge.example.test/owner/repo/pulls/7" if number else "",
        field: "Fixture provider response was malformed",
    })
    args = cli.build_parser().parse_args(["create-pr", caller.worktree_id, "--title", "Fix"])
    assert finalize_cli.cmd_create_pr(args) == 1
    captured = capfd.readouterr()
    text = captured.out + captured.err
    assert "Fixture provider response was malformed" in text
    assert "#None" not in text and "Reconcile" in text
    assert ("#7" in text) == (number == 7)


def test_github_creation_verification_uses_the_selected_enterprise_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    from agent_worktrees.providers import github

    keys = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    for key in keys:
        monkeypatch.setenv(key, "unrelated-ambient-credential")
    calls: list[list[str]] = []
    url = "https://forge.example.test/owner/repo/pull/7"

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        effective = {**os.environ, **kwargs.get("env", {})}
        assert all(effective[key] == "selected-fixture-credential" for key in keys)
        if argv[1:3] == ["pr", "create"]:
            return subprocess.CompletedProcess(argv, 0, url + "\n", "")
        assert argv[1:3] == ["pr", "view"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "number": 7, "url": url, "state": "OPEN", "headRefName": "topic",
            "baseRefName": "main", "headRepository": {
                "nameWithOwner": "owner/repo", "url": "https://forge.example.test/owner/repo",
            },
        }), "")

    monkeypatch.setattr(github, "run_cli", run)
    result = github.GitHubProvider().create_pull(PRScope(
        repo="owner/repo", head="topic", base="main", title="Fix", api_base="https://forge.example.test",
    ), token="selected-fixture-credential")
    assert result.number == 7 and len(calls) == 2


@pytest.mark.parametrize("outcome", ["valid", "post-timeout", "startup-delay"])
def test_foreign_creation_shares_the_frontend_deadline_through_provider_and_receipt_settlement(
    _tracking_setup: Any, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    import time
    from agent_worktrees import pr_config, pr_foreign_create, pr_foreign_receipts
    from agent_worktrees.providers import base as providers, bounded_read_cli, github
    from test_pr_foreign_create import _config, _foreign_resolution
    from test_pr_fork_source import _pull

    ledger, wid = _tracking_setup
    config = cast(Callable[..., cfg.Config], _config)()
    repo = cast(Callable[..., Any], _foreign_resolution)(provider="github").repo_config
    repo = replace(repo, pr=replace(repo.pr, api_base="https://forge.example.test"))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: (
        pr_config.ForeignRepoResolution(repo, "upstream/target")
    ))
    monkeypatch.setattr(providers, "get_provider", lambda *_: github.GitHubProvider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    started = time.monotonic()
    elapsed = 40.0 if outcome == "startup-delay" else 0.0
    monkeypatch.setenv("AGENT_WORKTREES_FOREIGN_CREATE_DEADLINE_V1", str(started + 75))
    monkeypatch.setattr(time, "monotonic", lambda: started + elapsed)
    calls: list[tuple[str, float]] = []
    original = subprocess.run

    def run(argv: list[str], *_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal elapsed
        if Path(argv[0]).stem.casefold() != "gh":
            return original(argv, **kwargs)
        path = next(value for value in argv if value.startswith("https://"))
        method = argv[argv.index("--method") + 1]
        if "/forks?" in path:
            data: object = [[{
                "full_name": "contributor/renamed-source", "fork": True,
                "owner": {"login": "contributor"},
            }]]
            duration = 20.0
        elif path.endswith("/pulls"):
            data = _pull() if method == "POST" else [[]]
            duration = 11.0 if method == "POST" and outcome == "post-timeout" else (
                5.0 if method == "POST" else 10.0
            )
        else:
            data = {"full_name": "upstream/target", "fork": False}
            duration = 20.0
        timeout = float(kwargs["timeout"])
        calls.append((method, timeout))
        elapsed += min(duration, timeout)
        if duration > timeout:
            return subprocess.CompletedProcess(argv, 124, "", "Fixture provider timed out")
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(bounded_read_cli, "run_bounded", run)
    result = pr_foreign_create.create_foreign_pr_from_branch(
        wid, config, target_repo="upstream/target", from_branch="contributor:topic", title="Fix",
    )
    record = tracking.load_record(ledger / f"{wid}.yaml")
    if outcome == "valid":
        assert result["claimed"] and result["number"] == 7, result
        assert not pr_foreign_receipts.owner_receipts(record)[1]
        assert elapsed == 55
    elif outcome == "post-timeout":
        assert result["publication_state"] == "unknown" and not result["claimed"], result
        assert pr_foreign_receipts.owner_receipts(record)[1]
        assert elapsed <= 60
    else:
        assert result.get("error") and not any(method == "POST" for method, _ in calls), result
        assert not pr_foreign_receipts.owner_receipts(record)[0]
        assert elapsed <= 60
    assert all(timeout <= 60 for _method, timeout in calls)
    assert elapsed < 75


@pytest.mark.parametrize("outcome", ["valid", "timeout", "malformed-id"])
def test_foreign_azure_creation_uses_the_same_durable_attempt_owner(
    _tracking_setup: Any, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create, pr_foreign_receipts
    from agent_worktrees.providers import azure_devops
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    config = cast(Callable[..., cfg.Config], _config)()
    repo = cast(Callable[..., Any], _foreign_resolution)(provider="azure-devops").repo_config
    repo = replace(repo, pr=replace(repo.pr, api_base="https://forge.example.test/organization"))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: (
        pr_config.ForeignRepoResolution(repo, "project/repo")
    ))
    provider = azure_devops.AzureDevOpsProvider()
    monkeypatch.setattr(provider, "update_pull_metadata", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(providers, "get_provider", lambda *_: provider)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    reservations: list[dict[str, Any]] = []
    calls = 0

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        reservations.extend(json.loads(path.read_text(encoding="utf-8")) for path in (
            ledger.glob("*.foreign-pr-receipts/*.json")
        ))
        if outcome == "timeout":
            return subprocess.CompletedProcess(argv, 124, "", "Fixture transport timeout")
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "pullRequestId": "7" if outcome == "malformed-id" else 7, "status": "active",
        }), "")

    monkeypatch.setattr(azure_devops, "run_cli", run)

    def create() -> dict[str, Any]:
        return pr_foreign_create.create_foreign_pr_from_branch(
            wid, config, target_repo="project/repo", from_branch="topic", title="Fix", new=True,
        )

    result = create()
    assert calls == 1 and len(reservations) == 1
    assert reservations[0]["phase"] == "publishing"
    record = tracking.load_record(ledger / f"{wid}.yaml")
    if outcome == "valid":
        assert result["claimed"] and result["number"] == 7, result
        assert not pr_foreign_receipts.owner_receipts(record)[1]
    else:
        assert result["publication_state"] == "unknown" and not result["claimed"]
        assert pr_foreign_receipts.owner_receipts(record)[1]
        assert create()["publication_state"] == "unknown" and calls == 1
