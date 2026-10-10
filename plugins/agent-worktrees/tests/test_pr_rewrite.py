"""Exact-lease intentional rewrites through the public push-changes contract."""

from dataclasses import replace
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg
from agent_worktrees import finalize, finalize_cli, git_ops, pr_ops, pr_publish, pr_rewrite, pr_state_cli, tracking
from agent_worktrees.providers.base import PullResult

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr_publish.explicit_rewrite"),
    pytest.mark.timeout(120),
]


def git(*args, cwd):
    if Path(cwd).name.endswith(".git"):
        args = ("--git-dir", str(cwd), *args)
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def record_for(wid):
    return tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")


@pytest.mark.parametrize("native", ["active", "completed", "abandoned"])
def test_persisted_native_pr_state_remains_truthful_and_live(pr_repo, native):
    _config, wid, _wt, _remote = pr_repo
    path = cfg.tracking_dir() / f"{wid}.yaml"
    record = record_for(wid)
    record.prs = [tracking.PRRecord(state=native, branch="pr/legacy", provider="azure-devops")]
    tracking.save_record(record)
    before = path.read_bytes()
    loaded = record_for(wid)
    assert loaded.pr.state == native
    assert loaded.has_live_pr() is (native == "active")
    assert path.read_bytes() == before


def test_existing_active_pr_can_refresh_ownership_and_rewrite(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    assert pr_ops.create_pr(wid, config, title="Legacy publication")["success"]
    record = record_for(wid)
    old = record.pr.head_sha
    record.pr.state = "active"
    record.pr.rewrite_owner = ""
    tracking.save_record(record)
    assert record_for(wid).has_live_pr()
    result = pr_ops.create_pr(wid, config, title="Legacy publication")
    assert result["success"] and result["rerun"]
    record = record_for(wid)
    record.pr.number = 42
    tracking.save_record(record)
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(head_sha=old))
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    (wt / "feedback.txt").write_text("legacy PR feedback\n")
    git("add", "feedback.txt", cwd=wt)
    git("commit", "-m", "legacy feedback", cwd=wt)
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    assert git("rev-parse", record.pr.branch, cwd=remote) == git("rev-parse", "HEAD", cwd=wt)


def test_explicit_rewrite_threads_configured_lock_deadlines(pr_repo, monkeypatch):
    from agent_worktrees import publication_deadline

    config, wid, wt, _remote, _old = publish_and_rebase(pr_repo, "refspec")
    repo = replace(config.default_repo, pr=replace(config.default_repo.pr, push_timeout_seconds=600))
    config = replace(config, repos={config.repo_name: repo})
    calls = []

    @contextlib.contextmanager
    def authority(**kwargs):
        calls.append(("authority", kwargs))
        yield

    @contextlib.contextmanager
    def publish(cwd, **kwargs):
        calls.append(("publish", kwargs))
        yield

    @contextlib.contextmanager
    def metadata(worktree_id, **kwargs):
        calls.append(("metadata", kwargs))
        yield

    monkeypatch.setattr(pr_rewrite.pr_authority, "guard", authority)
    monkeypatch.setattr(pr_publish, "publish_lock", publish)
    monkeypatch.setattr(pr_publish, "metadata_lock", metadata)
    assert finalize.push_changes(wid, config, rewrite_pr=True, dry_run=True)
    wait = publication_deadline.lock_wait(600)
    assert ("authority", {"timeout": wait}) in calls
    assert ("publish", {"push_timeout_seconds": 600}) in calls
    assert ("metadata", {"project": config.repo_name, "timeout": wait}) in calls


def publish_and_rebase(pr_repo, scheme):
    config, wid, wt, remote = pr_repo
    repo = config.default_repo
    config = replace(config, repos={"ext": replace(repo, pr=replace(repo.pr, head_scheme=scheme))})
    result = pr_ops.create_pr(wid, config, title="Add feature")
    assert result["success"], result
    rec = record_for(wid)
    old = rec.pr.head_sha
    anchor = Path(repo.anchor)
    git("checkout", "master", cwd=anchor)
    (anchor / "upstream.txt").write_text("advance\n")
    git("add", "-A", cwd=anchor)
    git("commit", "-m", "upstream advance", cwd=anchor)
    git("push", "origin", "master", cwd=anchor)
    git("fetch", "origin", cwd=wt)
    git("rebase", "origin/master", cwd=wt)
    assert git("rev-parse", "HEAD", cwd=wt) != old
    assert not git_ops.is_commit_ancestor(old, "HEAD", cwd=str(wt))
    return config, wid, wt, remote, old


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
def test_explicit_rewrite_public_cli_real_rebase(pr_repo, scheme, monkeypatch):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, scheme)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    rec = record_for(wid)
    rec.pr.number = 42
    tracking.save_record(rec)
    monkeypatch.setattr(pr_rewrite, "observe_pr",
                        lambda config, pr: PullResult(head_sha=pr_publish._tip("origin", pr.branch, str(wt))))
    attribution = []
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution",
                        lambda *a: attribution.append(a[-1]) or "")
    monkeypatch.chdir(wt)
    (wt / "squash.txt").write_text("intentional squash\n")
    git("add", "-A", cwd=wt)
    git("commit", "-m", "prepare intentional squash", cwd=wt)
    squashed, reason = git_ops.squash_branch("origin/master", "intentional squash", cwd=str(wt))
    assert squashed, reason
    assert not finalize.push_changes(wid, config)
    assert record_for(wid).pr.head_sha == old
    before = git("rev-parse", "HEAD", cwd=wt)
    args = cli.build_parser().parse_args(["push-changes", wid, "--rewrite-pr"])
    assert finalize_cli.cmd_push_changes(args) == 0
    rec = record_for(wid)
    assert rec.pr.head_sha == before
    assert attribution == [before]
    assert pr_publish._tip("origin", rec.pr.branch, str(wt)) == before
    if scheme == "snapshot":
        assert git("rev-parse", rec.pr.branch, cwd=wt) == before
    assert git("rev-parse", "HEAD", cwd=wt) == before
    assert git("branch", "--show-current", cwd=wt) == f"worktree/{wid}"
    assert git("rev-parse", "master", cwd=remote) == git("rev-parse", "origin/master", cwd=wt)
    # Ordinary follow-up publication still fast-forwards from the rewritten tip.
    (wt / "feedback.txt").write_text("feedback\n")
    git("add", "-A", cwd=wt)
    git("commit", "-m", "feedback", cwd=wt)
    assert finalize.push_changes(wid, config)
    assert git_ops.is_branch_merged(before, record_for(wid).pr.head_sha, cwd=str(wt))


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
@pytest.mark.parametrize("during_push", [False, True])
def test_explicit_rewrite_refuses_moved_remote(pr_repo, scheme, during_push, monkeypatch):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, scheme)
    pr = record_for(wid).pr
    moved = git("rev-parse", "origin/master", cwd=wt)
    real_push = git_ops.push

    def advance():
        git("update-ref", f"refs/heads/{pr.branch}", moved, old, cwd=remote)

    if during_push:
        def raced_push(*a, **k):
            advance()
            return real_push(*a, **k)
        monkeypatch.setattr(git_ops, "push", raced_push)
    else:
        advance()
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    assert pr_publish._tip("origin", pr.branch, str(wt)) == moved
    assert record_for(wid).pr.head_sha == old


@pytest.mark.parametrize("defect", [
    "missing-lease", "wrong-branch", "wrong-path", "wrong-record-branch",
    "shared-head", "default-head", "closed", "untracked", "pushurl",
    "wrong-repo", "wrong-provider-pr", "closed-provider-pr", "merged-provider-pr",
])
def test_explicit_rewrite_ownership_and_destination_guards(pr_repo, defect, monkeypatch):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    rec = record_for(wid)
    feature = rec.pr.branch
    if defect == "missing-lease":
        rec.pr.head_sha = ""
    elif defect == "wrong-branch":
        git("checkout", "-b", "unrelated", cwd=wt)
    elif defect == "wrong-path":
        rec.worktree_path = str(remote)
    elif defect == "wrong-record-branch":
        rec.branch = "worktree/other"
    elif defect == "shared-head":
        other = tracking.create_new_record(
            "other", "worktree/other", str(wt.parent / "other"),
            rec.repo, "test", "linux", cfg.tracking_dir(),
        )
        other.prs = [replace(rec.pr)]
        tracking.save_record(other)
    elif defect == "default-head":
        rec.pr.branch = "master"
    elif defect == "closed":
        rec.pr.state = "closed"
    elif defect == "untracked":
        rec.prs = []
    elif defect == "pushurl":
        git("config", "remote.origin.pushurl", str(remote.parent / "wrong.git"), cwd=wt)
    elif defect == "wrong-repo":
        rec.pr.repo = "other/project"
        rec.pr.pr_revision += 1
    elif defect == "wrong-provider-pr":
        rec.pr.number = 42
        monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(head_sha="a" * 40))
    elif defect in ("closed-provider-pr", "merged-provider-pr"):
        rec.pr.number = 42
        monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
            head_sha=old, state="closed", merged=defect == "merged-provider-pr"))
    if defect == "untracked":
        (cfg.tracking_dir() / f"{wid}.yaml").unlink()
    else:
        tracking.save_record(rec)
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    assert git("rev-parse", f"refs/heads/{feature}", cwd=remote) == old


def test_explicit_rewrite_preserves_hook_rejection_and_dry_run(pr_repo, capsys):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "snapshot")
    rec = record_for(wid)
    assert finalize.push_changes(wid, config, rewrite_pr=True, dry_run=True)
    assert record_for(wid).pr.head_sha == old
    hookdir = wt.parent / "hooks"
    hookdir.mkdir()
    hook = hookdir / "pre-push"
    hook.write_text("#!/bin/sh\necho 'explicit rewrite hook rejection' >&2\nexit 1\n")
    hook.chmod(0o755)
    git("config", "core.hooksPath", str(hookdir), cwd=wt)
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    captured = capsys.readouterr()
    assert "explicit rewrite hook rejection" in captured.out + captured.err
    assert git("rev-parse", f"refs/heads/{rec.pr.branch}", cwd=remote) == old
    assert record_for(wid).pr.head_sha == old


def test_low_level_rewrite_requires_exact_lease(monkeypatch):
    calls = []
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: calls.append(a))
    assert not git_ops.push("origin", "pr/x", cwd=".", allow_history_rewrite=True)
    assert calls == []


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
def test_explicit_rewrite_recorded_fork_and_repoint_refusal(pr_repo, scheme):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, scheme)
    rec = record_for(wid)
    feature = rec.pr.branch
    fork = remote.parent / "fork.git"
    git("init", "--bare", str(fork), cwd=wt)
    git("remote", "add", "fork", str(fork), cwd=wt)
    git("push", "fork", f"{old}:refs/heads/{feature}", cwd=wt)
    rec.pr.remote = "fork"
    rec.pr.head_repo = pr_publish.push_slug("fork", cwd=str(wt))
    rec.pr.head_identity = pr_publish.push_identity("fork", cwd=str(wt))
    rec.pr.rewrite_identity = rec.pr.head_identity
    tracking.save_record(rec)
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    assert pr_publish._tip("fork", feature, str(wt)) == git("rev-parse", "HEAD", cwd=wt)
    assert git("rev-parse", f"refs/heads/{feature}", cwd=remote) == old
    git("remote", "set-url", "fork", str(remote), cwd=wt)
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    assert git("rev-parse", f"refs/heads/{feature}", cwd=remote) == old


def test_explicit_rewrite_serializes_set_pr_and_pins_owned_branch(pr_repo, monkeypatch):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    rec = record_for(wid)
    rec.repo = rec.repo.upper()
    rec.pr.repo = rec.pr.repo.upper()
    rec.pr.number = 42
    tracking.save_record(rec)
    source = git("rev-parse", f"refs/heads/worktree/{wid}", cwd=wt)
    feature = rec.pr.branch
    script = (
        "import json,sys; from pathlib import Path;"
        "from agent_worktrees import config as cfg,pr_ops,pr_publish;"
        "cfg.tracking_dir=lambda *a:Path(sys.argv[1]);"
        "pr_publish.PUBLISH_LOCK_ACQUIRE_TIMEOUT_S=0.2;"
        "print(json.dumps(pr_ops.set_pr(sys.argv[2],branch='feature/corrected-aaaa')))"
    )

    def set_pr_process():
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(pr_ops.__file__).parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-c", script, str(cfg.tracking_dir()), wid],
            cwd=wt, env=env, capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def provider_probe(repo, pr):
        blocked = set_pr_process()
        assert not blocked["success"]
        assert "timed out" in blocked["error"].lower()
        git("checkout", "-b", "unrelated", "origin/master", cwd=wt)
        assert git("rev-parse", "HEAD", cwd=wt) != source
        return PullResult(head_sha=old)

    monkeypatch.setattr(pr_rewrite, "observe_pr", provider_probe)
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    assert git("rev-parse", f"refs/heads/{feature}", cwd=remote) == source
    assert record_for(wid).pr.head_sha == source
    assert set_pr_process()["success"]
    corrected = record_for(wid)
    assert corrected.pr.branch == "feature/corrected-aaaa"
    assert corrected.pr.head_sha == source


def test_explicit_rewrite_configured_project_not_ambient(pr_repo, monkeypatch, capfd):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    ledger = cfg.tracking_dir()
    ambient = ledger.parent / "ambient"
    ambient.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: ledger if name == "ext" else ambient)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.chdir(ambient)
    capfd.readouterr()
    args = cli.build_parser().parse_args([
        "push-changes", git_ops.worktree_suffix(wid), "--rewrite-pr", "--config", "selected-project.yaml", "--json",
    ])
    assert finalize_cli.cmd_push_changes(args) == 0
    rec = tracking.load_record(ledger / f"{wid}.yaml")
    response = json.loads(capfd.readouterr().out)
    assert response["status"] == rec.status
    assert rec.pr.head_sha != old
    assert git("rev-parse", f"refs/heads/{rec.pr.branch}", cwd=remote) == rec.pr.head_sha
    assert (ledger / f"{wid}.pr-authority.lock").exists()
    assert not (ambient / f"{wid}.pr-authority.lock").exists()
    assert pr_ops.set_pr(wid, config=config, state="open")["success"]
    assert not (ambient / f"{wid}.yaml").exists()
    rec.pr.number = 42
    tracking.save_record(rec)
    observed = []
    monkeypatch.setattr(pr_ops, "refresh_head_observation",
                        lambda conf, record, pr, sha: observed.append(record.worktree_path) or "")
    args = cli.build_parser().parse_args([
        "set-pr", git_ops.worktree_suffix(wid), "--state", "open", "--config", "selected-project.yaml", "--json",
    ])
    assert pr_state_cli.cmd_set_pr(args) == 0
    response = json.loads(capfd.readouterr().out)
    assert "head_observation_error" not in response
    assert observed == [str(wt)]


def test_explicit_rewrite_refuses_manual_same_suffix_association_and_stale_save(pr_repo):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    stale = record_for(wid)
    published = stale.pr.branch
    manual = "manual/name-" + git_ops.worktree_suffix(wid)
    git("push", "origin", f"{old}:refs/heads/{manual}", cwd=wt)
    assert pr_ops.set_pr(wid, branch=manual, config=config)["success"]
    rec = record_for(wid)
    assert rec.pr.head_sha == old
    assert not rec.pr.rewrite_owner
    # A stale publisher cannot resurrect the previous ownership or branch.
    tracking.save_record(stale)
    rec = record_for(wid)
    assert rec.pr.branch == manual
    assert not rec.pr.rewrite_owner
    pr_ops._finish_auto_open(
        {"success": True, "branch": published}, config, rec, rec.pr,
        title="Add feature", body=None, worktree_id=wid, head_sha=old,
        open_pr=False, draft=False, attribution=None, prcfg=config.default_repo.pr,
    )
    assert not record_for(wid).pr.rewrite_owner
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    assert git("rev-parse", f"refs/heads/{manual}", cwd=remote) == old


def test_explicit_rewrite_legacy_ownership_refresh_preserves_published_history(pr_repo):
    config, wid, wt, remote = pr_repo
    assert pr_ops.create_pr(wid, config, title="Add feature")["success"]
    rec = record_for(wid)
    old = rec.pr.head_sha
    rec.pr.rewrite_owner = ""
    tracking.save_record(rec)
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    rerun = pr_ops.create_pr(wid, config, title="Add feature")
    assert rerun["success"] and rerun["rerun"]
    rec = record_for(wid)
    assert rec.pr.head_sha == old
    assert rec.pr.rewrite_owner == f"{wid}:{rec.pr.branch}"
    config, wid, wt, remote, before_rebase = publish_and_rebase(pr_repo, "snapshot")
    assert before_rebase == old
    assert finalize.push_changes(wid, config, rewrite_pr=True)


@pytest.mark.parametrize("wid", ["test-wt-123456", "nodashid"])
def test_explicit_rewrite_uses_canonical_creation_suffix(pr_repo, wid):
    config, previous_id, wt, remote = pr_repo
    new_path = wt.parent / wid
    git("branch", "-m", f"worktree/{wid}", cwd=wt)
    git("worktree", "move", str(wt), str(new_path), cwd=Path(config.default_repo.anchor))
    tracking.create_new_record(
        wid, f"worktree/{wid}", str(new_path), "ext", "test", "linux", cfg.tracking_dir(),
    )
    config, wid, wt, remote, old = publish_and_rebase((config, wid, new_path, remote), "refspec")
    assert record_for(wid).pr.branch.endswith("-" + git_ops.worktree_suffix(wid))
    assert finalize.push_changes(wid, config, rewrite_pr=True)


def test_explicit_rewrite_ownership_generation_survives_stale_save(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    original = pr_publish.record_rewrite_ownership
    snapshots = []

    def capture_before_stamp(config, record, pr, branch, sha, identity):
        snapshots.append(record_for(wid))
        return original(config, record, pr, branch, sha, identity)

    monkeypatch.setattr(pr_publish, "record_rewrite_ownership", capture_before_stamp)
    assert pr_ops.create_pr(wid, config, title="Add feature")["success"]
    stale = snapshots[0]
    assert not stale.pr.rewrite_owner
    old_generation = stale.pr.pr_revision
    stale.title = "unrelated record update"
    tracking.save_record(stale)
    rec = record_for(wid)
    assert rec.pr.pr_revision > old_generation
    assert rec.pr.rewrite_owner == f"{wid}:{rec.pr.branch}"


def test_explicit_rewrite_rejects_same_record_sibling_head(pr_repo):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    rec = record_for(wid)
    feature = rec.pr.branch
    rec.prs.append(replace(rec.pr, pr_id="sibling", rewrite_owner="", opened_at="2000-01-01T00:00:00"))
    tracking.save_record(rec)
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    assert git("rev-parse", f"refs/heads/{feature}", cwd=remote) == old


def test_explicit_rewrite_selected_moved_path_with_ambient_same_id(pr_repo, monkeypatch):
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    ledger = cfg.tracking_dir()
    relocated = wt.parent.parent / "relocated"
    git("worktree", "move", str(wt), str(relocated), cwd=Path(config.default_repo.anchor))
    rec = record_for(wid)
    rec.worktree_path = str(relocated)
    tracking.save_record(rec)
    ambient = ledger.parent / "ambient"
    ambient.mkdir()
    other = tracking.create_new_record(
        wid, f"worktree/{wid}", str(ambient), "ambient", "test", "linux", ambient,
    )
    other.status = "orphaned"
    tracking.save_record(other)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: ledger if name == "ext" else ambient)
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    updated = tracking.load_record(ledger / f"{wid}.yaml")
    assert updated.pr.head_sha != old
    assert git("rev-parse", f"refs/heads/{updated.pr.branch}", cwd=remote) == updated.pr.head_sha
    assert tracking.load_record(ambient / f"{wid}.yaml").status == "orphaned"


def test_explicit_rewrite_rejects_repointed_origin_with_same_slug_and_head(pr_repo):
    config, wid, wt, remote = pr_repo
    git("remote", "set-url", "origin", remote.as_uri(), cwd=wt)
    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    rec = record_for(wid)
    mirror = remote.parent / "mirror" / remote.parent.name / remote.name
    mirror.parent.mkdir(parents=True)
    git("clone", "--bare", str(remote), str(mirror), cwd=wt)
    git("remote", "set-url", "origin", mirror.as_uri(), cwd=wt)
    assert pr_publish.push_slug("origin", cwd=str(wt)).lower() == rec.pr.repo.lower()
    assert pr_publish._tip("origin", rec.pr.branch, str(wt)) == old
    assert not finalize.push_changes(wid, config, rewrite_pr=True)
    # Ordinary publication must not silently redirect a previously attested head either.
    result = pr_publish.push_checked(
        rec, "origin", f"{old}:refs/heads/{rec.pr.branch}", cwd=str(wt),
        force_with_lease_expect=old,
    )
    assert not result and result.stderr == pr_publish.REPOINTED
    assert git("rev-parse", f"refs/heads/{rec.pr.branch}", cwd=mirror) == old


def test_explicit_rewrite_cross_project_process_race_and_lease_generation(pr_repo, monkeypatch):
    from test_pr_authority import run_writer
    from agent_worktrees import installer

    config, wid, wt, remote, old = publish_and_rebase(pr_repo, "refspec")
    rec = record_for(wid)
    ledger = cfg.tracking_dir()
    root = ledger.parent / "runtime"
    root.mkdir()
    registry = root / "projects.yaml"
    registry.write_text("projects:\n  ext: {}\n  alias: {}\n")
    other_ledger = ledger.parent / "alias"
    other_ledger.mkdir()
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: other_ledger if name == "alias" else ledger)
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: registry)
    other = tracking.create_new_record(
        "other", "worktree/other", str(wt), "alias", "test", "linux", other_ledger,
    )
    other.prs = [replace(rec.pr, branch="pr/unrelated-aaaa", pr_id="other-pr")]
    tracking.save_record(other)
    snapshots = []
    actual_push = git_ops.push

    def racing_push(*args, **kwargs):
        # Executed after exclusivity authorization, immediately before real CAS.
        blocked = run_writer(root, other_ledger, "other", "save")
        assert not blocked["success"]
        snapshots.append(record_for(wid))
        return actual_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", racing_push)
    assert finalize.push_changes(wid, config, rewrite_pr=True)
    current = record_for(wid)
    assert current.pr.head_sha != old
    assert current.pr.pr_revision > snapshots[-1].pr.pr_revision
    tracking.save_record(snapshots[-1])
    assert record_for(wid).pr.head_sha == current.pr.head_sha
    # Ordinary incremental publication and create-pr reruns have the same CAS.
    monkeypatch.setattr(git_ops, "push", actual_push)
    for publish in (lambda: finalize.push_changes(wid, config),
                    lambda: pr_ops.create_pr(wid, config, title="Add feature")["success"]):
        stale = record_for(wid)
        (wt / "feedback.txt").write_text(str(stale.pr.pr_revision))
        git("add", "-A", cwd=wt)
        git("commit", "-m", "feedback", cwd=wt)
        assert publish()
        updated = record_for(wid)
        assert updated.pr.head_sha != stale.pr.head_sha
        assert updated.pr.pr_revision > stale.pr.pr_revision
        tracking.save_record(stale)
        assert record_for(wid).pr.head_sha == updated.pr.head_sha
