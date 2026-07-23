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
        # Should include 2 Available + 1 Shutdown (connectable states)
        assert len(agents) == 3

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
        # Should contain ssh --stdio and --remote-cmd with acp_command
        assert "--stdio" in cmd
        assert "fuzzy-adventure-abc123" in cmd
        assert "--remote-cmd" in cmd
        # Default (no workspace_folder, no acp_command) resolves the workspace
        # on the remote then launches copilot (#33). acp_command is a single
        # element of the arg list, so scan the joined command.
        joined = " ".join(cmd)
        assert "copilot --acp --stdio" in joined
        assert "CODESPACE_VSCODE_FOLDER" in joined and "VM_REPO_PATH" in joined

    def test_codespace_metadata_block(self):
        """Each agent carries structured codespace metadata (#177) for the
        Session-Host dispatch path, matching its spawn_command's acp_command."""
        agents = build_agent_configs(SAMPLE_CODESPACES)
        first = agents[0]
        meta = first["codespace"]
        assert meta["name"] == "fuzzy-adventure-abc123"
        assert meta["repo"] == "user/my-repo"
        # acp_command in the metadata equals the --remote-cmd in spawn_command
        cmd = first["spawn_command"]
        rc = cmd[cmd.index("--remote-cmd") + 1]
        assert meta["acp_command"] == rc
        assert "workspace_folder" in meta

    def test_spawn_command_with_workspace_folder(self):
        """workspace_folder produces a 'cd <path> && copilot' command."""
        from agent_codespaces.config import CodespacesConfig

        mock_config = CodespacesConfig(workspace_folder="/workspaces/my-repo")
        with patch(
            "agent_codespaces.config.load_merged_config",
            return_value=mock_config,
        ):
            agents = build_agent_configs(SAMPLE_CODESPACES)
        cmd = agents[0]["spawn_command"]
        assert "cd /workspaces/my-repo && copilot --acp --stdio" in " ".join(cmd)

    def test_spawn_command_with_explicit_acp_command(self):
        """Explicit acp_command overrides workspace_folder."""
        from agent_codespaces.config import CodespacesConfig

        mock_config = CodespacesConfig(
            workspace_folder="/workspaces/my-repo",
            acp_command="custom-copilot --acp --stdio",
        )
        with patch(
            "agent_codespaces.config.load_merged_config",
            return_value=mock_config,
        ):
            agents = build_agent_configs(SAMPLE_CODESPACES)
        cmd = agents[0]["spawn_command"]
        assert "custom-copilot --acp --stdio" in cmd

    def test_spawn_command_uses_module_not_binstub(self):
        """Spawn via ``python -m agent_codespaces``, never the .cmd binstub,
        so agent-bridge does not route the spawn through cmd.exe (which would
        expand %VAR% tokens in --remote-cmd and mangle the payload)."""
        agents = build_agent_configs(SAMPLE_CODESPACES)
        cmd = agents[0]["spawn_command"]
        assert cmd[1:3] == ["-m", "agent_codespaces"]
        assert not cmd[0].lower().endswith((".cmd", ".bat"))

    def test_spawn_command_preserves_percent_var(self):
        """A %VAR% token in acp_command stays one intact argv element
        (regression: the .cmd binstub + cmd.exe would expand/mangle it)."""
        from agent_codespaces.config import CodespacesConfig

        payload = 'cd "%WORKSPACE%" && copilot --acp --stdio'
        mock_config = CodespacesConfig(acp_command=payload)
        with patch(
            "agent_codespaces.config.load_merged_config",
            return_value=mock_config,
        ):
            agents = build_agent_configs(SAMPLE_CODESPACES)
        cmd = agents[0]["spawn_command"]
        assert payload in cmd
        assert cmd[cmd.index("--remote-cmd") + 1] == payload

    def test_spawn_command_per_repo_workspace_folder(self):
        """Each agent's command uses ITS repo's workspace folder, not a single
        global default. A CodeSpaces repo (org/other-repo) mapped via
        workspace_repo lands in the product checkout; an unmapped one falls
        back to the remote-resolved workspace."""
        from agent_codespaces.config import CodespacesConfig, RepoConfig

        mock_config = CodespacesConfig(
            repos={"org/other-repo": RepoConfig(workspace_repo="example-web")}
        )
        with patch(
            "agent_codespaces.config.load_merged_config",
            return_value=mock_config,
        ):
            agents = build_agent_configs(SAMPLE_CODESPACES)

        by_name = {a["name"]: a for a in agents}
        mapped = " ".join(by_name["cs-shiny-potato-def456"]["spawn_command"])
        assert "cd /workspaces/example-web && copilot --acp --stdio" in mapped

        unmapped = " ".join(by_name["cs-fuzzy-adventure-abc123"]["spawn_command"])
        assert "CODESPACE_VSCODE_FOLDER" in unmapped

    def test_empty_codespace_list(self):
        agents = build_agent_configs([])
        assert agents == []

    def test_all_shutdown_returns_agents(self):
        shutdown_only = [
            CodespaceInfo(
                name="dead", display_name="dead",
                repository="x/y", branch="main",
                state="Shutdown", machine="basic",
            ),
        ]
        agents = build_agent_configs(shutdown_only)
        # Shutdown CodeSpaces are now connectable (auto-started by gh)
        assert len(agents) == 1

    def test_non_connectable_states_excluded(self):
        non_connectable = [
            CodespaceInfo(
                name="starting", display_name="starting",
                repository="x/y", branch="main",
                state="Starting", machine="basic",
            ),
        ]
        agents = build_agent_configs(non_connectable)
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
