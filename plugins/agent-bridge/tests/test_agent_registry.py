"""Tests for agent_registry.py -- agent parsing and resolution."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent_bridge.agent_registry import (
    AgentConfig,
    AgentResolver,
    load_agent_registry,
    parse_agent_registry,
)
from agent_bridge.topology import MachineConfig, SshEnvironment, parse_machines_yaml
from agent_bridge.transport import SpawnTarget


# -- Sample data ---------------------------------------------------------------

SAMPLE_AGENTS = {
    "local-agent": {
        "description": "Local test agent",
        "project": "my-project",
    },
    "remote-agent": {
        "host": "server-a",
        "description": "Agent on Server A",
        "copilot_args": ["--extensions-dir", "/opt/copilot/ext"],
        "env": {"MY_VAR": "hello"},
        "project": "my-project",
    },
    "lambda-agent": {
        "host": "workstation",
        "ssh_environment": "wsl",
        "cwd": "/home/user/src/project",
        "description": "Agent on Workstation WSL",
    },
    "managed-agent": {
        "managed": True,
        "host": "some-server",
        "description": "A managed TCP agent",
    },
    "windows-only-agent": {
        "host": "laptop",
        "cwd": "C:\\Users\\user\\src",
        "description": "Agent on pwsh-only machine",
    },
}

SAMPLE_MACHINES_DATA = {
    "machines": {
        "server-a": {
            "display_name": "Server A",
            "environment": "Debian 13",
            "role": "Services",
            "ssh": {
                "environments": [
                    {"name": "linux", "alias": "server-a", "port": 22, "user": "deploy", "shell": "bash"},
                ],
                "ip": "10.0.0.10",
                "ready": True,
            },
        },
        "workstation": {
            "display_name": "Workstation",
            "environment": "Windows 11",
            "role": "Dev",
            "ssh": {
                "environments": [
                    {"name": "windows", "alias": "workstation", "port": 2222, "user": "dev", "shell": "pwsh"},
                    {"name": "wsl", "alias": "workstation-wsl", "port": 22, "user": "dev", "shell": "bash"},
                ],
                "ip": "10.0.0.20",
                "ready": True,
            },
        },
        "laptop": {
            "display_name": "Laptop",
            "environment": "Windows 11",
            "role": "Field terminal",
            "ssh": {
                "environments": [
                    {"name": "windows", "alias": "laptop", "port": 2222, "user": "dev", "shell": "pwsh"},
                ],
                "ready": False,
            },
        },
    }
}


class TestParseAgentRegistry:

    def test_parse_all_agents(self):
        registry = parse_agent_registry(SAMPLE_AGENTS)
        assert len(registry) == 5

    def test_local_agent_fields(self):
        registry = parse_agent_registry(SAMPLE_AGENTS)
        agent = registry["local-agent"]
        assert agent.host is None
        assert agent.cwd is None
        assert agent.managed is False
        assert agent.project == "my-project"

    def test_ssh_agent_fields(self):
        registry = parse_agent_registry(SAMPLE_AGENTS)
        agent = registry["remote-agent"]
        assert agent.host == "server-a"
        assert agent.copilot_args == ["--extensions-dir", "/opt/copilot/ext"]
        assert agent.env == {"MY_VAR": "hello"}
        assert agent.project == "my-project"

    def test_managed_agent(self):
        registry = parse_agent_registry(SAMPLE_AGENTS)
        agent = registry["managed-agent"]
        assert agent.managed is True

    def test_empty_registry(self):
        assert parse_agent_registry({}) == {}


class TestAgentResolver:

    def setup_method(self):
        self.agents = parse_agent_registry(SAMPLE_AGENTS)
        self.machines = parse_machines_yaml(SAMPLE_MACHINES_DATA)
        self.resolver = AgentResolver(self.agents, self.machines)

    def test_resolve_local_agent(self):
        target = self.resolver.resolve("local-agent")
        assert target.type == "local"
        assert target.cwd is None
        assert target.host is None
        assert target.project == "my-project"

    def test_resolve_ssh_agent(self):
        target = self.resolver.resolve("remote-agent")
        assert target.type == "ssh"
        assert target.host == "server-a"
        assert target.user == "deploy"
        assert target.cwd is None
        assert target.env == {"MY_VAR": "hello"}
        assert target.project == "my-project"

    def test_resolve_ssh_agent_explicit_environment(self):
        target = self.resolver.resolve("lambda-agent")
        assert target.type == "ssh"
        assert target.host == "workstation-wsl"
        assert target.user == "dev"

    def test_resolve_managed_agent_raises(self):
        with pytest.raises(ValueError, match="managed"):
            self.resolver.resolve("managed-agent")

    def test_resolve_unknown_agent_raises(self):
        with pytest.raises(KeyError, match="not found"):
            self.resolver.resolve("nonexistent")

    def test_resolve_agent_on_not_ready_machine(self):
        """Agent targeting a machine that isn't SSH-ready should fail."""
        with pytest.raises(ValueError, match="not marked as SSH-ready"):
            self.resolver.resolve("windows-only-agent")

    def test_resolve_agent_no_posix_shell(self):
        """Non-binstub agent on a machine with only pwsh should fail."""
        # Make the machine ready but keep only pwsh shells
        self.machines["laptop"].ssh_ready = True
        resolver = AgentResolver(self.agents, self.machines)
        with pytest.raises(ValueError, match="POSIX"):
            resolver.resolve("windows-only-agent")

    def test_resolve_binstub_agent_windows_env(self):
        """Binstub agent with explicit windows env should resolve via pwsh."""
        agents = parse_agent_registry({
            "win-binstub": {
                "host": "workstation",
                "ssh_environment": "windows",
                "project": "my-project",
                "description": "Windows native with binstub",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        target = resolver.resolve("win-binstub")
        assert target.type == "ssh"
        assert target.host == "workstation"  # windows alias
        assert target.user == "dev"
        assert target.project == "my-project"

    def test_resolve_binstub_agent_auto_selects_wsl(self):
        """Binstub agent with no ssh_environment prefers wsl on dual-env machines."""
        agents = parse_agent_registry({
            "auto-binstub": {
                "host": "workstation",
                "project": "my-project",
                "description": "Auto-select env",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        target = resolver.resolve("auto-binstub")
        assert target.type == "ssh"
        assert target.host == "workstation-wsl"  # wsl preferred by default

    def test_resolve_binstub_pwsh_only_machine(self):
        """Binstub agent on pwsh-only machine should succeed (unlike non-binstub)."""
        self.machines["laptop"].ssh_ready = True
        agents = parse_agent_registry({
            "laptop-binstub": {
                "host": "laptop",
                "project": "my-project",
                "description": "Laptop with binstub",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        target = resolver.resolve("laptop-binstub")
        assert target.type == "ssh"
        assert target.host == "laptop"
        assert target.project == "my-project"

    def test_resolve_agent_missing_machine(self):
        """Agent targeting a machine not in topology should fail."""
        agents = parse_agent_registry({
            "ghost": {"host": "nonexistent-machine", "cwd": "."},
        })
        resolver = AgentResolver(agents, self.machines)
        with pytest.raises(ValueError, match="not in the topology"):
            resolver.resolve("ghost")

    def test_list_agents(self):
        agents = self.resolver.list_agents()
        assert len(agents) == 5
        names = {a["name"] for a in agents}
        assert "local-agent" in names
        assert "managed-agent" in names
        # Managed agents should be marked non-spawnable
        managed = next(a for a in agents if a["name"] == "managed-agent")
        assert managed["spawnable"] is False
        assert managed["managed"] is True


class TestLoadAgentRegistry:

    def test_load_valid_file(self, tmp_path: Path):
        reg_path = tmp_path / "agents.json"
        reg_path.write_text(json.dumps(SAMPLE_AGENTS))
        registry = load_agent_registry(reg_path)
        assert len(registry) == 5

    def test_load_missing_file(self, tmp_path: Path):
        registry = load_agent_registry(tmp_path / "nonexistent.json")
        assert registry == {}

    def test_load_invalid_json(self, tmp_path: Path):
        reg_path = tmp_path / "agents.json"
        reg_path.write_text("{invalid json")
        registry = load_agent_registry(reg_path)
        assert isinstance(registry, dict)
