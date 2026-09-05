"""Tests for the repos registry: schema, migration, and git hygiene."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_worktrees import repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the registry reads/writes under a tmp dir."""
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    return tmp_path


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True,
                   capture_output=True, text=True)


def _init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")


# ---------------------------------------------------------------------------
# Class normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("reference", "reference"),
    ("singleton", "singleton"),
    ("worktree", "worktree"),
    ("WORKTREE", "worktree"),
    ("project", "worktree"),   # legacy
    ("repo", "reference"),     # legacy
    ("bogus", "reference"),    # unknown -> safe default
    (None, "reference"),
    ("", "reference"),
])
def test_normalize_class(raw, expected):
    assert repos.normalize_class(raw) == expected


# ---------------------------------------------------------------------------
# Round-trip read/write
# ---------------------------------------------------------------------------

def test_write_read_roundtrip(home: Path):
    repos.set_srcroot("D:/Src", plat="windows")
    repos.add_repo(
        "copilot-extensions", "D:/Src/copilot-extensions",
        repo_class="worktree",
        remote="https://github.com/ThomasMichon/copilot-extensions.git",
        default_branch="main",
        tags=["multi-machine system"],
        contributing="CONTRIBUTING.md",
        plat="windows",
    )
    reg = repos.read_registry()
    e = reg.repos["copilot-extensions"]
    assert e.repo_class == "worktree"
    assert e.default_branch == "main"
    assert e.tags == ["multi-machine system"]
    assert e.contributing == "CONTRIBUTING.md"
    assert e.local_path("windows") == "D:/Src/copilot-extensions"
    assert reg.srcroot["windows"] == "D:/Src"


def test_legacy_type_field_is_mapped(home: Path):
    """A registry written with the old `type:` key still loads."""
    reg_path = home / ".agent-worktrees" / "repos.yaml"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(
        "repos:\n"
        "  old-proj:\n"
        "    type: project\n"
        "    windows: D:/Src/old-proj\n"
        "  old-lib:\n"
        "    type: repo\n"
        "    windows: D:/Src/old-lib\n",
        encoding="utf-8",
    )
    reg = repos.read_registry()
    assert reg.repos["old-proj"].repo_class == "worktree"
    assert reg.repos["old-lib"].repo_class == "reference"


# ---------------------------------------------------------------------------
# agent classification
# ---------------------------------------------------------------------------

def test_agent_defaults_by_class(home: Path):
    repos.add_repo("wt", "/home/u/wt", repo_class="worktree", plat="wsl")
    repos.add_repo("sg", "/home/u/sg", repo_class="singleton", plat="wsl")
    repos.add_repo("ref", "/home/u/ref", repo_class="reference", plat="wsl")
    reg = repos.read_registry()
    # worktree/singleton expose an agent by default; reference does not.
    assert reg.repos["wt"].agent is True
    assert reg.repos["sg"].agent is True
    assert reg.repos["ref"].agent is False


def test_no_agent_flag_overrides_and_roundtrips(home: Path):
    # A worktree repo can be adopted reference-style (no agent).
    repos.add_repo("plugin-src", "/home/u/plugin-src",
                   repo_class="worktree", agent=False, plat="wsl")
    e = repos.read_registry().repos["plugin-src"]
    assert e.repo_class == "worktree"
    assert e.agent is False
    # The deviation from the class default is persisted explicitly.
    text = (home / ".agent-worktrees" / "repos.yaml").read_text()
    assert "agent: false" in text


def test_agent_true_persisted_for_reference(home: Path):
    repos.add_repo("ref-agent", "/home/u/ref-agent",
                   repo_class="reference", agent=True, plat="wsl")
    text = (home / ".agent-worktrees" / "repos.yaml").read_text()
    assert "agent: true" in text
    assert repos.read_registry().repos["ref-agent"].agent is True


def test_add_repo_no_agent_preserved_on_reregister(home: Path):
    repos.add_repo("r", "/home/u/r", repo_class="worktree", agent=False, plat="wsl")
    # Re-registering without an agent flag must preserve the deliberate choice.
    repos.add_repo("r", "D:/Src/r", plat="windows")
    assert repos.find_repo("r").agent is False


# ---------------------------------------------------------------------------
# add_repo merge semantics
# ---------------------------------------------------------------------------

def test_add_repo_preserves_deliberate_class(home: Path):
    repos.add_repo("r", "D:/Src/r", repo_class="worktree", plat="windows")
    # Re-registering a path with the default class must not downgrade it.
    repos.add_repo("r", "/home/u/r", plat="wsl")
    e = repos.find_repo("r")
    assert e.repo_class == "worktree"
    assert e.local_path("windows") == "D:/Src/r"
    assert e.local_path("wsl") == "/home/u/r"


def test_local_path_expands_home_relative(home: Path, monkeypatch):
    """A home-relative registry path (``~/src/...``) must resolve to an absolute
    path, not the literal tilde (#4190): ``pathlib.Path`` does not expand ``~``,
    so a raw return breaks every ``Path(local_path()).is_dir()`` consumer and
    CWD->project discovery fails for that repo. Absolute entries are unchanged."""
    import os

    fake_home = "/home/tester"
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: p.replace("~", fake_home, 1)
                        if p.startswith("~") else p)
    e = repos.RepoEntry(name="r", paths={
        "wsl": "~/src/test-chamber",          # home-relative (the #4190 case)
        "linux": "/home/tester/src/test-chamber",  # already absolute
    })
    assert e.local_path("wsl") == "/home/tester/src/test-chamber"
    # expanduser is a no-op on an already-absolute path.
    assert e.local_path("linux") == "/home/tester/src/test-chamber"
    # No tilde ever leaks through to a consumer.
    assert "~" not in e.local_path("wsl")


def test_list_filter_by_class(home: Path):
    repos.add_repo("a", "D:/a", repo_class="worktree", plat="windows")
    repos.add_repo("b", "D:/b", repo_class="reference", plat="windows")
    worktrees = repos.list_repos(class_filter="worktree")
    assert [e.name for e in worktrees] == ["a"]
    # Legacy alias still filters.
    assert [e.name for e in repos.list_repos(class_filter="repo")] == ["b"]


# ---------------------------------------------------------------------------
# Migration from ~/.git-repos
# ---------------------------------------------------------------------------

def test_migrate_git_repos(home: Path):
    (home / ".git-repos").write_text(
        "srcroot: D:/Src\n"
        "repos:\n"
        "  sample-project:\n"
        "    remote: https://example/sample-project.git\n"
        "    default_branch: master\n"
        "    tags: [multi-machine system]\n"
        "  some-lib:\n"
        "    remote: https://github.com/x/some-lib.git\n"
        "    default_branch: main\n"
        "    path: D:/Other/some-lib\n",
        encoding="utf-8",
    )
    # sample-project is an adopted project -> should classify as worktree.
    proj = home / ".agent-worktrees" / "projects.yaml"
    proj.parent.mkdir(parents=True, exist_ok=True)
    proj.write_text("projects:\n  sample-project:\n    anchor: D:/Src/sample-project\n",
                    encoding="utf-8")

    migrated, skipped = repos.migrate_git_repos(default_class="singleton",
                                                plat="windows")
    assert (migrated, skipped) == (2, 0)
    reg = repos.read_registry()
    assert reg.srcroot["windows"] == "D:/Src"

    al = reg.repos["sample-project"]
    assert al.repo_class == "worktree"            # adopted project
    assert al.default_branch == "master"
    assert al.tags == ["multi-machine system"]
    assert al.local_path("windows") == str(Path("D:/Src/sample-project"))

    lib = reg.repos["some-lib"]
    assert lib.repo_class == "singleton"          # default
    assert lib.local_path("windows") == "D:/Other/some-lib"

    # ~/.git-repos is left in place.
    assert (home / ".git-repos").exists()


def test_migrate_no_legacy_file(home: Path):
    assert repos.migrate_git_repos() == (0, 0)


# ---------------------------------------------------------------------------
# Account resolution (repo-scoped identity)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("remote,owner", [
    ("https://github.com/example-org/proj.git", "example-org"),
    ("https://github.com/example-org/proj", "example-org"),
    ("git@github.com:example-org/proj.git", "example-org"),
    ("ssh://git@github.com/example-org/proj.git", "example-org"),
    ("https://gitlab.com/example-org/proj.git", None),   # non-github
    ("https://host.example.com/gitea/u/r.git", None),    # non-github
    ("", None),
])
def test_github_owner(remote, owner):
    assert repos.github_owner(remote) == owner


def test_resolve_account_explicit_wins():
    entry = repos.RepoEntry(
        name="proj", account="host-acct",
        remote="https://github.com/example-org/proj.git",
    )
    assert repos.resolve_account(entry) == "host-acct"


def test_resolve_account_derives_owner():
    entry = repos.RepoEntry(
        name="proj", remote="https://github.com/example-org/proj.git",
    )
    assert repos.resolve_account(entry) == "example-org"


def test_resolve_account_none_for_non_github():
    entry = repos.RepoEntry(name="proj", remote="https://gitlab.com/o/r.git")
    assert repos.resolve_account(entry) is None


def test_resolve_account_none_for_missing_entry():
    assert repos.resolve_account(None) is None


# --- account_map (decoupled org -> gh login) --------------------------------


def test_account_map_round_trips(home: Path):
    repos.set_account_map("github", "ThomasMichon")
    repos.set_account_map("example-org", "example-operator")
    reg = repos.read_registry()
    assert reg.account_map == {
        "github": "ThomasMichon",
        "example-org": "example-operator",
    }


def test_account_map_resolves_org_owned_repo(home: Path):
    """An org-owned repo resolves to the mapped login, not the org name."""
    repos.set_account_map("github", "ThomasMichon")
    assert repos.account_for_github_slug("github/copilot-agent-runtime") == "ThomasMichon"
    # A repo entry is not required for the map to apply.
    assert repos.account_for_github_owner("github") == "ThomasMichon"


def test_account_map_case_insensitive(home: Path):
    repos.set_account_map("Example-Org", "example-operator")
    assert repos.account_for_github_owner("example-org") == "example-operator"
    assert repos.account_from_map("example-org") == "example-operator"


def test_owner_fallback_when_unmapped(home: Path):
    """Unmapped owner falls back to itself (owner == login for personal repos)."""
    assert repos.account_for_github_slug("example-operator/dotfiles") == "example-operator"


def test_explicit_repo_account_beats_map(home: Path):
    """A per-repo account: override wins over the org map (finest grain)."""
    repos.set_account_map("example-org", "map-login")
    repos.add_repo(
        "proj", str(home / "proj"), repo_class="worktree",
        remote="https://github.com/example-org/proj.git",
        account="explicit-login", plat="windows",
    )
    assert repos.account_for_github_owner("example-org") == "explicit-login"


def test_resolve_account_uses_map_over_owner(home: Path):
    repos.set_account_map("example-org", "mapped-acct")
    entry = repos.RepoEntry(
        name="proj", remote="https://github.com/example-org/proj.git",
    )
    assert repos.resolve_account(entry) == "mapped-acct"


def test_set_account_map_replaces_case_variant(home: Path):
    repos.set_account_map("github", "old")
    repos.set_account_map("GitHub", "new")
    reg = repos.read_registry()
    # Only one entry survives (case-variant replaced, not duplicated).
    assert list(reg.account_map.values()) == ["new"]


def test_unset_account_map(home: Path):
    repos.set_account_map("github", "ThomasMichon")
    assert repos.unset_account_map("GITHUB") is True
    assert repos.read_registry().account_map == {}
    assert repos.unset_account_map("github") is False


# --- resolve_registration_account (#537) ------------------------------------

_AUTHED = ("ThomasMichon", "example-operator")


def _fake_token(login):
    return "tok" if login in _AUTHED else None


def test_registration_org_owner_needs_clarify(home: Path):
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"):
        r = repos.resolve_registration_account(
            "https://github.com/github/copilot-agent-runtime.git")
    assert r.source == "owner-fallback"
    assert r.owner == "github"
    assert r.needs_clarify is True


def test_registration_personal_owner_is_clean(home: Path):
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"):
        r = repos.resolve_registration_account(
            "https://github.com/example-operator/dotfiles.git")
    assert r.login == "example-operator"
    assert r.needs_clarify is False


def test_registration_account_map_resolves(home: Path):
    repos.set_account_map("github", "ThomasMichon")
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"):
        r = repos.resolve_registration_account(
            "https://github.com/github/copilot-agent-runtime.git")
    assert r.source == "account_map"
    assert r.login == "ThomasMichon"
    assert r.needs_clarify is False


def test_registration_explicit_account_is_clean(home: Path):
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"):
        r = repos.resolve_registration_account(
            "https://github.com/github/x.git", "someacct")
    assert r.source == "explicit"
    assert r.needs_clarify is False


def test_registration_sibling_explicit_resolves(home: Path):
    repos.add_repo(
        "sib", str(home / "sib"), repo_class="worktree",
        remote="https://github.com/github/other.git",
        account="ThomasMichon", plat="windows",
    )
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"):
        r = repos.resolve_registration_account(
            "https://github.com/github/copilot-agent-runtime.git")
    assert r.source == "sibling"
    assert r.login == "ThomasMichon"
    assert r.needs_clarify is False


def test_registration_non_github_is_none(home: Path):
    r = repos.resolve_registration_account(
        "https://my-org.visualstudio.com/x/_git/y")
    assert r.source == "none"
    assert r.needs_clarify is False


def test_registration_no_gh_never_nags(home: Path):
    # gh unavailable -> can't verify -> assume authenticated -> no clarify.
    with patch("agent_worktrees.repos.shutil.which", return_value=None):
        r = repos.resolve_registration_account(
            "https://github.com/github/copilot-agent-runtime.git")
    assert r.needs_clarify is False


def test_account_for_github_slug_derives(home: Path):
    # No override registered -> the account is the slug owner.
    assert repos.account_for_github_slug("example-org/proj") == "example-org"


def test_account_for_github_slug_honors_override(home: Path):
    # A registered repo with an explicit account whose remote owner matches the
    # slug owner overrides the derived owner (EMU accounts can span orgs).
    repos.add_repo(
        "proj", "D:/Src/proj", repo_class="worktree",
        remote="https://github.com/example-org/proj.git",
        account="host-acct", plat="windows",
    )
    assert repos.account_for_github_slug("example-org/other") == "host-acct"
    assert repos.account_for_github_slug("unrelated/repo") == "unrelated"
    assert repos.account_for_github_slug("") is None


def test_add_repo_persists_account(home: Path):
    repos.add_repo(
        "proj", "D:/Src/proj", repo_class="worktree",
        remote="https://github.com/example-org/proj.git",
        account="host-acct", plat="windows",
    )
    reg = repos.read_registry()
    assert reg.repos["proj"].account == "host-acct"
    assert repos.resolve_account(reg.repos["proj"]) == "host-acct"


# ---------------------------------------------------------------------------
# Git hygiene: status + sync
# ---------------------------------------------------------------------------

def test_repo_status_present_and_missing(home: Path, tmp_path: Path):
    work = tmp_path / "work" / "repo-a"
    _init_repo(work, branch="main")
    repos.add_repo("repo-a", str(work), repo_class="singleton",
                   default_branch="main", plat="windows")
    repos.add_repo("repo-gone", str(tmp_path / "nope"),
                   repo_class="reference", plat="windows")

    statuses = {s.name: s for s in repos.status_all(plat="windows")}
    a = statuses["repo-a"]
    assert a.present and a.branch == "main" and not a.dirty
    assert statuses["repo-gone"].present is False


def test_sync_repo_skips_dirty(home: Path, tmp_path: Path):
    work = tmp_path / "repo-b"
    _init_repo(work, branch="main")
    (work / "dirty.txt").write_text("x\n")  # untracked -> dirty
    e = repos.RepoEntry(name="repo-b", repo_class="singleton",
                        default_branch="main",
                        paths={"windows": str(work)})
    state, _ = repos.sync_repo(e, plat="windows")
    assert state == "skipped"


def test_sync_repo_missing(home: Path, tmp_path: Path):
    e = repos.RepoEntry(name="x", repo_class="reference",
                        paths={"windows": str(tmp_path / "absent")})
    state, _ = repos.sync_repo(e, plat="windows")
    assert state == "missing"


def test_sync_repo_skips_detached_head(home: Path, tmp_path: Path):
    """A detached HEAD (pinned reference checkout) must never be ff-merged."""
    work = tmp_path / "repo-detached"
    _init_repo(work, branch="main")
    _git(work, "commit", "--allow-empty", "-m", "second")
    head = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD~1"],
                          capture_output=True, text=True).stdout.strip()
    _git(work, "checkout", head)  # detach at the older commit
    before = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    e = repos.RepoEntry(name="repo-detached", repo_class="reference",
                        default_branch="main",
                        paths={"windows": str(work)})
    state, detail = repos.sync_repo(e, plat="windows")
    after = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    assert state == "skipped"
    assert "detached" in detail
    assert before == after  # HEAD was not moved


def test_sync_repo_fetches_and_fast_forwards_via_git_ops(home: Path, tmp_path: Path):
    """``sync_repo`` must route its fetch through ``git_ops.fetch`` (not a bare,
    unauthenticated ``git fetch``) so a cross-account remote gets the same
    credential resolution every other agent-worktrees git flow uses
    (dotfiles#2069). A local remote can't exercise the credential-injection
    branch itself (non-GitHub), but it proves the new call path still performs
    a real fetch + fast-forward end to end.
    """
    upstream = tmp_path / "upstream"
    _init_repo(upstream, branch="main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(upstream), str(clone)],
                    check=True, capture_output=True, text=True)

    # Advance the upstream past the clone so the fetch has real work to do.
    (upstream / "NEW.md").write_text("more\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-m", "second")

    e = repos.RepoEntry(name="repo-c", repo_class="singleton",
                        default_branch="main",
                        paths={"windows": str(clone)})
    state, detail = repos.sync_repo(e, plat="windows")
    assert state == "synced"
    assert detail == "main"
    head = subprocess.run(["git", "-C", str(clone), "log", "-1", "--format=%s"],
                          capture_output=True, text=True).stdout.strip()
    assert head == "second"


def test_sync_repo_surfaces_git_ops_fetch_error(home: Path, tmp_path: Path):
    """A fetch failure raised as ``git_ops.GitError`` must still be reported as
    an ``"error"`` state with readable detail, not propagate uncaught."""
    work = tmp_path / "repo-d"
    _init_repo(work, branch="main")
    _git(work, "remote", "add", "origin", str(tmp_path / "does-not-exist"))
    e = repos.RepoEntry(name="repo-d", repo_class="singleton",
                        default_branch="main",
                        paths={"windows": str(work)})
    state, detail = repos.sync_repo(e, plat="windows")
    assert state == "error"
    assert detail
