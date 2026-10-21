"""Explicit repository classification must remain independent of other axes."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from agent_worktrees import installer, repos, repos_cli


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(repos, "_current_platform", lambda: "linux")
    return tmp_path


def _seed(home: Path, repo_class: str, agent: bool) -> dict:
    installer.write_projects_registry({"projects": {"sample": {"expose_agent": agent}}})
    repos.add_repo(
        "sample", str(home / "sample"), repo_class=repo_class,
        remote="ssh://git@example.invalid/team/sample.git",
        default_branch="dev", tags=["library"], contributing="CONTRIBUTING.md",
        account="example", agent=agent, visibility="internal", plat="linux",
    )
    entry = repos.read_registry().repos["sample"]
    entry.copilot_account = "example-copilot"
    registry = repos.read_registry()
    registry.repos["sample"] = entry
    repos.write_registry(registry)
    return asdict(entry)


@pytest.mark.parametrize("repo_class", ["singleton", "worktree", "knowledge"])
@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("requested", [None, "reference"])
def test_add_repo_class_intent_preserves_other_axes(
    home: Path, repo_class: str, agent: bool, requested: str | None,
) -> None:
    expected = _seed(home, repo_class, agent)
    projects_before = installer.read_projects_registry()
    path = str(home / "other-platform" / "sample")
    if requested is None:
        repos.add_repo("sample", path, plat="windows")
    else:
        repos.add_repo("sample", path, repo_class=requested, plat="windows")

    expected["paths"]["windows"] = path
    expected["repo_class"] = requested or repo_class
    assert asdict(repos.read_registry().repos["sample"]) == expected
    assert installer.read_projects_registry() == projects_before


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("flags", [[], ["--class", "reference"], ["--type", "repo"]])
def test_repos_add_cli_class_intent(
    home: Path, agent: bool, flags: list[str],
) -> None:
    expected = _seed(home, "worktree", agent)
    projects_before = installer.read_projects_registry()
    path = str(home / "relocated")
    assert repos_cli.cmd_repos_dispatch(["add", "sample", path, *flags]) == 0

    expected["paths"]["linux"] = path
    expected["repo_class"] = "reference" if flags else "worktree"
    assert asdict(repos.read_registry().repos["sample"]) == expected
    assert installer.read_projects_registry() == projects_before


@pytest.mark.parametrize("through_cli", [False, True])
def test_new_repo_without_class_remains_reference(home: Path, through_cli: bool) -> None:
    path = str(home / "new")
    if through_cli:
        assert repos_cli.cmd_repos_dispatch(["add", "new", path]) == 0
    else:
        repos.add_repo("new", path)
    entry = repos.read_registry().repos["new"]
    assert entry.repo_class == "reference"
    assert entry.agent is False


@pytest.mark.parametrize("agent", [False, True])
def test_repos_add_cli_explicit_agent_independent_of_class(home: Path, agent: bool) -> None:
    _seed(home, "worktree", not agent)
    flag = "--agent" if agent else "--no-agent"
    assert repos_cli.cmd_repos_dispatch([
        "add", "sample", str(home / "sample"), "--class", "reference", flag,
    ]) == 0
    entry = repos.read_registry().repos["sample"]
    assert entry.repo_class == "reference"
    assert entry.agent is agent


@pytest.mark.parametrize("flags", [
    ["--class"], ["--type"], ["--class", "--no-agent"],
    ["--class", "worktre"], ["--type", "bogus"],
])
def test_repos_add_cli_rejects_invalid_class_without_writing(
    home: Path, flags: list[str], capsys: pytest.CaptureFixture[str],
) -> None:
    expected = _seed(home, "worktree", True)
    assert repos_cli.cmd_repos_dispatch([
        "add", "sample", str(home / "relocated"), *flags,
    ]) == 1
    assert asdict(repos.read_registry().repos["sample"]) == expected
    assert "class" in capsys.readouterr().out.lower()
