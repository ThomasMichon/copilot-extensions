"""Tests for the repos registry: schema, migration, and git hygiene."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_worktrees import installer, repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the registry reads/writes under a tmp dir."""
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
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


def test_migration_uses_adoptions_from_agent_home(home: Path, monkeypatch):
    legacy_source = home / ".git-repos"
    legacy_source.write_text(
        "srcroot: /example\nrepos:\n  selected: {}\n  host-only: {}\n",
        encoding="utf-8",
    )
    host_projects = home / ".agent-worktrees" / "projects.yaml"
    host_projects.parent.mkdir(parents=True)
    host_projects.write_text("projects:\n  host-only: {}\n", encoding="utf-8")
    before = host_projects.read_bytes()
    isolated_home = home / "sandbox"
    monkeypatch.setenv("AGENT_HOME", str(isolated_home))
    installer.write_projects_registry({"projects": {"selected": {}}})

    assert repos.migrate_git_repos(plat="linux") == (2, 0)

    registry = repos.read_registry()
    assert registry.repos["selected"].repo_class == "worktree"
    assert registry.repos["host-only"].repo_class == "singleton"
    assert (isolated_home / ".agent-worktrees" / "repos.yaml").is_file()
    assert not (home / ".agent-worktrees" / "repos.yaml").exists()
    assert host_projects.read_bytes() == before


# ---------------------------------------------------------------------------
# Class normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("reference", "reference"),
    ("singleton", "singleton"),
    ("worktree", "worktree"),
    ("WORKTREE", "worktree"),
    ("knowledge", "knowledge"),
    ("KNOWLEDGE", "knowledge"),
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
    ("https://notgithub.com/example-org/proj.git", None),  # lookalike host
    ("https://evilgithub.com/example-org/proj.git", None),  # lookalike host
    ("git@evilgithub.com:example-org/proj.git", None),   # lookalike host, ssh
    ("", None),
])
def test_github_owner(remote, owner):
    assert repos.github_owner(remote) == owner


@pytest.mark.parametrize("remote,slug", [
    ("https://github.com/example-org/proj.git", "example-org/proj"),
    ("https://github.com/example-org/proj", "example-org/proj"),
    ("git@github.com:example-org/proj.git", "example-org/proj"),
    ("ssh://git@github.com/example-org/proj.git", "example-org/proj"),
    ("https://gitlab.com/example-org/proj.git", None),
    ("https://notgithub.com/example-org/proj.git", None),  # lookalike host
    ("git@evilgithub.com:example-org/proj.git", None),   # lookalike host, ssh
    ("", None),
])
def test_github_slug(remote, slug):
    assert repos.github_slug(remote) == slug


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


# --- bare registered repo name resolution (#3032) ---------------------------


def test_resolve_slug_owner_bare_owner_unregistered():
    # No registered repo by that name -> treated as a literal owner, exactly
    # as before (personal/org owners keep resolving this way).
    assert repos.resolve_slug_owner("ThomasMichon") == "ThomasMichon"


def test_resolve_slug_owner_bare_registered_repo_name_derives_owner(home: Path):
    # A bare registered repo *name* is not itself an owner: resolve through
    # the registry -> remote -> owner chain, the same one `repos find` uses.
    repos.add_repo(
        "copilot-extensions", "D:/Src/copilot-extensions", repo_class="reference",
        remote="https://github.com/ThomasMichon/copilot-extensions.git",
        plat="windows",
    )
    assert repos.resolve_slug_owner("copilot-extensions") == "ThomasMichon"


def test_account_for_github_slug_bare_registered_repo_name_resolves_owner(home: Path):
    # The reported bug: `account_for_github_slug("copilot-extensions")` used to
    # return the literal string "copilot-extensions" (an invalid login) instead
    # of resolving the repo's actual owner.
    repos.add_repo(
        "copilot-extensions", "D:/Src/copilot-extensions", repo_class="reference",
        remote="https://github.com/ThomasMichon/copilot-extensions.git",
        plat="windows",
    )
    assert repos.account_for_github_slug("copilot-extensions") == "ThomasMichon"


def test_resolve_slug_owner_registered_non_github_remote_is_none(home: Path):
    # A registered repo whose remote isn't github.com has no github owner to
    # resolve -- returning the bare name itself here would be just as wrong as
    # the original bug, so this must be None (account_for_github_slug then
    # also returns None -> "no account preference", never a bogus login).
    repos.add_repo(
        "azdo-proj", "D:/Src/azdo-proj", repo_class="reference",
        remote="https://my-org.visualstudio.com/x/_git/azdo-proj",
        plat="windows",
    )
    assert repos.resolve_slug_owner("azdo-proj") is None
    assert repos.account_for_github_slug("azdo-proj") is None


def test_resolve_slug_owner_empty_and_slash_forms():
    assert repos.resolve_slug_owner("") is None
    assert repos.resolve_slug_owner(None) is None
    # An owner/name slug is unaffected -- the owner is taken verbatim, never
    # looked up as a registered repo name.
    assert repos.resolve_slug_owner("example-org/proj") == "example-org"


def test_is_unresolved_registered_target_true_for_non_github_registered_repo(
    home: Path,
):
    repos.add_repo(
        "azdo-proj", "D:/Src/azdo-proj", repo_class="reference",
        remote="https://my-org.visualstudio.com/x/_git/azdo-proj",
        plat="windows",
    )
    assert repos.is_unresolved_registered_target("azdo-proj") is True


def test_is_unresolved_registered_target_false_with_explicit_account_override(
    home: Path,
):
    # An explicit per-repo `account:` is a real, resolvable identity even
    # without a derivable github owner -- it must not be treated as unresolved.
    repos.add_repo(
        "azdo-proj", "D:/Src/azdo-proj", repo_class="reference",
        remote="https://my-org.visualstudio.com/x/_git/azdo-proj",
        account="explicit-login", plat="windows",
    )
    assert repos.is_unresolved_registered_target("azdo-proj") is False
    assert repos.account_for_github_slug("azdo-proj") == "explicit-login"


def test_account_for_github_slug_preserves_matched_entrys_own_override(
    home: Path,
):
    # Two registered repos share a github owner but have different explicit
    # `account:` overrides -- resolving the second by its bare registered name
    # must return *its own* account, not whichever entry the owner-wide
    # resolver happens to iterate first (the #3032 follow-up finding).
    repos.add_repo(
        "proj-a", "D:/Src/proj-a", repo_class="worktree",
        remote="https://github.com/shared-owner/proj-a.git",
        account="account-a", plat="windows",
    )
    repos.add_repo(
        "proj-b", "D:/Src/proj-b", repo_class="worktree",
        remote="https://github.com/shared-owner/proj-b.git",
        account="account-b", plat="windows",
    )
    assert repos.account_for_github_slug("proj-a") == "account-a"
    assert repos.account_for_github_slug("proj-b") == "account-b"


def test_is_unresolved_registered_target_false_for_github_registered_repo(
    home: Path,
):
    repos.add_repo(
        "copilot-extensions", "D:/Src/copilot-extensions", repo_class="reference",
        remote="https://github.com/ThomasMichon/copilot-extensions.git",
        plat="windows",
    )
    assert repos.is_unresolved_registered_target("copilot-extensions") is False


def test_is_unresolved_registered_target_false_for_unregistered_name():
    # No registered repo by this name -> not a "known but unresolvable"
    # target; it is just an ordinary (possibly personal) bare owner, and
    # ambient auth remains the safe, documented fallback.
    assert repos.is_unresolved_registered_target("ThomasMichon") is False


def test_is_unresolved_registered_target_false_for_slug_and_empty():
    assert repos.is_unresolved_registered_target("owner/name") is False
    assert repos.is_unresolved_registered_target("") is False
    assert repos.is_unresolved_registered_target(None) is False


def test_add_repo_persists_account(home: Path):
    repos.add_repo(
        "proj", "D:/Src/proj", repo_class="worktree",
        remote="https://github.com/example-org/proj.git",
        account="host-acct", plat="windows",
    )
    reg = repos.read_registry()
    assert reg.repos["proj"].account == "host-acct"
    assert repos.resolve_account(reg.repos["proj"]) == "host-acct"


# --- backfill_credential_pins ------------------------------------------------


def test_backfill_pins_a_resolvable_repo(home: Path, tmp_path: Path):
    work = tmp_path / "pin-me"
    _init_repo(work, branch="main")
    repos.add_repo(
        "pin-me", str(work), repo_class="worktree",
        remote="https://github.com/example-operator/pin-me.git", plat="windows",
    )
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"), \
         patch("agent_worktrees.git_ops.shutil.which", return_value="gh"):
        results = repos.backfill_credential_pins(plat="windows")
    assert len(results) == 1
    assert results[0].name == "pin-me"
    assert results[0].status == "pinned"
    assert results[0].login == "example-operator"

    username = subprocess.run(
        ["git", "-C", str(work), "config", "--local",
         "credential.https://github.com.username"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert username == "example-operator"


def test_backfill_restricts_to_named_repo(home: Path, tmp_path: Path):
    work_a = tmp_path / "a"
    work_b = tmp_path / "b"
    _init_repo(work_a, branch="main")
    _init_repo(work_b, branch="main")
    repos.add_repo("a", str(work_a), repo_class="worktree",
                    remote="https://github.com/example-operator/a.git", plat="windows")
    repos.add_repo("b", str(work_b), repo_class="worktree",
                    remote="https://github.com/example-operator/b.git", plat="windows")
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"), \
         patch("agent_worktrees.git_ops.shutil.which", return_value="gh"):
        results = repos.backfill_credential_pins("a", plat="windows")
    assert [r.name for r in results] == ["a"]


def test_backfill_skips_org_owner_needing_clarify(home: Path, tmp_path: Path):
    work = tmp_path / "org-owned"
    _init_repo(work, branch="main")
    repos.add_repo(
        "org-owned", str(work), repo_class="worktree",
        remote="https://github.com/github/org-owned.git", plat="windows",
    )
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"):
        results = repos.backfill_credential_pins(plat="windows")
    assert results[0].status == "needs_clarify"
    assert "repos account set" in results[0].detail


def test_backfill_skips_non_github_remote(home: Path, tmp_path: Path):
    work = tmp_path / "ado-repo"
    _init_repo(work, branch="main")
    repos.add_repo(
        "ado-repo", str(work), repo_class="worktree",
        remote="https://my-org.visualstudio.com/x/_git/ado-repo", plat="windows",
    )
    results = repos.backfill_credential_pins(plat="windows")
    assert results[0].status == "not_github"


def test_backfill_skips_missing_local_path(home: Path, tmp_path: Path):
    repos.add_repo(
        "gone", str(tmp_path / "does-not-exist"), repo_class="worktree",
        remote="https://github.com/example-operator/gone.git", plat="windows",
    )
    results = repos.backfill_credential_pins(plat="windows")
    assert results[0].status == "no_path"


def test_backfill_skips_ssh_remote(home: Path, tmp_path: Path):
    """The pin only ever writes credential.https://<host>.*, which an SSH
    transport never consults -- treating it as pinned would be a false
    positive that reports success while changing nothing."""
    work = tmp_path / "ssh-repo"
    _init_repo(work, branch="main")
    repos.add_repo(
        "ssh-repo", str(work), repo_class="worktree",
        remote="git@github.com:example-operator/ssh-repo.git", plat="windows",
    )
    results = repos.backfill_credential_pins(plat="windows")
    assert results[0].status == "ssh_remote"


def test_is_https_remote():
    assert repos.is_https_remote("https://github.com/o/r.git") is True
    assert repos.is_https_remote("http://github.com/o/r.git") is False
    assert repos.is_https_remote("git@github.com:o/r.git") is False
    assert repos.is_https_remote("ssh://git@github.com/o/r.git") is False
    assert repos.is_https_remote("") is False


def test_derive_https_host():
    assert repos.derive_https_host("https://github.com/o/r.git") == "github.com"
    assert repos.derive_https_host("https://www.github.com/o/r.git") == "www.github.com"
    assert repos.derive_https_host("https://ghe.example.com/o/r.git") == "ghe.example.com"
    assert repos.derive_https_host("git@github.com:o/r.git") is None
    assert repos.derive_https_host("") is None


def test_backfill_distinguishes_http_from_ssh_remote(home: Path, tmp_path: Path):
    """is_https_remote() rejects both SSH and plain http:// remotes, but the
    backfill report must never claim 'SSH remote' for an unencrypted HTTP
    checkout -- distinguish the two skip reasons."""
    work = tmp_path / "http-repo"
    _init_repo(work, branch="main")
    repos.add_repo(
        "http-repo", str(work), repo_class="worktree",
        remote="http://github.com/example-operator/http-repo.git", plat="windows",
    )
    results = repos.backfill_credential_pins(plat="windows")
    assert results[0].status == "not_https"


def test_backfill_pins_with_derived_host_not_hardcoded_github_com(home: Path, tmp_path: Path):
    """A www.github.com checkout must be pinned under credential.https://
    www.github.com, not the hardcoded 'github.com' default -- git's
    credential store matches by literal hostname."""
    work = tmp_path / "www-repo"
    _init_repo(work, branch="main")
    repos.add_repo(
        "www-repo", str(work), repo_class="worktree",
        remote="https://www.github.com/example-operator/www-repo.git", plat="windows",
    )
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"), \
         patch("agent_worktrees.git_ops.shutil.which", return_value="gh"), \
         patch("agent_worktrees.git_ops._active_gh_account", return_value=None):
        results = repos.backfill_credential_pins(plat="windows")
    assert results[0].status == "pinned"
    username = subprocess.run(
        ["git", "-C", str(work), "config", "--local",
         "credential.https://www.github.com.username"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert username == "example-operator"


def test_backfill_unknown_name_never_falls_back_to_all(home: Path, tmp_path: Path):
    """A typo in the requested name must report 'not found', never silently
    expand to every registered repo."""
    work = tmp_path / "real-one"
    _init_repo(work, branch="main")
    repos.add_repo(
        "real-one", str(work), repo_class="worktree",
        remote="https://github.com/example-operator/real-one.git", plat="windows",
    )
    results = repos.backfill_credential_pins("typo-name", plat="windows")
    assert len(results) == 1
    assert results[0].name == "typo-name"
    assert results[0].status == "not_registered"


# --- add_repo auto-pins (every registration path, not just repos add/clone) --


def test_add_repo_pins_credential_for_a_resolvable_owner(home: Path, tmp_path: Path):
    """Every caller of add_repo -- repos add/clone, plugin install/adoption,
    project-entry registration -- gets the pin, not only the CLI paths that
    separately call _clarify_registration_account."""
    work = tmp_path / "auto-pin"
    _init_repo(work, branch="main")
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"), \
         patch("agent_worktrees.git_ops.shutil.which", return_value="gh"):
        repos.add_repo(
            "auto-pin", str(work), repo_class="worktree",
            remote="https://github.com/example-operator/auto-pin.git", plat="windows",
        )
    username = subprocess.run(
        ["git", "-C", str(work), "config", "--local",
         "credential.https://github.com.username"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert username == "example-operator"


def test_add_repo_does_not_pin_when_account_needs_clarify(home: Path, tmp_path: Path):
    """An org-owned remote with no account_map entry must not guess -- no
    pin is written until an operator resolves it (repos account set /
    the interactive _clarify_registration_account flow)."""
    work = tmp_path / "org-owned-auto"
    _init_repo(work, branch="main")
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"):
        repos.add_repo(
            "org-owned-auto", str(work), repo_class="worktree",
            remote="https://github.com/github/org-owned-auto.git", plat="windows",
        )
    result = subprocess.run(
        ["git", "-C", str(work), "config", "--local",
         "credential.https://github.com.username"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0  # key was never set


def test_add_repo_does_not_pin_ssh_remote(home: Path, tmp_path: Path):
    """An SSH remote's registration must not attempt (and falsely report) a
    pin that credential.https://... can never affect."""
    work = tmp_path / "ssh-auto"
    _init_repo(work, branch="main")
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"), \
         patch("agent_worktrees.git_ops.shutil.which", return_value="gh"):
        repos.add_repo(
            "ssh-auto", str(work), repo_class="worktree",
            remote="git@github.com:example-operator/ssh-auto.git", plat="windows",
        )
    result = subprocess.run(
        ["git", "-C", str(work), "config", "--local",
         "credential.https://github.com.username"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0  # key was never set


def test_add_repo_pin_expands_home_relative_path(home: Path, tmp_path: Path, monkeypatch):
    """Path('~/src/repo').is_dir() is always False -- add_repo must resolve
    through entry.local_path() (which expands '~' via os.path.expanduser)
    rather than the raw caller-supplied path string, or a home-relative
    registration would silently never get pinned."""
    work = tmp_path / "home-rel"
    _init_repo(work, branch="main")
    monkeypatch.setattr(
        repos.os.path, "expanduser",
        lambda p: str(work) if p == "~/home-rel" else os.path.expanduser(p),
    )
    entry = repos.add_repo(
        "home-rel", "~/home-rel", repo_class="worktree",
        remote="https://github.com/example-operator/home-rel.git", plat="windows",
    )
    assert entry.local_path("windows") == str(work)
    with patch("agent_worktrees.git_ops.gh_token_for_account", side_effect=_fake_token), \
         patch("agent_worktrees.repos.shutil.which", return_value="gh"), \
         patch("agent_worktrees.git_ops.shutil.which", return_value="gh"):
        repos._best_effort_pin_credential(entry, "windows")
    username = subprocess.run(
        ["git", "-C", str(work), "config", "--local",
         "credential.https://github.com.username"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert username == "example-operator"


def test_add_repo_pin_failure_never_raises(home: Path, tmp_path: Path):
    """A broken resolver/pin call must never break registration itself."""
    with patch("agent_worktrees.repos.resolve_registration_account",
               side_effect=RuntimeError("boom")):
        entry = repos.add_repo(
            "still-registers", "D:/Src/still-registers", repo_class="worktree",
            remote="https://github.com/example-operator/still-registers.git", plat="windows",
        )
    assert entry.name == "still-registers"
    assert repos.read_registry().repos["still-registers"].name == "still-registers"


# --- clone_repo injects cross-account auth before the pin exists ------------


def test_clone_repo_injects_auth_args_into_initial_clone(home: Path, tmp_path: Path):
    """The repo-local credential pin only exists *after* add_repo runs, which
    is after the clone -- a private repo owned by a different account than
    gh's active one needs a one-shot override on the clone itself."""
    home_srcroot = tmp_path / "src"
    repos.set_srcroot(str(home_srcroot), plat="windows")
    captured: dict[str, list] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        (Path(argv[-1])).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("agent_worktrees.repos._current_platform", return_value="windows"), \
         patch("agent_worktrees.repos.subprocess.run", side_effect=fake_run), \
         patch(
             "agent_worktrees.git_ops._auth_config_args_for_url",
             return_value=["-c", "http.extraheader=AUTHORIZATION: basic FAKE"],
         ):
        entry = repos.clone_repo("https://github.com/example-operator/cloned.git")

    assert entry is not None
    argv = captured["argv"]
    assert argv[0] == "git"
    assert "-c" in argv and "http.extraheader=AUTHORIZATION: basic FAKE" in argv
    assert argv[argv.index("clone")] == "clone"


def test_clone_repo_no_extra_args_for_same_account(home: Path, tmp_path: Path):
    home_srcroot = tmp_path / "src"
    repos.set_srcroot(str(home_srcroot), plat="windows")
    captured: dict[str, list] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        (Path(argv[-1])).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("agent_worktrees.repos._current_platform", return_value="windows"), \
         patch("agent_worktrees.repos.subprocess.run", side_effect=fake_run), \
         patch("agent_worktrees.git_ops._auth_config_args_for_url", return_value=[]):
        repos.clone_repo("https://github.com/example-operator/cloned2.git")

    assert captured["argv"] == [
        "git", "clone", "https://github.com/example-operator/cloned2.git",
        str(home_srcroot / "cloned2"),
    ]


def test_clone_repo_redacts_extraheader_from_stderr_on_failure(home: Path, tmp_path: Path):
    """A failed clone's stderr must never surface the injected token, even
    if git itself echoes the -c argument back (e.g. with tracing enabled)."""
    home_srcroot = tmp_path / "src"
    repos.set_srcroot(str(home_srcroot), plat="windows")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 128,
            "", "fatal: could not clone (args were: "
                "http.extraheader=AUTHORIZATION: basic FAKESECRET)",
        )

    with patch("agent_worktrees.repos._current_platform", return_value="windows"), \
         patch("agent_worktrees.repos.subprocess.run", side_effect=fake_run), \
         patch(
             "agent_worktrees.git_ops._auth_config_args_for_url",
             return_value=["-c", "http.extraheader=AUTHORIZATION: basic FAKESECRET"],
         ), \
         patch("agent_worktrees.output.err") as mock_err:
        entry = repos.clone_repo("https://github.com/example-operator/redact-test.git")

    assert entry is None
    logged = " ".join(str(c) for c in mock_err.call_args_list)
    assert "FAKESECRET" not in logged
    assert "<redacted>" in logged


def test_clone_repo_redacts_authorization_header_trace_from_stderr(home: Path, tmp_path: Path):
    """With GIT_TRACE_CURL/GIT_CURL_VERBOSE, git can echo the literal
    resolved 'Authorization: Basic <token>' request header into stderr,
    independent of the -c argv form -- that must be redacted too."""
    home_srcroot = tmp_path / "src"
    repos.set_srcroot(str(home_srcroot), plat="windows")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 128, "",
            "=> Send header: Authorization: Basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0\r\n"
            "fatal: could not clone",
        )

    with patch("agent_worktrees.repos._current_platform", return_value="windows"), \
         patch("agent_worktrees.repos.subprocess.run", side_effect=fake_run), \
         patch("agent_worktrees.git_ops._auth_config_args_for_url", return_value=[]), \
         patch("agent_worktrees.output.err") as mock_err:
        entry = repos.clone_repo("https://github.com/example-operator/trace-redact.git")

    assert entry is None
    logged = " ".join(str(c) for c in mock_err.call_args_list)
    assert "eC1hY2Nlc3MtdG9rZW46c2VjcmV0" not in logged
    assert "<redacted>" in logged


def test_clone_repo_redacts_url_userinfo_from_exception_message(home: Path, tmp_path: Path):
    """A remote URL with embedded userinfo (https://user:token@host/...) is
    a second, independent credential surface from the injected extraheader
    -- the logged argv/exception text must never leak it either."""
    home_srcroot = tmp_path / "src"
    repos.set_srcroot(str(home_srcroot), plat="windows")
    remote = "https://x-access-token:realtoken@github.com/example-operator/leak-test.git"

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=300)

    with patch("agent_worktrees.repos._current_platform", return_value="windows"), \
         patch("agent_worktrees.repos.subprocess.run", side_effect=fake_run), \
         patch("agent_worktrees.git_ops._auth_config_args_for_url", return_value=[]), \
         patch("agent_worktrees.output.err") as mock_err:
        entry = repos.clone_repo(remote)

    assert entry is None
    logged = " ".join(str(c) for c in mock_err.call_args_list)
    assert "realtoken" not in logged
    assert "<redacted>@" in logged


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
