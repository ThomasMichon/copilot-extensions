"""Isolation contracts for the reusable base PR repository seed."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import git_ops, pr_publish, tracking
from conftest import _copy_pr_repo


def _files(root):
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


def test_session_seed_ignores_global_git_config_and_restores_environment(tmp_path):
    home = tmp_path / "launch-home"
    home.mkdir()
    hooks = home / "hooks"
    hooks.mkdir()
    marker = home / "hook-ran"
    for name in ("pre-commit", "pre-push"):
        hook = hooks / name
        hook.write_text(f'#!/bin/sh\n: > "{marker.as_posix()}"\nexit 41\n')
        hook.chmod(0o755)
    global_config = home / ".gitconfig"
    global_config.write_text(
        f'[core]\n hooksPath = "{hooks.as_posix()}"\n'
        f'[commit]\n gpgsign = true\n[gpg]\n program = "{(home / "absent-gpg").as_posix()}"\n',
        encoding="utf-8",
    )
    before = global_config.read_bytes()
    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from conftest import _pr_repo_seed
from agent_worktrees import git_ops
root = Path(sys.argv[2])
class Factory:
    def mktemp(self, key):
        seed = root / key
        seed.mkdir()
        return seed
assert git_ops.git('config', '--global', '--get', 'commit.gpgsign').stdout.strip() == 'true'
before = dict(os.environ)
seed = _pr_repo_seed.__wrapped__(Factory())
assert dict(os.environ) == before
worktree = seed / 'worktrees' / 'test-wt-20260618-aaaa'
assert git_ops.git('rev-list', '--count', 'master..HEAD', cwd=worktree).stdout.strip() == '2'
assert git_ops.git('config', '--global', '--get', 'commit.gpgsign').stdout.strip() == 'true'
assert not (root / 'hook-ran').exists()
"""
    env = os.environ.copy()
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / "xdg"))
    for name in (
        "COPILOT_EXTENSIONS_TEST_CONTAINED", "COPILOT_EXTENSIONS_TEST_SANDBOX",
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
    ):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent), str(home)],
        env=env, capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert global_config.read_bytes() == before
    assert not marker.exists()


def test_failed_session_seed_restores_git_environment(tmp_path_factory, monkeypatch):
    import conftest

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "synthetic-user-config")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")

    def failed_builder(seed):
        assert os.environ["GIT_CONFIG_GLOBAL"] == os.devnull
        assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(conftest, "_build_pr_repo_seed", failed_builder)
    with pytest.raises(RuntimeError, match="synthetic build failure"):
        conftest._pr_repo_seed.__wrapped__(tmp_path_factory)
    assert os.environ["GIT_CONFIG_GLOBAL"] == "synthetic-user-config"
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "0"


@pytest.mark.parametrize("iteration", range(2))
def test_pr_repo_copy_relocates_links_and_isolates_mutations(
    pr_repo, _pr_repo_seed, iteration,
):
    config, wid, worktree, remote = pr_repo
    anchor = Path(config.default_repo.anchor)
    seed_anchor = _pr_repo_seed / "anchor"
    seed_remote = _pr_repo_seed / "remote.git"
    seed_worktree = _pr_repo_seed / "worktrees" / wid
    seed_files = _files(_pr_repo_seed)
    seed_tip = git_ops.git("rev-parse", "HEAD", cwd=seed_worktree).stdout
    seed_config = (seed_anchor / ".git" / "config").read_bytes()
    seed_link = (seed_worktree / ".git").read_bytes()
    assert (anchor / ".git" / "hooks").is_dir()
    assert (remote / "hooks").is_dir()
    assert not list((anchor / ".git" / "hooks").iterdir())
    assert not list((remote / "hooks").iterdir())
    assert git_ops.git(
        "config", "--get", "maintenance.auto", cwd=anchor,
    ).stdout.strip() == "false"
    assert git_ops.git(
        "--git-dir", str(remote), "config", "--get", "maintenance.auto",
    ).stdout.strip() == "false"
    hook = anchor / ".git" / "hooks" / "fixture-only"
    hook.write_text("independent hook state\n", encoding="utf-8")
    assert not (seed_anchor / ".git" / "hooks" / hook.name).exists()

    listing = git_ops.git("worktree", "list", "--porcelain", cwd=anchor).stdout
    assert str(worktree).replace("\\", "/") in listing
    assert str(_pr_repo_seed).replace("\\", "/") not in listing
    assert git_ops.git("remote", "get-url", "origin", cwd=anchor).stdout.strip() == str(
        remote
    )
    assert git_ops.git("rev-list", "--count", "master..HEAD", cwd=worktree).stdout.strip() == "2"
    assert Path(git_ops.git(
        "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=worktree,
    ).stdout.strip()) == anchor / ".git"
    for store in (seed_anchor / ".git", seed_remote):
        assert not (store / "objects" / "info" / "alternates").exists()
        for source in store.rglob("*"):
            if source.is_file():
                assert not source.samefile(remote.parent / source.relative_to(_pr_repo_seed))

    (worktree / "isolated.txt").write_text(f"copy {iteration}\n", encoding="utf-8")
    git_ops.git("add", "isolated.txt", cwd=worktree)
    git_ops.git("commit", "-m", "isolated change", cwd=worktree)
    git_ops.git("push", "origin", "HEAD:refs/heads/isolated", cwd=worktree)
    git_ops.git("config", "fixture.private", "only-copy", cwd=worktree)
    record = tracking.load_record_by_id(wid)
    assert record is not None
    assert record.worktree_path == str(worktree)
    assert record.prs == []
    record.title = "private record"
    tracking.save_record(record)

    assert not (seed_worktree / "isolated.txt").exists()
    assert git_ops.git("rev-parse", "HEAD", cwd=seed_worktree).stdout == seed_tip
    assert (seed_anchor / ".git" / "config").read_bytes() == seed_config
    assert (seed_worktree / ".git").read_bytes() == seed_link
    assert git_ops.git(
        "show-ref", "--verify", "refs/heads/isolated", cwd=seed_remote, check=False,
    ).returncode != 0
    assert _files(_pr_repo_seed) == seed_files


def test_sibling_copy_has_independent_stores_config_index_hooks_refs_and_records(
    _pr_repo_seed, tmp_path,
):
    seed_files = _files(_pr_repo_seed)
    with pytest.MonkeyPatch.context() as patch:
        config, wid, first, remote = _copy_pr_repo(_pr_repo_seed, tmp_path / "first", patch)
        anchor = Path(config.default_repo.anchor)
        (first / "only-first").write_text("private", encoding="utf-8")
        git_ops.git("add", "only-first", cwd=first)
        git_ops.git("commit", "-m", "first-only commit", cwd=first)
        git_ops.git("push", "origin", "HEAD:refs/heads/only-first", cwd=first)
        git_ops.git("config", "fixture.private", "only-first", cwd=first)
        (anchor / ".git" / "hooks" / "only-first").write_text("private hook")
        record = tracking.load_record_by_id(wid)
        record.title = "only-first"
        tracking.save_record(record)
    with pytest.MonkeyPatch.context() as patch:
        config, wid, second, remote = _copy_pr_repo(_pr_repo_seed, tmp_path / "second", patch)
        assert not (second / "only-first").exists()
        assert not (Path(config.default_repo.anchor) / ".git" / "hooks" / "only-first").exists()
        assert git_ops.git("config", "--get", "fixture.private", cwd=second, check=False).returncode != 0
        assert git_ops.git(
            "--git-dir", str(remote), "show-ref", "--verify",
            "refs/heads/only-first", check=False,
        ).returncode != 0
        assert git_ops.git("status", "--porcelain", cwd=second).stdout == ""
        assert tracking.load_record_by_id(wid).title is None
    assert _files(_pr_repo_seed) == seed_files


@pytest.mark.parametrize("rewrite_identity", [False, True])
def test_copy_relocates_saved_record_and_fork_without_changing_seed(
    _pr_repo_seed, tmp_path, rewrite_identity,
):
    """Synthetic persisted metadata exercises relocation, not publication authority."""
    seed = tmp_path / "record-seed"
    with pytest.MonkeyPatch.context() as patch:
        config, wid, worktree, remote = _copy_pr_repo(_pr_repo_seed, seed, patch)
        git_ops.git("clone", "--bare", str(remote), str(seed / "fork.git"))
        git_ops.git("remote", "add", "fork", str(seed / "fork.git"), cwd=worktree)
        record = tracking.load_record_by_id(wid)
        record.prs = [
            tracking.PRRecord(
                repo=git_ops.slug_from_url(str(remote)),
                branch="feature/example", state="open", pr_revision=4,
                rewrite_identity=(
                    pr_publish._identity(str(remote), str(worktree))
                    if rewrite_identity else ""
                ),
            ),
            tracking.PRRecord(repo="unrelated/repository", pr_revision=8),
        ]
        for pr in record.prs:
            tracking.stamp_frozen_attribution(
                pr, attribution=config.default_repo.pr.source_attribution,
                explicit=config.default_repo.pr.source_attribution_configured,
            )
        tracking.save_record(record)
        original = tracking.load_record_by_id(wid)
        assert all(pr.pr_id for pr in original.prs)
    seed_files = _files(seed)
    with pytest.MonkeyPatch.context() as patch:
        config, wid, worktree, remote = _copy_pr_repo(seed, tmp_path / "relocated", patch)
        relocated = tracking.load_record_by_id(wid)
        assert relocated.worktree_path == str(worktree)
        assert relocated.prs[0].repo == git_ops.slug_from_url(str(remote))
        assert relocated.prs[0].pr_revision == original.prs[0].pr_revision + 1
        assert relocated.prs[0].pr_id == original.prs[0].pr_id
        assert relocated.prs[0].rewrite_identity == (
            pr_publish._identity(str(remote), str(worktree)) if rewrite_identity else ""
        )
        assert relocated.prs[1].repo == "unrelated/repository"
        assert relocated.prs[1].pr_revision == original.prs[1].pr_revision
        assert git_ops.git("remote", "get-url", "fork", cwd=worktree).stdout.strip() == str(
            remote.parent / "fork.git"
        )
    assert _files(seed) == seed_files


@pytest.fixture
def published_repo(request):
    name = request.param
    repo = request.getfixturevalue(name)
    seed_name = (
        "_published_refspec_pr_seed"
        if name == "published_refspec_pr_repo" else "_published_pr_seed"
    )
    scheme = "refspec" if name == "published_refspec_pr_repo" else "snapshot"
    return repo, request.getfixturevalue(seed_name), scheme


@pytest.mark.parametrize(
    "published_repo", ["published_pr_repo", "published_refspec_pr_repo"], indirect=True,
)
@pytest.mark.parametrize("iteration", range(2))
def test_published_pr_copy_preserves_state_and_isolates_updates(
    published_repo, iteration,
):
    repo, seed, scheme = published_repo
    config, wid, worktree, remote = repo
    assert config.default_repo.pr.head_scheme == scheme
    from agent_worktrees import config as cfg

    assert cfg.load_config() is config
    record = tracking.load_record_by_id(wid)
    assert record is not None
    assert record.worktree_path == str(worktree)
    assert record.pr is not None and record.pr.state == "open"
    assert record.pr.repo == git_ops.slug_from_url(str(remote))
    assert record.pr.rewrite_identity == pr_publish.push_identity("origin", cwd=str(worktree))
    branch = record.pr.branch
    original_head = record.pr.head_sha
    assert git_ops.git("rev-parse", "HEAD", cwd=worktree).stdout.strip() == original_head
    assert git_ops.git(
        "--git-dir", str(remote), "rev-parse", branch,
    ).stdout.strip() == original_head
    seed_files = _files(seed)

    (worktree / "published-only.txt").write_text(f"copy {iteration}\n", encoding="utf-8")
    git_ops.git("add", "published-only.txt", cwd=worktree)
    git_ops.git("commit", "-m", "isolated publication update", cwd=worktree)
    git_ops.git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree)
    record.pr.head_sha = git_ops.git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
    tracking.save_record(record)
    assert tracking.load_record_by_id(wid).pr.head_sha == record.pr.head_sha
    assert record.pr.head_sha != original_head
    assert git_ops.git(
        "--git-dir", str(remote), "rev-parse", branch,
    ).stdout.strip() == record.pr.head_sha
    assert _files(seed) == seed_files


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
def test_published_session_setup_isolates_home_git_identity_and_restores(tmp_path, scheme):
    home = tmp_path / "launch-home"
    home.mkdir()
    hooks = home / "hooks"
    hooks.mkdir()
    marker = home / "hook-ran"
    for name in ("pre-commit", "pre-push"):
        hook = hooks / name
        hook.write_text(f'#!/bin/sh\n: > "{marker.as_posix()}"\nexit 41\n')
        hook.chmod(0o755)
    global_config = home / ".gitconfig"
    global_config.write_text(
        f'[core]\n hooksPath = "{hooks.as_posix()}"\n'
        f'[commit]\n gpgsign = true\n[gpg]\n program = "{(home / "absent-gpg").as_posix()}"\n',
        encoding="utf-8",
    )
    registry = home / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text("sentinel: untouched\n")
    key = home / "operator-identity.key"
    key.write_bytes(b"operator key must not be read")
    original = _files(home)
    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import conftest
from agent_worktrees import config as cfg, git_ops
root = Path(sys.argv[2])
scheme = sys.argv[3]
class Factory:
    def mktemp(self, key):
        from tempfile import mkdtemp
        return Path(mkdtemp(prefix=key, dir=root))
factory = Factory()
assert git_ops.git('config', '--global', '--get', 'commit.gpgsign').stdout.strip() == 'true'
before = dict(os.environ)
path_home = Path.home()
tracking_dir = cfg.tracking_dir
load_config = cfg.load_config
base = conftest._pr_repo_seed.__wrapped__(factory)
builder = (
    conftest._published_pr_seed if scheme == 'snapshot'
    else conftest._published_refspec_pr_seed
)
seed = builder.__wrapped__(factory, base)
assert (seed / 'tracking' / 'test-wt-20260618-aaaa.yaml').is_file()
assert dict(os.environ) == before
assert Path.home() == path_home
assert cfg.tracking_dir is tracking_dir and cfg.load_config is load_config
assert git_ops.git('config', '--global', '--get', 'commit.gpgsign').stdout.strip() == 'true'
try:
    with conftest._published_seed_environment(factory):
        assert Path.home() != path_home
        assert cfg._home() == Path.home()
        assert os.environ['GIT_CONFIG_GLOBAL'] == os.devnull
        assert os.environ['GIT_CONFIG_NOSYSTEM'] == '1'
        assert os.environ['AGENT_WORKTREES_STATUS_MONITOR'] == '0'
        assert Path(os.environ['AGENT_WORKTREES_IDENTITY_KEY']).parent == Path.home()
        assert not Path(os.environ['AGENT_WORKTREES_IDENTITY_KEY']).exists()
        raise RuntimeError('synthetic setup failure')
except RuntimeError as exc:
    assert str(exc) == 'synthetic setup failure'
else:
    raise AssertionError('failure did not propagate')
assert dict(os.environ) == before and Path.home() == path_home
assert not (root / 'hook-ran').exists()
"""
    builds = tmp_path / "builds"
    builds.mkdir()
    env = os.environ.copy()
    env.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        XDG_CONFIG_HOME=str(home / "xdg"), AGENT_WORKTREES_IDENTITY_KEY=str(key),
    )
    for name in (
        "COPILOT_EXTENSIONS_TEST_CONTAINED", "COPILOT_EXTENSIONS_TEST_SANDBOX",
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
    ):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent), str(builds), scheme],
        env=env, capture_output=True, text=True, timeout=25, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _files(home) == original
    assert not marker.exists()
