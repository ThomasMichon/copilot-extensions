"""Regressions for provider identity, attribution-only races, and first-push retry."""

from dataclasses import replace
from contextlib import contextmanager
import os
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, finalize_cli, git_ops, pr_authority, pr_ops, pr_publish, pr_rewrite, providers, tracking
from agent_worktrees import __main__ as cli
from agent_worktrees.providers.base import PullResult

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr_publish.publication_state"),
    pytest.mark.timeout(120),
]


def load(wid):
    return tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")


def git(*args, cwd):
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def test_rewrite_provider_mismatch_never_resolves_credentials(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    pr = tracking.PRRecord(number=42, provider="github", repo="org/repo")
    monkeypatch.setattr(providers, "get_provider", lambda *a: pytest.fail("provider resolution"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: pytest.fail("credential resolution"))
    with pytest.raises(ValueError, match="provider differs"):
        pr_rewrite.observe_pr(config, pr)


def test_rewrite_provider_mismatch_refuses_before_destination_provider_lookup(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
    assert result["success"], result
    record = load(wid)
    record.pr.provider = "github"
    record.pr.number = 42
    record.pr.pr_revision += 1
    tracking.save_record(record)
    monkeypatch.setattr(pr_publish, "push_target", lambda *a: pytest.fail("destination provider lookup"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: pytest.fail("credential resolution"))
    assert not finalize.push_changes(wid, config, rewrite_pr=True)


def test_provider_identity_generation_survives_network_window_snapshot(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
    assert result["success"], result
    record = load(wid)
    snapshots = []

    def create(scope, **kwargs):
        snapshots.append(load(wid))
        return PullResult(number=42, url="https://example.test/org/repo/pull/42", state="open")

    monkeypatch.setattr(providers, "get_provider", lambda *a: SimpleNamespace(create_pull=create))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: "")
    pr_ops._open_via_provider(
        result, config, record, record.pr, "Feature", "", wid, record.pr.head_sha,
        prcfg=config.default_repo.pr, draft=True,
    )
    assert result["pr_opened"]
    published = load(wid)
    assert published.pr.pr_revision > snapshots[0].pr.pr_revision
    assert any(c.kind == "pr" and c.state == "active" for c in published.resources)
    tracking.save_record(snapshots[0])
    current = load(wid)
    assert current.pr.number == 42 and current.pr.url == published.pr.url
    assert current.pr.rewrite_owner == published.pr.rewrite_owner


def test_rewrite_merges_attribution_stamp_during_actual_push(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
    assert result["success"], result
    record = load(wid)
    before = record.pr.head_sha
    record.pr.attribution_mode = ""
    record.pr.pr_revision += 1
    tracking.save_record(record)
    (wt / "followup.txt").write_text("followup\n")
    git("add", "-A", cwd=wt)
    git("commit", "-m", "followup", cwd=wt)
    real_push = git_ops.push

    def stamp_then_push(*args, **kwargs):
        fresh = load(wid)
        tracking.stamp_frozen_attribution(fresh.pr, attribution=False, explicit=True, assign_pr_id=False)
        tracking.save_record(fresh)
        return real_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", stamp_then_push)
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    current = load(wid)
    assert current.pr.head_sha != before
    assert current.pr.head_sha == pr_publish._tip("origin", current.pr.branch, str(wt))
    assert current.pr.attribution_mode == "false" and current.pr.attribution_explicit


@pytest.mark.parametrize("ambiguous", [False, True])
def test_legacy_first_push_failure_is_retryable_or_explicitly_ambiguous(pr_repo, monkeypatch, ambiguous):
    config, wid, wt, remote = pr_repo
    branch = "feature/legacy-" + git_ops.worktree_suffix(wid)
    git("checkout", "-b", branch, cwd=wt)
    real_push = git_ops.push

    def failed(*args, **kwargs):
        if ambiguous:
            assert real_push(*args, **kwargs)
        return git_ops.PushResult(ok=False, stderr="transport outcome unavailable")

    monkeypatch.setattr(git_ops, "push", failed)
    first = pr_ops.create_pr(wid, config, title="Legacy feature", branch=branch, open_pr=False)
    assert "error" in first
    assert load(wid).pr.state == "creating" and not load(wid).pr.head_sha
    monkeypatch.setattr(git_ops, "push", real_push)
    retry = pr_ops.create_pr(wid, config, title="Legacy feature", branch=branch, open_pr=False)
    if ambiguous:
        assert "ambiguous push" in retry["error"]
        assert not load(wid).pr.head_sha
    else:
        assert retry["success"], retry
        current = load(wid)
        assert current.pr.head_sha == pr_publish._tip("origin", branch, str(wt))


def test_title_only_rewrite_uses_selected_ledger(pr_repo, monkeypatch, tmp_path):
    config, wid, wt, remote = pr_repo
    selected = cfg.tracking_dir()
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    shadow_path = ambient / f"{wid}.yaml"
    shadow = replace(load(wid), title="Ambient")
    tracking.save_record(shadow, shadow_path)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: selected if name == config.repo_name else ambient)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    args = cli.build_parser().parse_args([
        "push-changes", wid, "--config", "selected.yaml", "--rewrite-pr", "--title-only", "--title", "Selected",
    ])
    assert finalize_cli.cmd_push_changes(args) == 0
    assert tracking.load_record(selected / f"{wid}.yaml").title == "Selected"
    assert tracking.load_record(shadow_path).title == "Ambient"


def authority_process(root, ready, *, probe=False):
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
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


@pytest.mark.parametrize("operation", ["first", "ordinary", "provider"])
def test_publication_contention_precedes_external_mutation(pr_repo, monkeypatch, tmp_path, operation):
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
                    pr_ops._open_via_provider(
                        result, config, record, record.pr, "Feature", "", wid, record.pr.head_sha,
                        prcfg=config.default_repo.pr, draft=True,
                    )
    finally:
        holder.communicate(input=b"", timeout=10)


@pytest.mark.parametrize("operation", ["first", "ordinary", "provider"])
def test_publication_holds_authority_through_io_and_persistence(pr_repo, monkeypatch, tmp_path, operation):
    config, wid, wt, remote = pr_repo
    if operation != "first":
        result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
        assert result["success"], result
    record = load(wid)
    real_push = git_ops.push
    probes = []

    def probe():
        child = authority_process(cfg.install_dir(), tmp_path / "probe-ready", probe=True)
        _, stderr = child.communicate(timeout=10)
        assert child.returncode == 3, stderr.decode()
        probes.append(True)

    def push(*args, **kwargs):
        probe()
        return real_push(*args, **kwargs)

    if operation == "provider":
        def create(*args, **kwargs):
            probe()
            return PullResult(number=42, url="https://example.test/org/repo/pull/42", state="open")
        monkeypatch.setattr(providers, "get_provider", lambda *a: SimpleNamespace(create_pull=create))
        monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: "")
        pr_ops._open_via_provider(
            result, config, record, record.pr, "Feature", "", wid, record.pr.head_sha,
            prcfg=config.default_repo.pr, draft=True,
        )
        assert load(wid).pr.number == 42
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
        assert current.pr.head_sha == pr_publish._tip("origin", current.pr.branch, str(wt))
    assert probes


def test_ordinary_publication_rechecks_authority_after_admission_wait(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
    assert result["success"], result
    before = load(wid).pr.head_sha
    original = pr_authority.guard
    changed = False

    @contextmanager
    def changed_before_admission(*args, **kwargs):
        nonlocal changed
        with original():
            if not changed:
                changed = True
                current = load(wid)
                current.pr.branch = "feature/changed-aaaa"
                current.pr.pr_revision += 1
                tracking.save_record(current)
            yield

    monkeypatch.setattr(pr_authority, "guard", changed_before_admission)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("stale authority reached push"))
    assert not finalize.push_changes(wid, config)
    assert load(wid).pr.head_sha == before
    assert not (Path(config.default_repo.worktree_root) / ".finalize.lock").exists()


def test_ownership_stamping_merges_attribution_only_revision(pr_repo):
    config, wid, wt, remote = pr_repo
    result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
    assert result["success"], result
    record = load(wid)
    record.pr.rewrite_owner = record.pr.rewrite_identity = ""
    record.pr.attribution_mode = ""
    record.pr.pr_revision += 1
    tracking.save_record(record)
    expected = load(wid)
    fresh = load(wid)
    tracking.stamp_frozen_attribution(fresh.pr, attribution=False, explicit=True, assign_pr_id=False)
    tracking.save_record(fresh)
    error = pr_publish.record_rewrite_ownership(
        config, expected, expected.pr, expected.pr.branch, expected.pr.head_sha,
        pr_publish.push_identity("origin", cwd=str(wt)),
    )
    assert not error
    assert expected.pr.rewrite_owner and expected.pr.attribution_mode == "false"
    assert load(wid).pr.rewrite_owner == expected.pr.rewrite_owner


@pytest.mark.parametrize("stamp", ["attribution", "observation"])
def test_rewrite_admission_allows_non_authority_stamp(pr_repo, monkeypatch, stamp):
    config, wid, wt, remote = pr_repo
    result = pr_ops.create_pr(wid, config, title="Feature", open_pr=False)
    assert result["success"], result
    record = load(wid)
    record.pr.attribution_mode = ""
    record.pr.pr_revision += 1
    tracking.save_record(record)
    (wt / "followup.txt").write_text("followup\n")
    git("add", "-A", cwd=wt)
    git("commit", "-m", "followup", cwd=wt)
    acquire = finalize.FinalizeLock.acquire

    def stamped_acquire(lock):
        acquire(lock)
        fresh = load(wid)
        if stamp == "attribution":
            tracking.stamp_frozen_attribution(fresh.pr, attribution=False, explicit=True, assign_pr_id=False)
        else:
            fresh.pr.head_observed_at = "2026-10-09T00:00:00Z"
        tracking.save_record(fresh)

    monkeypatch.setattr(finalize.FinalizeLock, "acquire", stamped_acquire)
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    current = load(wid)
    assert current.pr.head_sha == pr_publish._tip("origin", current.pr.branch, str(wt))
    if stamp == "attribution":
        assert current.pr.attribution_mode == "false"


@pytest.mark.parametrize("native,canonical", [("active", "open"), ("completed", "merged"), ("abandoned", "closed")])
def test_azure_create_and_observe_use_canonical_lifecycle(monkeypatch, native, canonical):
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


def test_azure_unknown_lifecycle_is_explicit_error():
    from agent_worktrees.providers.azure_devops import AzureDevOpsProvider
    from agent_worktrees.providers.base import ProviderError

    with pytest.raises(ProviderError, match="Unknown Azure DevOps PR status"):
        AzureDevOpsProvider._canonical_state("future")
