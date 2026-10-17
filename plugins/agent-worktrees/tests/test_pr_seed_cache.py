"""Run-local immutable seed publication, process exclusion and copy isolation."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from agent_worktrees import git_ops
from conftest import _build_pr_repo_seed, _copy_pr_repo, _seed_directory

pytestmark = pytest.mark.contract("agent_worktrees.pr_fixture.run_local_seeds")


class Factory:
    def __init__(self, root):
        self.root = root
        self.calls = 0

    def mktemp(self, key):
        self.calls += 1
        target = self.root / f"{key}-{self.calls}"
        target.mkdir(parents=True)
        return target


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(root))
    return root


def test_distinct_group_factories_reuse_only_completed_seed(sandbox, tmp_path):
    first, second = Factory(tmp_path / "group-one"), Factory(tmp_path / "group-two")
    with _seed_directory(first, "example") as (seed, build):
        assert build
        (seed / "data").write_text("immutable")
    with _seed_directory(second, "example") as (again, build):
        assert not build
        assert again == seed
        assert (again / "data").read_text() == "immutable"
    assert first.calls == second.calls == 0
    assert json.loads((sandbox / "s" / "example.ready").read_text())["path"] == str(seed)


def test_uncontained_calls_keep_independent_session_directories(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(tmp_path / "unused"))
    factory = Factory(tmp_path)
    with _seed_directory(factory, "example") as (first, build):
        assert build
    with _seed_directory(factory, "example") as (second, build):
        assert build
    assert first != second and factory.calls == 2
    assert not (tmp_path / "unused").exists()


def test_failed_builder_is_never_published_or_silently_rebuilt(sandbox, tmp_path):
    factory = Factory(tmp_path)
    with pytest.raises(RuntimeError, match="builder failed"):
        with _seed_directory(factory, "example") as (seed, build):
            assert build
            (seed / "partial").write_text("not ready")
            raise RuntimeError("builder failed")
    assert not (sandbox / "s" / "example.ready").exists()
    with pytest.raises(RuntimeError, match="incomplete fixture seed"):
        with _seed_directory(factory, "example"):
            pytest.fail("partial seed was consumed")


def test_malformed_readiness_fails_closed(sandbox, tmp_path):
    factory = Factory(tmp_path)
    with _seed_directory(factory, "example"):
        pass
    (sandbox / "s" / "example.ready").write_text('{"version": 2}')
    with pytest.raises(ValueError, match="invalid fixture seed readiness"):
        with _seed_directory(factory, "example"):
            pytest.fail("invalid readiness was consumed")


@pytest.mark.parametrize("name", ["s", "example.ready", "example.lock"])
def test_seed_cache_rejects_symlink_controls(sandbox, tmp_path, monkeypatch, name):
    original = Path.is_symlink
    rejected = sandbox / "s" if name == "s" else sandbox / "s" / name
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == rejected or original(path))
    with pytest.raises(ValueError, match="symlink"):
        with _seed_directory(Factory(tmp_path), "example"):
            pytest.fail("redirected cache path was accepted")


def test_process_exit_releases_lock_but_does_not_publish_partial_seed(sandbox, tmp_path):
    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from conftest import _seed_directory
class Factory:
    def mktemp(self, key):
        raise AssertionError('unexpected group-local factory')
with _seed_directory(Factory(), 'interrupted') as (seed, build):
    assert build
    (seed / 'partial').write_text('not complete')
    os._exit(2)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent)],
        env=os.environ.copy(), capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 2, result.stderr
    assert not (sandbox / "s" / "interrupted.ready").exists()
    with pytest.raises(RuntimeError, match="incomplete fixture seed"):
        with _seed_directory(Factory(tmp_path), "interrupted"):
            pytest.fail("aborted builder was consumed")


@pytest.mark.parametrize("key", ["../escape", ".hidden", "nested/name"])
def test_seed_keys_cannot_escape_cache(sandbox, tmp_path, key):
    with pytest.raises(ValueError, match="invalid fixture seed key"):
        with _seed_directory(Factory(tmp_path), key):
            pytest.fail("invalid key was accepted")
    assert not (sandbox / "s").exists()


def test_two_processes_publish_once_and_reuse_after_group_cleanup(sandbox, tmp_path):
    script = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from conftest import _seed_directory
root, group = Path(sys.argv[2]), Path(sys.argv[3])
class Factory:
    def mktemp(self, key):
        raise AssertionError('run-local seed used group-local factory')
with _seed_directory(Factory(), 'concurrent') as (seed, build):
    if build:
        with (root / 'builds').open('a') as out:
            out.write('built\\n')
        time.sleep(0.2)
        (seed / 'data').write_text('complete')
    assert (seed / 'data').read_text() == 'complete'
    print(json.dumps(str(seed)))
"""
    processes = []
    try:
        for group in ("one", "two"):
            processes.append(subprocess.Popen(
                [sys.executable, "-c", script, str(Path(__file__).parent),
                 str(sandbox), str(tmp_path / group)],
                env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **no_window_kwargs(),
            ))
        paths = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
            paths.append(json.loads(stdout))
        assert paths[0] == paths[1] == str(sandbox / "s" / "concurrent")
        assert (sandbox / "builds").read_text().splitlines() == ["built"]
        group = tmp_path / "group-temp"
        group.mkdir()
        (group / "temporary").write_text("group only")
        tempfile.TemporaryDirectory._rmtree(group)
        assert not group.exists()
        with _seed_directory(Factory(tmp_path), "concurrent") as (seed, build):
            assert not build and (seed / "data").read_text() == "complete"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_real_git_copies_do_not_mutate_cached_seed_or_sibling(sandbox, tmp_path):
    with _seed_directory(Factory(tmp_path), "real-git") as (seed, build):
        assert build
        _build_pr_repo_seed(seed)
    before = git_ops.git("--git-dir", str(seed / "remote.git"), "show-ref").stdout
    worktree_id = "test-wt-20260618-aaaa"
    links = [
        seed / "anchor" / ".git" / "config",
        seed / "anchor" / ".git" / "worktrees" / worktree_id / "gitdir",
        seed / "worktrees" / worktree_id / ".git",
    ]
    before_links = {path: path.read_bytes() for path in links}
    with pytest.MonkeyPatch.context() as patch:
        _config, _wid, first, remote = _copy_pr_repo(seed, tmp_path / "first", patch)
        (first / "later.txt").write_text("only this copy")
        git_ops.git("add", "later.txt", cwd=first)
        git_ops.git("commit", "-m", "copy mutation", cwd=first)
        git_ops.git("push", str(remote), "HEAD:refs/heads/only-first", cwd=first)
    with pytest.MonkeyPatch.context() as patch:
        _config, _wid, second, remote = _copy_pr_repo(seed, tmp_path / "second", patch)
        assert not (second / "later.txt").exists()
        assert git_ops.git(
            "--git-dir", str(remote), "for-each-ref",
            "--format=%(refname)", "refs/heads/only-first",
        ).stdout == ""
    assert git_ops.git("--git-dir", str(seed / "remote.git"), "show-ref").stdout == before
    assert {path: path.read_bytes() for path in links} == before_links
