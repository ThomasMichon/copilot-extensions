"""Regression tests for #2426 (Worktree Manager/Picker can show/act on the
wrong project's content): ``_cmd_picker``'s no-positional project resolution
must never silently pick an arbitrary registered project.
"""

from __future__ import annotations

from worktree_manager import __main__ as cli
from worktree_manager.harness_state import ProjectInfo, RepoInfo


def _project(name: str, klass: str = "worktree") -> ProjectInfo:
    repo = RepoInfo(
        name=name, klass=klass, agent=True, remote=None, path=None,
        account=None,
    )
    return ProjectInfo(
        name=name, config_dir=None, expose_agent=False,
        knowledge_repo=None, profiles=0, repo=repo,
    )


class TestPickerNoPositionalProjectResolution:
    def test_no_positional_zero_projects_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "engine_available", lambda: True)
        monkeypatch.setattr(cli, "build_projects", lambda: [])
        rc = cli._cmd_picker([])
        assert rc == 2
        assert "no project to open" in capsys.readouterr().out

    def test_no_positional_one_project_uses_it_unambiguously(self, monkeypatch):
        monkeypatch.setattr(cli, "engine_available", lambda: True)
        monkeypatch.setattr(cli, "build_projects", lambda: [_project("solo-repo")])
        seen = {}
        monkeypatch.setattr(
            cli, "_run_production_picker",
            lambda project: (seen.update(project=project), 0)[1])
        rc = cli._cmd_picker([])
        assert rc == 0
        assert seen["project"] == "solo-repo"

    def test_no_positional_multiple_projects_refuses_not_first(self, monkeypatch, capsys):
        """The actual #2426 bug: previously silently picked projects[0] (an
        arbitrary, registration-order-dependent project) with no visible
        error, potentially opening the wrong repo's worktree content."""
        monkeypatch.setattr(cli, "engine_available", lambda: True)
        monkeypatch.setattr(
            cli, "build_projects",
            lambda: [_project("dotfiles"), _project("odsp-web-harness")])
        monkeypatch.setattr(
            cli, "_run_production_picker",
            lambda project: (_ for _ in ()).throw(
                AssertionError("must not silently open any project")))
        rc = cli._cmd_picker([])
        assert rc == 2
        out = capsys.readouterr().out
        assert "multiple projects are registered" in out
        assert "dotfiles" in out and "odsp-web-harness" in out

    def test_explicit_positional_still_wins_with_multiple_projects(self, monkeypatch):
        monkeypatch.setattr(cli, "engine_available", lambda: True)
        monkeypatch.setattr(
            cli, "build_projects",
            lambda: [_project("dotfiles"), _project("odsp-web-harness")])
        seen = {}
        monkeypatch.setattr(
            cli, "_run_production_picker",
            lambda project: (seen.update(project=project), 0)[1])
        rc = cli._cmd_picker(["odsp-web-harness"])
        assert rc == 0
        assert seen["project"] == "odsp-web-harness"


class TestPickerExcludesKnowledgeOnlyProjects:
    """A ``knowledge`` class repo exists only to be carved as another
    project's paired ``-k`` companion -- it must never be silently picked as
    the implicit single-project default, nor offered in the ambiguous-
    selection prompt (though an explicit positional naming it still works, for
    browsing)."""

    def test_knowledge_only_repo_not_used_as_sole_implicit_default(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "engine_available", lambda: True)
        monkeypatch.setattr(
            cli, "build_projects", lambda: [_project("dotfiles", klass="knowledge")])
        rc = cli._cmd_picker([])
        assert rc == 2
        assert "no project to open" in capsys.readouterr().out

    def test_knowledge_only_repo_excluded_from_ambiguous_listing(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "engine_available", lambda: True)
        monkeypatch.setattr(
            cli, "build_projects",
            lambda: [
                _project("dotfiles", klass="knowledge"),
                _project("odsp-web-harness"),
                _project("copilot-extensions"),
            ])
        rc = cli._cmd_picker([])
        # Exactly one non-knowledge project remains -> unambiguous default.
        assert rc == 2
        out = capsys.readouterr().out
        assert "multiple projects are registered" in out
        assert "dotfiles" not in out
        assert "odsp-web-harness" in out and "copilot-extensions" in out

    def test_knowledge_only_repo_sole_survivor_is_still_implicit_default(self, monkeypatch):
        monkeypatch.setattr(cli, "engine_available", lambda: True)
        monkeypatch.setattr(
            cli, "build_projects",
            lambda: [_project("dotfiles", klass="knowledge"), _project("odsp-web-harness")])
        seen = {}
        monkeypatch.setattr(
            cli, "_run_production_picker",
            lambda project: (seen.update(project=project), 0)[1])
        rc = cli._cmd_picker([])
        assert rc == 0
        assert seen["project"] == "odsp-web-harness"
