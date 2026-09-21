"""Tests for the ``repos gh`` token-injection env builder (``_gh_env_for_repo``).

The wrapper runs ``gh`` against a repo under the account that owns it via
``GH_TOKEN`` injection -- never ``gh auth switch`` -- so it is race-safe on a
shared box where the active gh account is global per-machine. These tests cover
the env-builder seam (the exec itself is a thin ``subprocess.run`` passthrough).
"""

from __future__ import annotations

from agent_worktrees import __main__ as m
from agent_worktrees import git_ops, repos


def test_injects_token_when_account_and_token_resolve(monkeypatch):
    monkeypatch.setattr(repos, "account_for_github_slug", lambda t: "acct-x")
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda a: "tok-123")

    env, login, injected = m._gh_env_for_repo("owner/name")
    assert login == "acct-x"
    assert injected is True
    assert env["GH_TOKEN"] == "tok-123"


def test_ambient_when_no_account(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(repos, "account_for_github_slug", lambda t: None)

    minted = {"n": 0}

    def _tok(a):
        minted["n"] += 1
        return "should-not-be-used"

    monkeypatch.setattr(git_ops, "gh_token_for_account", _tok)

    env, login, injected = m._gh_env_for_repo("owner/name")
    assert login is None
    assert injected is False
    assert "GH_TOKEN" not in env
    assert minted["n"] == 0, "must not mint a token when no account resolves"


def test_no_inject_when_token_missing(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(repos, "account_for_github_slug", lambda t: "acct-x")
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda a: None)

    env, login, injected = m._gh_env_for_repo("owner/name")
    assert login == "acct-x"
    assert injected is False
    assert "GH_TOKEN" not in env, "no token means fall back to ambient auth"


def test_never_switches_active_account(monkeypatch):
    """The builder must be side-effect-free: it never calls `gh auth switch`."""
    monkeypatch.setattr(repos, "account_for_github_slug", lambda t: "acct-x")
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda a: "tok")

    calls: list[list[str]] = []
    import subprocess

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: calls.append(list(a[0]) if a else []),
    )
    m._gh_env_for_repo("owner/name")
    # env-builder does no subprocess of its own (token mint is monkeypatched);
    # crucially it issues no `gh auth switch`.
    assert not any("switch" in c for c in calls)


def test_repos_gh_rejects_unresolved_registered_target(monkeypatch, capfd):
    """`repos gh <registered-non-github-repo>` must hard-fail rather than run
    under ambient auth (#3032's fail-fast requirement) -- a registered repo
    with no derivable github owner is a known-but-unresolvable identity, not
    an ordinary "no preference" bare owner.
    """
    import subprocess

    monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(repos, "is_unresolved_registered_target", lambda t: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: calls.append(list(a[0]) if a else []),
    )

    rc = m.cmd_repos_dispatch(["gh", "azdo-proj", "--", "issue", "list"])

    assert rc == 1
    assert not calls, "gh must never be invoked for an unresolved registered target"
    assert "azdo-proj" in capfd.readouterr().out


def test_repos_gh_allows_unregistered_bare_owner(monkeypatch):
    """An ordinary unregistered bare owner (no registry entry at all) keeps
    the existing ambient-auth fallback -- only a *registered* target with an
    unresolvable identity is rejected.
    """
    import subprocess

    monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(repos, "is_unresolved_registered_target", lambda t: False)
    monkeypatch.setattr(repos, "account_for_github_slug", lambda t: None)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: calls.append(list(a[0]) if a else []) or type(
            "R", (), {"returncode": 0},
        )(),
    )

    rc = m.cmd_repos_dispatch(["gh", "ThomasMichon", "--", "issue", "list"])

    assert rc == 0
    assert calls == [["gh", "issue", "list"]]
