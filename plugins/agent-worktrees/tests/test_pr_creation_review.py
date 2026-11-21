"""Creation holds protect their actual consumers and original source identity."""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_worktrees import config as cfg, git_ops, tracking, tracking_claims
from agent_worktrees.providers import github
from agent_worktrees.providers.base import PRScope, PullResult
from test_parallel_publication import git, repository
from test_pr_foreign_create import _tracking_setup as _tracking_setup
from test_pr_source_binding import NativeOwners
from test_pr_transport_fence import native_source_owners as native_source_owners
from test_terminal_conclusion import _worker_seed as _worker_seed


def _attempt(config: cfg.Config, record: tracking.WorktreeRecord) -> Any:
    from agent_worktrees.pr_foreign_receipts import Attempt

    return Attempt(
        config, record.yaml_path, record, provider="github", endpoint="github.com",
        target="owner/other-repo", source="contributor/source", branch="topic",
    )


@pytest.mark.parametrize("destination,annotated", [
    ("absent", False), ("absent", True), ("branch", False), ("tag", False),
])
def test_explicit_short_tag_destination_matches_native_git(
    tmp_path: Path, destination: str, annotated: bool,
) -> None:
    root, remote = repository(tmp_path)
    git(root, "tag", *(("-a", "release", "-m", "Release") if annotated else ("release",)))
    if destination != "absent":
        namespace = "heads" if destination == "branch" else "tags"
        for name in ("native", "managed"):
            git(root, "push", "origin", f"HEAD:refs/{namespace}/{name}")
    git(root, "push", "origin", "refs/tags/release:native")
    expected_namespace = "heads" if destination == "branch" else "tags"
    expected = git(remote, "rev-parse", f"refs/{expected_namespace}/native")
    result = git_ops.push("origin", "refs/tags/release:managed", cwd=root)
    assert result.ok, result.stderr
    assert git(remote, "rev-parse", f"refs/{expected_namespace}/managed") == expected
    other = "tags" if expected_namespace == "heads" else "heads"
    assert git_ops.git("show-ref", "--verify", f"refs/{other}/managed", cwd=remote, check=False).returncode


@pytest.mark.parametrize("force_new", [False, True])
def test_ordinary_unknown_creation_blocks_before_reconciliation_or_push(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch, force_new: bool,
) -> None:
    from agent_worktrees import pr_ops

    config, caller, _owner, _workspace = native_source_owners
    caller.prs = [tracking.PRRecord(
        pr_id="uncertain", branch="pr/uncertain", repo="owner/repo",
        provider=config.default_repo.pr.provider, state="creating",
        head_sha="a" * 40, creation={"provider_outcome": "unknown"},
    )]
    tracking.save_record(caller)
    before = caller.yaml_path.read_bytes()
    monkeypatch.setattr(pr_ops, "_reconcile_active_pr", lambda *a, **k: pytest.fail(
        "An unknown create must be refused before reconciliation can terminalize it",
    ))
    result = pr_ops.create_pr(caller.worktree_id, config, title="Fix", new=force_new, open_pr=False)
    assert not result.get("success") and result["publication_state"] == "unknown", result
    assert caller.yaml_path.read_bytes() == before


@pytest.mark.parametrize("kind,write_fails", [
    ("busy", False), ("busy", True), ("stale", False), ("mention", False), ("missing-token", False),
])
def test_proven_pretransport_refusal_settles_only_after_owner_persistence(
    native_source_owners: NativeOwners, monkeypatch: pytest.MonkeyPatch,
    kind: str, write_fails: bool,
) -> None:
    from agent_worktrees import pr_publish, pr_transport_fence, pr_unverified_creation, providers
    from agent_worktrees.providers.gitea import GiteaProvider

    config, caller, _owner, _workspace = native_source_owners
    provider = "gitea" if kind == "missing-token" else "github"
    target = tracking.PRRecord(pr_id="pending", branch="topic", repo="owner/repo",
                               provider=provider, state="creating")
    caller.prs = [target]
    tracking.save_record(caller)
    monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_ACQUIRE_TIMEOUT_S", 0)
    monkeypatch.setattr(providers, "get_provider", lambda *_: (
        GiteaProvider() if provider == "gitea" else github.GitHubProvider()
    ))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    monkeypatch.setattr(github, "run_cli", lambda *a, **k: pytest.fail("A busy fence must prevent transport"))
    monkeypatch.setattr(github.GitHubProvider, "observe_branch", lambda *a, **k: {
        "repository": target.repo, "branch": target.branch, "head_sha": "b" * 40,
    })
    scope = PRScope(repo=target.repo, head="topic", base="main", title="Fix")
    if kind == "stale":
        scope.expected_head_sha = "a" * 40
    elif kind == "mention":
        scope.body = "Ask @copilot to change this"
    save = tracking.save_record

    def persist(record: tracking.WorktreeRecord, *args: Any, **kwargs: Any) -> None:
        if write_fails and any(pr.creation.get("provider_outcome") == "refused" for pr in record.prs):
            raise OSError("Fixture refusal persistence unavailable")
        save(record, *args, **kwargs)

    monkeypatch.setattr(tracking, "save_record", persist)
    with pr_transport_fence.Head("github.com/owner/repo", "topic").guard() if kind == "busy" else nullcontext():
        result: dict[str, Any] = {}
        assert pr_unverified_creation.create(
            result, config, caller, target, replace(config.default_repo.pr, provider=provider), scope,
        ) is None
    fresh = tracking.load_record(caller.yaml_path).prs[0]
    assert result.get("pr_open_error")
    assert (result.get("publication_state") == "unknown") is write_fails
    assert pr_unverified_creation.pending({}, fresh) is write_fails
    assert fresh.creation.get("provider_outcome") == ("unknown" if write_fails else "refused")


@pytest.mark.parametrize("response", ["empty", "wrong-url", "wrong-source", "valid"])
def test_unqualified_github_creation_verifies_returned_identity(
    monkeypatch: pytest.MonkeyPatch, response: str,
) -> None:
    from agent_worktrees.providers.github_pull_source import CreatedSourceMismatch

    scope = PRScope(repo="upstream/target", head="topic", base="main", title="Fix",
                    expected_head_sha="a" * 40)
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        url = "" if response == "empty" else "https://github.com/" + (
            "unrelated/target" if response == "wrong-url" else "upstream/target"
        ) + "/pull/7"
        return subprocess.CompletedProcess(argv, 0, url, "")

    monkeypatch.setattr(github, "run_cli", run)
    monkeypatch.setattr(github.GitHubProvider, "observe_branch", lambda *a, **k: {
        "repository": "upstream/target", "branch": "topic", "head_sha": "a" * 40,
    })
    monkeypatch.setattr(github.GitHubProvider, "get_pull", lambda *a, **k: PullResult(
        number=7, url="https://github.com/upstream/target/pull/7", state="open",
        head_ref="topic", head_sha="a" * 40, base_ref="main",
        head_url="https://github.com/" + (
            "unrelated/source" if response == "wrong-source" else "upstream/target"
        ) + ".git",
    ))
    if response == "valid":
        assert github.GitHubProvider().create_pull(scope).number == 7
    else:
        with pytest.raises(CreatedSourceMismatch) as error:
            github.GitHubProvider().create_pull(scope)
        assert error.value.pull.number == (None if response == "empty" else 7)
    assert len(calls) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinked HOME control")
def test_trusted_ledger_ancestor_alias_preserves_verified_receipts(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, tmp_path: Path,
) -> None:
    from agent_worktrees import pr_foreign_receipts, retirement_safety
    from test_pr_foreign_create import _config

    ledger, wid = _tracking_setup
    record = tracking.load_record(ledger / f"{wid}.yaml")
    config = cast(Callable[..., cfg.Config], _config)()
    attempt = _attempt(config, record)
    attempt.reserve("main", None)
    attempt.finish(PullResult(number=7, url="https://github.com/owner/other-repo/pull/7"), verified=True)
    alias = tmp_path / "home-alias"
    alias.symlink_to(ledger.parent, target_is_directory=True)
    selected = alias / ledger.name
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: selected)
    through_alias = tracking.load_record(selected / f"{wid}.yaml")
    assert pr_foreign_receipts.owner_receipts(through_alias) == (True, False)
    assert retirement_safety.retirement_blocker(through_alias) is None
    assert _attempt(config, through_alias).pending() is None


@pytest.mark.parametrize("intervening", ["publishing", "verified"])
def test_foreign_lookup_does_not_borrow_an_intervening_attempt(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, intervening: str,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    config = cast(Callable[..., cfg.Config], _config)()
    resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)(provider="github")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    record = tracking.load_record(ledger / f"{wid}.yaml")
    competitor_record = replace(record, worktree_id="competitor", branch="worktree/competitor")
    tracking.save_record(competitor_record, ledger / "competitor.yaml")
    competitor_record = tracking.load_record(ledger / "competitor.yaml")
    pull = PullResult(
        number=7, url="https://github.com/owner/other-repo/pull/7", state="open",
        head_ref="topic", head_url="https://github.com/contributor/source.git", base_ref="dev",
    )
    calls: list[PRScope] = []

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> PullResult | None:
            competitor = _attempt(config, competitor_record)
            assert competitor.reserve("dev", None) is None
            if intervening == "verified":
                competitor.finish(pull, verified=True)
                return None
            return pull

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            calls.append(scope)
            return pull

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    result = pr_foreign_create.create_foreign_pr_from_branch(
        wid, config, target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/source", title="Fix", base="dev",
    )
    assert not result.get("claimed") and result.get("error"), result
    assert not calls and not tracking.load_record(record.yaml_path).resources


@pytest.mark.parametrize("consumer", ["cleanup", "managed-guard", "conclusion"])
def test_foreign_pending_attempt_blocks_the_earlier_lifecycle_consumer(
    native_source_owners: NativeOwners, consumer: str,
) -> None:
    from agent_worktrees import managed_worktree_guard, pr_transport_fence, terminal_conclusion

    config, caller, _owner, _workspace = native_source_owners
    attempt = _attempt(config, caller)
    attempt.reserve("main", None)
    with tracking._RecordLock(caller.yaml_path, require_sidecar=True):
        fresh = tracking.load_record(caller.yaml_path)
        if consumer == "cleanup":
            with pytest.raises(ValueError, match="Foreign|foreign"):
                with pr_transport_fence.cleanup_guard(fresh, config.default_repo):
                    pytest.fail("Cleanup entered with an unresolved provider attempt")
        elif consumer == "managed-guard":
            assert any("foreign" in item.lower() for item in managed_worktree_guard.blockers_for(
                fresh, config.default_repo,
            ))
        else:
            assert terminal_conclusion._preservation_reason(
                fresh, policy=terminal_conclusion.DISPOSABLE_CLI_POLICY,
            ) == "unknown-foreign-publication"


@pytest.mark.parametrize("consumer", ["finalize", "managed-remove", "forced-managed-remove"])
def test_native_cleanup_preserves_checkout_branch_and_owner_before_settlement(
    native_source_owners: NativeOwners, git_only_mux: Any, consumer: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import finalize, reap_cli, sessions

    config, caller, _owner, _workspace = native_source_owners
    git(Path(caller.worktree_path), "reset", "--hard", "origin/master")
    caller.kind = "bridge" if "managed" in consumer else "session"
    tracking.save_record(caller)
    attempt = _attempt(config, caller)
    attempt.reserve("main", None)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: set())
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda *_: SimpleNamespace(active_sessions={}))
    before = caller.yaml_path.read_bytes()
    if consumer == "finalize":
        assert not finalize.validate_and_finalize(caller.worktree_id, config)
    else:
        removed, warnings = reap_cli._remove_managed_worktree(
            caller, config.default_repo, caller.yaml_path.parent,
            force=consumer == "forced-managed-remove",
        )
        assert not removed and any("foreign" in item.lower() for item in warnings), warnings
    assert Path(caller.worktree_path).is_dir() and caller.yaml_path.read_bytes() == before
    assert git(Path(config.default_repo.anchor), "rev-parse", caller.branch)
    assert json.loads(attempt.path.read_text(encoding="utf-8"))["phase"] == "publishing"


@pytest.mark.parametrize("paired", [False, True])
def test_native_conclusion_preserves_foreign_pending_owner_and_reciprocal_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paired: bool, _worker_seed: Path,
) -> None:
    from agent_worktrees import terminal_conclusion as tc
    from test_terminal_conclusion import TestReciprocalDisposal, _conclude, _pair_sibling, _worker

    monkeypatch.setattr(socket, "gethostname", lambda: "host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    sibling_path = None
    sibling_before = b""
    if paired:
        sibling, sibling_path, _ = cast(Callable[..., Any], _pair_sibling)(
            tmp_path, monkeypatch, project="knowledge-proj", harness_machine="host",
            harness_project="demo", harness_worktree_id="worker-20260901-abcd",
        )
        repo, path, worktree = cast(Callable[..., Any], TestReciprocalDisposal()._harness)(
            tmp_path, monkeypatch, _worker_seed,
            pair_ref=tracking_claims.format_claim_ref("host", "knowledge-proj", sibling.worktree_id),
        )
        pending_owner = tracking.load_record(sibling_path)
        pending_owner.status = "active"
        tracking.save_record(pending_owner)
    else:
        repo, path, worktree = _worker(tmp_path, monkeypatch, _worker_seed)
        pending_owner = tracking.load_record(path)
    config = cfg.Config(srcroot=str(tmp_path), repo_name=pending_owner.repo, machine="host", platform="linux")
    attempt = _attempt(config, pending_owner)
    attempt.reserve("main", None)
    if paired:
        pending_owner.status = "finalized"
        tracking.save_record(pending_owner)
        assert sibling_path is not None
        sibling_before = sibling_path.read_bytes()
    before = path.read_bytes()
    result = _conclude(
        path, repo, policy=tc.DISPATCH_ATTEMPT_POLICY if paired else tc.DISPOSABLE_CLI_POLICY,
        reservation_key="dispatch-task:task-1:1" if paired else None,
    )
    expected = "pair-sibling-unknown-foreign-publication" if paired else "unknown-foreign-publication"
    assert result["reason"] == expected, result
    assert path.read_bytes() == before and worktree.is_dir()
    if sibling_path is not None:
        assert sibling_path.read_bytes() == sibling_before and tracking.load_record(path).is_paired


def test_foreign_verified_receipt_is_not_exposed_before_its_claim_is_durable(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_create, pr_foreign_receipts
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    config = cast(Callable[..., cfg.Config], _config)()
    resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)(provider="github")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    original = pr_foreign_receipts.Attempt.finish
    retirements: list[bool] = []

    def finish(attempt: Any, pull: PullResult | None, *, verified: bool) -> None:
        original(attempt, pull, verified=verified)
        if verified:
            owner = tracking.load_record(ledger / f"{wid}.yaml")
            retirements.append(tracking.retire_record(owner, ledger))

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            return PullResult(number=7, url="https://github.com/owner/other-repo/pull/7")

    monkeypatch.setattr(pr_foreign_receipts.Attempt, "finish", finish)
    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    result = pr_foreign_create.create_foreign_pr_from_branch(
        wid, config, target_repo="owner/other-repo", from_branch="contributor:topic",
        source_repository="contributor/source", title="Fix",
    )
    assert result.get("claimed") and not retirements, result
    owner = tracking.load_record(ledger / f"{wid}.yaml")
    assert owner.live_resources and json.loads(next(ledger.glob("*.foreign-pr-receipts/*.json")).read_text())[
        "phase"
    ] == "verified"


def test_real_foreign_publishers_recheck_lookup_after_another_owner_completes(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import sys
    from agent_procutil.owned_process import run_caller_owned
    from machine_transport import detect_platform
    from agent_worktrees import tracking_lifecycle

    ledger = tmp_path / "worktrees"
    ledger.mkdir()
    for role in ("first", "second"):
        tracking_lifecycle.create_new_record(
            role, "worktree/" + role, str(tmp_path / role), "caller-repo", "test",
            detect_platform(), ledger,
        )
    (tmp_path / "projects.yaml").write_text('projects:\n  caller-repo: {}\n', encoding="utf-8")
    code = """
import sys, time
from pathlib import Path
from machine_transport import detect_platform
from agent_worktrees import config as cfg, installer, pr_config, pr_foreign_create
from agent_worktrees.providers import base as providers
from agent_worktrees.providers.github import GitHubProvider
from agent_worktrees.providers.base import PullResult
root, role = Path(sys.argv[1]), sys.argv[2]
cfg.install_dir = lambda: root / 'installation'
cfg.tracking_dir = lambda name=None: root / 'worktrees'
installer.projects_yaml_path = lambda: root / 'projects.yaml'
config = cfg.Config(srcroot=str(root), machine='test', platform=detect_platform(),
    repo_name='caller-repo', repos={'caller-repo': cfg.RepoConfig(
        anchor=str(root / 'anchor'), worktree_root=str(root / 'checkouts'))})
target = cfg.RepoConfig(anchor=str(root / 'target'), worktree_root=str(root / 'targets'),
    pr=cfg.PRConfig(enabled=True, provider='github'))
pr_config.resolve_repo_config_for_slug = lambda *a: pr_config.ForeignRepoResolution(target, 'upstream/target')
def wait(path):
    deadline = time.monotonic() + 15
    while not path.exists():
        assert time.monotonic() < deadline, str(path)
        time.sleep(0.01)
class GitHub(GitHubProvider):
    def find_pull_by_source(self, *a, **k):
        if role == 'second':
            (root / 'second.lookup').write_text('lookup')
            wait(root / 'first.done')
        return None
    def create_pull(self, scope, **k):
        (root / (role + '.post')).write_text('post')
        return PullResult(number=7, url='https://github.com/upstream/target/pull/7')
providers.get_provider = lambda *a: GitHub()
providers.account_token_for_slug = lambda *a: None
if role == 'first':
    wait(root / 'second.lookup')
result = pr_foreign_create.create_foreign_pr_from_branch(role, config,
    target_repo='upstream/target', from_branch='contributor:topic',
    source_repository='contributor/source', title='Fix')
if role == 'first':
    assert result.get('claimed'), result
    (root / 'first.done').write_text('done')
else:
    assert not result.get('claimed') and result.get('publication_state') == 'not-started', result
print(role + ':ok')
"""

    def run(role: str) -> subprocess.CompletedProcess[str]:
        return run_caller_owned(
            [sys.executable, "-c", code, str(tmp_path), role], timeout=20,
            max_stdout_bytes=8192, max_stderr_bytes=8192,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run, role) for role in ("first", "second")]
        results = [job.result(timeout=25) for job in jobs]
    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    assert (tmp_path / "first.post").is_file() and not (tmp_path / "second.post").exists()
