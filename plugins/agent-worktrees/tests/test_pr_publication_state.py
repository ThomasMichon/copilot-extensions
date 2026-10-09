"""Regressions for provider identity, attribution-only races, and first-push retry."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, finalize_cli, git_ops, pr_ops, pr_publish, pr_rewrite, providers, tracking
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
