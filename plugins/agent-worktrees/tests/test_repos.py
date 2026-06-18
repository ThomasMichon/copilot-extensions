"""Tests for the repos registry: schema, migration, and git hygiene."""

from __future__ import annotations

import subprocess
from pathlib import Path

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
        tags=["facility"],
        contributing="CONTRIBUTING.md",
        plat="windows",
    )
    reg = repos.read_registry()
    e = reg.repos["copilot-extensions"]
    assert e.repo_class == "worktree"
    assert e.default_branch == "main"
    assert e.tags == ["facility"]
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
        "    tags: [facility]\n"
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
    assert al.tags == ["facility"]
    assert al.local_path("windows") == str(Path("D:/Src/sample-project"))

    lib = reg.repos["some-lib"]
    assert lib.repo_class == "singleton"          # default
    assert lib.local_path("windows") == "D:/Other/some-lib"

    # ~/.git-repos is left in place.
    assert (home / ".git-repos").exists()


def test_migrate_no_legacy_file(home: Path):
    assert repos.migrate_git_repos() == (0, 0)


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
