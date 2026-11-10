from __future__ import annotations

from types import SimpleNamespace

import pytest

from worktree_manager import first_run, harness_state


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(first_run, "core_status", lambda: SimpleNamespace(state="absent"))
    monkeypatch.setattr(first_run, "engine_available", lambda: False)
    return tmp_path


def test_fresh_home_has_no_project_or_writes(home):
    state = first_run.observe()
    assert state.projects == ()
    assert not state.engine_available
    assert "absent" in state.summary
    assert "No launchable" in state.summary
    assert list(home.iterdir()) == []


def test_observation_preserves_terminal_cleanup_custody(home, monkeypatch):
    error = first_run.EngineCleanupError("retirement unconfirmed")

    def fail():
        raise error

    monkeypatch.setattr(first_run, "engine_available", fail)
    with pytest.raises(first_run.EngineCleanupError) as caught:
        first_run.observe()
    assert caught.value is error


def test_project_observation_excludes_knowledge_and_optional_readiness(home, monkeypatch):
    registry = home / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text(
        "projects:\n  example: {}\n  private-state: {}\n", encoding="utf-8",
    )
    (registry / "repos.yaml").write_text(
        "repos:\n  example:\n    class: worktree\n"
        "  private-state:\n    class: knowledge\n", encoding="utf-8",
    )
    monkeypatch.setattr(first_run, "engine_available", lambda: True)
    monkeypatch.setattr(
        harness_state, "build_projects",
        lambda: pytest.fail("optional roster/plugin reads must not gate entry"),
    )
    assert first_run.observe().projects == ("example",)


def test_unknown_registry_is_not_empty_and_recovers_on_reload(home):
    registry = home / ".agent-worktrees"
    registry.mkdir()
    path = registry / "projects.yaml"
    path.write_text("projects: [broken]", encoding="utf-8")
    state = first_run.observe()
    assert state.projects is None
    assert "unavailable" in state.summary.lower()
    assert "No launchable" not in state.summary
    path.write_text("projects: {}", encoding="utf-8")
    assert first_run.observe().projects == ()


def test_diagnostics_use_existing_owner_and_never_request_apply(monkeypatch):
    from worktree_manager import engine_client, installation_updates

    monkeypatch.setattr(engine_client, "inherited_engine_command", lambda: ["engine", "fixture"])
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "fixture-context")
    owner_action = next(
        action for action in installation_updates.configuration_pivot().actions
        if action.key == "doctor"
    )
    assert first_run.diagnostic_argv("doctor") == [
        part.replace("{id}", "worktree-manager") for part in owner_action.run
    ]
    assert first_run.diagnostic_argv("setup")[-1] == "setup"
    for action in ("setup", "doctor"):
        assert "--apply" not in first_run.diagnostic_argv(action)
        assert "--apply-daemon-health" not in first_run.diagnostic_argv(action)
    with pytest.raises(ValueError):
        first_run.diagnostic_argv("install")


@pytest.mark.parametrize(
    "available,projects,bootstrap",
    [(False, (), False), (True, (), False), (True, None, False),
     (True, ("alpha", "beta"), False), (True, ("example",), True)],
)
def test_runner_only_bootstraps_one_available_project(
    monkeypatch, available, projects, bootstrap,
):
    from worktree_manager.production_picker import runner

    calls = []
    runner.context.reset()
    monkeypatch.setattr(first_run, "observe", lambda: first_run.SetupState(available, projects, "state"))
    monkeypatch.setattr(runner.engine_client, "engine_available", lambda: True)
    monkeypatch.setattr(
        runner.engine_group_b, "picker_bootstrap",
        lambda project: calls.append(project) or {
            "version": 1, "project": project, "should_switch_cwd": False,
            "default_live": False,
        },
    )

    def run_picker(**kwargs):
        assert calls == []
        assert kwargs["front_door"] is True
        result = kwargs["prepare"]()
        if bootstrap:
            assert calls == ["example"]
            result.activate()
            assert runner.context.project() == "example"
        else:
            assert isinstance(result, first_run.SetupState)
            assert calls == []
            assert runner.context.project_bootstrap() is None
            with pytest.raises(RuntimeError, match="not bound"):
                runner.context.project()
        return None

    monkeypatch.setattr(runner, "run_tui_picker", run_picker)
    try:
        assert runner.run("", front_door=True) is None
    finally:
        runner.context.reset()


def test_runner_rereads_selected_project_before_bootstrap(monkeypatch):
    from worktree_manager.production_picker import runner

    states = iter([
        first_run.SetupState(True, ("alpha", "beta"), "Choose a project"),
        first_run.SetupState(True, ("alpha",), "beta is no longer a project"),
    ])
    monkeypatch.setattr(first_run, "observe", lambda: next(states))
    monkeypatch.setattr(
        runner.engine_group_b, "picker_bootstrap",
        lambda _: pytest.fail("stale/knowledge-only project must not bootstrap"),
    )

    def run_picker(**kwargs):
        assert kwargs["prepare"]().projects == ("alpha", "beta")
        selected = kwargs["prepare"](project_override="beta", auto_open=False)
        assert selected.projects == ("alpha",)
        assert "no longer launchable: beta" in selected.summary

    monkeypatch.setattr(runner, "run_tui_picker", run_picker)
    runner.run("", front_door=True)
