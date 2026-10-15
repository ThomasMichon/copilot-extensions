"""Recovery previews distinguish stale bookkeeping from the agreed live PR head."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, git_collab, git_ops, pr_break_glass, pr_ops, pr_publish, pr_recovery, pr_rewrite, tracking
from agent_worktrees.config import Config
from agent_worktrees.providers.base import PullResult
from test_pr_break_glass import _recovered_pr

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr.operator_recovery"),
    pytest.mark.timeout(120),
]

REASON = "Recover the exact inspected publication"
PrRepo = tuple[Config, str, Path, Path]


def git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def record(wid: str) -> tracking.WorktreeRecord:
    return tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")


def preview(wid: str, config: Config, confirmation: str | None = None) -> dict:
    with pytest.raises(pr_break_glass.ConfirmationRequired) as required:
        finalize.push_changes(
            wid, config, break_glass_reason=REASON, confirm_break_glass=confirmation,
        )
    return required.value.preview


def advance(wt: Path, remote: Path, branch: str, old: str) -> str:
    advanced = git("rev-parse", "origin/master", cwd=wt)
    git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", advanced, old, cwd=wt)
    return advanced


def metadata(wt: Path) -> Path:
    return Path(git("rev-parse", "--absolute-git-dir", cwd=wt))


def test_advanced_head_preview_is_no_effect_and_keeps_normal_refusal(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    actual = advance(wt, remote, branch, saved)
    assert not finalize.push_changes(wid, config)
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    path = cfg.tracking_dir() / f"{wid}.yaml"
    before = path.read_bytes(), git("show-ref", cwd=wt), list(metadata(wt).glob("*break-glass*"))
    operation = preview(wid, config)["operation"]
    assert operation["saved_head"] == saved
    assert operation["expected_head"] == actual
    assert operation["source_head"] == git("rev-parse", "HEAD", cwd=wt)
    assert operation["action"] == "rewrite"
    assert operation["publication_fingerprint"]
    assert operation["evidence_fingerprint"] == ""
    assert (path.read_bytes(), git("show-ref", cwd=wt), list(metadata(wt).glob("*break-glass*"))) == before


def test_confirmed_advanced_head_retains_actual_objects_and_original_evidence(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    actual = advance(wt, remote, branch, saved)
    source = git("rev-parse", "HEAD", cwd=wt)
    operation = preview(wid, config)
    assert finalize.push_changes(
        wid, config, break_glass_reason=REASON, confirm_break_glass=operation["confirmation"],
    )
    assert pr_publish._tip("origin", branch, str(wt)) == source
    assert record(wid).pr.head_sha == source
    audit = json.loads(next(metadata(wt).glob("*break-glass*.json")).read_text())
    assert audit["operation"] == json.loads(json.dumps(operation["operation"]))
    assert audit["saved_publication"]["head_sha"] == saved
    assert audit["saved_publication"]["branch"] == branch
    assert audit["outcome"] == "succeeded"
    assert git("rev-parse", audit["recovery_refs"]["source"], cwd=wt) == source
    assert git("rev-parse", audit["recovery_refs"]["published"], cwd=wt) == actual


@pytest.mark.parametrize("change", ["saved-head", "saved-base", "source", "remote"])
def test_changed_bound_inputs_require_new_confirmation(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    actual = advance(wt, remote, branch, saved)
    first = preview(wid, config)
    if change.startswith("saved"):
        current = record(wid)
        if change == "saved-head":
            current.pr.head_sha = actual
        else:
            current.pr.base_sha = actual
        tracking.save_record(current)
    elif change == "source":
        (wt / "later.txt").write_text("unapproved source\n")
        git("add", "later.txt", cwd=wt)
        git("commit", "-m", "later source", cwd=wt)
    else:
        git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", saved, actual, cwd=wt)
    changed = preview(wid, config, first["confirmation"])
    assert changed["confirmation"] != first["confirmation"]
    assert not list(metadata(wt).glob("*break-glass*.json"))


def test_provider_and_ref_must_agree_before_advanced_preview(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    advance(wt, remote, branch, saved)
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
        number=42, head_sha=saved, head_ref=branch, head_url=str(remote),
    ))
    assert not finalize.push_changes(wid, config, break_glass_reason=REASON)
    assert record(wid).pr.head_sha == saved
    assert not list(metadata(wt).glob("*break-glass*.json"))


def test_recovery_cas_never_falls_back_to_unapproved_fast_forward(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    actual = advance(wt, remote, branch, saved)
    source = git("rev-parse", "HEAD", cwd=wt)
    racing = git("rev-parse", "origin/master^", cwd=wt)
    first = preview(wid, config)
    real_push = git_ops.push
    calls = []
    failures = []

    def race(*args: object, **kwargs: object) -> git_ops.PushResult:
        calls.append(kwargs)
        # This new remote tip is an ancestor of the approved source, but was
        # never the lease the operator inspected.
        git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", racing, actual, cwd=wt)
        result = real_push(*args, **kwargs)
        failures.append(result.stderr)
        return result

    monkeypatch.setattr(git_ops, "push", race)
    assert not finalize.push_changes(
        wid, config, break_glass_reason=REASON, confirm_break_glass=first["confirmation"],
    )
    assert len(calls) == 1
    assert "stale info" in failures[0]
    assert calls[0]["force_with_lease_expect"] == actual
    assert pr_publish._tip("origin", branch, str(wt)) == racing != source
    assert record(wid).pr.head_sha == saved


def backed_publication(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, *, with_base: bool = False,
) -> tuple[Config, str, Path, Path, str, str, str]:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    if with_base:
        current = record(wid)
        branch = current.pr.branch = f"pr/owned-topic-{git_ops.worktree_suffix(wid)}"
        current.pr.base_sha = git("merge-base", saved, "origin/master", cwd=wt)
        current.pr.patch_id = pr_ops._patch_id(current.pr.base_sha, saved, cwd=str(wt))
        tracking.save_record(current)
        git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", saved, cwd=wt)
    git("reset", "--hard", saved, cwd=wt)
    assert git_collab.sync_forward(wid, config)
    published = git("rev-parse", "HEAD", cwd=wt)
    git("--git-dir", str(remote), "fetch", "--no-tags", str(wt), published, cwd=wt)
    git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", published, saved, cwd=wt)
    (wt / "unpublished.txt").write_text("keep this unpublished\n")
    git("add", "unpublished.txt", cwd=wt)
    git("commit", "-m", "local follow-up", cwd=wt)
    return config, wid, wt, remote, saved, branch, published


def test_proven_prior_publication_settles_without_push_or_forge_edit(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch, published = backed_publication(pr_repo, monkeypatch)
    first = preview(wid, config)
    assert first["operation"]["action"] == "settle-publication"
    assert first["operation"]["evidence_fingerprint"]
    assert first["operation"]["expected_head"] == published
    source = git("rev-parse", "HEAD", cwd=wt)

    def no_transport(*args: object, **kwargs: object) -> None:
        pytest.fail("Settlement must not push or edit provider attribution.")

    monkeypatch.setattr(git_ops, "push", no_transport)
    monkeypatch.setattr(pr_rewrite.pr_publish, "record_pushed_head", no_transport)
    monkeypatch.setattr(pr_rewrite.providers, "get_provider", no_transport)
    assert finalize.push_changes(
        wid, config, break_glass_reason=REASON, confirm_break_glass=first["confirmation"],
    )
    assert record(wid).pr.head_sha == published != source
    assert git("rev-parse", "HEAD", cwd=wt) == source
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == published
    audit = json.loads(next(metadata(wt).glob("*break-glass*.json")).read_text())
    assert audit["saved_publication"]["head_sha"] == saved
    assert audit["operation"]["action"] == "settle-publication"
    assert audit["outcome"] == "succeeded"


def test_mutable_checkpoint_target_is_unusable_settlement_evidence(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch, published = backed_publication(pr_repo, monkeypatch)
    path = pr_recovery._path(str(wt))
    data = json.loads(path.read_text())
    data["checkpoints"][record(wid).pr.pr_id]["target_head"] = "refs/remotes/origin/master"
    path.write_text(json.dumps(data))
    first = preview(wid, config)
    assert first["operation"]["action"] == "rewrite"
    assert first["operation"]["evidence_status"] == "unusable"
    assert first["operation"]["rebase_base_sha"] == ""
    assert pr_publish._tip("origin", branch, str(wt)) == published
    assert record(wid).pr.head_sha == saved


def test_changed_backed_evidence_invalidates_settlement_confirmation(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, _remote, _saved, _branch, _published = backed_publication(pr_repo, monkeypatch)
    first = preview(wid, config)
    data = json.loads(pr_recovery._path(str(wt)).read_text())
    point = data["checkpoints"][data["latest"]]
    # The source and destination stay identical; only the claimed proof changes.
    point["target_head"] = point["synced_head"]
    pr_recovery._path(str(wt)).write_text(json.dumps(data))
    changed = preview(wid, config, first["confirmation"])
    assert changed["confirmation"] != first["confirmation"]
    assert not list(metadata(wt).glob("*break-glass*.json"))


def test_settlement_updates_base_and_patch_before_next_sync_publication(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, _saved, branch, published = backed_publication(
        pr_repo, monkeypatch, with_base=True,
    )
    original_base = record(wid).pr.base_sha
    target = git("rev-parse", "origin/master", cwd=wt)
    first = preview(wid, config)
    assert original_base and original_base != target
    assert first["operation"]["action"] == "settle-publication"
    assert first["operation"]["rebase_base_sha"] == target
    with monkeypatch.context() as no_transport:
        no_transport.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Settlement must not push."))
        no_transport.setattr(pr_rewrite.providers, "get_provider", lambda *a: pytest.fail("No provider edits."))
        assert finalize.push_changes(
            wid, config, break_glass_reason=REASON, confirm_break_glass=first["confirmation"],
        )
    settled = record(wid).pr
    assert settled.head_sha == published
    assert settled.base_sha == target
    assert settled.patch_id == pr_ops._patch_id(target, published, cwd=str(wt))
    assert settled.patch_id != pr_ops._patch_id(original_base, published, cwd=str(wt))
    anchor = Path(config.default_repo.anchor)
    (anchor / "next-upstream.txt").write_text("later upstream work\n")
    git("add", "next-upstream.txt", cwd=anchor)
    git("commit", "-m", "advance upstream again", cwd=anchor)
    git("push", "origin", "master", cwd=anchor)
    assert git_collab.sync_forward(wid, config)
    assert finalize.push_changes(wid, config)
    current = record(wid).pr
    assert current.head_sha == git("rev-parse", "HEAD", cwd=wt)
    assert current.base_sha == git("rev-parse", "origin/master", cwd=wt) != target
    assert current.patch_id == pr_ops._patch_id(current.base_sha, current.head_sha, cwd=str(wt))
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == current.head_sha
    assert git("show", f"{current.head_sha}:unpublished.txt", cwd=wt) == "keep this unpublished"


def test_remote_equal_to_source_without_proof_is_not_automatic_settlement(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    source = git("rev-parse", "HEAD", cwd=wt)
    git("--git-dir", str(remote), "fetch", "--no-tags", str(wt), source, cwd=wt)
    git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", source, saved, cwd=wt)
    assert preview(wid, config)["operation"]["action"] == "rewrite"


@pytest.mark.parametrize("changed", ["provider", "tracking", "source", "remote", "destination"])
def test_confirmed_plan_revalidates_after_retention_before_effect(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    real_push_slug = pr_publish.push_slug
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    if changed == "destination":
        # Keep real fetch/push destination validation; the common fixture only
        # stubs slug extraction because local paths have no provider slug.
        monkeypatch.setattr(git_ops, "slug_from_url", lambda *a: "example/project")
        monkeypatch.setattr(pr_publish, "push_slug", real_push_slug)
    actual = advance(wt, remote, branch, saved)
    first = preview(wid, config)
    real_retain = pr_recovery.retain_rewrite

    def interpose(*args: object, **kwargs: object) -> pr_recovery.RecoveryPoint:
        point = real_retain(*args, **kwargs)
        if changed == "provider":
            monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
                number=42, head_sha=saved, head_ref=branch, head_url=str(remote),
            ))
        elif changed == "tracking":
            current = record(wid)
            current.pr.base_sha = actual
            tracking.save_record(current)
        elif changed == "source":
            (wt / "interposed.txt").write_text("late source\n")
            git("add", "interposed.txt", cwd=wt)
            git("commit", "-m", "late source", cwd=wt)
        elif changed == "remote":
            git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", saved, actual, cwd=wt)
        else:
            git("config", "remote.origin.pushurl", str(remote.parent / "elsewhere.git"), cwd=wt)
        return point

    monkeypatch.setattr(pr_recovery, "retain_rewrite", interpose)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Changed plan must not push."))
    assert not finalize.push_changes(
        wid, config, break_glass_reason=REASON, confirm_break_glass=first["confirmation"],
    )
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == (
        saved if changed == "remote" else actual
    )


@pytest.mark.guard
def test_plan_fields_bind_action_original_record_and_evidence() -> None:
    operation = pr_break_glass.RecoveryPreview(
        provider="github", repo="example/project", number=42, branch="pr/topic",
        destination_fingerprint="d" * 64, source_head="a" * 40, expected_head="b" * 40,
        saved_head="c" * 40, publication_fingerprint="e" * 64,
        action="settle-publication", evidence_fingerprint="f" * 64,
    )
    for change in (
        {"saved_head": "a" * 40}, {"publication_fingerprint": "a" * 64},
        {"action": "rewrite"}, {"evidence_fingerprint": "a" * 64},
        {"rebase_base_sha": "b" * 40},
    ):
        assert replace(operation, **change).confirmation(REASON) != operation.confirmation(REASON)


def test_remote_only_advanced_commit_is_fetched_only_after_confirmation(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    other = remote.parent / "prior-publisher"
    git("clone", str(remote), str(other), cwd=remote.parent)
    git("config", "user.email", "publisher@example.com", cwd=other)
    git("config", "user.name", "Prior publisher", cwd=other)
    git("checkout", "-b", "advanced", saved, cwd=other)
    (other / "remote-only.txt").write_text("prior publication\n")
    git("add", "remote-only.txt", cwd=other)
    git("commit", "-m", "prior publication", cwd=other)
    actual = git("rev-parse", "HEAD", cwd=other)
    git("--git-dir", str(remote), "fetch", "--no-tags", str(other), actual, cwd=wt)
    git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", actual, saved, cwd=wt)
    assert not git_ops.ref_exists(actual, cwd=str(wt))
    first = preview(wid, config)
    assert first["operation"]["expected_head"] == actual
    assert not git_ops.ref_exists(actual, cwd=str(wt))
    assert finalize.push_changes(
        wid, config, break_glass_reason=REASON, confirm_break_glass=first["confirmation"],
    )
    audit = json.loads(next(metadata(wt).glob("*break-glass*.json")).read_text())
    assert git("rev-parse", audit["recovery_refs"]["published"], cwd=wt) == actual


@pytest.mark.parametrize("failure", ["tracking", "finish"])
def test_settlement_failures_report_bookkeeping_effect_without_claiming_a_push(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    config, wid, wt, _remote, saved, branch, published = backed_publication(pr_repo, monkeypatch)
    first = preview(wid, config)
    if failure == "tracking":
        def denied(*args: object, **kwargs: object) -> None:
            raise OSError("settlement persistence denied")
        monkeypatch.setattr(pr_publish, "record_reconciled_head", denied)
    else:
        real_write = pr_break_glass.RecoveryAudit._write

        def denied_finish(audit: pr_break_glass.RecoveryAudit) -> None:
            if audit.outcome != "attempted":
                raise OSError("audit completion denied")
            real_write(audit)
        monkeypatch.setattr(pr_break_glass.RecoveryAudit, "_write", denied_finish)
    capfd.readouterr()
    assert not finalize.push_changes(
        wid, config, break_glass_reason=REASON, confirm_break_glass=first["confirmation"],
    )
    captured = capfd.readouterr()
    text = captured.out + captured.err
    assert "Remote head was published" not in text
    assert pr_publish._tip("origin", branch, str(wt)) == published
    assert record(wid).pr.head_sha == (published if failure == "finish" else saved)
    if failure == "finish":
        assert "bookkeeping was settled without a push; audit needs reconciliation" in text


def test_advanced_head_cli_returns_actual_plan_with_exit_two(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import __main__ as cli, finalize_cli

    config, wid, wt, remote, saved, branch = _recovered_pr(pr_repo, monkeypatch)
    actual = advance(wt, remote, branch, saved)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.chdir(wt)
    capfd.readouterr()
    args = cli.build_parser().parse_args([
        "push-changes", wid, "--break-glass-reason", REASON, "--json",
    ])
    assert finalize_cli.cmd_push_changes(args) == 2
    payload = json.loads(capfd.readouterr().out)
    assert payload["success"] is False
    assert payload["confirmation_required"] is True
    assert payload["operation"]["expected_head"] == actual
    assert payload["operation"]["saved_head"] == saved
    assert payload["operation"]["action"] == "rewrite"
    assert record(wid).pr.head_sha == saved
    assert not list(metadata(wt).glob("*break-glass*.json"))
