"""Tests for agent_registry.py -- agent parsing and resolution."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent_bridge.agent_registry import (
    AgentConfig,
    AgentResolver,
    discover_local_agents,
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
        with pytest.raises(ValueError, match="not found by key or SSH alias"):
            resolver.resolve("ghost")

    # -- Alias-based resolution (#10) ----------------------------------------

    def test_resolve_via_ssh_alias(self):
        """host matching an SSH alias should resolve to that machine+env."""
        agents = parse_agent_registry({
            "wsl-via-alias": {
                "host": "workstation-wsl",
                "project": "my-project",
                "description": "Uses alias to reach WSL",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        target = resolver.resolve("wsl-via-alias")
        assert target.type == "ssh"
        assert target.host == "workstation-wsl"
        assert target.project == "my-project"
        assert target.ssh_shell == "bash"

    def test_resolve_alias_non_binstub_posix(self):
        """Non-binstub agent via alias to POSIX shell should succeed."""
        agents = parse_agent_registry({
            "raw-wsl": {
                "host": "workstation-wsl",
                "cwd": "/home/dev/src",
                "description": "No binstub, WSL alias",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        target = resolver.resolve("raw-wsl")
        assert target.type == "ssh"
        assert target.host == "workstation-wsl"
        assert target.ssh_shell == "bash"

    def test_resolve_alias_non_binstub_pwsh_fails(self):
        """Non-binstub agent via alias to pwsh should fail."""
        self.machines["laptop"].ssh_ready = True
        agents = parse_agent_registry({
            "raw-laptop": {
                "host": "laptop",
                "cwd": "C:\\Users\\dev",
                "description": "No binstub, pwsh alias",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        with pytest.raises(ValueError, match="POSIX-compatible shell"):
            resolver.resolve("raw-laptop")

    def test_resolve_alias_binstub_pwsh_succeeds(self):
        """Binstub agent via alias to pwsh should succeed."""
        self.machines["laptop"].ssh_ready = True
        agents = parse_agent_registry({
            "binstub-laptop": {
                "host": "laptop",
                "project": "my-project",
                "description": "Binstub on pwsh alias",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        target = resolver.resolve("binstub-laptop")
        assert target.type == "ssh"
        assert target.host == "laptop"
        assert target.project == "my-project"

    def test_resolve_alias_conflicting_ssh_environment(self):
        """Alias match + conflicting ssh_environment should raise."""
        agents = parse_agent_registry({
            "conflict": {
                "host": "workstation-wsl",
                "ssh_environment": "windows",
                "project": "my-project",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        with pytest.raises(ValueError, match="conflict"):
            resolver.resolve("conflict")

    def test_resolve_alias_matching_ssh_environment(self):
        """Alias match + matching ssh_environment should succeed."""
        agents = parse_agent_registry({
            "matching": {
                "host": "workstation-wsl",
                "ssh_environment": "wsl",
                "project": "my-project",
            },
        })
        resolver = AgentResolver(agents, self.machines)
        target = resolver.resolve("matching")
        assert target.type == "ssh"
        assert target.host == "workstation-wsl"

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


class TestDiscoverLocalAgents:

    def test_discovers_projects(self, tmp_path: Path, monkeypatch):
        projects_yaml = tmp_path / "projects.yaml"
        projects_yaml.write_text(
            "projects:\n"
            "  my-app:\n"
            '    anchor: "/home/user/src/my-app"\n'
            '    registered_at: "2026-01-01"\n'
            "  dotfiles:\n"
            '    anchor: "/home/user/src/dotfiles"\n'
        )
        monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects_yaml))
        agents = discover_local_agents()
        assert len(agents) == 2
        assert "my-app" in agents
        assert "dotfiles" in agents
        assert agents["my-app"].project == "my-app"
        assert agents["my-app"].host is None
        assert agents["my-app"].auto_discovered is True
        assert agents["my-app"].cwd == "/home/user/src/my-app"

    def test_missing_projects_yaml(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(
            "AGENT_WORKTREES_PROJECTS_YAML",
            str(tmp_path / "nonexistent.yaml"),
        )
        agents = discover_local_agents()
        assert agents == {}

    def test_malformed_yaml(self, tmp_path: Path, monkeypatch):
        projects_yaml = tmp_path / "projects.yaml"
        projects_yaml.write_text("{{{not valid yaml")
        monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects_yaml))
        agents = discover_local_agents()
        assert agents == {}

    def test_empty_projects(self, tmp_path: Path, monkeypatch):
        projects_yaml = tmp_path / "projects.yaml"
        projects_yaml.write_text("projects: {}\n")
        monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects_yaml))
        agents = discover_local_agents()
        assert agents == {}


# -- Provider tests ------------------------------------------------------------


def _make_resolver_with_agents():
    """Build a resolver with sample agents for provider tests."""
    agents = parse_agent_registry(SAMPLE_AGENTS)
    machines = parse_machines_yaml(SAMPLE_MACHINES_DATA)
    return AgentResolver(agents, machines)


class TestProviderRegistration:

    def test_register_provider(self):
        resolver = _make_resolver_with_agents()
        provider_agents = {
            "cs-my-codespace": AgentConfig(
                name="cs-my-codespace",
                display_name="My Codespace",
                spawn_command=["agent-codespaces", "ssh", "--stdio", "my-cs"],
                provider="codespaces",
            ),
        }
        provider = resolver.register_provider("codespaces", provider_agents, ttl=300)
        assert provider.name == "codespaces"
        assert len(provider.agents) == 1

    def test_unregister_provider(self):
        resolver = _make_resolver_with_agents()
        resolver.register_provider("codespaces", {}, ttl=300)
        assert resolver.unregister_provider("codespaces") is True
        assert resolver.unregister_provider("codespaces") is False

    def test_provider_agents_in_list(self):
        resolver = _make_resolver_with_agents()
        resolver.register_provider("codespaces", {
            "cs-test": AgentConfig(
                name="cs-test",
                display_name="Test CS",
                spawn_command=["echo", "hello"],
                provider="codespaces",
            ),
        })
        names = [a["name"] for a in resolver.list_agents()]
        assert "cs-test" in names

    def test_static_agent_overrides_provider(self):
        resolver = _make_resolver_with_agents()
        # Register a provider agent with same name as a static agent
        resolver.register_provider("codespaces", {
            "local-agent": AgentConfig(
                name="local-agent",
                spawn_command=["should", "not", "appear"],
                provider="codespaces",
            ),
        })
        # Static should win -- no provider field
        agents = resolver.list_agents()
        local = [a for a in agents if a["name"] == "local-agent"]
        assert len(local) == 1
        assert local[0]["provider"] is None

    def test_provider_agent_resolves(self):
        resolver = _make_resolver_with_agents()
        resolver.register_provider("codespaces", {
            "cs-test": AgentConfig(
                name="cs-test",
                spawn_command=["agent-codespaces", "ssh", "--stdio", "test"],
                provider="codespaces",
            ),
        })
        target = resolver.resolve("cs-test")
        assert target.type == "command"
        assert target.spawn_command == [
            "agent-codespaces", "ssh", "--stdio", "test",
        ]

    def test_expired_provider_removed(self, monkeypatch):
        import time as time_mod
        resolver = _make_resolver_with_agents()
        resolver.register_provider("codespaces", {
            "cs-expired": AgentConfig(
                name="cs-expired",
                spawn_command=["echo"],
                provider="codespaces",
            ),
        }, ttl=10)
        # Patch monotonic to simulate time passing
        original = resolver._providers["codespaces"].registered_at
        monkeypatch.setattr(
            time_mod, "monotonic", lambda: original + 20,
        )
        names = [a["name"] for a in resolver.list_agents()]
        assert "cs-expired" not in names

    def test_expired_provider_resolve_fails(self, monkeypatch):
        import time as time_mod
        resolver = _make_resolver_with_agents()
        resolver.register_provider("codespaces", {
            "cs-gone": AgentConfig(
                name="cs-gone",
                spawn_command=["echo"],
                provider="codespaces",
            ),
        }, ttl=5)
        original = resolver._providers["codespaces"].registered_at
        monkeypatch.setattr(
            time_mod, "monotonic", lambda: original + 10,
        )
        with pytest.raises(KeyError, match="cs-gone"):
            resolver.resolve("cs-gone")

    def test_zero_ttl_never_expires(self, monkeypatch):
        import time as time_mod
        resolver = _make_resolver_with_agents()
        resolver.register_provider("permanent", {
            "cs-perm": AgentConfig(
                name="cs-perm",
                spawn_command=["echo"],
                provider="permanent",
            ),
        }, ttl=0)
        original = resolver._providers["permanent"].registered_at
        monkeypatch.setattr(
            time_mod, "monotonic", lambda: original + 999999,
        )
        names = [a["name"] for a in resolver.list_agents()]
        assert "cs-perm" in names

    def test_list_providers(self):
        resolver = _make_resolver_with_agents()
        resolver.register_provider("codespaces", {
            "cs-one": AgentConfig(
                name="cs-one",
                spawn_command=["echo"],
                provider="codespaces",
            ),
            "local-agent": AgentConfig(
                name="local-agent",
                spawn_command=["conflict"],
                provider="codespaces",
            ),
        })
        providers = resolver.list_providers()
        assert len(providers) == 1
        p = providers[0]
        assert p["name"] == "codespaces"
        assert p["agents"] == 2
        assert p["active_agents"] == 1
        assert p["conflicts"] == ["local-agent"]

    def test_provider_agent_target_type_is_command(self):
        resolver = _make_resolver_with_agents()
        resolver.register_provider("codespaces", {
            "cs-cmd": AgentConfig(
                name="cs-cmd",
                spawn_command=["agent-codespaces", "ssh", "--stdio", "x"],
                provider="codespaces",
            ),
        })
        agents = resolver.list_agents()
        cs = [a for a in agents if a["name"] == "cs-cmd"][0]
        assert cs["target_type"] == "command"
        assert cs["provider"] == "codespaces"

    def test_no_projects_key(self, tmp_path: Path, monkeypatch):
        projects_yaml = tmp_path / "projects.yaml"
        projects_yaml.write_text("other_key: value\n")
        monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects_yaml))
        agents = discover_local_agents()
        assert agents == {}

    def test_explicit_agents_win_over_discovered(self):
        """Verify that explicit registry entries take precedence."""
        explicit = parse_agent_registry({
            "my-app": {
                "host": "remote-server",
                "description": "Explicit remote agent",
            },
        })
        discovered = {
            "my-app": AgentConfig(
                name="my-app",
                project="my-app",
                auto_discovered=True,
            ),
            "other-project": AgentConfig(
                name="other-project",
                project="other-project",
                auto_discovered=True,
            ),
        }
        # Simulate merge logic: explicit wins
        merged = dict(explicit)
        for name, agent in discovered.items():
            if name not in merged:
                merged[name] = agent

        assert merged["my-app"].host == "remote-server"
        assert merged["my-app"].auto_discovered is False
        assert merged["other-project"].auto_discovered is True

    def test_list_agents_shows_auto_discovered(self):
        agents = {
            "explicit": AgentConfig(name="explicit", description="Explicit"),
            "discovered": AgentConfig(
                name="discovered",
                project="discovered",
                auto_discovered=True,
                description="Auto-discovered",
            ),
        }
        resolver = AgentResolver(agents, {})
        listing = resolver.list_agents()
        by_name = {a["name"]: a for a in listing}
        assert by_name["explicit"]["auto_discovered"] is False
        assert by_name["discovered"]["auto_discovered"] is True
