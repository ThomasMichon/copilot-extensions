"""Real process-boundary publication concurrency and destination pinning."""

from pathlib import Path
import os
import subprocess
import sys

import pytest

from agent_worktrees import git_ops, pr_publish
from agent_procutil import no_window_kwargs


def git(root, *args):
    return git_ops.git("-c", "safe.bareRepository=all", *args, cwd=root).stdout.strip()


def repository(tmp_path):
    root = tmp_path / "source"
    remote = tmp_path / "remote.git"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "test")
    git(root, "config", "user.email", "test@example.test")
    git(root, "commit", "--allow-empty", "-qm", "initial")
    git(root, "init", "--bare", "-q", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    return root, remote


def test_pinned_transport_survives_remote_repoint_and_retains_hook(tmp_path, monkeypatch):
    root, remote = repository(tmp_path)
    other = tmp_path / "other.git"
    git(root, "init", "--bare", "-q", str(other))
    marker = tmp_path / "hook-ran"
    hook = root / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf8")
    hook.chmod(0o755)
    original = git_ops.push

    def repoint_then_push(name, refspec, **kwargs):
        with pr_publish.publish_lock(str(root), acquire_timeout=1):
            git(root, "remote", "set-url", "origin", str(other))
        return original(name, refspec, **kwargs)

    monkeypatch.setattr(git_ops, "push", repoint_then_push)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/first", cwd=str(root))
    assert result, result.stderr
    assert marker.exists()
    assert git(remote, "rev-parse", "refs/heads/first") == git(root, "rev-parse", "HEAD")
    assert git_ops.git("show-ref", "--verify", "refs/heads/first", cwd=other,
                       check=False).returncode != 0


@pytest.mark.timeout(30)
def test_unrelated_publications_overlap_at_real_pre_push_boundary(tmp_path):
    root, remote = repository(tmp_path)
    second = tmp_path / "second"
    git(root, "worktree", "add", "-qb", "second", str(second))
    markers = tmp_path / "barrier"
    markers.mkdir()
    hook = root / ".git" / "hooks" / "pre-push"
    helper = tmp_path / "barrier.py"
    helper.write_text(
        "import pathlib,sys,time\n"
        "root=pathlib.Path(sys.argv[1]); name=sys.argv[2]\n"
        "(root/name).write_text('ready')\n"
        "deadline=time.monotonic()+10\n"
        "while len(list(root.iterdir()))<2:\n"
        " if time.monotonic()>deadline: raise SystemExit('publication serialized')\n"
        " time.sleep(.02)\n",
        encoding="utf8",
    )
    # Use a shell hook solely to call the isolated barrier; no host services.
    hook.write_text(
        "#!/bin/sh\n"
        f"exec '{Path(sys.executable).as_posix()}' '{helper.as_posix()}' "
        f"'{markers.as_posix()}' \"$PUBLISH_TEST_NAME\"\n",
        encoding="utf8",
    )
    hook.chmod(0o755)
    script = (
        "import sys\nfrom agent_worktrees import pr_publish\n"
        "r=pr_publish.push_checked(None,'origin',"
        "'HEAD:refs/heads/'+sys.argv[2],cwd=sys.argv[1])\n"
        "if not r: raise SystemExit(r.stderr)\n"
    )
    children = []
    try:
        for path, branch in [(root, "one"), (second, "two")]:
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, str(path), branch],
                env={**os.environ, "PUBLISH_TEST_NAME": branch},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                **no_window_kwargs(),
            ))
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, stderr or stdout
        assert git(remote, "rev-parse", "refs/heads/one") == git(root, "rev-parse", "HEAD")
        assert git(remote, "rev-parse", "refs/heads/two") == git(root, "rev-parse", "HEAD")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=5)


def test_remote_rewrite_rule_cannot_redirect_captured_destination(tmp_path, monkeypatch):
    root, remote = repository(tmp_path)
    other = tmp_path / "redirect.git"
    git(root, "init", "--bare", "-q", str(other))
    original = git_ops.push

    def rewrite_then_push(name, refspec, **kwargs):
        git(root, "config", f"url.{other}.pushInsteadOf", str(remote))
        return original(name, refspec, **kwargs)

    monkeypatch.setattr(git_ops, "push", rewrite_then_push)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/pinned", cwd=str(root))
    assert result, result.stderr
    assert git(remote, "rev-parse", "refs/heads/pinned") == git(root, "rev-parse", "HEAD")


def test_same_branch_stale_expected_object_is_rejected(tmp_path):
    root, remote = repository(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root))
    git(root, "commit", "--allow-empty", "-qm", "first update")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root),
                                   force_with_lease_expect=old)
    first = git(root, "rev-parse", "HEAD")
    git(root, "commit", "--allow-empty", "-qm", "stale writer")
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root),
                                     force_with_lease_expect=old)
    assert not result
    assert git(remote, "rev-parse", "refs/heads/shared") == first
