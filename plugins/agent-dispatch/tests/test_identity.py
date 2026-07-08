"""Tests for CWD-based worker-identity resolution (via agent-worktrees)."""

from __future__ import annotations

import subprocess

from agent_dispatch import identity


def test_resolve_identity_via_agent_worktrees(monkeypatch):
    monkeypatch.setattr(identity.shutil, "which", lambda _n: "/usr/bin/agent-worktrees")

    def fake_run(cmd, **_kw):
        key = cmd[-1]
        out = {
            "machine": "host-a",
            "worktree-dir": "/home/u/src/x.worktrees/host-a-wt-123",
        }[key]
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    machine, worktree = identity.resolve_identity()
    assert machine == "host-a"
    assert worktree == "host-a-wt-123"


def test_resolve_identity_absent_agent_worktrees(monkeypatch):
    monkeypatch.setattr(identity.shutil, "which", lambda _n: None)
    assert identity.resolve_identity() == (None, None)


def test_resolve_identity_not_in_worktree(monkeypatch):
    monkeypatch.setattr(identity.shutil, "which", lambda _n: "/usr/bin/agent-worktrees")

    def fake_run(cmd, **_kw):
        # machine resolves, but worktree-dir is empty (not inside a worktree)
        out = "host-a" if cmd[-1] == "machine" else ""
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    machine, worktree = identity.resolve_identity()
    assert machine == "host-a"
    assert worktree is None


# -- repo (lane) resolution --------------------------------------------------


def test_canonicalize_remote_variants():
    c = identity.canonicalize_remote
    expected = "git.example.com/acme/widget"
    assert c("https://git.example.com/acme/widget.git") == expected
    assert c("https://user@git.example.com:443/acme/widget") == expected
    assert c("git@git.example.com:acme/widget.git") == expected
    assert c("https://GIT.EXAMPLE.COM/acme/widget/") == expected  # host lowercased
    # nested path prefix is preserved
    assert (
        c("https://host.example.com/forge/acme/widget.git")
        == "host.example.com/forge/acme/widget"
    )
    assert c(None) is None
    assert c("") is None


def test_resolve_repo_prefers_aw_key(monkeypatch):
    monkeypatch.setattr(
        identity, "_aw_get",
        lambda key: "https://github.com/ThomasMichon/copilot-extensions.git"
        if key == "repo-remote" else None,
    )
    # host is lowercased; the path (owner/repo) is preserved as-is
    assert identity.resolve_repo() == "github.com/ThomasMichon/copilot-extensions"


def test_resolve_repo_falls_back_to_git_origin(monkeypatch):
    monkeypatch.setattr(identity, "_aw_get", lambda _key: None)
    monkeypatch.setattr(identity, "_git_origin", lambda: "git@github.com:acme/widget.git")
    assert identity.resolve_repo() == "github.com/acme/widget"


def test_resolve_repo_selector_name_and_remote(monkeypatch):
    identity._repo_registry.cache_clear()
    monkeypatch.setattr(
        identity, "_repo_registry",
        lambda: (("widget", "git.example.com/acme/widget"),),
    )
    # a known local name resolves to its canonical remote
    assert (
        identity.resolve_repo_selector("widget")
        == "git.example.com/acme/widget"
    )
    # an unknown value is treated as a remote URL and canonicalized
    assert (
        identity.resolve_repo_selector("git@example.com:x/y.git") == "example.com/x/y"
    )
    # reverse: canonical remote -> local name
    assert (
        identity.name_for_repo("git.example.com/acme/widget") == "widget"
    )
    assert identity.name_for_repo("example.com/x/y") is None

