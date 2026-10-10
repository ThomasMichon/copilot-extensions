"""Tests for the machine-identity migration to the session-machine sessionStart
hook.

Machine identity is now emitted live as ``additionalContext`` by the
``machine-context`` command (dotfiles#1056) instead of being materialized into
``machine.instructions.md`` / the nested ``AGENTS.md`` and loaded via
COPILOT_CUSTOM_INSTRUCTIONS_DIRS. The deploy path only retires the stale files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg


class _Args:
    pass


class _Repo:
    anchor = "/repo"


class _Config:
    def __init__(self, repo_name="proj", machine="foo"):
        self.repo_name = repo_name
        self.machine = machine
        self.default_repo = _Repo()


def test_registered_as_no_project_hook_command():
    assert m.COMMAND_MAP.get("machine-context") is m.cmd_machine_context
    # Resolves its own project + gates, so it must dispatch even from ~/.
    assert "machine-context" in m._NO_PROJECT_COMMANDS


def test_emits_empty_outside_a_project(monkeypatch, capsys):
    monkeypatch.setattr(m, "_resolve_active_project", lambda _p: (None, None))
    rc = m.cmd_machine_context(_Args())
    assert rc == 0
    assert capsys.readouterr().out.strip() == "{}"


def test_emits_identity_without_machines_yaml(monkeypatch, capsys):
    monkeypatch.setattr(m, "_resolve_active_project", lambda _p: ("proj", None))
    monkeypatch.setattr(m.cfg, "set_active_project", lambda _p: None)
    monkeypatch.setattr(m.cfg, "load_config", lambda: _Config())

    def _raise(_rd):
        raise FileNotFoundError

    monkeypatch.setattr(m.cfg, "load_machines_yaml", _raise)
    rc = m.cmd_machine_context(_Args())
    assert rc == 0
    context = json.loads(capsys.readouterr().out)["additionalContext"]
    assert "Machine: foo" in context
    assert "Project: proj" in context


def test_emits_additional_context_in_project(monkeypatch, capsys):
    sentinel = object()
    monkeypatch.setattr(m, "_resolve_active_project", lambda _p: ("proj", None))
    monkeypatch.setattr(m.cfg, "set_active_project", lambda _p: None)
    monkeypatch.setattr(m.cfg, "load_config", lambda: _Config())
    monkeypatch.setattr(m.cfg, "load_machines_yaml", lambda _rd: {"foo": sentinel})
    monkeypatch.setattr(m.cfg, "find_machine_entry", lambda _reg, _name: sentinel)
    monkeypatch.setattr(
        m.cfg, "render_copilot_instructions",
        lambda _entry, project="", machine="": (
            f"Machine: {machine}\nHostname: bar\nProject: " + project
        ),
    )

    rc = m.cmd_machine_context(_Args())
    assert rc == 0
    obj = json.loads(capsys.readouterr().out)
    assert set(obj.keys()) == {"additionalContext"}
    assert "Machine: foo" in obj["additionalContext"]
    assert "Project: proj" in obj["additionalContext"]


def test_render_includes_normalized_machine_metadata_after_role(monkeypatch):
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    entry = cfg.MachineEntry(
        key="host-a",
        display_name="Host A",
        environment="Linux",
        role="worker",
        description="General-purpose worker.",
        capabilities=["builds", "tests"],
    )

    rendered = cfg.render_copilot_instructions(entry, project="example")

    assert rendered.splitlines() == [
        "Machine: Host A",
        "Hostname: host-a",
        "Environment: Linux",
        "Platform: linux",
        "Role: worker",
        "Description: General-purpose worker.",
        "Capabilities: builds, tests",
        "Project: example",
        "Binstub: example",
    ]


def test_render_omits_empty_machine_metadata(monkeypatch):
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    entry = cfg.MachineEntry(
        key="host-a", display_name="Host A", environment="Linux", role="worker",
    )

    rendered = cfg.render_copilot_instructions(entry)

    assert "Role: worker" in rendered
    assert "Description:" not in rendered
    assert "Capabilities:" not in rendered


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_renderer_uses_explicit_platform_and_wrapper_remains_patchable(monkeypatch, platform):
    from agent_worktrees import machine_instructions

    entry = cfg.MachineEntry(
        key="example-host", display_name="Example host", environment="Example environment",
        role="worker", description="Shared metadata", capabilities=["tests"],
        ssh_environments=[
            cfg.SSHEnvironment(name=name, alias=f"independent-{name}-transport")
            for name in ("windows", "wsl", "linux")
        ],
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    machine = "example-host-wsl" if platform == "wsl" else "example-host"
    direct = machine_instructions.render_copilot_instructions(
        entry, "example", machine=machine, platform=platform,
    )
    assert cfg.render_copilot_instructions(entry, "example", machine=machine) == direct
    assert f"Machine: {machine}\n" in direct
    assert f"Platform: {platform}\n" in direct
    assert f"Deployment environment: independent-{platform}-transport\n" in direct
    assert direct.endswith("Binstub: example\n")


@pytest.mark.parametrize("failure", ["config", "topology", "metadata", "missing", "render"])
def test_machine_context_warning_stream(monkeypatch, capsys, failure):
    monkeypatch.setattr(m, "_resolve_active_project", lambda _p: ("proj", None))
    monkeypatch.setattr(cfg, "set_active_project", lambda _p: None)
    monkeypatch.setattr(cfg, "load_config", lambda: _Config(machine="example-host-wsl"))
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda _repo: {})
    monkeypatch.setattr(cfg, "find_machine_metadata", lambda *_args: object())
    monkeypatch.setattr(
        cfg, "render_copilot_instructions", lambda *_args, **_kwargs: "Machine: example-host-wsl",
    )

    def fail(*_args, **_kwargs):
        raise ValueError("fixture error")

    if failure == "missing":
        monkeypatch.setattr(cfg, "find_machine_metadata", lambda *_args: None)
    else:
        target = {
            "config": "load_config", "topology": "load_machines_yaml",
            "metadata": "find_machine_metadata", "render": "render_copilot_instructions",
        }[failure]
        monkeypatch.setattr(cfg, target, fail)
    assert m.cmd_machine_context(_Args()) == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    payload = json.loads(captured.out.splitlines()[0])
    assert payload == {} if failure == "config" else "additionalContext" in payload
    assert "warning:" in captured.err


def test_deploy_retires_machine_files(tmp_path: Path):
    proj = tmp_path / ".proj"
    instr = proj / ".github" / "instructions"
    instr.mkdir(parents=True)
    (instr / "machine.instructions.md").write_text(f"{m._INSTRUCTION_MARKER}\nstale\n")
    (proj / "AGENTS.md").write_text(f"{m._INSTRUCTION_MARKER}\nstale\n")

    m._deploy_copilot_instructions(proj, object(), project="proj")

    assert not (instr / "machine.instructions.md").exists()
    assert not (proj / "AGENTS.md").exists()


def test_deploy_leaves_unmarked_agents_md(tmp_path: Path):
    proj = tmp_path / ".proj"
    proj.mkdir(parents=True)
    (proj / "AGENTS.md").write_text("# user's own AGENTS.md, not ours\n")

    m._deploy_copilot_instructions(proj, object(), project="proj")

    assert (proj / "AGENTS.md").exists(), "an unmarked user AGENTS.md must never be deleted"
