"""Private local-Git source publication; not live provider or installed evidence."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_config, pr_publish, pr_worker_config, repos
from agent_worktrees import pr_publication_checks as checks
from agent_worktrees import pr_source_publication as source
from agent_worktrees.pr_publication_config import PublicationChecksConfig

pytestmark = pytest.mark.timeout(120)


def test_registered_ado_destination_accepts_percent_encoded_names(monkeypatch, tmp_path):
    from agent_worktrees import config as cfg

    remote = "https://dev.azure.com/example/My%20Project/_git/My%20Repo"
    repo = cfg.RepoConfig(
        anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
        pr=cfg.PRConfig(enabled=True, provider="azure-devops", api_base="https://dev.azure.com/example"),
    )
    monkeypatch.setattr(repos, "list_repos", lambda: [
        SimpleNamespace(remote=remote, repo_class="worktree"),
    ])
    assert source._canonical_destination(repo, "My Project/My Repo", str(tmp_path)) == remote


@pytest.fixture
def publication(pr_repo, monkeypatch, tmp_path):
    caller, worktree_id, caller_root, remote = pr_repo
    workspace = tmp_path / "source"
    git_ops.git("clone", "--quiet", str(remote), str(workspace), cwd=tmp_path)
    git_ops.git("config", "user.name", "Source owner", cwd=workspace)
    git_ops.git("config", "user.email", "source@example.invalid", cwd=workspace)
    git_ops.git("checkout", "-qb", "users/demo/fix", cwd=workspace)
    (workspace / "source.txt").write_text("source-only\n", encoding="utf-8")
    git_ops.git("add", "source.txt", cwd=workspace)
    git_ops.git("commit", "-qm", "source candidate", cwd=workspace)
    target_repo = git_ops.slug_from_url(str(remote))
    target = replace(caller.default_repo, anchor=str(workspace), pr=replace(
        caller.default_repo.pr, publication_checks=PublicationChecksConfig(),
        branch_update_strategy="merge", source_attribution=False,
    ))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *args: SimpleNamespace(
        resolved=True, repo_config=target, same_as_active=False,
    ))
    monkeypatch.setattr(repos, "list_repos", lambda: [
        SimpleNamespace(remote=str(remote), repo_class="worktree"),
    ])
    # A private file transport substitutes for a forge endpoint, not for Git,
    # publication policy, destination comparison, hooks or lease enforcement.
    monkeypatch.setattr(pr_worker_config, "matches_remote", lambda url, slug, pr, root:
                        git_ops.slug_from_url(url) == slug)
    head = git_ops.git("rev-parse", "HEAD", cwd=workspace).stdout.strip()
    checks.record_self_review(workspace, "origin/master", target.pr.publication_checks,
                              lenses=["source"], summary="reviewed exact source")
    return SimpleNamespace(
        caller=caller, wid=worktree_id, caller_root=caller_root, workspace=workspace,
        remote=remote, repo=target_repo, target=target, head=head, branch="users/demo/fix",
    )


def publish(p, **overrides):
    kwargs = dict(target_repo=p.repo, workspace=p.workspace, branch=p.branch, expected_head=p.head)
    kwargs.update(overrides)
    return source.publish_source(p.wid, p.caller, **kwargs)


def remote_head(p):
    return git_ops.git("--git-dir", str(p.remote), "rev-parse", "--verify", f"refs/heads/{p.branch}",
                       cwd=p.remote, check=False).stdout.strip()


def review(p):
    p.head = git_ops.git("rev-parse", "HEAD", cwd=p.workspace).stdout.strip()
    checks.record_self_review(p.workspace, "origin/master", p.target.pr.publication_checks,
                              lenses=["source"], summary="reviewed updated source")


def test_publishes_only_source_and_preserves_user_branch_and_hook(publication):
    p = publication
    hook = p.workspace / ".git" / "hooks" / "pre-push"
    marker = p.workspace / ".git" / "hook-received"
    hook.write_text('#!/bin/sh\nprintf "%s" "$1" > "' + marker.as_posix() + '"\n', encoding="utf-8")
    hook.chmod(0o700)
    caller_head = git_ops.git("rev-parse", "HEAD", cwd=p.caller_root).stdout.strip()
    result = publish(p, expected_remote_head="")
    assert result["ok"], result
    assert result["observed_head"] == remote_head(p) == p.head
    assert marker.read_text() == "origin"
    assert git_ops.git("symbolic-ref", "--short", "HEAD", cwd=p.workspace).stdout.strip() == p.branch
    assert git_ops.git("rev-parse", "HEAD", cwd=p.caller_root).stdout.strip() == caller_head
    files = git_ops.git("--git-dir", str(p.remote), "ls-tree", "--name-only", p.head,
                        cwd=p.remote).stdout.splitlines()
    assert "source.txt" in files and "a.txt" not in files and "b.txt" not in files


def test_delegated_probes_and_transport_release_clone_lock_for_another_worktree(
    publication, monkeypatch, tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor

    p = publication
    other = tmp_path / "other-source"
    git_ops.git("worktree", "add", "--detach", str(other), "HEAD", cwd=p.workspace)
    original_probe, original_push = source.observed_ref, git_ops.push
    phases = []
    def acquire():
        with pr_publish.publish_lock(str(other), acquire_timeout=1):
            return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        def probe(*args, **kwargs):
            assert pool.submit(acquire).result(timeout=3)
            phases.append("probe")
            return original_probe(*args, **kwargs)
        def transport(*args, **kwargs):
            assert pool.submit(acquire).result(timeout=3)
            phases.append("transport")
            return original_push(*args, **kwargs)
        monkeypatch.setattr(source, "observed_ref", probe)
        monkeypatch.setattr(git_ops, "push", transport)
        value = publish(p)
    assert value["ok"] and remote_head(p) == p.head, value
    assert "probe" in phases and "transport" in phases


@pytest.mark.parametrize("drift", ["source", "caller"])
def test_final_local_admission_rechecks_source_and_caller_after_transport_preparation(
    publication, monkeypatch, drift,
):
    from agent_worktrees import tracking

    p = publication
    original_push, original_settings = pr_publish.push_checked, pr_publish._transport_settings
    preparing = [False]
    moved = []
    def push(*args, **kwargs):
        preparing[0] = True
        try:
            return original_push(*args, **kwargs)
        finally:
            preparing[0] = False
    def settings(*args, **kwargs):
        result = original_settings(*args, **kwargs)
        if preparing[0] and not moved:
            moved.append(drift)
            if drift == "source":
                git_ops.git("commit", "--allow-empty", "-qm", "source moved before transport", cwd=p.workspace)
            else:
                record = tracking.load_record_by_id(p.wid)
                record.status = "finalized"
                tracking.save_record(record)
        return result
    monkeypatch.setattr(pr_publish, "push_checked", push)
    monkeypatch.setattr(pr_publish, "_transport_settings", settings)
    value = publish(p)
    assert moved and not value["ok"] and not remote_head(p), value
    assert "HEAD differs" in value["error"] if drift == "source" else "managed workspace" in value["error"]


def test_real_pre_push_refusal_preserves_remote(publication):
    p = publication
    hook = p.workspace / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho 'source hook refused' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o700)
    result = publish(p)
    assert not result["ok"] and "source hook refused" in result["error"]
    assert not remote_head(p)


def test_central_cli_publishes_admitted_source_with_exact_lease(publication, monkeypatch):
    import json
    from agent_worktrees import __main__ as cli, output, pr_cli

    p = publication
    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", lambda *args: True)
    monkeypatch.setattr(source.cfg, "load_config", lambda *args: p.caller)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (p.wid, ""))
    with output.capture_json_output() as capture:
        code = cli.main([
            "pr-push", "--repo", p.repo, "--workspace", str(p.workspace), "--branch", p.branch,
            "--expected-head", p.head, "--expected-remote-head=", "--json",
        ])
    result = json.loads(capture.getvalue())
    assert code == 0 and result["publication_state"] == "pushed", result
    assert result["expected_remote_head"] == "" and result["observed_head"] == remote_head(p) == p.head


@pytest.mark.parametrize("origin_mode", ["inert", "absent", "conflicting"])
def test_configured_push_validates_the_invoking_managed_caller(
    publication, monkeypatch, tmp_path, origin_mode,
):
    import json
    from agent_worktrees import __main__ as cli, output, pr_cli, providers
    from agent_worktrees.pr_publication_labels import PublicationOrigin

    p = publication
    caller = replace(p.caller, repos={p.caller.repo_name: replace(
        p.caller.default_repo, pr=replace(
            p.caller.default_repo.pr, publication_origin=(
                PublicationOrigin("author", ("source:caller",)) if origin_mode == "conflicting" else None
            ),
        ),
    )})
    policy = tmp_path / "worker-author.json"
    target = replace(p.target, pr=replace(
        p.target.pr, api_base="https://forge.invalid", repository=p.repo,
        publication_origin=PublicationOrigin("author", ("source:target",)),
    ))
    pr_worker_config.export_config(
        p.repo, target, str(p.workspace), "origin/master", policy, include_author=True,
    )
    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", lambda *args: True)
    monkeypatch.setattr(
        source.cfg, "load_config",
        lambda path=None, **options: (
            caller if path is None else source.cfg._load_config_uncached(path, **options)
        ),
    )
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda config, repo:
                        pr_config.ForeignRepoResolution(config.explicit_pr_target, "pr-worker"))
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (p.wid, ""))
    current = ["unrelated"]
    backend = SimpleNamespace(
        find_pull_by_head=lambda *a, **k: SimpleNamespace(
            state="open", merged=False, number=7, url="https://forge.invalid/7",
        ),
        get_publication_labels=lambda *a, **k: tuple(current),
        add_publication_labels=lambda repo, number, values, **k: current.extend(values),
    )
    monkeypatch.setattr(providers, "get_provider", lambda *_: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    origin_args = [] if origin_mode == "inert" else ["--publication-origin", "author"]
    with output.capture_json_output() as capture:
        code = cli.main([
            "pr-push", "--repo", p.repo, "--workspace", str(p.workspace), "--branch", p.branch,
            "--expected-head", p.head, "--expected-remote-head=", "--config", str(policy), "--json",
            *origin_args,
        ])
    result = json.loads(capture.getvalue())
    assert code == 0 and result["publication_state"] == "pushed", result
    assert result["observed_head"] == remote_head(p) == p.head
    if origin_mode == "inert":
        assert "publication_labels" not in result and current == ["unrelated"]
    else:
        assert result["publication_labels"]["ok"]
        assert current == ["unrelated", "source:target"]


@pytest.mark.parametrize("change", ["dirty", "untracked", "wrong-branch", "wrong-head", "subfolder"])
def test_refuses_unreviewed_or_inexact_workspace(publication, change):
    p = publication
    overrides = {}
    if change == "dirty":
        (p.workspace / "source.txt").write_text("not committed")
    elif change == "untracked":
        (p.workspace / "untracked.txt").write_text("not admitted")
    elif change == "wrong-branch":
        overrides["branch"] = "users/demo/other"
    elif change == "wrong-head":
        overrides["expected_head"] = "0" * 40
    else:
        nested = p.workspace / "nested"
        nested.mkdir()
        overrides["workspace"] = nested
    result = publish(p, **overrides)
    assert not result["ok"] and result["publication_state"] == "not_pushed", result
    assert not remote_head(p)


def test_registry_and_remote_must_bind_same_canonical_destination(publication, tmp_path):
    p = publication
    wrong = tmp_path / "wrong.git"
    git_ops.git("init", "--bare", "--quiet", str(wrong), cwd=tmp_path)
    git_ops.git("remote", "set-url", "--push", "origin", str(wrong), cwd=p.workspace)
    result = publish(p)
    assert not result["ok"] and "destination" in result["error"], result
    assert not remote_head(p)


def test_target_binding_cannot_borrow_caller_provider(publication, monkeypatch):
    p = publication
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *args: SimpleNamespace(
        resolved=False, error="No separately configured target binding",
    ))
    result = publish(p)
    assert not result["ok"] and "target binding" in result["error"]
    assert not remote_head(p)


def test_source_revalidation_detects_commit_during_checks(publication, monkeypatch):
    p = publication
    original = checks.before_publish

    def move(*args, **kwargs):
        result = original(*args, **kwargs)
        git_ops.git("commit", "--allow-empty", "-qm", "source moved during checks", cwd=p.workspace)
        return result

    monkeypatch.setattr(checks, "before_publish", move)
    result = publish(p)
    assert not result["ok"] and "HEAD differs" in result["error"], result
    assert not remote_head(p)


def test_refuses_reference_registration_and_unrelated_caller(publication, monkeypatch):
    p = publication
    monkeypatch.setattr(repos, "list_repos", lambda: [
        SimpleNamespace(remote=str(p.remote), repo_class="reference"),
    ])
    assert "writable canonical" in publish(p)["error"]
    monkeypatch.setattr(source.cfg, "tracking_dir", lambda name=None: p.workspace / "absent")
    assert "managed caller record" in publish(p)["error"]
    assert not remote_head(p)


def test_target_checks_not_callers_and_source_evidence_is_reusable(publication):
    p = publication
    assert p.caller.default_repo.pr.publication_checks is None
    (p.workspace / "source.txt").write_text("unreviewed followup\n")
    git_ops.git("add", "source.txt", cwd=p.workspace)
    git_ops.git("commit", "-qm", "followup", cwd=p.workspace)
    p.head = git_ops.git("rev-parse", "HEAD", cwd=p.workspace).stdout.strip()
    refused = publish(p)
    assert not refused["ok"] and "review" in refused["error"]
    review(p)
    evidence = source.source_evidence(p.target, p.repo, p.workspace, p.branch, p.head)
    assert evidence["head"] == p.head and evidence["publication_checks"]["ok"]
    assert publish(p)["ok"]


def test_remote_history_requires_merge_forward_not_rebase(publication, tmp_path):
    p = publication
    assert publish(p)["ok"]
    old_head = p.head
    peer = tmp_path / "peer"
    git_ops.git("clone", "--quiet", str(p.remote), str(peer), cwd=tmp_path)
    git_ops.git("config", "user.name", "Peer", cwd=peer)
    git_ops.git("config", "user.email", "peer@example.invalid", cwd=peer)
    git_ops.git("checkout", "--quiet", p.branch, cwd=peer)
    (peer / "peer.txt").write_text("remote work\n")
    git_ops.git("add", "peer.txt", cwd=peer)
    git_ops.git("commit", "-qm", "peer work", cwd=peer)
    git_ops.git("push", "--quiet", "origin", p.branch, cwd=peer)
    peer_head = remote_head(p)
    (p.workspace / "source.txt").write_text("local followup\n")
    git_ops.git("add", "source.txt", cwd=p.workspace)
    git_ops.git("commit", "-qm", "local followup", cwd=p.workspace)
    review(p)
    refused = publish(p)
    assert not refused["ok"] and "merge forward" in refused["error"], refused
    assert remote_head(p) == peer_head
    git_ops.git("fetch", "--quiet", "origin", cwd=p.workspace)
    git_ops.git("merge", "--no-edit", f"origin/{p.branch}", cwd=p.workspace)
    review(p)
    assert not publish(p, expected_remote_head=old_head)["ok"]
    result = publish(p, expected_remote_head=peer_head)
    assert result["ok"], result
    assert git_ops.is_commit_ancestor(peer_head, p.head, cwd=p.workspace)


def test_racing_remote_advance_is_fenced_without_recovery(publication, monkeypatch):
    p = publication
    assert publish(p)["ok"]
    old = p.head
    (p.workspace / "source.txt").write_text("new candidate\n")
    git_ops.git("add", "source.txt", cwd=p.workspace)
    git_ops.git("commit", "-qm", "new candidate", cwd=p.workspace)
    review(p)
    original = pr_publish.push_checked
    tree = git_ops.git("--git-dir", str(p.remote), "rev-parse", old + "^{tree}",
                       cwd=p.remote).stdout.strip()
    peer_head = git_ops.git(
        "--git-dir", str(p.remote),
        "-c", "user.name=Peer", "-c", "user.email=peer@example.invalid",
        "commit-tree", tree, "-p", old, "-m", "racing remote work", cwd=p.remote,
    ).stdout.strip()
    calls = []

    def race(*args, **kwargs):
        calls.append(True)
        git_ops.git("--git-dir", str(p.remote), "update-ref",
                    f"refs/heads/{p.branch}", peer_head, old, cwd=p.remote)
        return original(*args, **kwargs)

    monkeypatch.setattr(pr_publish, "push_checked", race)
    result = publish(p, expected_remote_head=old)
    assert not result["ok"] and result["publication_state"] == "unknown", result
    assert remote_head(p) == peer_head
    assert result["expected_remote_head"] == old
    assert len(calls) == 1


def test_inherited_git_context_cannot_select_callers_commits(publication, monkeypatch):
    p = publication
    monkeypatch.setenv("GIT_DIR", str(p.caller_root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(p.caller_root))
    result = publish(p)
    assert result["ok"], result
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert remote_head(p) == p.head
