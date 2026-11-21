"""Exact destinations and retained publication evidence at real consumers."""

from collections.abc import Callable
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from typing import Any, cast

import pytest

from agent_worktrees import config as cfg, git_ops, tracking
from agent_worktrees.providers import github
from agent_worktrees.providers.base import PullResult
from test_parallel_publication import git, repository
from test_pr_creation_review import _attempt
from test_pr_foreign_create import _tracking_setup as _tracking_setup
from test_pr_source_binding import NativeOwners
from test_pr_transport_fence import native_source_owners as native_source_owners


@pytest.mark.parametrize("source", ["HEAD", "refs/tags/release"])
def test_short_destination_does_not_select_an_unrelated_nested_suffix(
    tmp_path: Path, source: str,
) -> None:
    root, remote = repository(tmp_path)
    git(root, "tag", "release")
    git(root, "push", "origin", "HEAD:refs/heads/team/topic")
    old = git(remote, "rev-parse", "refs/heads/team/topic")
    result = git_ops.push("origin", source + ":topic", cwd=root)
    assert result.ok, result.stderr
    namespace = "tags" if source.startswith("refs/tags/") else "heads"
    assert git(remote, "rev-parse", "refs/" + namespace + "/topic") == old
    assert git(remote, "rev-parse", "refs/heads/team/topic") == old


def test_short_destination_probe_uses_pinned_transport_and_selected_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import git_push_transport
    from agent_worktrees.github_credentials import GitAuthArgs

    root, remote = repository(tmp_path)
    original = git_ops.git
    probes: list[tuple[str, ...]] = []
    auth = GitAuthArgs(["-c", "credential.helper=selected-fixture"], allow_ambient_retry=False)
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", lambda *_: auth)

    def run(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "ls-remote" in args:
            probes.append(args)
            assert args[:len(auth)] == tuple(auth)
            assert any(".uploadpack=" in value for value in args)
            assert args[args.index("ls-remote") + 1].startswith("agent-worktrees-")
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", run)
    with git_push_transport.pinned_destination(
        "origin", (str(remote),), (("uploadpack", "git-upload-pack"),),
    ):
        result = git_ops.push("origin", "HEAD:topic", cwd=root)
    assert result.ok and probes, result.stderr
    assert git(remote, "rev-parse", "refs/heads/topic") == git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("consumer", ["finalize", "managed", "forced-managed"])
@pytest.mark.parametrize("phase", ["publishing", "unknown"])
def test_ordinary_unverified_publication_blocks_before_native_retirement(
    native_source_owners: NativeOwners, git_only_mux: Any, monkeypatch: pytest.MonkeyPatch,
    consumer: str, phase: str,
) -> None:
    from agent_worktrees import finalize, reap_cli, sessions

    config, caller, _owner, _workspace = native_source_owners
    git(Path(caller.worktree_path), "reset", "--hard", "origin/master")
    caller.kind = "session" if consumer == "finalize" else "bridge"
    caller.prs = [tracking.PRRecord(
        pr_id="uncertain", branch="pr/uncertain", repo="owner/repo", provider="github",
        state="creating", creation={"provider_outcome": phase},
    )]
    tracking.save_record(caller)
    before = caller.yaml_path.read_bytes()
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: set())
    if consumer == "finalize":
        assert not finalize.validate_and_finalize(caller.worktree_id, config, force_open_pr=True)
    else:
        removed, warnings = reap_cli._remove_managed_worktree(
            caller, config.default_repo, caller.yaml_path.parent, force=consumer == "forced-managed",
        )
        assert not removed and warnings
    assert Path(caller.worktree_path).is_dir() and caller.yaml_path.read_bytes() == before
    assert git(Path(config.default_repo.anchor), "rev-parse", caller.branch)


def test_completed_managed_foreign_owner_cannot_settle_without_a_durable_claim(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create, pr_foreign_receipts
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    config = cast(Callable[..., cfg.Config], _config)()
    record = tracking.load_record(ledger / f"{wid}.yaml")
    record.kind, record.status = "bridge", "complete"
    tracking.save_record(record)
    resolution = cast(Callable[..., Any], _foreign_resolution)(provider="github")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def create_pull(self, *_args: Any, **_kwargs: Any) -> PullResult:
            return PullResult(number=7, url="https://github.com/owner/other-repo/pull/7")

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    result = pr_foreign_create.create_foreign_pr_from_branch(
        wid, config, target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/source", title="Fix",
    )
    fresh = tracking.load_record(record.yaml_path)
    receipts = list(ledger.glob("*.foreign-pr-receipts/*.json"))
    assert not result.get("claimed")
    assert not receipts or pr_foreign_receipts.owner_receipts(fresh)[1]
    if receipts:
        assert result["publication_state"] == "unknown"
        assert json.loads(receipts[0].read_text())["phase"] != "verified"


@pytest.mark.parametrize("explicit", [False, True])
def test_foreign_source_remote_is_independent_of_target_policy(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, explicit: bool,
) -> None:
    from agent_worktrees import pr_foreign_checks

    config, _caller, _owner, workspace = native_source_owners
    root = Path(workspace)
    git(root, "switch", "-c", "topic")
    git(root, "remote", "set-url", "origin", "https://github.com/contributor/source.git")
    git(root, "remote", "add", "upstream", "https://github.com/upstream/target.git")
    head = git(root, "rev-parse", "HEAD")
    branch = git(root, "symbolic-ref", "--short", "HEAD")
    git(root, "update-ref", "refs/remotes/upstream/master", head)
    repo = replace(config.default_repo, remote="upstream", pr=replace(
        config.default_repo.pr, provider="github",
    ))
    provider = github.GitHubProvider()
    monkeypatch.setattr(provider, "observe_branch", lambda repository, branch, **_kwargs: {
        "repository": repository, "branch": branch, "head_sha": head,
    })
    source, selected_branch = pr_foreign_checks.source_binding(
        "upstream/target", "contributor:" + branch,
        "contributor/source" if explicit else None, str(root), repo, provider,
    )
    result = pr_foreign_checks.qualify(
        str(root), "upstream/target", selected_branch, head, repo, provider,
        source_repository=source,
    )
    assert result["ok"] and result["source_repository"] == "contributor/source"
    assert result["target_binding"]["remote"] == "upstream"


def test_legacy_qualified_head_resolves_an_emu_login(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        data: object = {"full_name": "upstream/target", "fork": False}
        if "--paginate" in argv:
            data = [[{"full_name": "user_shortcode/copy", "fork": True,
                      "owner": {"login": "user_shortcode"}}]]
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(github, "run_cli", run)
    assert github.GitHubProvider().resolve_head_repository(
        "upstream/target", "user_shortcode",
    ) == "user_shortcode/copy"


@pytest.mark.skipif(os.name == "nt", reason="POSIX native undecodable path bytes")
def test_source_admission_preserves_native_undecodable_path_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import source_path_admission

    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "installation")
    source = tmp_path / os.fsdecode(b"source-\xff")
    source.mkdir()
    with source_path_admission.guard(source):
        with pytest.raises(TimeoutError):
            with source_path_admission.guard(os.fsdecode(os.fsencode(source)), timeout=0):
                pytest.fail("The same native path must not enter twice")


def test_verified_receipt_still_rejects_a_changed_owner(_tracking_setup: Any) -> None:
    from test_pr_foreign_create import _config

    ledger, wid = _tracking_setup
    record = tracking.load_record(ledger / f"{wid}.yaml")
    config = cast(Callable[..., cfg.Config], _config)()
    attempt = _attempt(config, record)
    assert attempt.reserve("main", None) is None
    attempt.finish(PullResult(number=7, url="https://github.com/owner/other-repo/pull/7"), verified=True)
    changed = tracking.load_record(record.yaml_path)
    changed.creation_nonce = "different-owner"
    assert _attempt(config, changed).pending() is not None


def test_terminal_pr_state_does_not_discard_an_unverified_provider_receipt(_tracking_setup: Any) -> None:
    from agent_worktrees import retirement_safety

    ledger, wid = _tracking_setup
    record = tracking.load_record(ledger / f"{wid}.yaml")
    record.status = "finalized"
    record.prs = [tracking.PRRecord(
        pr_id="uncertain", branch="topic", repo="owner/repo", provider="github",
        state="closed", creation={"provider_outcome": "unknown"},
    )]
    tracking.save_record(record)
    before = record.yaml_path.read_bytes()
    assert retirement_safety.retains_artifact_metadata(record)
    with pytest.raises(ValueError, match="state unconfirmed"):
        tracking.retire_record(record, ledger)
    assert record.yaml_path.read_bytes() == before
