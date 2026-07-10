"""Tests for agent_registry.py -- agent parsing and resolution."""

from __future__ import annotations

import json
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
from agent_bridge.transport import PluginRef, SpawnTarget


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


    def test_resolve_loopback_returns_local(self):
        """SSH agent targeting the local machine should resolve as local."""
        agents = parse_agent_registry({
            "loopback-agent": {
                "host": "workstation",
                "ssh_environment": "wsl",
                "project": "my-project",
                "description": "Same machine agent",
            },
        })
        from unittest.mock import patch
        local_machine = self.machines["workstation"]
        with patch(
            "agent_bridge.agent_registry._detect_local_machine",
            return_value=(local_machine, "wsl"),
        ):
            resolver = AgentResolver(agents, self.machines)
            target = resolver.resolve("loopback-agent")
        assert target.type == "local"
        assert target.host is None
        assert target.project == "my-project"

    def test_resolve_loopback_different_platform_stays_ssh(self):
        """SSH agent targeting local machine but different platform stays SSH."""
        agents = parse_agent_registry({
            "cross-env-agent": {
                "host": "workstation",
                "ssh_environment": "windows",
                "project": "my-project",
                "description": "Windows env from WSL",
            },
        })
        from unittest.mock import patch
        local_machine = self.machines["workstation"]
        with patch(
            "agent_bridge.agent_registry._detect_local_machine",
            return_value=(local_machine, "wsl"),
        ):
            resolver = AgentResolver(agents, self.machines)
            target = resolver.resolve("cross-env-agent")
        assert target.type == "ssh"
        assert target.host == "workstation"

    def test_resolve_loopback_on_not_ready_machine(self):
        """Loopback dispatch works even when the machine is ssh_ready=false.

        The inter-machine SSH mesh being retired (ssh_ready=false everywhere,
        issue #168) must not disable *local* loopback -- a same-platform agent
        on the local box needs no SSH and should still spawn locally.
        """
        agents = parse_agent_registry({
            "local-cp": {
                "host": "laptop",  # ssh_ready=false in SAMPLE_MACHINES_DATA
                "ssh_environment": "windows",
                "project": "my-project",
            },
        })
        from unittest.mock import patch
        local_machine = self.machines["laptop"]
        assert local_machine.ssh_ready is False
        with patch(
            "agent_bridge.agent_registry._detect_local_machine",
            return_value=(local_machine, "windows"),
        ):
            resolver = AgentResolver(agents, self.machines)
            target = resolver.resolve("local-cp")
        assert target.type == "local"
        assert target.host is None
        assert target.project == "my-project"


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

    def test_reference_only_project_exposes_no_agent(self, tmp_path: Path, monkeypatch):
        # expose_agent defaults ON; an explicit false (reference-only adoption,
        # e.g. agent-worktrees `register --no-agent`) suppresses the agent while
        # the project stays worktree-managed.
        projects_yaml = tmp_path / "projects.yaml"
        projects_yaml.write_text(
            "projects:\n"
            "  facility:\n"
            '    anchor: "/home/user/src/facility"\n'
            "    expose_agent: true\n"
            "  plugin-src:\n"
            '    anchor: "/home/user/src/plugin-src"\n'
            "    expose_agent: false\n"
            "  legacy:\n"  # no key -> defaults ON
            '    anchor: "/home/user/src/legacy"\n'
        )
        monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects_yaml))
        agents = discover_local_agents()
        assert set(agents) == {"facility", "legacy"}
        assert "plugin-src" not in agents

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


# -- Namespace resolver tests --------------------------------------------------


class _MockResolver:
    """A test namespace resolver (implements NamespaceResolver protocol)."""

    def __init__(self, prefix_val: str = "mock"):
        self._prefix = prefix_val

    @property
    def prefix(self) -> str:
        return self._prefix

    async def resolve(self, name: str) -> SpawnTarget:
        if name == "missing":
            raise KeyError(f"Agent '{name}' not found")
        return SpawnTarget(type="command", spawn_command=["echo", name])

    async def list(self):
        from agent_bridge.agent_registry import NamespaceAgentInfo
        return [
            NamespaceAgentInfo(name="test-agent", display_name="Test Agent",
                               description="A mock agent", state="available"),
        ]

    async def ensure_ready(self, name: str) -> None:
        if name == "unready":
            raise RuntimeError("Agent is not ready")


class TestNamespaceResolvers:
    """Namespace resolver registration and dispatch."""

    def test_register_and_parse(self):
        resolver = AgentResolver({}, {})
        mock = _MockResolver()
        resolver.register_namespace_resolver(mock)
        assert "mock" in resolver.namespace_resolvers
        parsed = resolver._parse_namespaced_agent("mock:my-agent")
        assert parsed == ("mock", "my-agent")

    def test_parse_unknown_prefix_returns_none(self):
        resolver = AgentResolver({}, {})
        assert resolver._parse_namespaced_agent("unknown:agent") is None

    def test_parse_no_colon_returns_none(self):
        resolver = AgentResolver({}, {})
        assert resolver._parse_namespaced_agent("plain-agent") is None

    def test_duplicate_prefix_raises(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver("dup"))
        with pytest.raises(ValueError, match="already registered"):
            resolver.register_namespace_resolver(_MockResolver("dup"))

    def test_unregister(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver())
        assert resolver.unregister_namespace_resolver("mock") is True
        assert resolver.unregister_namespace_resolver("mock") is False

    @pytest.mark.asyncio
    async def test_resolve_async_namespace(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver())
        target = await resolver.resolve_async("mock:my-agent")
        assert target.type == "command"
        assert target.spawn_command == ["echo", "my-agent"]

    @pytest.mark.asyncio
    async def test_resolve_async_not_found(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver())
        with pytest.raises(KeyError, match="not found"):
            await resolver.resolve_async("mock:missing")

    @pytest.mark.asyncio
    async def test_resolve_async_ensure_ready_fails(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver())
        with pytest.raises(RuntimeError, match="not ready"):
            await resolver.resolve_async("mock:unready")

    def test_sync_resolve_rejects_namespace(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver())
        with pytest.raises(ValueError, match="resolve_async"):
            resolver.resolve("mock:my-agent")

    @pytest.mark.asyncio
    async def test_list_agents_async_includes_namespace(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver())
        agents = await resolver.list_agents_async()
        ns_agents = [a for a in agents if a["name"].startswith("mock:")]
        assert len(ns_agents) == 1
        assert ns_agents[0]["name"] == "mock:test-agent"
        assert ns_agents[0]["provider"] == "mock"
        assert ns_agents[0]["state"] == "available"

    def test_list_agents_sync_excludes_namespace(self):
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_MockResolver())
        agents = resolver.list_agents()
        ns_agents = [a for a in agents if a["name"].startswith("mock:")]
        assert len(ns_agents) == 0


# -- AdminResolver tests ------------------------------------------------------


class TestAdminResolver:
    """Tests for admin: namespace resolver."""

    def _make_resolver_with_agents(self):
        """Build an AgentResolver with a local and SSH agent."""
        agents = {
            "local-agent": AgentConfig(
                name="local-agent",
                description="Local test agent",
                project="my-project",
                requires_admin=True,
            ),
            "ssh-agent": AgentConfig(
                name="ssh-agent",
                host="server-a",
                description="Remote agent",
            ),
            "managed-agent": AgentConfig(
                name="managed-agent",
                managed=True,
                description="Managed agent",
            ),
        }
        return AgentResolver(agents, {})

    def test_prefix(self):
        from agent_bridge.admin_resolver import AdminResolver

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        assert admin.prefix == "admin"

    @pytest.mark.asyncio
    async def test_resolve_local_agent(self, monkeypatch):
        from agent_bridge import elevated
        from agent_bridge.admin_resolver import AdminResolver

        # Windows path: admin: routes through the elevated sub-daemon relay.
        monkeypatch.setattr(elevated, "relay_applicable", lambda req: True)
        monkeypatch.setattr(elevated, "ensure_running", lambda: "subtok")

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        target = await admin.resolve("local-agent")
        assert target.type == "command"
        assert target.project == "my-project"
        assert target.spawn_command[-4:] == [
            "ws://127.0.0.1:9281/acp/local-agent",
            "--token", "subtok", "--stdio",
        ]

    @pytest.mark.asyncio
    async def test_resolve_posix_uses_sudo(self, monkeypatch):
        from agent_bridge import elevated
        from agent_bridge.admin_resolver import AdminResolver

        # Off Windows there is no sub-daemon; admin: falls back to sudo -A.
        monkeypatch.setattr(elevated, "relay_applicable", lambda req: False)
        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        admin._platform = "linux"
        target = await admin.resolve("local-agent")
        assert target.type == "command"
        assert target.spawn_command[:2] == ["sudo", "-A"]

    @pytest.mark.asyncio
    async def test_resolve_ssh_agent_raises(self):
        """SSH agents with resolvable topology should raise on elevation."""
        from agent_bridge.admin_resolver import AdminResolver

        # Need topology for the SSH agent to resolve through _resolve_static
        machines = {
            "server-a": MachineConfig(
                key="server-a",
                display_name="Server A",
                ssh_ready=True,
                ssh_environments=[
                    SshEnvironment(name="linux", alias="server-a", shell="bash"),
                ],
            ),
        }
        agents = {
            "ssh-agent": AgentConfig(
                name="ssh-agent",
                host="server-a",
                description="Remote agent",
                project="my-project",
            ),
        }
        resolver = AgentResolver(agents, machines)
        admin = AdminResolver(resolver)
        with pytest.raises(ValueError, match="Cannot elevate SSH"):
            await admin.resolve("ssh-agent")

    @pytest.mark.asyncio
    async def test_resolve_unknown_agent_raises(self):
        from agent_bridge.admin_resolver import AdminResolver

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        with pytest.raises(KeyError, match="not found"):
            await admin.resolve("nonexistent")

    @pytest.mark.asyncio
    async def test_list_excludes_ssh_and_managed(self):
        from agent_bridge.admin_resolver import AdminResolver

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        agents = await admin.list()
        names = [a.name for a in agents]
        assert "local-agent" in names
        assert "ssh-agent" not in names
        assert "managed-agent" not in names

    @pytest.mark.asyncio
    async def test_list_adds_elevated_suffix(self):
        from agent_bridge.admin_resolver import AdminResolver

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        agents = await admin.list()
        for a in agents:
            assert "(elevated)" in a.display_name

    @pytest.mark.asyncio
    async def test_ensure_ready_unknown_raises(self):
        from agent_bridge.admin_resolver import AdminResolver

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        with pytest.raises(RuntimeError, match="not found"):
            await admin.ensure_ready("nonexistent")

    @pytest.mark.asyncio
    async def test_ensure_ready_known_succeeds(self):
        from agent_bridge.admin_resolver import AdminResolver

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        # Should not raise
        await admin.ensure_ready("local-agent")

    @pytest.mark.asyncio
    async def test_integration_via_resolver(self, monkeypatch):
        """Test admin: dispatch through the full AgentResolver path."""
        from agent_bridge import elevated
        from agent_bridge.admin_resolver import AdminResolver

        monkeypatch.setattr(elevated, "relay_applicable", lambda req: True)
        monkeypatch.setattr(elevated, "ensure_running", lambda: "subtok")

        resolver = self._make_resolver_with_agents()
        admin = AdminResolver(resolver)
        resolver.register_namespace_resolver(admin)

        target = await resolver.resolve_async("admin:local-agent")
        assert target.type == "command"
        assert target.spawn_command[-4:] == [
            "ws://127.0.0.1:9281/acp/local-agent",
            "--token", "subtok", "--stdio",
        ]


class TestElevatedRelayRouting:
    """Cap 2 Slice 3: a bare requires_admin agent routes to the sub-daemon."""

    def _resolver(self):
        agents = {
            "SPO.Core": AgentConfig(
                name="SPO.Core",
                project="SPO.Core",
                description="Elevated enlistment agent",
                requires_admin=True,
                auto_discovered=True,
            ),
            "plain": AgentConfig(name="plain", project="p", description="plain"),
        }
        return AgentResolver(agents, {})

    @pytest.mark.asyncio
    async def test_bare_elevated_agent_routes_to_relay(self, monkeypatch):
        from agent_bridge import elevated

        monkeypatch.setattr(elevated, "relay_applicable", lambda req: bool(req))
        monkeypatch.setattr(elevated, "ensure_running", lambda: "subtok")

        target = await self._resolver().resolve_async("SPO.Core")

        assert target.type == "command"
        assert target.project == "SPO.Core"
        assert target.spawn_command[1:] == [
            "-m", "agent_bridge", "acp-connect",
            "ws://127.0.0.1:9281/acp/SPO.Core", "--token", "subtok", "--stdio",
        ]

    @pytest.mark.asyncio
    async def test_bare_elevated_agent_local_when_not_applicable(self, monkeypatch):
        """When relay is not applicable (e.g. already elevated / non-Windows),
        the elevated agent resolves locally instead of relaying."""
        from agent_bridge import elevated

        monkeypatch.setattr(elevated, "relay_applicable", lambda req: False)

        def _boom():
            raise AssertionError("ensure_running must not be called")

        monkeypatch.setattr(elevated, "ensure_running", _boom)

        target = await self._resolver().resolve_async("SPO.Core")

        assert target.type == "local"
        assert target.project == "SPO.Core"

    @pytest.mark.asyncio
    async def test_non_elevated_agent_never_relays(self, monkeypatch):
        from agent_bridge import elevated

        # relay_applicable would say yes for requires_admin, but this agent
        # is not requires_admin, so the relay branch must be skipped entirely.
        monkeypatch.setattr(elevated, "relay_applicable", lambda req: True)

        def _boom():
            raise AssertionError("ensure_running must not be called")

        monkeypatch.setattr(elevated, "ensure_running", _boom)

        target = await self._resolver().resolve_async("plain")

        assert target.type == "local"


class TestElevatedDiscovery:
    """projects.yaml `elevated: true` (what register --elevated writes) maps
    to requires_admin so routing can find it."""

    def test_discover_honors_elevated_key(self, tmp_path, monkeypatch):
        projects = tmp_path / "projects.yaml"
        projects.write_text(
            "projects:\n"
            "  SPO.Core:\n"
            "    anchor: 'D:/Git/SPO'\n"
            "    base_repo: true\n"
            "    elevated: true\n"
            "  Plain:\n"
            "    anchor: 'D:/Git/Plain'\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects))
        discovered = discover_local_agents()
        assert discovered["SPO.Core"].requires_admin is True
        assert discovered["Plain"].requires_admin is False


# -- Plugin injection contract (related-repo plugins) -------------------------


class _PluginAwareResolver:
    """Namespace resolver that records the extra_plugins it was resolved with."""

    def __init__(self, prefix_val: str = "pl"):
        self._prefix = prefix_val
        self.seen_extra: object = "UNSET"

    @property
    def prefix(self) -> str:
        return self._prefix

    async def resolve(
        self, name: str, *, extra_plugins: "list[PluginRef]" = ()
    ) -> SpawnTarget:
        self.seen_extra = list(extra_plugins)
        return SpawnTarget(type="command", spawn_command=["echo", name])

    async def list(self):
        return []

    async def ensure_ready(self, name: str) -> None:
        return None


class TestPluginInjectionContract:
    """agent-bridge decides related-repo plugins; resolvers fold them."""

    def test_pluginref_defaults(self):
        ref = PluginRef("odsp-web-codespace@dev-tmichon")
        assert ref.source == "odsp-web-codespace@dev-tmichon"
        assert ref.enable is True
        assert PluginRef("x", enable=False).enable is False

    @pytest.mark.asyncio
    async def test_no_extra_plugins_by_default(self):
        # Default sourcing returns [] -> resolver is called WITHOUT extra_plugins
        # (so resolvers that never adopted the kwarg keep working).
        r = AgentResolver({}, {})
        pr = _PluginAwareResolver()
        r.register_namespace_resolver(pr)
        await r.resolve_async("pl:agent")
        assert pr.seen_extra == []

    @pytest.mark.asyncio
    async def test_extra_plugins_forwarded_when_present(self, monkeypatch):
        r = AgentResolver({}, {})
        pr = _PluginAwareResolver()
        r.register_namespace_resolver(pr)
        refs = [PluginRef("a@m"), PluginRef("b@m", enable=False)]

        async def _fake(resolver, name):
            return refs

        monkeypatch.setattr(r, "_related_plugins_for", _fake)
        await r.resolve_async("pl:agent")
        assert pr.seen_extra == refs

    @pytest.mark.asyncio
    async def test_legacy_resolver_without_kwarg_still_works(self):
        # _MockResolver.resolve has no extra_plugins kwarg; with empty sourcing
        # (the default) it must resolve fine.
        r = AgentResolver({}, {})
        r.register_namespace_resolver(_MockResolver("legacy"))
        target = await r.resolve_async("legacy:my-agent")
        assert target.spawn_command == ["echo", "my-agent"]

    @pytest.mark.asyncio
    async def test_target_repo_drives_related_sourcing(self, monkeypatch):
        # A resolver that reports a target_repo -> bridge sources related-repo
        # plugins for that repo and forwards them as extra_plugins.
        import agent_bridge.related_plugins as rp

        captured = {}

        class _RepoResolver(_PluginAwareResolver):
            async def target_repo(self, name: str):
                return "org/some-codespaces"

        refs = [PluginRef("p@m")]

        def _fake_source(repo, anchors=None):
            captured["repo"] = repo
            return refs

        monkeypatch.setattr(rp, "related_plugins_for_repo", _fake_source)
        r = AgentResolver({}, {})
        pr = _RepoResolver("repo")
        r.register_namespace_resolver(pr)
        await r.resolve_async("repo:agent")
        assert captured["repo"] == "org/some-codespaces"
        assert pr.seen_extra == refs

    @pytest.mark.asyncio
    async def test_target_repo_none_means_no_injection(self, monkeypatch):
        # target_repo returning None -> no sourcing, resolver called plainly.
        import agent_bridge.related_plugins as rp

        called = {"n": 0}
        monkeypatch.setattr(
            rp, "related_plugins_for_repo",
            lambda *a, **k: (called.__setitem__("n", called["n"] + 1) or []),
        )
        r = AgentResolver({}, {})
        pr = _PluginAwareResolver("norepo")  # no target_repo hook -> None
        r.register_namespace_resolver(pr)
        await r.resolve_async("norepo:agent")
        assert pr.seen_extra == []
        assert called["n"] == 0  # sourcing not even attempted without a repo


# -- Topology-derived roster (machines x repos x envs) -------------------------

import textwrap

from agent_bridge.agent_registry import (
    derive_topology_agents,
    _short_machine_agent_name,
    _match_machine_shortname,
    _load_related_entries,
)
from agent_bridge.topology import load_control_plane_project


TOPO_MACHINES_DATA = {
    "control_plane": {"project": "dotfiles"},
    "machines": {
        "tmichon-dev6": {
            "display_name": "dev6",
            "ssh": {
                "ready": True,
                "environments": [
                    {"name": "windows", "alias": "tmichon-dev6", "shell": "pwsh"},
                    {"name": "wsl", "alias": "tmichon-dev6-wsl", "shell": "bash"},
                ],
            },
        },
        "tmichon-cloud1": {
            "display_name": "cloud1",
            "ssh": {
                "ready": True,
                "environments": [
                    {"name": "windows", "alias": "tmichon-cloud1", "shell": "pwsh"},
                ],
            },
        },
        "tmichon-book2": {
            "display_name": "book2",
            "ssh": {"ready": False},
        },
    },
}


def _topo_machines():
    return parse_machines_yaml(TOPO_MACHINES_DATA)


class TestShortMachineAgentName:

    def test_windows_is_bare(self):
        m = _topo_machines()["tmichon-dev6"]
        win = next(e for e in m.ssh_environments if e.name == "windows")
        assert _short_machine_agent_name(m, win) == "dev6"

    def test_wsl_suffix(self):
        m = _topo_machines()["tmichon-dev6"]
        wsl = next(e for e in m.ssh_environments if e.name == "wsl")
        assert _short_machine_agent_name(m, wsl) == "dev6-wsl"


class TestMatchMachineShortname:

    def test_by_display_name(self):
        ms = _topo_machines()
        assert _match_machine_shortname(ms, "dev6").key == "tmichon-dev6"
        assert _match_machine_shortname(ms, "cloud1").key == "tmichon-cloud1"

    def test_by_full_key_and_prefix_strip(self):
        ms = _topo_machines()
        assert _match_machine_shortname(ms, "tmichon-dev6").key == "tmichon-dev6"

    def test_unknown_returns_none(self):
        assert _match_machine_shortname(_topo_machines(), "nope") is None


class TestControlPlaneMachineAgents:

    def test_names_and_envs(self):
        ms = _topo_machines()
        agents = derive_topology_agents(ms, "dotfiles", [], None)
        assert set(agents) == {"dev6", "dev6-wsl", "cloud1"}
        assert agents["dev6"].project == "dotfiles"
        assert agents["dev6"].host == "tmichon-dev6"
        assert agents["dev6"].ssh_environment == "windows"
        assert agents["dev6"].derived is True
        assert agents["dev6-wsl"].ssh_environment == "wsl"
        assert agents["cloud1"].host == "tmichon-cloud1"

    def test_book2_has_no_agent(self):
        # No ssh environments -> no control-plane agent.
        agents = derive_topology_agents(_topo_machines(), "dotfiles", [], None)
        assert not any(a.host == "tmichon-book2" for a in agents.values())

    def test_no_project_no_control_plane_agents(self):
        agents = derive_topology_agents(_topo_machines(), None, [], None)
        assert agents == {}


class TestRelatedRemoteAgents:

    def test_remote_related_synthesized(self):
        ms = _topo_machines()
        related = [
            ("odsp-web", ["cloud1"], "agent-bridge"),
            ("SPO.Core", ["dev6"], "agent-bridge"),
            ("skip-me", ["cloud1"], "none"),
        ]
        local = ms["tmichon-dev6"]  # we are "on" dev6
        agents = derive_topology_agents(ms, None, related, local)
        # Remote related repo -> <repo>@<machine>.
        assert "odsp-web@cloud1" in agents
        assert agents["odsp-web@cloud1"].project == "odsp-web"
        assert agents["odsp-web@cloud1"].host == "tmichon-cloud1"
        assert agents["odsp-web@cloud1"].derived is True
        # Local related repo -> skipped (covered by projects.yaml discovery).
        assert "SPO.Core@dev6" not in agents
        # Non-agent-bridge delegate -> skipped.
        assert not any(n.startswith("skip-me") for n in agents)


class TestLoadControlPlaneProject:

    def test_dict_form(self, tmp_path):
        p = tmp_path / "machines.yaml"
        p.write_text("control_plane:\n  project: dotfiles\nmachines: {}\n", encoding="utf-8")
        assert load_control_plane_project(p) == "dotfiles"

    def test_bare_string_form(self, tmp_path):
        p = tmp_path / "machines.yaml"
        p.write_text("control_plane: dotfiles\nmachines: {}\n", encoding="utf-8")
        assert load_control_plane_project(p) == "dotfiles"

    def test_absent(self, tmp_path):
        p = tmp_path / "machines.yaml"
        p.write_text("machines: {}\n", encoding="utf-8")
        assert load_control_plane_project(p) is None


class TestLoadRelatedEntries:

    def test_parses_locus_and_delegate(self, tmp_path):
        d = tmp_path / ".agent-worktrees"
        d.mkdir()
        (d / "related.yaml").write_text(textwrap.dedent("""
            primary: odsp-web
            related:
              SPO.Core:
                locus: { machines: [dev6, cloud1] }
                delegate: { via: agent-bridge }
              PushChannel:
                locus: { machines: [dev6] }
                delegate: { via: none }
        """), encoding="utf-8")
        entries = dict((n, (m, d_)) for n, m, d_ in _load_related_entries(tmp_path))
        assert entries["SPO.Core"] == (["dev6", "cloud1"], "agent-bridge")
        assert entries["PushChannel"] == (["dev6"], "none")

    def test_missing_file(self, tmp_path):
        assert _load_related_entries(tmp_path) == []


class TestReachability:
    """Only loopback or ssh_ready (machine,env) pairs are emitted (#168)."""

    UNREADY = {
        "control_plane": {"project": "dotfiles"},
        "machines": {
            "tmichon-dev6": {
                "display_name": "dev6",
                "ssh": {
                    "ready": False,
                    "environments": [
                        {"name": "windows", "alias": "tmichon-dev6", "shell": "pwsh"},
                        {"name": "wsl", "alias": "tmichon-dev6-wsl", "shell": "bash"},
                    ],
                },
            },
            "tmichon-cloud1": {
                "display_name": "cloud1",
                "ssh": {
                    "ready": False,
                    "environments": [
                        {"name": "windows", "alias": "tmichon-cloud1", "shell": "pwsh"},
                    ],
                },
            },
        },
    }

    def test_unreachable_remote_skipped_but_loopback_kept(self):
        ms = parse_machines_yaml(self.UNREADY)
        local = ms["tmichon-dev6"]
        # We are on dev6 (windows). Nothing is ssh_ready.
        agents = derive_topology_agents(ms, "dotfiles", [], local, "windows")
        # Local same-platform env -> loopback -> kept.
        assert "dev6" in agents
        # Cross-env (wsl) on the local box needs SSH -> unreachable -> skipped.
        assert "dev6-wsl" not in agents
        # Remote, not ssh_ready -> unreachable -> skipped.
        assert "cloud1" not in agents

    def test_all_skipped_without_local_machine_when_unready(self):
        ms = parse_machines_yaml(self.UNREADY)
        # No local machine + nothing ssh_ready -> nothing reachable.
        assert derive_topology_agents(ms, "dotfiles", [], None, "") == {}

    def test_related_remote_requires_ssh_ready(self):
        ms = parse_machines_yaml(self.UNREADY)
        local = ms["tmichon-dev6"]
        related = [("odsp-web", ["cloud1"], "agent-bridge")]
        # cloud1 not ssh_ready -> related-remote agent skipped.
        agents = derive_topology_agents(ms, None, related, local, "windows")
        assert "odsp-web@cloud1" not in agents


class TestSplitRepoVenue:
    def test_no_at_is_bare(self):
        from agent_bridge.agent_registry import _split_repo_venue
        assert _split_repo_venue("dev6") == (None, "dev6")
        assert _split_repo_venue("codespace:foo") == (None, "codespace:foo")

    def test_repo_at_venue(self):
        from agent_bridge.agent_registry import _split_repo_venue
        assert _split_repo_venue("SPO.Core@dev6") == ("SPO.Core", "dev6")

    def test_namespaced_venue(self):
        from agent_bridge.agent_registry import _split_repo_venue
        assert _split_repo_venue("odsp-web@codespace:foo") == ("odsp-web", "codespace:foo")

    def test_empty_side_is_bare(self):
        from agent_bridge.agent_registry import _split_repo_venue
        assert _split_repo_venue("@dev6") == (None, "@dev6")
        assert _split_repo_venue("repo@") == (None, "repo@")


class TestVenueBoundResolve:
    def setup_method(self):
        self.machines = parse_machines_yaml(TOPO_MACHINES_DATA)
        self.agents = {
            "dev6": AgentConfig(
                name="dev6", host="tmichon-dev6", ssh_environment="windows",
                project="dotfiles", derived=True,
            ),
        }

    @pytest.mark.asyncio
    async def test_repo_at_machine_rebinds_project_loopback(self):
        from unittest.mock import patch
        local = self.machines["tmichon-dev6"]
        with patch(
            "agent_bridge.agent_registry._detect_local_machine",
            return_value=(local, "windows"),
        ):
            resolver = AgentResolver(self.agents, self.machines)
            target = await resolver.resolve_async("SPO.Core@dev6")
        # Loopback (dev6 windows == local) -> local spawn running SPO.Core.
        assert target.type == "local"
        assert target.project == "SPO.Core"

    @pytest.mark.asyncio
    async def test_repo_at_machine_default_project_when_bare(self):
        from unittest.mock import patch
        local = self.machines["tmichon-dev6"]
        with patch(
            "agent_bridge.agent_registry._detect_local_machine",
            return_value=(local, "windows"),
        ):
            resolver = AgentResolver(self.agents, self.machines)
            target = await resolver.resolve_async("dev6")
        # Bare venue keeps the control-plane default project.
        assert target.project == "dotfiles"

    @pytest.mark.asyncio
    async def test_cross_repo_to_command_venue_unsupported(self):
        # A namespace resolver whose resolve() has no `repo` kwarg -> a
        # <repo>@<venue> request must raise, not silently launch the default.
        class _NoRepoResolver:
            prefix = "widget"
            async def ensure_ready(self, name): ...
            async def list_agents(self): return []
            async def resolve(self, name, *, extra_plugins=()):
                from agent_bridge.transport import SpawnTarget
                return SpawnTarget(type="command", spawn_command=["x"])
        resolver = AgentResolver({}, {})
        resolver.register_namespace_resolver(_NoRepoResolver())
        with pytest.raises(ValueError, match="not supported"):
            await resolver.resolve_async("dotfiles@widget:thing")


class TestSenderRepoFallback:
    """Bare machine venue -> sender's repo (venue-default-else-sender, #173)."""

    def setup_method(self):
        self.machines = parse_machines_yaml(TOPO_MACHINES_DATA)
        self.agents = {
            "dev6": AgentConfig(
                name="dev6", host="tmichon-dev6", ssh_environment="windows",
                project="dotfiles", derived=True,
            ),
            "SPO.Core": AgentConfig(
                name="SPO.Core", project="SPO.Core", auto_discovered=True,
            ),
        }

    def _resolver(self):
        from unittest.mock import patch
        local = self.machines["tmichon-dev6"]
        return patch(
            "agent_bridge.agent_registry._detect_local_machine",
            return_value=(local, "windows"),
        )

    @pytest.mark.asyncio
    async def test_bare_machine_uses_sender_repo(self):
        with self._resolver():
            r = AgentResolver(self.agents, self.machines)
            target = await r.resolve_async("dev6", sender_repo="SPO.Core")
        assert target.type == "local"
        assert target.project == "SPO.Core"  # sender repo, not the dotfiles default

    @pytest.mark.asyncio
    async def test_bare_machine_no_sender_keeps_default(self):
        with self._resolver():
            r = AgentResolver(self.agents, self.machines)
            target = await r.resolve_async("dev6")
        assert target.project == "dotfiles"

    @pytest.mark.asyncio
    async def test_sender_equal_default_is_noop(self):
        with self._resolver():
            r = AgentResolver(self.agents, self.machines)
            target = await r.resolve_async("dev6", sender_repo="dotfiles")
        assert target.project == "dotfiles"

    @pytest.mark.asyncio
    async def test_sender_repo_does_not_override_project_agent(self):
        # A bare project agent (auto-discovered, not a machine venue) is NOT
        # a venue -- the sender repo must not rebind it.
        with self._resolver():
            r = AgentResolver(self.agents, self.machines)
            target = await r.resolve_async("SPO.Core", sender_repo="whatever")
        assert target.project == "SPO.Core"


class TestResolveRepoRemote:
    """#174: resolve a logical repo name -> git remote from the repos registry."""

    def _write(self, tmp_path, monkeypatch, body):
        p = tmp_path / "repos.yaml"
        p.write_text(body, encoding="utf-8")
        monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(p))

    def test_reads_remote_from_registry(self, tmp_path, monkeypatch):
        from agent_bridge.agent_registry import resolve_repo_remote
        self._write(
            tmp_path, monkeypatch,
            "repos:\n  dev.tmichon:\n    remote: https://x/dev.tmichon\n",
        )
        assert resolve_repo_remote("dev.tmichon") == "https://x/dev.tmichon"

    def test_basename_fallback_is_case_insensitive(self, tmp_path, monkeypatch):
        from agent_bridge.agent_registry import resolve_repo_remote
        self._write(
            tmp_path, monkeypatch,
            "repos:\n  onedrive/Dev.Tmichon:\n    remote: https://x/d\n",
        )
        assert resolve_repo_remote("dev.tmichon") == "https://x/d"

    def test_unknown_repo_is_none(self, tmp_path, monkeypatch):
        from agent_bridge.agent_registry import resolve_repo_remote
        self._write(tmp_path, monkeypatch, "repos:\n  other:\n    remote: https://x\n")
        assert resolve_repo_remote("nope") is None

    def test_missing_remote_field_is_none(self, tmp_path, monkeypatch):
        from agent_bridge.agent_registry import resolve_repo_remote
        self._write(tmp_path, monkeypatch, "repos:\n  x:\n    class: worktree\n")
        assert resolve_repo_remote("x") is None

    def test_missing_registry_is_none(self, tmp_path, monkeypatch):
        from agent_bridge.agent_registry import resolve_repo_remote
        monkeypatch.setenv(
            "AGENT_WORKTREES_REPOS_YAML", str(tmp_path / "absent.yaml")
        )
        assert resolve_repo_remote("x") is None


class _RepoRemoteAwareResolver:
    """Codespace-like resolver that records repo + repo_remote it receives."""

    def __init__(self, prefix_val="cs"):
        self._prefix = prefix_val
        self.seen: dict = {}

    @property
    def prefix(self) -> str:
        return self._prefix

    async def resolve(self, name, *, extra_plugins=(), repo=None, repo_remote=None):
        self.seen = {"name": name, "repo": repo, "repo_remote": repo_remote}
        return SpawnTarget(type="command", spawn_command=["echo", name])

    async def list(self):
        return []

    async def ensure_ready(self, name):
        return None


class _RepoOnlyResolver(_RepoRemoteAwareResolver):
    """Older provider: accepts repo but NOT repo_remote."""

    async def resolve(self, name, *, extra_plugins=(), repo=None):
        self.seen = {"name": name, "repo": repo}
        return SpawnTarget(type="command", spawn_command=["echo", name])


class TestRepoRemoteThreading:
    """#174: agent-bridge threads repo_remote into <repo>@<venue> dispatch."""

    @pytest.mark.asyncio
    async def test_repo_remote_forwarded_to_resolver(self, monkeypatch):
        monkeypatch.setattr(
            "agent_bridge.agent_registry.resolve_repo_remote",
            lambda repo: (
                "https://x/dev.tmichon" if repo == "dev.tmichon" else None
            ),
        )
        r = AgentResolver({}, {})
        pr = _RepoRemoteAwareResolver("cs")
        r.register_namespace_resolver(pr)
        await r.resolve_async("dev.tmichon@cs:mycs")
        assert pr.seen["repo"] == "dev.tmichon"
        assert pr.seen["repo_remote"] == "https://x/dev.tmichon"
        assert pr.seen["name"] == "mycs"

    @pytest.mark.asyncio
    async def test_repo_only_resolver_still_works(self, monkeypatch):
        # repo_remote absent from the resolver signature -> silently dropped
        # (back-compat), while repo is still honored (no raise).
        monkeypatch.setattr(
            "agent_bridge.agent_registry.resolve_repo_remote",
            lambda repo: "https://x/dev.tmichon",
        )
        r = AgentResolver({}, {})
        pr = _RepoOnlyResolver("cs2")
        r.register_namespace_resolver(pr)
        await r.resolve_async("dev.tmichon@cs2:mycs")
        assert pr.seen == {"name": "mycs", "repo": "dev.tmichon"}
