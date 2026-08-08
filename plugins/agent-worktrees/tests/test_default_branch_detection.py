"""Tests for remote-default-branch resolution (dotfiles#1046).

Adoption must record the branch the **remote** is configured to default to --
never a stale local `master`. `_resolve_remote_default_branch` is the shared
resolver; `_detect_upstream_branch` is its offline wrapper for the status
segment.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_worktrees import __main__ as m


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


def _make_remote(tmp_path: Path, default: str, extra: tuple[str, ...] = ()) -> Path:
    """A bare 'remote' whose HEAD (default branch) is *default*, plus any
    *extra* branches pushed alongside it."""
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-b", default, str(remote), cwd=tmp_path)
    work = tmp_path / "work"
    _git("init", "-b", default, str(work), cwd=tmp_path)
    _git("commit", "--allow-empty", "-m", "init", cwd=work)
    _git("remote", "add", "origin", str(remote), cwd=work)
    _git("push", "origin", default, cwd=work)
    for b in extra:
        _git("branch", b, cwd=work)
        _git("push", "origin", b, cwd=work)
    return remote


def _clone(tmp_path: Path, remote: Path) -> Path:
    clone = tmp_path / "clone"
    _git("clone", str(remote), str(clone), cwd=tmp_path)
    return clone


def _unset_origin_head(clone: Path) -> None:
    """Simulate the common post-clone state where origin/HEAD is not set."""
    _git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=clone)
    assert not (clone / ".git" / "refs" / "remotes" / "origin" / "HEAD").exists()


# ---------------------------------------------------------------------------
# _resolve_remote_default_branch
# ---------------------------------------------------------------------------

def test_uses_local_origin_head_when_set(tmp_path):
    """A clone records origin/HEAD; resolution reads it (offline)."""
    clone = _clone(tmp_path, _make_remote(tmp_path, "main"))
    assert m._resolve_remote_default_branch(str(clone), "origin") == "main"


def test_ls_remote_authoritative_when_origin_head_unset(tmp_path):
    """origin/HEAD unset + a non-conventional remote default (`trunk`):
    only the network `ls-remote --symref` path can resolve it."""
    clone = _clone(tmp_path, _make_remote(tmp_path, "trunk"))
    _unset_origin_head(clone)
    # Offline probe can't find trunk (not main/master, and origin/HEAD gone).
    assert m._resolve_remote_default_branch(
        str(clone), "origin", allow_remote=False) is None
    # ls-remote asks the remote directly -> authoritative.
    assert m._resolve_remote_default_branch(
        str(clone), "origin", allow_remote=True) == "trunk"


def test_prefers_remote_main_over_stale_local_master(tmp_path):
    """THE regression: remote default is `main`, but the clone has a stale
    local `master` branch and no origin/HEAD. Resolution must yield `main`,
    never the local master."""
    clone = _clone(tmp_path, _make_remote(tmp_path, "main"))
    _unset_origin_head(clone)
    _git("branch", "master", cwd=clone)  # stale local branch
    assert (clone / ".git" / "refs" / "heads" / "master").exists()
    # Offline (remote-ref probe, main-first) and online both pick main.
    assert m._resolve_remote_default_branch(
        str(clone), "origin", allow_remote=False) == "main"
    assert m._resolve_remote_default_branch(
        str(clone), "origin", allow_remote=True) == "main"


def test_remote_ref_probe_is_main_first(tmp_path):
    """When both remote branches exist and origin/HEAD is unset, the offline
    probe prefers main over master."""
    clone = _clone(tmp_path, _make_remote(tmp_path, "main", extra=("master",)))
    _unset_origin_head(clone)
    assert m._resolve_remote_default_branch(
        str(clone), "origin", allow_remote=False) == "main"


def test_config_default_honored_when_valid_on_remote(tmp_path):
    """An explicit config default is honored when it exists on the remote."""
    clone = _clone(tmp_path, _make_remote(tmp_path, "main"))
    _unset_origin_head(clone)
    assert m._resolve_remote_default_branch(
        str(clone), "origin", config_default="main") == "main"


def test_returns_none_when_nothing_resolves_offline(tmp_path):
    """No remote, no main/master remote refs, offline -> None."""
    repo = tmp_path / "local"
    _git("init", "-b", "feature", str(repo), cwd=tmp_path)
    _git("commit", "--allow-empty", "-m", "x", cwd=repo)
    assert m._resolve_remote_default_branch(
        str(repo), "origin", allow_remote=False) is None


# ---------------------------------------------------------------------------
# _detect_upstream_branch (offline wrapper)
# ---------------------------------------------------------------------------

def test_detect_upstream_never_hits_network(tmp_path, monkeypatch):
    """The status wrapper must resolve without allow_remote (no ls-remote)."""
    clone = _clone(tmp_path, _make_remote(tmp_path, "main"))
    _unset_origin_head(clone)

    real_git = m.git_ops.git

    def _guard(*args, **kwargs):
        assert not (args and args[0] == "ls-remote"), "status path hit network"
        return real_git(*args, **kwargs)

    monkeypatch.setattr(m.git_ops, "git", _guard)
    assert m._detect_upstream_branch(str(clone), "origin", None) == "main"


def test_detect_upstream_falls_back_to_config_hint(tmp_path):
    """When nothing resolves, the wrapper returns the config hint (may be
    stale) rather than None."""
    repo = tmp_path / "local"
    _git("init", "-b", "feature", str(repo), cwd=tmp_path)
    _git("commit", "--allow-empty", "-m", "x", cwd=repo)
    assert m._detect_upstream_branch(str(repo), "origin", "develop") == "develop"
