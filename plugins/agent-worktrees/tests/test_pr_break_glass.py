"""Immutable preview binding and private, truthful recovery audit contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from pr_test_helpers import _stub_remote_target

from agent_worktrees import pr_break_glass as recovery
from agent_worktrees import config as cfg, finalize, finalize_cli, git_ops, pr_ops, pr_publish, pr_rewrite, tracking
from agent_worktrees.providers.base import PullResult

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr.operator_recovery"),
]


@pytest.fixture
def operation() -> recovery.RecoveryPreview:
    return recovery.RecoveryPreview(
        provider="github", repo="example/project", number=42,
        branch="pr/recovered-plan-old-source",
        destination_fingerprint=hashlib.sha256(b"example destination").hexdigest(),
        source_head="a" * 40, expected_head="b" * 40,
    )


@pytest.mark.guard
def test_unconfirmed_preview_mutates_nothing(
    operation: recovery.RecoveryPreview, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_write(*args: object, **kwargs: object) -> None:
        pytest.fail("An unconfirmed preview must not write audit or tracking data.")

    monkeypatch.setattr(recovery.tracking, "_atomic_write", unexpected_write)
    before = list(tmp_path.iterdir())
    with pytest.raises(recovery.ConfirmationRequired) as error:
        recovery.confirm(operation, "Operator-authorized recovery", None)
    assert error.value.preview["confirmation_required"] is True
    assert error.value.preview["operation"]["source_head"] == operation.source_head
    assert error.value.preview["operation"]["expected_head"] == operation.expected_head
    assert list(tmp_path.iterdir()) == before


@pytest.mark.guard
@pytest.mark.parametrize("changes", [
    {"source_head": "c" * 40},
    {"expected_head": "c" * 40},
    {"repo": "example/other"},
    {"number": 43},
    {"branch": "pr/another-target"},
    {"provider": "gitea"},
    {"destination_fingerprint": "c" * 64},
])
def test_changed_operation_requires_fresh_signoff(
    operation: recovery.RecoveryPreview, changes: dict[str, object],
) -> None:
    reason = "Operator-authorized recovery"
    confirmation = operation.confirmation(reason)
    changed = replace(operation, **changes)
    with pytest.raises(recovery.ConfirmationRequired, match="no longer matches"):
        recovery.confirm(changed, reason, confirmation)


@pytest.mark.guard
def test_confirmation_binds_reason_and_preserves_approved_source(
    operation: recovery.RecoveryPreview,
) -> None:
    reason = "Operator-authorized recovery"
    confirmation = operation.confirmation(reason)
    approved = recovery.confirm(operation, f"  {reason}  ", confirmation)
    assert approved.operation.source_head == operation.source_head
    assert approved.operation.expected_head == operation.expected_head
    assert approved.reason == reason
    with pytest.raises(recovery.ConfirmationRequired):
        recovery.confirm(operation, "Different recovery decision", confirmation)


@pytest.mark.guard
@pytest.mark.parametrize("reason", ["", " ", "\n\t"])
def test_blank_reason_is_not_confirmation(
    operation: recovery.RecoveryPreview, reason: str,
) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        recovery.confirm(operation, reason, None)


@pytest.mark.guard
@pytest.mark.parametrize("changes", [
    {"number": 0},
    {"number": True},
    {"repo": "https://example.test/project.git"},
    {"provider": ""},
    {"branch": " "},
    {"source_head": "short"},
    {"expected_head": ""},
    {"destination_fingerprint": "short"},
    {"saved_head": "short"},
    {"publication_fingerprint": "short"},
    {"evidence_fingerprint": "short"},
    {"action": "refresh-lease"},
    {"action": "settle-publication"},
    {"evidence_status": "assumed"},
])
def test_malformed_operation_is_explicit(changes: dict[str, object]) -> None:
    fields = {
        "provider": "github", "repo": "example/project", "number": 42,
        "branch": "pr/recovered-plan",
        "destination_fingerprint": "d" * 64,
        "source_head": "a" * 40, "expected_head": "b" * 40,
    }
    with pytest.raises(ValueError):
        recovery.RecoveryPreview(**(fields | changes))


@pytest.mark.guard
def test_audit_records_observed_invoker_without_claiming_identity(
    operation: recovery.RecoveryPreview, tmp_path: Path,
) -> None:
    reason = "Operator-authorized recovery"
    approved = recovery.confirm(operation, reason, operation.confirmation(reason))
    invoker = recovery.InvocationAttribution(
        user="example-user", session_id="example-session", worktree_id="recovery-context",
    )
    audit = recovery.begin_audit(tmp_path, approved, invoker)
    attempted = json.loads(audit.path.read_text(encoding="utf-8"))
    assert attempted["outcome"] == "attempted"
    assert attempted["invoker"]["user"] == "example-user"
    assert attempted["invoker"]["session_id"] == "example-session"
    assert attempted["invoker"]["provenance"] == "observed"
    assert attempted["invoker"]["authenticated_identity"] is False
    assert attempted["operation"]["source_head"] == operation.source_head
    assert "://" not in audit.path.read_text(encoding="utf-8")
    assert audit.finish("succeeded") is audit
    completed = json.loads(audit.path.read_text(encoding="utf-8"))
    assert completed["outcome"] == "succeeded"
    assert completed["finished_at"] is not None
    with pytest.raises(ValueError, match="completed again"):
        audit.finish("failed")


@pytest.mark.guard
def test_unknown_invoker_stays_unknown(operation: recovery.RecoveryPreview, tmp_path: Path) -> None:
    reason = "Operator-authorized recovery"
    approved = recovery.confirm(operation, reason, operation.confirmation(reason))
    audit = recovery.begin_audit(tmp_path, approved, recovery.InvocationAttribution())
    payload = json.loads(audit.path.read_text(encoding="utf-8"))
    assert payload["invoker"] == {
        "user": None, "session_id": None, "worktree_id": None,
        "provenance": "unknown", "authenticated_identity": False,
    }


@pytest.mark.guard
@pytest.mark.parametrize("error", [OSError("caller user unavailable"), KeyError("unknown uid")])
def test_unavailable_caller_context_is_observed_as_unknown(monkeypatch, error):
    def unavailable_user():
        raise error

    monkeypatch.setattr(recovery.getpass, "getuser", unavailable_user)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: subprocess.CompletedProcess(
        ["git"], 1, stdout="", stderr="not a repository",
    ))
    assert recovery.invocation_attribution().to_dict() == {
        "user": None, "session_id": None, "worktree_id": None,
        "provenance": "unknown", "authenticated_identity": False,
    }


@pytest.mark.guard
def test_audit_failure_is_not_success(
    operation: recovery.RecoveryPreview, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "Operator-authorized recovery"
    approved = recovery.confirm(operation, reason, operation.confirmation(reason))

    def fail_write(*args: object, **kwargs: object) -> None:
        raise OSError("audit write denied")

    monkeypatch.setattr(recovery.tracking, "_atomic_write", fail_write)
    with pytest.raises(OSError, match="audit write denied"):
        recovery.begin_audit(tmp_path, approved, recovery.InvocationAttribution())
    assert not list(tmp_path.iterdir())


def _recovered_pr(pr_repo, monkeypatch: pytest.MonkeyPatch, *, stub_attribution=True, attribution=None,
                  provider="gitea"):
    from test_pr_rewrite import publish_and_rebase

    monkeypatch.setattr(git_ops, "remote_slug", lambda *a, **k: "example/project")
    _stub_remote_target(monkeypatch, None, "example/project")
    if attribution is not None or provider != "gitea":
        config, wid, wt, remote = pr_repo
        config = replace(config, repos={
            config.repo_name: replace(config.default_repo, pr=replace(
                config.default_repo.pr,
                source_attribution=(
                    attribution if attribution is not None else config.default_repo.pr.source_attribution
                ),
                source_attribution_configured=(
                    attribution is not None or config.default_repo.pr.source_attribution_configured
                ),
                provider=provider,
            )),
        })
        pr_repo = config, wid, wt, remote
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    associated = pr_ops.set_pr(
        wid, url="https://gitea.example.com/example/project/pulls/42",
        number=42, provider=provider, config=config,
    )
    assert associated["success"], associated
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    record.pr.branch = "pr/recovered-plan-previous-owner"
    record.pr.rewrite_owner = ""
    record.pr.base_sha = record.pr.patch_id = ""
    tracking.save_record(record)
    git_ops.git("--git-dir", str(remote), "update-ref", f"refs/heads/{record.pr.branch}", old, cwd=str(wt))
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda config, pr: PullResult(
        number=pr.number, head_sha=pr_publish._tip("origin", pr.branch, str(wt)),
        head_ref=pr.branch, head_url=str(remote),
    ))
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    if stub_attribution:
        monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    return config, wid, wt, remote, old, record.pr.branch


@pytest.mark.guard
@pytest.mark.timeout(120)
@pytest.mark.parametrize("source_repository", ["project", "other"])
def test_azure_recovery_preview_matches_ssh_destination(
    pr_repo, monkeypatch: pytest.MonkeyPatch, source_repository: str,
) -> None:
    config, wid, wt, _remote, old, branch = _recovered_pr(pr_repo, monkeypatch, provider="azure-devops")
    record_path = cfg.tracking_dir() / f"{wid}.yaml"
    record = tracking.load_record(record_path)
    destination = pr_publish._identity("git@ssh.dev.azure.com:v3/example/team/project", str(wt))
    record.pr.rewrite_identity = destination
    tracking.save_record(record)
    _stub_remote_target(monkeypatch, None, "example/project", destination)
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
        number=42, head_sha=old, head_ref=branch,
        head_url=f"https://dev.azure.com/example/team/_git/{source_repository}",
    ))
    before = record_path.read_bytes()
    refs_before = git_ops.git("show-ref", cwd=str(wt)).stdout
    if source_repository == "project":
        with pytest.raises(recovery.ConfirmationRequired) as required:
            finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
        assert required.value.preview["operation"]["provider"] == "azure-devops"
    else:
        assert not finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    assert record_path.read_bytes() == before
    assert git_ops.git("show-ref", cwd=str(wt)).stdout == refs_before
    assert pr_publish._tip("origin", branch, str(wt)) == old


@pytest.mark.guard
@pytest.mark.timeout(120)
def test_real_recovery_requires_confirmation_and_retains_commits(
    pr_repo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    source = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    before = (cfg.tracking_dir() / f"{wid}.yaml").read_bytes()
    refs_before = git_ops.git("show-ref", cwd=str(wt)).stdout
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    assert (cfg.tracking_dir() / f"{wid}.yaml").read_bytes() == before
    assert git_ops.git("show-ref", cwd=str(wt)).stdout == refs_before
    assert pr_publish._tip("origin", branch, str(wt)) == old
    assert finalize.push_changes(
        wid, config, break_glass_reason="Approved recovered plan",
        confirm_break_glass=required.value.preview["confirmation"],
    )
    assert pr_publish._tip("origin", branch, str(wt)) == source
    assert git_ops.git("--git-dir", str(remote), "rev-parse", "master", cwd=str(wt)).stdout.strip() != source
    directory = Path(git_ops.git("rev-parse", "--absolute-git-dir", cwd=str(wt)).stdout.strip())
    audits = list(directory.glob("agent-worktrees-pr-break-glass-*.json"))
    assert len(audits) == 1
    audit = json.loads(audits[0].read_text(encoding="utf-8"))
    assert audit["outcome"] == "succeeded"
    for name, expected in (("source", source), ("published", old)):
        assert git_ops.git("rev-parse", audit["recovery_refs"][name], cwd=str(wt)).stdout.strip() == expected


@pytest.mark.timeout(120)
def test_real_recovery_refuses_unseen_local_commit(
    pr_repo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, _remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    (wt / "later.txt").write_text("not previewed\n")
    git_ops.git("add", "later.txt", cwd=str(wt))
    git_ops.git("commit", "-m", "unseen later commit", cwd=str(wt))
    with pytest.raises(recovery.ConfirmationRequired, match="no longer matches"):
        finalize.push_changes(
            wid, config, break_glass_reason="Approved recovered plan",
            confirm_break_glass=required.value.preview["confirmation"],
        )
    assert pr_publish._tip("origin", branch, str(wt)) == old


@pytest.mark.timeout(120)
def test_recovery_cli_returns_bound_preview_json(
    pr_repo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import __main__ as cli

    config, wid, wt, _remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.chdir(wt)
    capfd.readouterr()
    args = cli.build_parser().parse_args([
        "push-changes", wid, "--break-glass-reason", "Approved recovered plan", "--json",
    ])
    assert finalize_cli.cmd_push_changes(args) == 2
    payload = json.loads(capfd.readouterr().out)
    assert payload["success"] is False
    assert payload["confirmation_required"] is True
    assert payload["operation"]["source_head"]
    assert payload["operation"]["expected_head"] == old
    assert pr_publish._tip("origin", branch, str(wt)) == old


@pytest.mark.timeout(120)
def test_recovery_cli_selects_target_project_from_other_cwd(pr_repo, monkeypatch, capfd):
    from agent_worktrees import __main__ as cli

    config, wid, wt, _remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    selected = cfg.tracking_dir()
    caller = wt.parent / "caller-context"
    caller.mkdir()
    caller_ledger = caller / "tracking"
    caller_ledger.mkdir()
    config_path = caller / "selected-config.yaml"
    config_path.write_text("selected project configuration\n")
    loaded = []

    def load_config(path):
        loaded.append(path)
        return config

    monkeypatch.setattr(cfg, "load_config", load_config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: selected if name == config.repo_name else caller_ledger)
    monkeypatch.chdir(caller)
    before = (selected / f"{wid}.yaml").read_bytes()
    capfd.readouterr()
    args = cli.build_parser().parse_args([
        "push-changes", wid, "--config", str(config_path),
        "--break-glass-reason", "Approved recovered plan", "--json",
    ])
    assert finalize_cli.cmd_push_changes(args) == 2
    payload = json.loads(capfd.readouterr().out)
    assert loaded == [config_path]
    assert payload["operation"]["expected_head"] == old
    assert payload["operation"]["branch"] == branch
    assert (selected / f"{wid}.yaml").read_bytes() == before
    assert not list(caller_ledger.iterdir())
    assert pr_publish._tip("origin", branch, str(wt)) == old


@pytest.mark.timeout(120)
@pytest.mark.parametrize("defect", ["moved", "deleted", "provider-head", "protected", "dirty", "hook"])
def test_recovery_keeps_transport_and_protected_history_gates(
    pr_repo, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    config, wid, wt, remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    if defect == "moved":
        moved = git_ops.git("rev-parse", "origin/master", cwd=str(wt)).stdout.strip()
        git_ops.git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", moved, old, cwd=str(wt))
    elif defect == "deleted":
        git_ops.git("--git-dir", str(remote), "update-ref", "-d", f"refs/heads/{branch}", old, cwd=str(wt))
    elif defect == "provider-head":
        monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
            number=42, head_sha=old, head_ref="pr/unrelated", head_url=str(remote),
        ))
    elif defect == "protected":
        monkeypatch.setattr(pr_rewrite.hooks, "_protected_branches", lambda *a: [branch])
    elif defect == "dirty":
        (wt / "uncommitted.txt").write_text("not committed\n")
    else:
        hook_dir = wt.parent / "rejecting-hooks"
        hook_dir.mkdir()
        hook = hook_dir / "pre-push"
        hook.write_text("#!/bin/sh\necho recovery-hook-refusal >&2\nexit 1\n")
        hook.chmod(0o755)
        git_ops.git("config", "core.hooksPath", str(hook_dir), cwd=str(wt))
    if defect == "moved":
        with pytest.raises(recovery.ConfirmationRequired, match="no longer matches"):
            finalize.push_changes(
                wid, config, break_glass_reason="Approved recovered plan",
                confirm_break_glass=required.value.preview["confirmation"],
            )
    else:
        assert not finalize.push_changes(
            wid, config, break_glass_reason="Approved recovered plan",
            confirm_break_glass=required.value.preview["confirmation"],
        )
    expected = moved if defect == "moved" else "" if defect == "deleted" else old
    assert pr_publish._tip("origin", branch, str(wt)) == expected
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").pr.head_sha == old


@pytest.mark.timeout(120)
@pytest.mark.parametrize("race", ["remote", "local"])
def test_recovery_transport_pins_approved_objects(pr_repo, monkeypatch, race):
    config, wid, wt, remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    source = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    moved = git_ops.git("rev-parse", "origin/master", cwd=str(wt)).stdout.strip()
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    real_push = git_ops.push

    def raced_push(*args, **kwargs):
        if race == "remote":
            git_ops.git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", moved, old, cwd=str(wt))
        else:
            git_ops.git("update-ref", f"refs/heads/worktree/{wid}", moved, source, cwd=str(wt))
        return real_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", raced_push)
    assert finalize.push_changes(
        wid, config, break_glass_reason="Approved recovered plan",
        confirm_break_glass=required.value.preview["confirmation"],
    ) is (race == "local")
    assert pr_publish._tip("origin", branch, str(wt)) == (moved if race == "remote" else source)


@pytest.mark.timeout(120)
@pytest.mark.parametrize("remote_state", ["accepted", "unchanged", "unreadable", "changed"])
def test_recovery_reconciles_failed_transport_before_audit(pr_repo, monkeypatch, remote_state, capfd):
    config, wid, wt, remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    source = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    moved = git_ops.git("rev-parse", "origin/master", cwd=str(wt)).stdout.strip()
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    real_push = git_ops.push
    real_tip = pr_publish._tip
    failed = False

    def lost_response(*args, **kwargs):
        nonlocal failed
        if remote_state == "accepted":
            assert real_push(*args, **kwargs)
        elif remote_state == "changed":
            git_ops.git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", moved, old, cwd=str(wt))
        failed = True
        return git_ops.PushResult(ok=False, stderr="lost transport response")

    monkeypatch.setattr(git_ops, "push", lost_response)
    monkeypatch.setattr(pr_publish, "_tip", lambda *args: (
        None if failed and remote_state == "unreadable" else real_tip(*args)
    ))
    capfd.readouterr()
    assert finalize.push_changes(
        wid, config, break_glass_reason="Approved recovered plan",
        confirm_break_glass=required.value.preview["confirmation"],
    ) is (remote_state == "accepted")
    directory = Path(git_ops.git("rev-parse", "--absolute-git-dir", cwd=str(wt)).stdout.strip())
    audit = json.loads(next(directory.glob("agent-worktrees-pr-break-glass-*.json")).read_text())
    assert audit["outcome"] == (
        "succeeded" if remote_state == "accepted" else "failed" if remote_state == "unchanged" else "unknown"
    )
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    assert record.pr.head_sha == (source if remote_state == "accepted" else old)
    if remote_state in {"unreadable", "changed"}:
        output = capfd.readouterr()
        assert "Do not repeat the push" in output.out + output.err


@pytest.mark.timeout(120)
def test_recovery_missing_tip_fetch_is_bounded_and_audited(pr_repo, monkeypatch):
    config, wid, wt, _remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    real_git = git_ops.git
    real_exists = git_ops.ref_exists
    monkeypatch.setattr(git_ops, "ref_exists", lambda ref, **kwargs: (
        False if ref == old else real_exists(ref, **kwargs)
    ))

    def stalled(*args, **kwargs):
        if "fetch" in args:
            assert kwargs["timeout"] == git_ops.DEFAULT_FETCH_TIMEOUT
            assert kwargs["kill_tree"] is True
            assert f"refs/heads/{branch}" in args
            raise subprocess.TimeoutExpired(["git", "fetch"], kwargs["timeout"])
        return real_git(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", stalled)
    assert not finalize.push_changes(
        wid, config, break_glass_reason="Approved recovered plan",
        confirm_break_glass=required.value.preview["confirmation"],
    )
    assert pr_publish._tip("origin", branch, str(wt)) == old
    directory = Path(real_git("rev-parse", "--absolute-git-dir", cwd=str(wt)).stdout.strip())
    audit = json.loads(next(directory.glob("agent-worktrees-pr-break-glass-*.json")).read_text())
    assert audit["outcome"] == "failed"
    monkeypatch.setattr(git_ops, "ref_exists", real_exists)
    with pytest.raises(recovery.ConfirmationRequired):
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")


@pytest.mark.timeout(120)
@pytest.mark.parametrize("failure", ["begin", "tracking", "finish"])
def test_recovery_audit_failures_report_actual_transport(pr_repo, monkeypatch, capfd, failure):
    config, wid, wt, _remote, old, branch = _recovered_pr(pr_repo, monkeypatch)
    source = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Approved recovered plan")
    if failure == "tracking":
        def denied(*args, **kwargs):
            raise OSError("tracking persistence denied")
        monkeypatch.setattr(pr_publish, "record_pushed_head", denied)
    else:
        real_write = recovery.RecoveryAudit._write

        def denied(audit):
            if failure == "begin" or audit.outcome != "attempted":
                raise OSError("audit persistence denied")
            return real_write(audit)

        monkeypatch.setattr(recovery.RecoveryAudit, "_write", denied)
    capfd.readouterr()
    assert not finalize.push_changes(
        wid, config, break_glass_reason="Approved recovered plan",
        confirm_break_glass=required.value.preview["confirmation"],
    )
    captured = capfd.readouterr()
    assert pr_publish._tip("origin", branch, str(wt)) == (old if failure == "begin" else source)
    if failure != "begin":
        assert "Remote head was published; persistence/audit needs reconciliation" in captured.out + captured.err
    if failure == "tracking":
        directory = Path(git_ops.git("rev-parse", "--absolute-git-dir", cwd=str(wt)).stdout.strip())
        audit = json.loads(next(directory.glob("agent-worktrees-pr-break-glass-*.json")).read_text())
        assert audit["outcome"] == "succeeded"
        assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").pr.head_sha == old


@pytest.mark.timeout(120)
@pytest.mark.parametrize("mode", ["false", "codename"])
def test_recovered_pr_keeps_frozen_public_attribution(pr_repo, monkeypatch, mode):
    from agent_worktrees import providers

    config, wid, wt, _remote, _old, _branch = _recovered_pr(
        pr_repo, monkeypatch, stub_attribution=False,
        attribution=False if mode == "false" else "codename",
    )
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    assert record.pr.attribution_mode == mode
    record.codename = "harbor-lattice"
    record.codename_source = "built-in"
    tracking.save_record(record)
    config = replace(config, repos={
        config.repo_name: replace(config.default_repo, pr=replace(
            config.default_repo.pr, source_attribution=True, source_attribution_configured=True,
        )),
    })
    markers = []

    class Provider:
        def publish_source_marker(self, repo, number, marker, **kwargs):
            markers.append(marker)
            return ""

    monkeypatch.setattr(providers, "get_provider", lambda *a: Provider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: None)
    with pytest.raises(recovery.ConfirmationRequired) as required:
        finalize.push_changes(wid, config, break_glass_reason="Private operator rationale")
    assert finalize.push_changes(
        wid, config, break_glass_reason="Private operator rationale",
        confirm_break_glass=required.value.preview["confirmation"],
    )
    current = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    assert current.pr.attribution_mode == mode
    assert len(markers) == (1 if mode == "codename" else 0)
    assert all("Private operator rationale" not in marker and wid not in marker for marker in markers)
    if markers:
        assert "harbor-lattice" in markers[0]
