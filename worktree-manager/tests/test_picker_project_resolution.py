"""Project ambiguity remains a refusal, resolved after pending chrome paints."""

from __future__ import annotations

import pytest

from worktree_manager import __main__ as cli
from worktree_manager.harness_state import ProjectInfo, RepoInfo
from worktree_manager.production_picker import runner


def _project(name: str, klass: str = "worktree") -> ProjectInfo:
    return ProjectInfo(
        name=name, config_dir=None, expose_agent=False,
        knowledge_repo=None, profiles=0,
        repo=RepoInfo(name=name, klass=klass, agent=True, remote=None, path=None, account=None),
    )


@pytest.fixture(autouse=True)
def available_engine(monkeypatch):
    monkeypatch.setattr(runner.engine_client, "engine_available", lambda: True)


@pytest.mark.parametrize("projects", [[], [_project("notes", "knowledge")]])
def test_no_launchable_project_refuses(monkeypatch, projects):
    monkeypatch.setattr(runner.harness_state, "build_projects", lambda: projects)
    with pytest.raises(RuntimeError, match="no project to open"):
        runner._select_project("")


@pytest.mark.parametrize("with_knowledge", [False, True])
def test_only_launchable_project_is_unambiguous(monkeypatch, with_knowledge):
    projects = [_project("example")]
    if with_knowledge:
        projects.insert(0, _project("notes", "knowledge"))
    monkeypatch.setattr(runner.harness_state, "build_projects", lambda: projects)
    assert runner._select_project("") == "example"


def test_multiple_launchable_projects_refuse_without_offering_knowledge(monkeypatch):
    monkeypatch.setattr(
        runner.harness_state, "build_projects",
        lambda: [_project("notes", "knowledge"), _project("example"), _project("second")],
    )
    with pytest.raises(RuntimeError, match="multiple projects") as error:
        runner._select_project("")
    assert "example, second" in str(error.value)
    assert "notes" not in str(error.value)


def test_explicit_project_is_validated_by_engine_without_full_roster(monkeypatch):
    def forbidden():
        raise AssertionError("explicit selection does not require roster enumeration")
    monkeypatch.setattr(runner.harness_state, "build_projects", forbidden)
    assert runner._select_project("example") == "example"


@pytest.mark.parametrize("args,expected", [([], ""), (["example"], "example")])
def test_cli_enters_production_without_discovery(monkeypatch, args, expected):
    def forbidden():
        raise AssertionError("discovery must wait until pending frame")
    monkeypatch.setattr(cli, "build_projects", forbidden)
    monkeypatch.setattr(cli, "engine_available", forbidden)
    seen = []
    monkeypatch.setattr(cli, "_run_production_picker", lambda project: seen.append(project) or 0)
    assert cli._cmd_picker(args) == 0
    assert seen == [expected]


def test_deferred_selection_failure_retains_usage_exit(monkeypatch, capsys):
    def fail(project):
        raise runner.ProjectSelectionError("no project to open")
    monkeypatch.setattr(runner, "run", fail)
    assert cli._run_production_picker("") == 2
    assert "no project to open" in capsys.readouterr().out


@pytest.mark.parametrize("key,value", [
    ("AGENT_WORKTREES_PICKER_MOCK", "1"), ("AGENT_WORKTREES_PICKER_REAL_OPS", "0"),
])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_environment_mock_still_resolves_implicit_project(monkeypatch, key, value, ambiguous):
    monkeypatch.setenv(key, value)
    projects = [_project("example")]
    if ambiguous:
        projects.append(_project("second"))
    monkeypatch.setattr(runner.harness_state, "build_projects", lambda: projects)
    selected = []

    def bootstrap(project):
        selected.append(project)
        return {"project": project, "default_live": True, "should_switch_cwd": False}

    def run_picker(*, prepare, **kwargs):
        prepare()

    monkeypatch.setattr(runner.engine_group_b, "picker_bootstrap", bootstrap)
    monkeypatch.setattr(runner, "run_tui_picker", run_picker)
    if ambiguous:
        with pytest.raises(runner.ProjectSelectionError, match="multiple projects"):
            runner.run("")
        assert selected == []
    else:
        assert runner.run("") is None
        assert selected == ["example"]
