"""Tests for the ``repos gh`` token-injection env builder (``_gh_env_for_repo``).

The wrapper runs ``gh`` against a repo under the account that owns it via
``GH_TOKEN`` injection -- never ``gh auth switch`` -- so it is race-safe on a
shared box where the active gh account is global per-machine. These tests cover
the env-builder seam (the exec itself is a thin ``subprocess.run`` passthrough).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import git_ops, repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the registry reads/writes under a tmp dir."""
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    return tmp_path


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


# --- `repos account-for` dispatcher (command-level, #3032 follow-up) -------
#
# The original bug was specifically an incorrect *successful* `account-for`
# result (echoing an unresolved bare name back with exit 0), so exercising
# only `account_for_github_slug()` in isolation is not enough -- the
# dispatcher's own parsing/output/exit-status wiring needs its own coverage.


def test_repos_account_for_bare_registered_repo_prints_owner(
    home: Path, capfd,
):
    repos.add_repo(
        "copilot-extensions", str(home / "src" / "copilot-extensions"),
        repo_class="reference",
        remote="https://github.com/ThomasMichon/copilot-extensions.git",
        plat="windows",
    )
    capfd.readouterr()  # drain add_repo's own registration confirmation

    rc = m.cmd_repos_dispatch(["account-for", "copilot-extensions"])

    assert rc == 0
    assert capfd.readouterr().out.strip() == "ThomasMichon"


def test_repos_account_for_bare_registered_repo_json(home: Path, capfd):
    repos.add_repo(
        "copilot-extensions", str(home / "src" / "copilot-extensions"),
        repo_class="reference",
        remote="https://github.com/ThomasMichon/copilot-extensions.git",
        plat="windows",
    )
    capfd.readouterr()  # drain add_repo's own registration confirmation

    rc = m.cmd_repos_dispatch(["account-for", "copilot-extensions", "--json"])

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["target"] == "copilot-extensions"
    assert payload["account"] == "ThomasMichon"


def test_repos_account_for_unresolved_registered_repo_fails(home: Path, capfd):
    # A registered non-GitHub repo with no explicit account: override has no
    # resolvable identity -- must print nothing and exit 1, never echo the
    # bare name back as if it were a valid login (the original #3032 bug).
    repos.add_repo(
        "azdo-proj", str(home / "src" / "azdo-proj"), repo_class="reference",
        remote="https://my-org.visualstudio.com/x/_git/azdo-proj",
        plat="windows",
    )
    capfd.readouterr()  # drain add_repo's own registration confirmation

    rc = m.cmd_repos_dispatch(["account-for", "azdo-proj"])

    assert rc == 1
    assert capfd.readouterr().out == ""


def test_repos_account_for_unregistered_bare_owner_prints_itself(
    home: Path, capfd,
):
    # An ordinary unregistered bare owner is unaffected by the #3032 fix --
    # owner == login is still a valid, intentional fallback.
    rc = m.cmd_repos_dispatch(["account-for", "ThomasMichon"])

    assert rc == 0
    assert capfd.readouterr().out.strip() == "ThomasMichon"


# --- omitted-target GitHub-only inference wiring (#3032 follow-up) --------
#
# `repos gh`/`account-for` with no explicit target infer the active repo via
# `_infer_active_github_slug`. A dispatcher-level test guards the actual call
# site -- a helper-level test alone would still pass if the dispatcher
# reverted to the provider-generic `_infer_active_repo_slug`.


def test_repos_gh_omitted_target_infers_github_remote(monkeypatch):
    import subprocess

    from agent_worktrees import config as cfg

    monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(cfg, "load_config", lambda: object())
    monkeypatch.setattr(
        m, "_infer_active_github_slug", lambda cfg: "ThomasMichon/copilot-extensions",
    )
    monkeypatch.setattr(repos, "is_unresolved_registered_target", lambda t: False)
    monkeypatch.setattr(repos, "account_for_github_slug", lambda t: "ThomasMichon")
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda a: "tok")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: calls.append(list(a[0]) if a else []) or type(
            "R", (), {"returncode": 0},
        )(),
    )

    rc = m.cmd_repos_dispatch(["gh", "--", "issue", "list"])

    assert rc == 0
    assert calls == [["gh", "issue", "list"]]


def test_repos_gh_omitted_target_refuses_non_github_active_remote(monkeypatch):
    # The active project's remote is non-GitHub (e.g. Azure DevOps) --
    # `_infer_active_github_slug` returns None, so with no explicit target the
    # command must refuse (never fall back to a provider-generic slug that
    # would mis-resolve as a bogus GitHub owner).
    import subprocess

    from agent_worktrees import config as cfg

    monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(cfg, "load_config", lambda: object())
    monkeypatch.setattr(m, "_infer_active_github_slug", lambda cfg: None)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: calls.append(list(a[0]) if a else []),
    )

    rc = m.cmd_repos_dispatch(["gh", "--", "issue", "list"])

    assert rc == 1
    assert not calls


def test_repos_account_for_omitted_target_infers_github_remote(
    monkeypatch, capfd,
):
    from agent_worktrees import config as cfg

    monkeypatch.setattr(cfg, "load_config", lambda: object())
    monkeypatch.setattr(
        m, "_infer_active_github_slug", lambda cfg: "ThomasMichon/copilot-extensions",
    )
    monkeypatch.setattr(repos, "account_for_github_slug", lambda t: "ThomasMichon")

    rc = m.cmd_repos_dispatch(["account-for"])

    assert rc == 0
    assert capfd.readouterr().out.strip() == "ThomasMichon"


def test_repos_account_for_omitted_target_refuses_non_github_active_remote(
    monkeypatch, capfd,
):
    from agent_worktrees import config as cfg

    monkeypatch.setattr(cfg, "load_config", lambda: object())
    monkeypatch.setattr(m, "_infer_active_github_slug", lambda cfg: None)

    rc = m.cmd_repos_dispatch(["account-for"])

    assert rc == 1
    # The usage/error message goes to stdout (via output.err) -- what matters
    # is that no *login* was printed as if it were a resolved account.
    assert "Usage:" in capfd.readouterr().out
