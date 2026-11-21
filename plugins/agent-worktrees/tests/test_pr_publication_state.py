"""Regressions for provider identity, attribution-only races, and first-push retry."""

from dataclasses import replace
from collections.abc import Iterator
from contextlib import contextmanager
import os
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, finalize_cli, git_ops, pr_authority, pr_ops, pr_publish, pr_rewrite, providers, publication_deadline, tracking
from agent_worktrees import __main__ as cli
from agent_worktrees.providers.base import PRScope, PullResult

PrRepo = tuple[cfg.Config, str, Path, Path]

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr_publish.publication_state"),
    pytest.mark.timeout(120),
]


@pytest.fixture(autouse=True)
def _isolated_authority_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "authority-profile")


@pytest.mark.parametrize("iteration", range(2))
def test_authority_profile_and_real_lock_are_function_private(tmp_path: Path, iteration: int) -> None:
    profile = tmp_path / "authority-profile"
    assert cfg.install_dir() == profile
    assert not profile.exists()
    with pr_authority.guard(timeout=0):
        assert (profile / "pr-rewrite-authority.lock").is_file()
        (profile / "case.txt").write_text(str(iteration), encoding="utf-8")
    assert (profile / "case.txt").read_text(encoding="utf-8") == str(iteration)


def load(wid: str) -> tracking.WorktreeRecord:
    return tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")


def pr_entry(record: tracking.WorktreeRecord) -> tracking.PRRecord:
    selected = record.pr
    assert selected is not None
    return selected


def git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def test_rewrite_provider_mismatch_never_resolves_credentials(pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    config, wid, wt, remote = pr_repo
    pr = tracking.PRRecord(number=42, provider="github", repo="org/repo")
    monkeypatch.setattr(providers, "get_provider", lambda *a: pytest.fail("provider resolution"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: pytest.fail("credential resolution"))
    with pytest.raises(ValueError, match="provider differs"):
        pr_rewrite.observe_pr(config, pr)


def test_rewrite_provider_mismatch_refuses_before_destination_provider_lookup(
    published_pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote = published_pr_repo
    record = load(wid)
    pr_entry(record).provider = "github"
    pr_entry(record).number = 42
    pr_entry(record).pr_revision += 1
    tracking.save_record(record)
    monkeypatch.setattr(pr_publish, "push_target", lambda *a: pytest.fail("destination provider lookup"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: pytest.fail("credential resolution"))
    assert not finalize.push_changes(wid, config, rewrite_pr=True)


def test_provider_identity_generation_survives_network_window_snapshot(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote = pr_repo
    result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
    assert result["success"], result
    record = load(wid)
    pr_entry(record).repo = result["repo"] = "org/repo"
    tracking.save_record(record)
    record = load(wid)
    snapshots = []

    def create(scope: PRScope, **kwargs: Any) -> PullResult:
        snapshots.append(load(wid))
        return PullResult(number=42, url=f"{scope.api_base}/{scope.repo}/pulls/42", state="open")

    monkeypatch.setattr(providers, "get_provider", lambda *a: SimpleNamespace(create_pull=create))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: "")
    pr_ops._open_via_provider(
        result, config, record, pr_entry(record), "Feature", "", wid, pr_entry(record).head_sha,
        prcfg=replace(config.default_repo.pr, api_base="https://example.test"), draft=True,
    )
    assert "pr_open_error" not in result, result
    assert result["pr_opened"]
    published = load(wid)
    assert pr_entry(published).pr_revision > pr_entry(snapshots[0]).pr_revision
    assert any(c.kind == "pr" and c.state == "active" for c in published.resources)
    tracking.save_record(snapshots[0])
    current = load(wid)
    assert pr_entry(current).number == 42 and pr_entry(current).url == pr_entry(published).url
    assert pr_entry(current).rewrite_owner == pr_entry(published).rewrite_owner


def test_rewrite_merges_attribution_stamp_during_actual_push(
    published_pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote = published_pr_repo
    record = load(wid)
    before = pr_entry(record).head_sha
    pr_entry(record).attribution_mode = ""
    pr_entry(record).pr_revision += 1
    tracking.save_record(record)
    (wt / "followup.txt").write_text("followup\n")
    git("add", "-A", cwd=wt)
    git("commit", "-m", "followup", cwd=wt)
    real_push = git_ops.push

    def stamp_then_push(*args: Any, **kwargs: Any) -> git_ops.PushResult:
        fresh = load(wid)
        tracking.stamp_frozen_attribution(pr_entry(fresh), attribution=False, explicit=True, assign_pr_id=False)
        tracking.save_record(fresh)
        return real_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", stamp_then_push)
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    current = load(wid)
    assert pr_entry(current).head_sha != before
    assert pr_entry(current).head_sha == pr_publish._tip("origin", pr_entry(current).branch, str(wt))
    assert pr_entry(current).attribution_mode == "false" and pr_entry(current).attribution_explicit


@pytest.mark.parametrize("ambiguous", [False, True])
def test_legacy_first_push_failure_is_retryable_or_explicitly_ambiguous(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, ambiguous: bool,
) -> None:
    config, wid, wt, remote = pr_repo
    branch = "feature/legacy-" + git_ops.worktree_suffix(wid)
    git("checkout", "-b", branch, cwd=wt)
    real_push = git_ops.push

    def failed(*args: Any, **kwargs: Any) -> git_ops.PushResult:
        if ambiguous:
            assert real_push(*args, **kwargs)
        return git_ops.PushResult(ok=False, stderr="transport outcome unavailable")

    monkeypatch.setattr(git_ops, "push", failed)
    first = pr_ops.create_pr(wid, config, title="Legacy feature", branch=branch, open_pr=False)
    assert "error" in first
    assert pr_entry(load(wid)).state == "creating" and not pr_entry(load(wid)).head_sha
    monkeypatch.setattr(git_ops, "push", real_push)
    retry = pr_ops.create_pr(wid, config, title="Legacy feature", branch=branch, open_pr=False)
    if ambiguous:
        assert "ambiguous push" in retry["error"]
        assert not pr_entry(load(wid)).head_sha
    else:
        assert retry["success"], retry
        current = load(wid)
        assert pr_entry(current).head_sha == pr_publish._tip("origin", branch, str(wt))


def test_title_only_rewrite_uses_selected_ledger(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import disposition_history

    config, wid, wt, remote = pr_repo
    selected = cfg.tracking_dir()
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    shadow_path = ambient / f"{wid}.yaml"
    shadow = replace(load(wid), title="Ambient")
    tracking.save_record(shadow, shadow_path)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: selected if name == config.repo_name else ambient)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    title = "Selected full title retains every identifying word outside the compact projection"
    args = cli.build_parser().parse_args([
        "push-changes", wid, "--config", "selected.yaml", "--rewrite-pr", "--title-only", "--title", title,
    ])
    assert finalize_cli.cmd_push_changes(args) == 0
    assert tracking.load_record(selected / f"{wid}.yaml").title == title
    assert disposition_history.read(wid, tracking_path=selected)[-1]["title"] == title
    assert not disposition_history.read(wid, tracking_path=ambient)
    assert tracking.load_record(shadow_path).title == "Ambient"


def authority_process(root: Path, ready: Path, *, probe: bool = False) -> subprocess.Popen[bytes]:
    script = """
import sys
from pathlib import Path
from agent_worktrees import config as cfg,pr_authority
cfg.install_dir=lambda:Path(sys.argv[1])
try:
    with pr_authority.guard(timeout=0.1):
        Path(sys.argv[2]).write_text('held')
        if sys.argv[3]=='hold': sys.stdin.read()
except TimeoutError:
    raise SystemExit(3)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(pr_authority.__file__).parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-c", script, str(root), str(ready), "probe" if probe else "hold"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
    )


@pytest.mark.parametrize("operation", ["first", "ordinary", "provider"])
def test_publication_contention_precedes_external_mutation(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str,
) -> None:
    config, wid, wt, remote = pr_repo
    record = None
    if operation != "first":
        result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
        assert result["success"], result
        record = load(wid)
    ready = tmp_path / "authority-ready"
    holder = authority_process(cfg.install_dir(), ready)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), holder.poll()
        monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_ACQUIRE_TIMEOUT_S", 0.1)
        monkeypatch.setattr(publication_deadline, "lock_wait", lambda *a: 0.1)
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("push before authority admission"))
        monkeypatch.setattr(providers, "get_provider", lambda *a: pytest.fail("provider call before authority admission"))
        if operation == "ordinary":
            assert not finalize.push_changes(wid, config)
            assert not (Path(config.default_repo.worktree_root) / ".finalize.lock").exists()
        else:
            with pytest.raises(TimeoutError, match="PR rewrite authority"):
                if operation == "first":
                    pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
                else:
                    assert record is not None
                    pr_ops._open_via_provider(
                        result, config, record, pr_entry(record), "Feature", "", wid, pr_entry(record).head_sha,
                        prcfg=config.default_repo.pr, draft=True,
                    )
    finally:
        holder.communicate(input=b"", timeout=10)


@pytest.mark.parametrize("operation", ["first", "ordinary", "provider"])
def test_publication_holds_authority_through_io_and_persistence(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str,
) -> None:
    config, wid, wt, remote = pr_repo
    if operation != "first":
        result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
        assert result["success"], result
    record = load(wid)
    real_push = git_ops.push
    probes = []

    def probe() -> None:
        child = authority_process(cfg.install_dir(), tmp_path / "probe-ready", probe=True)
        _, stderr = child.communicate(timeout=10)
        assert child.returncode == 3, stderr.decode()
        probes.append(True)

    def push(*args: Any, **kwargs: Any) -> git_ops.PushResult:
        probe()
        return real_push(*args, **kwargs)

    if operation == "provider":
        pr_entry(record).repo = result["repo"] = "org/repo"
        tracking.save_record(record)
        record = load(wid)
        def create(scope: PRScope, **kwargs: Any) -> PullResult:
            probe()
            return PullResult(number=42, url=f"{scope.api_base}/{scope.repo}/pulls/42", state="open")
        monkeypatch.setattr(providers, "get_provider", lambda *a: SimpleNamespace(create_pull=create))
        monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: "")
        pr_ops._open_via_provider(
            result, config, record, pr_entry(record), "Feature", "", wid, pr_entry(record).head_sha,
            prcfg=replace(config.default_repo.pr, api_base="https://example.test"), draft=True,
        )
        assert "pr_open_error" not in result, result
        assert pr_entry(load(wid)).number == 42
    else:
        monkeypatch.setattr(git_ops, "push", push)
        if operation == "first":
            result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
            assert result["success"], result
        else:
            (wt / "followup.txt").write_text("followup\n")
            git("add", "-A", cwd=wt)
            git("commit", "-m", "followup", cwd=wt)
            assert finalize.push_changes(wid, config)
        current = load(wid)
        assert pr_entry(current).head_sha == pr_publish._tip("origin", pr_entry(current).branch, str(wt))
    assert probes


def test_ordinary_publication_rechecks_authority_after_admission_wait(
    published_pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote = published_pr_repo
    before = pr_entry(load(wid)).head_sha
    original = pr_authority.guard
    changed = False

    @contextmanager
    def changed_before_admission(*args: Any, **kwargs: Any) -> Iterator[None]:
        nonlocal changed
        with original():
            if not changed:
                changed = True
                current = load(wid)
                pr_entry(current).branch = "feature/changed-aaaa"
                pr_entry(current).pr_revision += 1
                tracking.save_record(current)
            yield

    monkeypatch.setattr(pr_authority, "guard", changed_before_admission)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("stale authority reached push"))
    assert not finalize.push_changes(wid, config)
    assert pr_entry(load(wid)).head_sha == before
    assert not (Path(config.default_repo.worktree_root) / ".finalize.lock").exists()


def test_ownership_stamping_merges_attribution_only_revision(published_pr_repo: PrRepo) -> None:
    config, wid, wt, remote = published_pr_repo
    record = load(wid)
    pr_entry(record).rewrite_owner = pr_entry(record).rewrite_identity = ""
    pr_entry(record).attribution_mode = ""
    pr_entry(record).pr_revision += 1
    tracking.save_record(record)
    expected = load(wid)
    fresh = load(wid)
    tracking.stamp_frozen_attribution(pr_entry(fresh), attribution=False, explicit=True, assign_pr_id=False)
    tracking.save_record(fresh)
    error = pr_publish.record_rewrite_ownership(
        config, expected, pr_entry(expected), pr_entry(expected).branch, pr_entry(expected).head_sha,
        pr_publish.push_identity("origin", cwd=str(wt)),
    )
    assert not error
    assert pr_entry(expected).rewrite_owner and pr_entry(expected).attribution_mode == "false"
    assert pr_entry(load(wid)).rewrite_owner == pr_entry(expected).rewrite_owner


@pytest.mark.parametrize("stamp", ["attribution", "observation"])
def test_rewrite_admission_allows_non_authority_stamp(
    published_pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, stamp: str,
) -> None:
    config, wid, wt, remote = published_pr_repo
    record = load(wid)
    pr_entry(record).attribution_mode = ""
    pr_entry(record).pr_revision += 1
    tracking.save_record(record)
    (wt / "followup.txt").write_text("followup\n")
    git("add", "-A", cwd=wt)
    git("commit", "-m", "followup", cwd=wt)
    acquire = finalize.FinalizeLock.acquire

    def stamped_acquire(lock: finalize.FinalizeLock) -> None:
        acquire(lock)
        fresh = load(wid)
        if stamp == "attribution":
            tracking.stamp_frozen_attribution(pr_entry(fresh), attribution=False, explicit=True, assign_pr_id=False)
        else:
            pr_entry(fresh).head_observed_at = "2026-10-09T00:00:00Z"
        tracking.save_record(fresh)

    monkeypatch.setattr(finalize.FinalizeLock, "acquire", stamped_acquire)
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    current = load(wid)
    assert pr_entry(current).head_sha == pr_publish._tip("origin", pr_entry(current).branch, str(wt))
    if stamp == "attribution":
        assert pr_entry(current).attribution_mode == "false"


@pytest.mark.parametrize("native,canonical", [("active", "open"), ("completed", "merged"), ("abandoned", "closed")])
def test_azure_create_and_observe_use_canonical_lifecycle(
    monkeypatch: pytest.MonkeyPatch, native: str, canonical: str,
) -> None:
    from agent_worktrees.providers import azure_devops
    from agent_worktrees.providers.base import PRScope

    monkeypatch.setattr(azure_devops, "run_cli", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({"pullRequestId": 42, "status": native}), stderr="",
    ))
    provider = azure_devops.AzureDevOpsProvider()
    scope = PRScope(repo="project/repo", head="feature/test", base="main", title="Feature",
                    api_base="https://dev.azure.com/example")
    created = provider.create_pull(scope)
    observed = provider.get_pull(scope.repo, 42, api_base=scope.api_base)
    assert created.state == observed.state == canonical
    assert created.merged == observed.merged == (canonical == "merged")


def test_azure_unknown_lifecycle_is_explicit_error() -> None:
    from agent_worktrees.providers.azure_devops import AzureDevOpsProvider
    from agent_worktrees.providers.base import ProviderError

    with pytest.raises(ProviderError, match="Unknown Azure DevOps PR status"):
        AzureDevOpsProvider._canonical_state("future")
