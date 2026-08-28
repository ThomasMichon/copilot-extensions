"""Guards for the wholesale production Picker transplant."""

from __future__ import annotations

from pathlib import Path

from worktree_manager import __main__ as entrypoint
from worktree_manager.production_picker import runner


def test_transplanted_picker_sources_match_production_copy():
    root = Path(__file__).resolve().parents[2]
    source = root / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
    transplanted = (
        root / "worktree-manager" / "src" / "worktree_manager" / "production_picker"
    )
    relative_paths = [
        Path("picker.py"),
        *(
            path.relative_to(source)
            for path in sorted((source / "picker_tui").glob("*.py"))
        ),
    ]

    assert relative_paths
    for relative in relative_paths:
        assert (transplanted / relative).read_bytes() == (source / relative).read_bytes()


def test_production_runner_activates_project_and_uses_transplanted_ui(monkeypatch):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    class Config:
        @staticmethod
        def set_active_project(project):
            calls.append(("active", project))

    class Cli:
        @staticmethod
        def _resolve_active_project(project):
            return project, None

        @staticmethod
        def _in_ssh_session():
            return False

        @staticmethod
        def _heal_stale_anchor_if_self_missing(config):
            calls.append(("heal", config))
            return config

        @staticmethod
        def reap_orphan_mux_sessions():
            calls.append(("reap",))

        @staticmethod
        def _sweep_managed_on_exit():
            calls.append(("managed",))

        @staticmethod
        def _sweep_launcher_shells_on_exit():
            calls.append(("shells",))

        @staticmethod
        def _sweep_finished_sessions_on_cadence():
            calls.append(("finished",))

        @staticmethod
        def _start_picker_monitor_root():
            return None

    Config.load_config = staticmethod(lambda: "config")

    monkeypatch.setattr(
        runner,
        "engine_module",
        lambda name: Config if name == "config" else Cli,
    )
    monkeypatch.setattr(
        runner,
        "run_tui_picker",
        lambda *, live: calls.append(("picker", live)) or {"action": "new"},
    )
    monkeypatch.setattr(runner.threading, "Thread", ImmediateThread)

    assert runner.run("demo") == {"action": "new"}
    assert calls == [
        ("active", "demo"),
        ("heal", "config"),
        ("reap",),
        ("managed",),
        ("shells",),
        ("finished",),
        ("picker", True),
    ]


def test_manager_acts_on_production_picker_new_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"no_mux": False},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 23,
    )

    assert entrypoint._run_production_picker("demo") == 23
    assert requests[0].project == "demo"
    assert requests[0].mode == "new"
    assert requests[0].worktree_id is None
    assert requests[0].no_mux is False


def test_manager_acts_on_production_picker_resume_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume me",
            "is_local": True,
            "options": {"bare_resume": True, "no_mux": True},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "bare-resume"
    assert requests[0].no_mux is True


def test_normal_picker_command_uses_production_transplant(monkeypatch):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: True)
    monkeypatch.setattr(entrypoint, "build_projects", lambda: [])
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_run_production_picker",
        lambda project: calls.append(project) or 19,
    )

    assert entrypoint._cmd_picker(["demo"]) == 19
    assert calls == ["demo"]
