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
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import session_context


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

    def _raise(_rd, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(m.cfg, "load_machines_yaml", _raise)
    rc = m.cmd_machine_context(_Args())
    assert rc == 0
    context = json.loads(capsys.readouterr().out)["additionalContext"]
    assert "Machine: unresolved" in context
    assert "Execution identity: foo" in context
    assert "Hostname:" in context
    assert "Project: proj" in context


def test_emits_additional_context_in_project(monkeypatch, capsys):
    sentinel = cfg.MachineEntry(key="foo", display_name="Foo")
    monkeypatch.setattr(m, "_resolve_active_project", lambda _p: ("proj", None))
    monkeypatch.setattr(m.cfg, "set_active_project", lambda _p: None)
    monkeypatch.setattr(m.cfg, "load_config", lambda: _Config())
    monkeypatch.setattr(m.cfg, "load_machines_yaml", lambda _rd, **kwargs: {"foo": sentinel})
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
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "os-host-a")
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
        "Machine: host-a",
        "Hostname: os-host-a",
        "Machine name: host-a",
        "Execution identity: host-a",
        "Alias/friendly name: Host A",
        "Environment: linux",
        "Platform: linux",
        "System environment: Linux",
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


def test_session_machine_identity_reads_registry_without_worktree_binding(tmp_path, monkeypatch):
    (tmp_path / "machines.yaml").write_text(
        "machines:\n"
        "  worker-a:\n"
        "    hostname: VM-ABC\n"
        "    display_name: builder\n"
        "    environment: Windows\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(session_context.socket, "gethostname", lambda: "VM-ABC")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    config = SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))

    context = session_context.render_machine_context(config)
    facts = json.loads(context.split("```json\n", 1)[1].split("\n```", 1)[0])

    assert facts == {
        "hostname": "VM-ABC", "machine": "worker-a", "alias": "builder",
        "execution_machine": "worker-a", "environment": "windows",
        "platform": "windows", "status": "resolved", "source": "machine-registry",
    }
    assert len(context.encode("utf-8")) < 1024


@pytest.mark.parametrize("config_available", [True, False])
def test_session_machine_identity_keeps_unresolved_hostname_explicit(
    tmp_path, monkeypatch, config_available,
):
    monkeypatch.setattr(session_context.socket, "gethostname", lambda: "VM-ABC")
    config = (
        SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))
        if config_available else None
    )

    context = session_context.render_machine_context(config)
    facts = json.loads(context.split("```json\n", 1)[1].split("\n```", 1)[0])

    assert facts["hostname"] == "VM-ABC"
    assert facts["machine"] is None and facts["alias"] is None
    assert facts["execution_machine"] is None
    assert facts["environment"] == facts["platform"]
    assert facts["status"] == "unresolved"
    assert facts["source"] == "os-hostname"
    assert facts["reason"] == ("FileNotFoundError" if config_available else "project-unavailable")


def test_session_machine_identity_does_not_guess_from_configured_machine(tmp_path, monkeypatch):
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  other-host:\n    hostname: VM-OTHER\n    display_name: other\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(session_context.socket, "gethostname", lambda: "VM-ABC")
    config = SimpleNamespace(
        machine="other-host", default_repo=SimpleNamespace(anchor=str(tmp_path)),
    )
    facts = json.loads(
        session_context.render_machine_context(config).split("```json\n", 1)[1].split("\n```", 1)[0]
    )

    assert facts["machine"] is None
    assert facts["reason"] == "hostname-not-registered"


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_session_machine_identity_selects_current_execution_platform(tmp_path, monkeypatch, platform):
    (tmp_path / "machines.yaml").write_text(
        "machines:\n"
        "  worker-a:\n"
        "    hostname: VM-ABC\n"
        "    display_name: builder\n"
        "    ssh:\n"
        "      environments:\n"
        "        - name: windows\n"
        "          alias: worker-a\n"
        "        - name: wsl\n"
        "          alias: worker-a-wsl\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(session_context.socket, "gethostname", lambda: "VM-ABC")
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    config = SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))
    facts = json.loads(
        session_context.render_machine_context(config).split("```json\n", 1)[1].split("\n```", 1)[0]
    )

    assert facts["machine"] == "worker-a"
    assert facts["execution_machine"] == ("worker-a" if platform == "windows" else "worker-a-wsl")
    assert facts["environment"] == platform
    assert facts["platform"] == platform
    assert facts["status"] == "resolved"


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
        hostname=cfg.socket.gethostname(),
    )
    assert cfg.render_copilot_instructions(entry, "example", machine=machine) == direct
    assert "Machine: example-host\n" in direct
    assert f"Execution identity: {machine}\n" in direct
    assert f"Environment: {platform}\n" in direct
    assert "System environment: Example environment\n" in direct
    assert f"Platform: {platform}\n" in direct
    assert f"SSH alias: independent-{platform}-transport\n" in direct
    assert direct.endswith("Binstub: example\n")


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_machine_guidance_keeps_physical_environment_and_execution_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    machines = {
        "custom-native": {"execution_platform": "windows"},
        "custom-guest": {"execution_platform": "wsl"},
    }
    for metadata in machines.values():
        metadata.update(
            physical_host="physical-host-wsl", hostname="Independent-Hostname",
            display_name="Friendly host",
        )
    (tmp_path / "machines.yaml").write_text(
        json.dumps({"machines": machines}), encoding="utf-8",
    )
    monkeypatch.setattr(session_context.socket, "gethostname", lambda: "Independent-Hostname")
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    config = SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))
    facts = json.loads(
        session_context.render_machine_context(config).split("```json\n", 1)[1].split("\n```", 1)[0]
    )
    execution = "custom-native" if platform == "windows" else "custom-guest"
    entry = cfg.load_machines_yaml(tmp_path)[execution]
    text = cfg.render_copilot_instructions(entry, machine=execution)
    assert facts["machine"] == "physical-host-wsl"
    assert facts["execution_machine"] == execution
    assert facts["environment"] == platform
    assert facts["hostname"] == "Independent-Hostname"
    assert facts["alias"] == "Friendly host"
    assert "Machine: physical-host-wsl\n" in text
    assert "Machine name: physical-host-wsl\n" in text
    assert f"Execution identity: {execution}\n" in text
    assert f"Environment: {platform}\n" in text


@pytest.mark.parametrize("failure", ["config", "topology", "metadata", "missing", "render"])
def test_machine_context_warning_stream(monkeypatch, capsys, failure):
    monkeypatch.setattr(m, "_resolve_active_project", lambda _p: ("proj", None))
    monkeypatch.setattr(cfg, "set_active_project", lambda _p: None)
    monkeypatch.setattr(cfg, "load_config", lambda: _Config(machine="example-host-wsl"))
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda _repo, **kwargs: {})
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
