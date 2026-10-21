"""Owned-PR rebase publication against real bare Git remotes (#5796)."""

from __future__ import annotations

import dataclasses
import json
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, git_collab, git_ops, pr_ops, pr_publish, tracking
from agent_worktrees import pr_rebase
from conftest import _copy_pr_repo, _published_seed_environment, _seed_directory

# Each case exercises multiple real publications and bounded Git graph probes.
pytestmark = pytest.mark.timeout(180)
exhaustive = pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_PR_REBASE_EXHAUSTIVE") != "1",
    reason="Run PR rebase full or set AGENT_WORKTREES_PR_REBASE_EXHAUSTIVE=1",
)


def _git(*args, cwd):
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def _record(wid):
    return tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")


@pytest.fixture(scope="session")
def _rebase_snapshot_pr_seed(tmp_path_factory, _pr_repo_seed):
    # The general snapshot fixture publishes "Add feature", not "Own change".
    with _seed_directory(tmp_path_factory, "rebase-snapshot") as (seed, build):
        if build:
            with _published_seed_environment(tmp_path_factory) as patch:
                config, wid, _, _ = _copy_pr_repo(_pr_repo_seed, seed, patch)
                first = pr_ops.create_pr(wid, config, title="Own change")
                assert first["success"], first
        return seed


@pytest.fixture
def pr_repo(request):
    params = getattr(getattr(request.node, "callspec", None), "params", {})
    if params.get("branch") is not None:
        seed = request.getfixturevalue("_pr_repo_seed")
    elif (
        params.get("scheme") == "snapshot"
        or request.node.originalname
        == "test_owned_pr_rebase_publishes_legacy_checked_out_private_head"
    ):
        seed = request.getfixturevalue("_rebase_snapshot_pr_seed")
    else:
        return request.getfixturevalue("published_refspec_pr_repo")
    return _copy_pr_repo(
        seed, request.getfixturevalue("tmp_path"), request.getfixturevalue("monkeypatch"),
    )


def _prepare(pr_repo, scheme="refspec", branch=None):
    config, wid, path, remote = pr_repo
    record = _record(wid)
    published = record.pr
    expected_branch = pr_ops.feature_branch_name(
        "pr" if scheme == "refspec" else config.default_repo.pr.branch_prefix,
        "Own change", wid,
    )
    if (
        branch is None and config.default_repo.pr.head_scheme == scheme
        and record.title == "Own change"
        and published is not None and published.branch == expected_branch
    ):
        return config, wid, path, remote, published.branch, published.head_sha
    repo = dataclasses.replace(
        config.default_repo, pr=dataclasses.replace(config.default_repo.pr, head_scheme=scheme),
    )
    config = dataclasses.replace(config, repos={"ext": repo})
    first = pr_ops.create_pr(wid, config, title="Own change", branch=branch)
    assert first["success"], first
    return config, wid, path, remote, first["branch"], first["head_sha"]


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
@pytest.mark.parametrize("branch", [None, "feature/custom-rebase"])
def test_rebase_setup_preserves_publication_scheme_title_and_custom_branch(
    pr_repo, monkeypatch, scheme, branch,
):
    config, wid, path, remote = pr_repo
    before = _record(wid).pr
    create_pr = pr_ops.create_pr
    calls = []

    def observe(*args, **kwargs):
        calls.append(kwargs)
        return create_pr(*args, **kwargs)

    monkeypatch.setattr(pr_ops, "create_pr", observe)
    prepared, _, _, _, actual_branch, head = _prepare(pr_repo, scheme, branch)
    assert prepared.default_repo.pr.head_scheme == scheme
    assert actual_branch == (
        branch or {"snapshot": "feature/own-change-aaaa", "refspec": "pr/own-change-aaaa"}[scheme]
    )
    assert _git("--git-dir", str(remote), "rev-parse", actual_branch, cwd=path) == head
    assert _record(wid).pr.head_sha == head
    if branch is None:
        assert before is not None and before.head_sha == head
        assert calls == []
    else:
        assert before is None
        assert calls == [{"title": "Own change", "branch": branch}]
    assert _record(wid).title == "Own change"
    assert _git("log", "-1", "--format=%s", head, cwd=path) == "Own change"
    assert _git("rev-parse", "HEAD", cwd=path) == head
    local_head = git_ops.git("show-ref", "--verify", f"refs/heads/{actual_branch}", cwd=path, check=False)
    assert (local_head.returncode == 0) is (scheme == "snapshot")


@pytest.mark.parametrize("mismatch", ["scheme", "title", "branch"])
def test_rebase_setup_does_not_reuse_mismatched_publication(pr_repo, monkeypatch, mismatch):
    config, wid, path, remote = pr_repo
    if mismatch == "scheme":
        config = dataclasses.replace(config, repos={"ext": dataclasses.replace(
            config.default_repo,
            pr=dataclasses.replace(config.default_repo.pr, head_scheme="snapshot"),
        )})
    else:
        record = _record(wid)
        if mismatch == "title":
            record.title = "Add feature"
        else:
            record.pr.branch = "pr/add-feature-aaaa"
        monkeypatch.setattr(sys.modules[__name__], "_record", lambda _: record)
    calls = []

    def create(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "branch": "new-branch", "head_sha": "new-head"}

    monkeypatch.setattr(pr_ops, "create_pr", create)
    prepared = _prepare((config, wid, path, remote))
    assert prepared[-2:] == ("new-branch", "new-head")
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == wid and args[1].default_repo.pr.head_scheme == "refspec"
    assert kwargs == {"title": "Own change", "branch": None}


def _advance(config, path, filename="upstream.txt"):
    anchor = Path(config.default_repo.anchor)
    (anchor / filename).write_text("upstream advance\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "advance base", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    _git("fetch", "origin", cwd=path)
    return _git("rev-parse", "origin/master", cwd=path)


def _publish(command, config, wid):
    if command == "push-changes":
        return finalize.push_changes(wid, config)
    result = pr_ops.create_pr(wid, config, title="Own change")
    return result.get("success", False)


def _partial_success_then_rebase(pr_repo, monkeypatch):
    config, wid, path, remote, branch, old = _prepare(pr_repo, "refspec")
    (path / "accepted.txt").write_text("already accepted source feedback\n")
    _git("add", "accepted.txt", cwd=path)
    _git("commit", "-m", "source feedback", cwd=path)
    accepted = _git("rev-parse", "HEAD", cwd=path)
    original_push = git_ops.push

    def lost_acknowledgement(*args, **kwargs):
        result = original_push(*args, **kwargs)
        assert result
        return git_ops.PushResult(ok=False, stderr="transport acknowledgement lost after acceptance")

    with monkeypatch.context() as patch:
        patch.setattr(git_ops, "push", lost_acknowledgement)
        assert not finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == accepted
    assert _record(wid).pr.head_sha == old
    (path / "unpublished.txt").write_text("preserve final local feedback\n")
    _git("add", "unpublished.txt", cwd=path)
    _git("commit", "-m", "unpublished source feedback", cwd=path)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    return config, wid, path, remote, branch, old, accepted


@pytest.fixture(scope="session")
def _partial_success_seed(tmp_path_factory, _published_refspec_pr_seed):
    with _seed_directory(tmp_path_factory, "rebase-partial-success") as (seed, build):
        if build:
            with _published_seed_environment(tmp_path_factory) as patch:
                *_, branch, old, accepted = _partial_success_then_rebase(
                    _copy_pr_repo(_published_refspec_pr_seed, seed, patch), patch,
                )
                (seed / "partial-success-input.json").write_text(json.dumps({
                    "branch": branch, "old": old, "accepted": accepted,
                }), encoding="utf-8")
        return seed


@pytest.fixture
def partial_success_repo(tmp_path, monkeypatch, _partial_success_seed):
    config, wid, path, remote = _copy_pr_repo(_partial_success_seed, tmp_path, monkeypatch)
    config = dataclasses.replace(config, repos={"ext": dataclasses.replace(
        config.default_repo, pr=dataclasses.replace(config.default_repo.pr, head_scheme="refspec"),
    )})
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    inputs = json.loads((tmp_path / "partial-success-input.json").read_text(encoding="utf-8"))
    return config, wid, path, remote, inputs["branch"], inputs["old"], inputs["accepted"]


@pytest.mark.parametrize("iteration", range(2))
def test_partial_success_copy_preserves_saved_inputs_and_private_history(
    partial_success_repo, _partial_success_seed, iteration, tmp_path, monkeypatch,
):
    config, wid, path, remote, branch, old, accepted = partial_success_repo
    assert config.default_repo.pr.head_scheme == "refspec"
    assert cfg.load_config() is config
    record = _record(wid)
    assert record.worktree_path == str(path)
    assert record.title == "Own change" and record.pr.state == "open"
    assert record.pr.repo == git_ops.slug_from_url(str(remote))
    assert record.pr.rewrite_identity == pr_publish.push_identity("origin", cwd=str(path))
    assert record.pr.head_sha == old != accepted
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == accepted
    assert _git("rev-parse", f"{accepted}^", cwd=path) == old
    assert _git("log", "-1", "--format=%s", old, cwd=path) == "Own change"
    assert _git("log", "-1", "--format=%s", accepted, cwd=path) == "source feedback"
    assert _git("show", f"{accepted}:accepted.txt", cwd=path) == "already accepted source feedback"
    head = _git("rev-parse", "HEAD", cwd=path)
    assert not git_ops.is_commit_ancestor(accepted, head, cwd=str(path))
    assert _git("rev-parse", "HEAD~3", cwd=path) == _git("rev-parse", "origin/master", cwd=path)
    assert _git("log", "--format=%s", "origin/master..HEAD", cwd=path).splitlines() == [
        "unpublished source feedback", "source feedback", "Own change",
    ]
    assert _git("log", "-1", "--format=%s", cwd=path) == "unpublished source feedback"
    assert (path / "accepted.txt").read_text() == "already accepted source feedback\n"
    assert (path / "unpublished.txt").read_text() == "preserve final local feedback\n"
    assert _git("remote", "get-url", "origin", cwd=path) == str(remote)
    inputs_path = remote.parent / "partial-success-input.json"
    assert json.loads(inputs_path.read_text(encoding="utf-8")) == {
        "branch": branch, "old": old, "accepted": accepted,
    }
    seed_files = {
        entry.relative_to(_partial_success_seed): entry.read_bytes()
        for entry in _partial_success_seed.rglob("*") if entry.is_file()
    }
    sibling_root = tmp_path / "sibling"
    with monkeypatch.context() as patch:
        _, _, sibling, sibling_remote = _copy_pr_repo(_partial_success_seed, sibling_root, patch)
        assert _git("rev-parse", "HEAD", cwd=sibling) == head
        assert _record(wid).pr.head_sha == old
        assert _git("--git-dir", str(sibling_remote), "rev-parse", branch, cwd=sibling) == accepted
    assert cfg.load_config() is config
    sibling_files = {
        entry.relative_to(sibling_root): entry.read_bytes()
        for entry in sibling_root.rglob("*") if entry.is_file()
    }
    assert not inputs_path.samefile(_partial_success_seed / inputs_path.name)
    assert not inputs_path.samefile(sibling_root / inputs_path.name)
    inputs_path.write_text("{}\n", encoding="utf-8")
    (remote / "hooks" / "private-only").write_text("private hook\n")
    _git("--git-dir", str(remote), "config", "fixture.private", str(iteration), cwd=path)
    (path / "private-only.txt").write_text(f"private feedback {iteration}\n")
    _git("add", "private-only.txt", cwd=path)
    _git("commit", "-m", "private partial-success update", cwd=path)
    changed = _git("rev-parse", "HEAD", cwd=path)
    _git("push", f"--force-with-lease=refs/heads/{branch}:{accepted}",
         "origin", f"HEAD:refs/heads/{branch}", cwd=path)
    record.pr.head_sha = changed
    tracking.save_record(record)
    assert _record(wid).pr.head_sha == changed != old
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == changed
    assert {
        entry.relative_to(_partial_success_seed): entry.read_bytes()
        for entry in _partial_success_seed.rglob("*") if entry.is_file()
    } == seed_files
    assert {
        entry.relative_to(sibling_root): entry.read_bytes()
        for entry in sibling_root.rglob("*") if entry.is_file()
    } == sibling_files


def test_partial_success_before_rebase_recovers_only_verified_source_lineage(pr_repo, monkeypatch):
    config, wid, path, remote, branch, old, accepted = _partial_success_then_rebase(pr_repo, monkeypatch)
    monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr, **kwargs: accepted)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert not git_ops.is_commit_ancestor(accepted, tip, cwd=path)
    assert finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    assert _record(wid).pr.head_sha == tip
    assert (path / "accepted.txt").read_text() == "already accepted source feedback\n"
    assert (path / "unpublished.txt").read_text() == "preserve final local feedback\n"
    metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
    proof = json.loads((metadata / "agent-worktrees-pr-rebase.json").read_text())
    assert proof["expected_head"] == old
    assert proof["transport_expected_head"] == accepted


@pytest.mark.parametrize("provider", ["unavailable", "different", "foreign", "racing"])
def test_partial_success_recovery_rejects_unconfirmed_or_unrelated_remote_heads(
    partial_success_repo, monkeypatch, provider, _partial_success_seed,
):
    config, wid, path, remote, branch, old, accepted = partial_success_repo
    seed_tracking = (_partial_success_seed / "tracking" / f"{wid}.yaml").read_bytes()
    seed_refs = _git("--git-dir", str(_partial_success_seed / "remote.git"), "show-ref", cwd=path)
    remote_head = accepted
    if provider in ("foreign", "racing"):
        other = remote.parent / "concurrent"
        _git("clone", str(remote), str(other), cwd=remote.parent)
        _git("config", "user.email", "reviewer@example.com", cwd=other)
        _git("config", "user.name", "Reviewer", cwd=other)
        _git("checkout", "-B", branch, f"origin/{branch}", cwd=other)
        (other / "foreign.txt").write_text("do not overwrite\n")
        _git("add", "foreign.txt", cwd=other)
        _git("commit", "-m", "concurrent feedback", cwd=other)
        remote_head = _git("rev-parse", "HEAD", cwd=other)
        if provider == "foreign":
            _git("push", "origin", branch, cwd=other)
    observed = "" if provider == "unavailable" else "0" * 40 if provider == "different" else remote_head
    observations = []
    def provider_head(repo, pr, **kwargs):
        observations.append(observed)
        if provider == "racing":
            _git("push", "origin", branch, cwd=other)
            return accepted
        return observed
    monkeypatch.setattr(pr_publish, "_provider_head", provider_head)
    assert not finalize.push_changes(wid, config)
    assert bool(observations) is (provider != "foreign")
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == remote_head
    assert _record(wid).pr.head_sha == old
    assert (_partial_success_seed / "tracking" / f"{wid}.yaml").read_bytes() == seed_tracking
    assert _git("--git-dir", str(_partial_success_seed / "remote.git"), "show-ref", cwd=path) == seed_refs


@pytest.mark.parametrize("scheme,command,flow", [
    pytest.param("snapshot", "push-changes", "sync", marks=exhaustive),
    pytest.param("snapshot", "create-pr", "manual", marks=exhaustive),
    pytest.param("refspec", "push-changes", "manual", marks=pytest.mark.guard),
    pytest.param("refspec", "create-pr", "sync", marks=exhaustive),
])
def test_owned_pr_rebase_publishes_with_original_lease(pr_repo, monkeypatch, scheme, command, flow):
    config, wid, path, remote, branch, old = _prepare(pr_repo, scheme)
    (path / "feedback.txt").write_text("preserve unpublished source work\n")
    _git("add", "-A", cwd=path)
    _git("commit", "-m", "feedback before rebase", cwd=path)
    onto = _advance(config, path)
    if flow == "sync":
        assert git_collab.sync_forward(wid, config)
        metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
        proof = json.loads((metadata / "agent-worktrees-pr-rebase.json").read_text())
        assert proof["expected_head"] == old
        assert proof["new_base"] == onto
    else:
        _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert not git_ops.is_commit_ancestor(old, tip, cwd=path)
    raw_url = "".join(["https://", "developer:", "synthetic-password", "@example.com/repo.git"])
    original_git = pr_rebase._git

    def url_with_credentials(*args, cwd):
        if args == ("remote", "get-url", "--push", "origin"):
            return raw_url
        return original_git(*args, cwd=cwd)

    with monkeypatch.context() as patch:
        patch.setattr(pr_rebase, "_git", url_with_credentials)
        record = _record(wid)
        proof = pr_rebase.verify(
            record, config.default_repo, "origin", f"{record.branch}:refs/heads/{branch}",
            old, cwd=str(path),
        )
        assert proof is not None
        pr_rebase._save(proof, str(path))
        metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
        serialized = (metadata / "agent-worktrees-pr-rebase.json").read_text()
        assert raw_url not in serialized and "synthetic-password" not in serialized
        assert json.loads(serialized)["remote_fingerprint"] == hashlib.sha256(raw_url.encode()).hexdigest()
    assert _publish(command, config, wid)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    record = _record(wid)
    assert record.pr.head_sha == tip
    assert record.pr.base_sha == onto
    assert record.pr.patch_id == pr_ops._patch_id(onto, tip, cwd=str(path))
    assert (path / "feedback.txt").read_text() == "preserve unpublished source work\n"
    if scheme == "refspec" and command == "push-changes":
        # Reuse after the rewrite is incremental again, with a refreshed base/head.
        (path / "later.txt").write_text("later feedback\n")
        _git("add", "-A", cwd=path)
        _git("commit", "-m", "later feedback", cwd=path)
        assert _publish(command, config, wid)


@pytest.mark.parametrize("command", [
    pytest.param("push-changes", marks=pytest.mark.guard),
    pytest.param("create-pr", marks=exhaustive),
])
def test_owned_pr_rebase_rejects_concurrent_reviewer_commit(pr_repo, command):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    other = remote.parent / "reviewer"
    _git("clone", str(remote), str(other), cwd=remote.parent)
    _git("config", "user.email", "reviewer@example.com", cwd=other)
    _git("config", "user.name", "Reviewer", cwd=other)
    _git("checkout", "-B", branch, f"origin/{branch}", cwd=other)
    (other / "reviewer.txt").write_text("must not drop\n")
    _git("add", "-A", cwd=other)
    _git("commit", "-m", "reviewer's commit", cwd=other)
    foreign = _git("rev-parse", "HEAD", cwd=other)
    _git("push", "origin", branch, cwd=other)
    # Caller fetches the moved remote, but must not refresh the expected lease.
    assert not _publish(command, config, wid)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == foreign
    assert _record(wid).pr.head_sha == old


@pytest.mark.guard
@pytest.mark.parametrize("drop_source", [False, True], ids=["preserved", "dropped"])
@pytest.mark.parametrize("empty_upstream", [False, True], ids=["content-only", "empty-upstream"])
def test_owned_pr_rebase_with_stale_base_preserves_exact_upstream_ancestors(
    pr_repo, drop_source: bool, empty_upstream: bool,
) -> None:
    config, wid, path, remote, branch, _ = _prepare(pr_repo)
    old_base = _record(wid).pr.base_sha
    if empty_upstream:
        _git("commit", "--allow-empty", "-m", "upstream bookkeeping", cwd=config.default_repo.anchor)
    first_base = _advance(config, path, "README.md")
    _git("rebase", "origin/master", cwd=path)
    assert finalize.push_changes(wid, config)
    record = _record(wid)
    published = record.pr.head_sha
    assert record.pr.base_sha == first_base
    # Older integrations could advance the source fork without refreshing base_sha.
    record.pr.base_sha = old_base
    tracking.save_record(record)
    (path / "feedback.txt").write_text("unpublished source work\n")
    _git("add", "feedback.txt", cwd=path)
    _git("commit", "-m", "source feedback", cwd=path)
    anchor = Path(config.default_repo.anchor)
    (anchor / "README.md").write_text("later upstream revision\n")
    _git("add", "README.md", cwd=anchor)
    _git("commit", "-m", "edit earlier upstream change", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    _git("fetch", "origin", cwd=path)
    onto = _git("rev-parse", "origin/master", cwd=path)
    _, conflicted = pr_rebase._merge_tree(old_base, onto, first_base, str(path))
    assert conflicted  # Reapplying the historical upstream change is not a no-op.
    if drop_source:
        _git("rebase", "--onto", "origin/master", published, cwd=path)
    else:
        _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert finalize.push_changes(wid, config) is not drop_source
    expected = published if drop_source else tip
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == expected
    if not drop_source:
        assert (path / "a.txt").read_text() == "one\n"
        assert (path / "b.txt").read_text() == "two\n"
        assert (path / "README.md").read_text() == "later upstream revision\n"
        assert (path / "feedback.txt").read_text() == "unpublished source work\n"
        assert _record(wid).pr.base_sha == onto


@pytest.mark.guard
@pytest.mark.parametrize("drop_empty", [False, True], ids=["retained", "dropped"])
def test_owned_pr_rebase_refuses_source_owned_empty_commits(pr_repo, drop_empty: bool) -> None:
    config, wid, path, remote, branch, published = _prepare(pr_repo)
    _git("commit", "--allow-empty", "-m", "source bookkeeping", cwd=path)
    _advance(config, path)
    options = ("--no-keep-empty",) if drop_empty else ()
    _git("rebase", *options, "origin/master", cwd=path)
    assert not finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == published
    assert _record(wid).pr.head_sha == published


@pytest.mark.guard
def test_owned_pr_rebase_still_refuses_upstream_merge_commits(pr_repo) -> None:
    _, wid, path, _, _, published = _prepare(pr_repo)
    base = _record(wid).pr.base_sha
    tree = _git("rev-parse", f"{base}^{{tree}}", cwd=path)
    side = _git("commit-tree", tree, "-p", base, "-m", "side bookkeeping", cwd=path)
    merged = _git("commit-tree", tree, "-p", base, "-p", side, "-m", "upstream merge", cwd=path)
    source_tree = _git("rev-parse", f"{published}^{{tree}}", cwd=path)
    original = _git("commit-tree", source_tree, "-p", merged, "-m", "source change", cwd=path)
    assert pr_rebase._series(base, original, str(path), upstream=merged) is None


@exhaustive
@pytest.mark.parametrize("mutation", ["reset", "same-tree-reset", "amend", "drop"])
def test_owned_pr_rebase_refuses_unproved_source_rewrite(pr_repo, mutation):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    _advance(config, path)
    if mutation == "reset":
        _git("reset", "--hard", "origin/master", cwd=path)
    elif mutation == "same-tree-reset":
        _git("rebase", "origin/master", cwd=path)
        _git("reset", "--hard", "HEAD", cwd=path)
    elif mutation == "amend":
        _git("rebase", "origin/master", cwd=path)
        (path / "a.txt").write_text("silently changed\n")
        _git("add", "-A", cwd=path)
        _git("commit", "--amend", "--no-edit", cwd=path)
    else:
        (path / "feedback.txt").write_text("source work\n")
        _git("add", "-A", cwd=path)
        _git("commit", "-m", "source feedback", cwd=path)
        # A real rebase journal alone does not authorize skipping the published patch.
        _git("rebase", "--onto", "origin/master", old, cwd=path)
    assert not finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old


@exhaustive
def test_owned_pr_rebase_does_not_authorize_shared_default_or_foreign_heads(pr_repo):
    config, wid, path, remote, original_branch, old = _prepare(pr_repo)
    onto = _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    record = _record(wid)
    for branch in ("master", "feature/shared", "pr/someone-else"):
        record.pr.branch = branch
        if branch != "master":
            _git("push", "origin", f"{old}:refs/heads/{branch}", cwd=path)
        result = pr_publish.push_checked(
            record, "origin", f"{record.branch}:refs/heads/{branch}", cwd=str(path),
            force_with_lease_expect=old, repo=config.default_repo,
        )
        assert not result
        expected = onto if branch == "master" else old
        assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == expected
    assert _git("--git-dir", str(remote), "rev-parse", original_branch, cwd=path) == old


@exhaustive
def test_owned_pr_rebase_keeps_generic_push_fail_closed(pr_repo):
    config, _, path, remote, branch, old = _prepare(pr_repo)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    result = git_ops.push(
        "origin", f"HEAD:refs/heads/{branch}", cwd=path, force_with_lease_expect=old,
    )
    assert not result
    assert "not an ancestor" in result.stderr
    missing = git_ops.push(
        "origin", f"HEAD:refs/heads/{branch}", cwd=path,
        force_with_lease_expect="1" * 40,
    )
    assert not missing
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old


@exhaustive
def test_owned_pr_rebase_publishes_legacy_checked_out_private_head(pr_repo):
    config, wid, path, remote, branch, old = _prepare(pr_repo, "snapshot")
    _git("checkout", branch, cwd=path)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert tip != old
    assert _publish("create-pr", config, wid)
    assert _record(wid).pr.head_sha == tip
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip


@exhaustive
def test_owned_pr_rebase_recovers_legacy_stale_published_patch_cache(pr_repo):
    config, wid, path, remote, branch, _ = _prepare(pr_repo)
    original_patch = _record(wid).pr.patch_id
    (path / "reviewed-feedback.txt").write_text("already published\n")
    _git("add", "-A", cwd=path)
    _git("commit", "-m", "reviewed feedback", cwd=path)
    assert finalize.push_changes(wid, config)
    record = _record(wid)
    old = record.pr.head_sha
    # Reproduce older runtimes: their published head advanced but patch_id did not.
    record.pr.patch_id = original_patch
    tracking.save_record(record)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
    proof = json.loads((metadata / "agent-worktrees-pr-rebase.json").read_text())
    assert proof["expected_head"] == old
    assert proof["recorded_patch_id"] == original_patch
    assert proof["published_patch_id"] != original_patch
    assert (path / "reviewed-feedback.txt").read_text() == "already published\n"


@pytest.mark.parametrize("scheme", [
    pytest.param("snapshot", marks=exhaustive),
    pytest.param("refspec", marks=pytest.mark.guard),
])
def test_owned_pr_rebase_honors_real_pre_push_rejection(pr_repo, scheme, capsys, monkeypatch):
    config, wid, path, remote, branch, old = _prepare(pr_repo, scheme)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    common = Path(_git("rev-parse", "--git-common-dir", cwd=path))
    if not common.is_absolute():
        common = path / common
    hook = common / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho 'BLOCKED: release guard'\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(common / "disabled-hooks"))
    assert not finalize.push_changes(wid, config)
    output = capsys.readouterr()
    assert "BLOCKED: release guard" in output.out + output.err
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old


@exhaustive
@pytest.mark.parametrize("command", ["push-changes", "create-pr"])
def test_owned_pr_rebase_publishes_explicit_conflict_continuation(pr_repo, command):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    # The resolver's explicit continuation changes a patch; retain one-to-one
    # source identity, base lineage, and all other source work.
    anchor = Path(config.default_repo.anchor)
    (anchor / "a.txt").write_text("upstream's different content\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "conflicting base", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    _git("fetch", "origin", cwd=path)
    result = git_ops.git("rebase", "origin/master", cwd=path, check=False)
    assert result.returncode
    (path / "a.txt").write_text("new resolution\n")
    _git("add", "-A", cwd=path)
    git_ops.git("-c", "core.editor=true", "rebase", "--continue", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert _publish(command, config, wid)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    assert (path / "b.txt").read_text() == "two\n"
    metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
    proof = json.loads((metadata / "agent-worktrees-pr-rebase.json").read_text())
    assert proof["conflict_replays"] == [[old, tip]]


@pytest.mark.guard
@pytest.mark.parametrize("changed_message", [" Message\n", "Message\n\n"])
def test_owned_pr_rebase_conflict_message_is_compared_without_stripping(monkeypatch, changed_message):
    def fake_git(*args, cwd, check, isolated_repository):
        assert isolated_repository
        if args[-1] == "base..original":
            stdout = "old base\n"
        elif args[-1] == "onto..finished":
            stdout = "new onto\n"
        else:
            stdout = "current-tree\n"
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    def fake_run(args, **kwargs):
        assert not kwargs.get("text")
        message = "Message\n" if args[-1] == "old" else changed_message
        stdout = b"Author\x00author@example.com\x002026-01-01T00:00:00Z\x00" + message.encode()
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(git_ops, "git", fake_git)
    monkeypatch.setattr(pr_rebase.subprocess, "run", fake_run)
    monkeypatch.setattr(pr_rebase, "_ancestor", lambda *args: False)
    monkeypatch.setattr(pr_rebase, "_merge_tree", lambda *a: ("replay-tree", True))
    assert pr_rebase._conflict_lineage(
        "base", "original", "onto", "finished", ["old-patch"], ["new-patch"],
        [("new", "rebase (continue): Message")], "unused",
    ) is None


@pytest.mark.guard
def test_owned_pr_rebase_patch_pipeline_preserves_non_utf8_and_trailing_space(tmp_path):
    path = tmp_path / "raw-patches"
    path.mkdir()
    _git("init", cwd=path)
    _git("config", "user.name", "Developer", cwd=path)
    _git("config", "user.email", "developer@example.com", cwd=path)
    data = path / "data.txt"
    data.write_bytes(b"base\n")
    _git("add", "data.txt", cwd=path)
    _git("commit", "-m", "base", cwd=path)
    base = _git("rev-parse", "HEAD", cwd=path)
    patches = []
    for content in (b"\xff", b"\xfe", b"line ", b"line  "):
        data.write_bytes(content)
        _git("add", "data.txt", cwd=path)
        tree = _git("write-tree", cwd=path)
        head = _git("commit-tree", tree, "-p", base, "-m", "candidate", cwd=path)
        series = pr_rebase._series(base, head, str(path))
        assert series is not None and len(series) == 1
        patches.append(series[0])
    assert patches[0] != patches[1]
    assert patches[2] != patches[3]


@pytest.mark.guard
def test_owned_pr_rebase_poisoned_git_context_cannot_redirect_proof_or_push(pr_repo, monkeypatch):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    onto = _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    foreign = remote.parent / "foreign.git"
    _git("init", "--bare", str(foreign), cwd=remote.parent)
    record = _record(wid)
    with monkeypatch.context() as patch:
        patch.setenv("GIT_DIR", str(foreign))
        patch.setenv("GIT_WORK_TREE", str(remote.parent))
        patch.setenv("GIT_INDEX_FILE", str(foreign / "index"))
        assert pr_rebase._git("rev-parse", "HEAD", cwd=str(path)) == tip
        assert git_ops._remote_url("origin", cwd=path) == str(remote)
        proof = pr_rebase.verify(
            record, config.default_repo, "origin", f"{record.branch}:refs/heads/{branch}",
            old, cwd=str(path),
        )
        assert proof is not None
        result = pr_rebase.push(
            record, config.default_repo, "origin", f"{record.branch}:refs/heads/{branch}",
            old, cwd=str(path),
        )
        assert result, result.stderr
        assert result.published_head_sha == tip and result.rebase_base_sha == onto
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    assert git_ops.git(
        "--git-dir", str(foreign), "show-ref", "--verify", f"refs/heads/{branch}",
        cwd=path, check=False,
    ).returncode != 0


@pytest.mark.guard
def test_owned_pr_rebase_repeated_regions_require_actual_tree_replay(tmp_path, monkeypatch):
    path = tmp_path / "repeated-regions"
    path.mkdir()
    _git("init", cwd=path)
    _git("config", "user.name", "Developer", cwd=path)
    _git("config", "user.email", "developer@example.com", cwd=path)
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")
    region = "zero\none\ntwo\ntarget\nfour\nfive\nsix\nseven\n"
    original_text = region * 2
    data = path / "data.txt"

    def commit(content, parent, message):
        data.write_text(content)
        _git("add", "data.txt", cwd=path)
        tree = _git("write-tree", cwd=path)
        args = ("-p", parent) if parent else ()
        return _git("commit-tree", tree, *args, "-m", message, cwd=path)

    base = commit(original_text, None, "base")
    first = original_text.replace("target\n", "edited\n", 1)
    second = region + region.replace("target\n", "edited\n", 1)
    original = commit(first, base, "edit repeated region")
    onto = commit("upstream prefix\n" + original_text, base, "shift line numbers")
    correct = commit("upstream prefix\n" + first, onto, "edit repeated region")
    relocated = commit("upstream prefix\n" + second, onto, "edit repeated region")
    old = pr_rebase._series(base, original, str(path))
    correct_ids = pr_rebase._series(onto, correct, str(path))
    relocated_ids = pr_rebase._series(onto, relocated, str(path))
    assert old and old == correct_ids == relocated_ids
    assert pr_rebase._conflict_lineage(
        base, original, onto, correct, old, correct_ids,
        [(correct, "rebase (pick): edit repeated region")], str(path),
    ) == ()
    assert pr_rebase._conflict_lineage(
        base, original, onto, relocated, old, relocated_ids,
        [(relocated, "rebase (pick): edit repeated region")], str(path),
    ) is None
    wrong_applied = commit(second, base, "edit repeated region")
    assert pr_rebase._series(base, wrong_applied, str(path)) == old
    assert pr_rebase._conflict_lineage(
        base, original, wrong_applied, wrong_applied, old, [], [], str(path),
    ) is None


@pytest.mark.guard
def test_owned_pr_rebase_proof_reads_raw_objects_despite_replacement_refs(tmp_path):
    path = tmp_path / "replacement-refs"
    path.mkdir()
    _git("init", cwd=path)
    _git("config", "user.name", "Developer", cwd=path)
    _git("config", "user.email", "developer@example.com", cwd=path)
    data = path / "data.txt"
    data.write_text("base\n")
    _git("add", "data.txt", cwd=path)
    _git("commit", "-m", "base", cwd=path)
    base = _git("rev-parse", "HEAD", cwd=path)
    candidates = []
    for content, message in (("preserved\n", "original"), ("lost work\n", "changed")):
        data.write_text(content)
        _git("add", "data.txt", cwd=path)
        tree = _git("write-tree", cwd=path)
        candidates.append(_git("commit-tree", tree, "-p", base, "-m", message, cwd=path))
    original, changed = candidates
    expected = pr_rebase._series(base, changed, str(path))
    assert expected and expected != pr_rebase._series(base, original, str(path))
    _git("replace", changed, original, cwd=path)
    assert _git("show", "-s", "--format=%s", changed, cwd=path) == "original"
    assert pr_rebase._git("show", "-s", "--format=%s", changed, cwd=str(path)) == "changed"
    assert pr_rebase._series(base, changed, str(path)) == expected
    assert pr_rebase._conflict_lineage(
        base, original, base, changed, ["original-patch"], ["changed-patch"],
        [(changed, "rebase (continue): original")], str(path),
    ) is None
    descendant = _git("commit-tree", tree, "-p", original, "-m", "descendant", cwd=path)
    _git("replace", "-f", changed, descendant, cwd=path)
    assert git_ops.git(
        "merge-base", "--is-ancestor", original, changed, cwd=path, check=False,
    ).returncode == 0
    assert not git_ops.is_commit_ancestor(original, changed, cwd=path)
    assert not pr_rebase._ancestor(original, changed, str(path))
    result = git_ops.push("origin", changed, cwd=path, force_with_lease_expect=original)
    assert not result and "not an ancestor" in result.stderr


@pytest.mark.guard
@pytest.mark.parametrize("command", ["log", "patch-id"])
def test_owned_pr_rebase_binary_timeouts_are_reported_as_git_errors(monkeypatch, command):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(pr_rebase.subprocess, "run", timeout)
    with pytest.raises(git_ops.GitError, match="timed out after 30 seconds") as error:
        pr_rebase._git_bytes(command, cwd="unused", stdin=b"patch" if command == "patch-id" else None)
    assert error.value.returncode == 124
    monkeypatch.setattr(
        pr_rebase, "verify",
        lambda *a, **k: pr_rebase._git_bytes(command, cwd="unused"),
    )
    result = pr_rebase.push(None, None, "origin", "source:target", "expected", cwd="unused")
    assert not result
    assert "timed out after 30 seconds" in result.stderr


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENT_WORKTREES_NATIVE_HEADLESS_TEST") != "1",
    reason="Opt-in native Windows consoleless-parent observation",
)
def test_owned_pr_rebase_native_headless_two_cycles(pr_repo, tmp_path):
    from agent_procutil import no_window_kwargs

    _config, _wid, path, _remote, _branch, head = _prepare(pr_repo)
    base = _record(_wid).pr.base_sha
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    assert pythonw.is_file()
    result = tmp_path / "window-observation.json"
    source = Path(git_ops.__file__).resolve().parents[1]
    proc = subprocess.run(
        [str(pythonw), "-I", str(Path(__file__).with_name("native_pr_rebase_headless.py")),
         str(source), str(path), base, head, str(result)],
        capture_output=True, text=True, timeout=90, **no_window_kwargs(),
    )
    assert result.is_file(), proc.stderr
    observation = json.loads(result.read_text(encoding="utf-8"))
    assert proc.returncode == 0, (proc.stderr, observation)
    assert observation["cycles"] == 2
    assert observation["observed_descendants"] > 0
    assert observation["visible_windows"] == observation["foreground_owned"] == 0
    assert observation["new_visible_windows"] == observation["foreground_transitions"] == 0
    assert observation["errors"] == []
