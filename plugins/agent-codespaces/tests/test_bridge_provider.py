"""Tests for bridge_provider.py -- codespace-to-agent-bridge integration."""

from __future__ import annotations

from unittest.mock import patch

from agent_codespaces.bridge_provider import (
    PROVIDER_NAME,
    build_agent_configs,
)
from agent_codespaces.lifecycle import CodespaceInfo

# -- Test data -----------------------------------------------------------------

SAMPLE_CODESPACES = [
    CodespaceInfo(
        name="fuzzy-adventure-abc123",
        display_name="fuzzy-adventure",
        repository="user/my-repo",
        branch="main",
        state="Available",
        machine="largePremiumLinux",
    ),
    CodespaceInfo(
        name="shiny-potato-def456",
        display_name="shiny-potato",
        repository="org/other-repo",
        branch="feat/test",
        state="Available",
        machine="basicLinux32gb",
    ),
    CodespaceInfo(
        name="stopped-cs-ghi789",
        display_name="stopped-cs",
        repository="user/stopped",
        branch="main",
        state="Shutdown",
        machine="basicLinux32gb",
    ),
]


class TestBuildAgentConfigs:

    def test_only_available_codespaces(self):
        agents = build_agent_configs(SAMPLE_CODESPACES)
        # Should include 2 Available, skip 1 Shutdown
        assert len(agents) == 2

    def test_agent_name_format(self):
        agents = build_agent_configs(SAMPLE_CODESPACES)
        names = [a["name"] for a in agents]
        assert "cs-fuzzy-adventure-abc123" in names
        assert "cs-shiny-potato-def456" in names

    def test_display_name_includes_repo(self):
        agents = build_agent_configs(SAMPLE_CODESPACES)
        first = agents[0]
        assert "my-repo" in first["display_name"]

    def test_description_includes_repo_and_branch(self):
        agents = build_agent_configs(SAMPLE_CODESPACES)
        first = agents[0]
        assert "user/my-repo" in first["description"]
        assert "main" in first["description"]

    def test_spawn_command_structure(self):
        agents = build_agent_configs(SAMPLE_CODESPACES)
        cmd = agents[0]["spawn_command"]
        # Should contain ssh --stdio and --remote-cmd
        assert "--stdio" in cmd
        assert "fuzzy-adventure-abc123" in cmd
        assert "--remote-cmd" in cmd
        assert "copilot --acp --stdio" in cmd

    def test_empty_codespace_list(self):
        agents = build_agent_configs([])
        assert agents == []

    def test_all_shutdown_returns_empty(self):
        shutdown_only = [
            CodespaceInfo(
                name="dead", display_name="dead",
                repository="x/y", branch="main",
                state="Shutdown", machine="basic",
            ),
        ]
        agents = build_agent_configs(shutdown_only)
        assert agents == []

    def test_icon_is_codespace(self):
        agents = build_agent_configs(SAMPLE_CODESPACES)
        assert all(a["icon"] == "codespace" for a in agents)

    def test_provider_name_constant(self):
        assert PROVIDER_NAME == "codespaces"


class TestBridgeProviderCLI:
    """Test the CLI bridge subcommand wiring."""

    def test_bridge_register_calls_provider(self):
        from agent_codespaces.__main__ import main

        with patch(
            "agent_codespaces.bridge_provider.register_with_bridge"
        ) as mock_reg:
            mock_reg.return_value = {"agents": 2, "ttl": 300}
            rc = main(["bridge", "register", "--ttl", "600"])
            assert rc == 0
            mock_reg.assert_called_once()
            call_kwargs = mock_reg.call_args
            assert call_kwargs[1]["ttl"] == 600.0

    def test_bridge_unregister_calls_provider(self):
        from agent_codespaces.__main__ import main

        with patch(
            "agent_codespaces.bridge_provider.unregister_from_bridge"
        ) as mock_unreg:
            mock_unreg.return_value = {"status": "unregistered"}
            rc = main(["bridge", "unregister"])
            assert rc == 0
            mock_unreg.assert_called_once()

    def test_bridge_status_not_registered(self):
        from agent_codespaces.__main__ import main

        with patch(
            "agent_codespaces.bridge_provider.get_bridge_status"
        ) as mock_status:
            mock_status.return_value = None
            rc = main(["bridge", "status"])
            assert rc == 0

    def test_bridge_status_active(self):
        from agent_codespaces.__main__ import main

        with patch(
            "agent_codespaces.bridge_provider.get_bridge_status"
        ) as mock_status:
            mock_status.return_value = {
                "name": "codespaces",
                "agents": 3,
                "active_agents": 2,
                "ttl": 300,
                "age": 42,
                "expired": False,
                "conflicts": ["lambda-core"],
            }
            rc = main(["bridge", "status"])
            assert rc == 0

    def test_bridge_no_subcommand(self):
        from agent_codespaces.__main__ import main
        rc = main(["bridge"])
        assert rc == 1
