"""One-command migration of attested pre-generation publication records."""

from dataclasses import replace

import pytest
import yaml

from agent_worktrees import config as cfg
from agent_worktrees import finalize_cli, git_ops, pr_ops, pr_publish, pr_rewrite, tracking
from agent_worktrees import __main__ as cli
from agent_worktrees.providers.base import PullResult
from test_pr_rewrite import git, record_for

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr_publish.legacy_rewrite"),
    pytest.mark.timeout(120),
]


def legacy_record(wid, *, state="active", defect=""):
    path = cfg.tracking_dir() / f"{wid}.yaml"
    data = yaml.safe_load(path.read_text())
    pr = data["prs"][-1]
    pr["head_identity"] = pr["rewrite_identity"]
    pr.update(state=state, number=42, url="https://example.test/pulls/42")
    for key in ("rewrite_owner", "rewrite_identity", "pr_revision"):
        pr.pop(key, None)
    if defect == "unattested":
        pr.pop("head_identity", None)
    elif defect == "corrected":
        pr["pr_revision"] = 1
    elif defect == "partial":
        pr["rewrite_owner"] = f"{wid}:{pr['branch']}"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
def test_legacy_rewrite_single_public_command_after_rebase(rebased_pr_repo, scheme, monkeypatch):
    config, wid, wt, remote, old = rebased_pr_repo
    legacy_record(wid)
    rec = record_for(wid)
    assert rec.active_pr().state == "active"
    assert not rec.pr.rewrite_owner and rec.pr.pr_revision == 0
    source = git("rev-parse", "HEAD", cwd=wt)
    assert source != old and not git_ops.is_commit_ancestor(old, source, cwd=str(wt))
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(head_sha=old))
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    monkeypatch.chdir(wt)
    args = cli.build_parser().parse_args(["push-changes", wid, "--rewrite-pr"])
    assert finalize_cli.cmd_push_changes(args) == 0
    updated = record_for(wid)
    assert updated.pr.head_sha == source
    assert updated.pr.rewrite_owner == f"{wid}:{updated.pr.branch}"
    assert updated.pr.rewrite_identity == rec.pr.head_identity
    assert updated.pr.pr_revision > 0
    assert pr_publish._tip("origin", updated.pr.branch, str(wt)) == source
    assert git("rev-parse", "HEAD", cwd=wt) == source


@pytest.mark.parametrize("revision", [1, 17])
def test_revised_legacy_record_can_intentionally_rewrite_its_own_head(rebased_refspec_pr_repo, revision, monkeypatch):
    config, wid, wt, remote, old = rebased_refspec_pr_repo
    path = legacy_record(wid, defect="corrected")
    data = yaml.safe_load(path.read_text())
    data["prs"][-1]["pr_revision"] = revision
    path.write_text(yaml.safe_dump(data))
    rec = record_for(wid)
    source = git("rev-parse", "HEAD", cwd=wt)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
        number=42, head_sha=old, head_ref=rec.pr.branch, head_url=str(remote),
    ))
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    monkeypatch.chdir(wt)
    args = cli.build_parser().parse_args(["push-changes", wid, "--rewrite-pr"])
    assert finalize_cli.cmd_push_changes(args) == 0
    updated = record_for(wid)
    assert updated.pr.head_sha == source
    assert updated.pr.rewrite_owner == f"{wid}:{updated.pr.branch}"
    assert updated.pr.rewrite_identity == rec.pr.head_identity
    assert updated.pr.pr_revision > revision
    assert git("rev-parse", f"refs/heads/{rec.pr.branch}", cwd=remote) == source


@pytest.mark.parametrize("defect", ["number", "branch", "destination", "moved", "shared"])
def test_revised_legacy_rewrite_requires_exact_live_authority(rebased_refspec_pr_repo, defect, monkeypatch):
    config, wid, wt, remote, old = rebased_refspec_pr_repo
    path = legacy_record(wid, defect="corrected")
    rec = record_for(wid)
    observed = PullResult(number=42, head_sha=old, head_ref=rec.pr.branch, head_url=str(remote))
    if defect == "number":
        observed.number = 43
    elif defect == "branch":
        observed.head_ref = "pr/another-owner"
    elif defect == "destination":
        observed.head_url = str(remote.parent / "another.git")
    elif defect == "moved":
        observed.head_sha = "a" * 40
    elif defect == "shared":
        other = tracking.create_new_record(
            "other", "worktree/other", str(wt.parent / "other"),
            rec.repo, "test", "linux", cfg.tracking_dir(),
        )
        other.prs = [replace(rec.pr)]
        tracking.save_record(other)
    before = path.read_bytes()
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: observed)
    assert not pr_rewrite.push_changes(wid, config, rec)
    assert path.read_bytes() == before
    assert git("rev-parse", f"refs/heads/{rec.pr.branch}", cwd=remote) == old


@pytest.mark.parametrize("defect", ["unattested", "partial", "provider-moved", "shared"])
def test_legacy_rewrite_refuses_incompatible_evidence(rebased_refspec_pr_repo, defect, monkeypatch):
    config, wid, wt, remote, old = rebased_refspec_pr_repo
    path = legacy_record(wid, defect=defect)
    rec = record_for(wid)
    if defect == "shared":
        other = tracking.create_new_record(
            "other", "worktree/other", str(wt.parent / "other"),
            rec.repo, "test", "linux", cfg.tracking_dir(),
        )
        other.prs = [replace(rec.pr)]
        tracking.save_record(other)
    before = path.read_bytes()
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
        head_sha="a" * 40 if defect == "provider-moved" else old,
    ))
    assert not pr_rewrite.push_changes(wid, config, rec)
    assert path.read_bytes() == before
    assert git("rev-parse", f"refs/heads/{rec.pr.branch}", cwd=remote) == old


@pytest.mark.parametrize("state,expected,terminal", [
    ("active", "active", False), ("completed", "completed", True), ("abandoned", "abandoned", True),
])
def test_legacy_provider_state_normalization(pr_repo, state, expected, terminal):
    config, wid, wt, remote = pr_repo
    assert pr_ops.create_pr(wid, config, title="Feature")["success"]
    legacy_record(wid, state=state)
    rec = record_for(wid)
    assert rec.prs[-1].state == expected
    assert tracking._pr_is_terminal(rec.prs[-1]) is terminal


@pytest.mark.parametrize("url,number,expected", [
    ("https://github.com/example/project/pull/42", 42, "github.com/example/project"),
    ("https://forge.test/example/project/pulls/42", 42, "forge.test/example/project"),
    ("https://dev.azure.com/org/proj/_git/repo/pullrequest/42", 42, "dev.azure.com/org/proj/_git/repo"),
    ("https://github.com/example/project/pull/43", 42, ""),
    ("http://github.com/example/project/pull/42", 42, ""),
])
def test_legacy_destination_from_numbered_provider_url(url, number, expected):
    assert pr_rewrite.legacy_identity(tracking.PRRecord(url=url, number=number), ".") == expected
