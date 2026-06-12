"""Tests for codespaces.yaml config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_codespaces.config import (
    AdoptedRepo,
    CodespacesConfig,
    CredentialSourceConfig,
    CredentialsConfig,
    load_merged_config,
    load_repo_config,
    save_adopted_repos,
    validate_config,
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Set up a temp runtime dir and adopted repo."""
    runtime = tmp_path / ".agent-codespaces"
    runtime.mkdir()
    monkeypatch.setattr("agent_codespaces.config.RUNTIME_DIR", runtime)
    monkeypatch.setattr(
        "agent_codespaces.config.ADOPTED_REPOS_FILE",
        runtime / "adopted-repos.yaml",
    )
    return tmp_path


def _write_codespaces_yaml(repo_dir: Path, data: dict) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "codespaces.yaml").write_text(yaml.safe_dump(data))


class TestLoadRepoConfig:
    def test_loads_existing(self, tmp_path):
        _write_codespaces_yaml(tmp_path, {"defaults": {"machine_type": "big"}})
        result = load_repo_config(tmp_path)
        assert result is not None
        assert result["defaults"]["machine_type"] == "big"

    def test_returns_none_if_missing(self, tmp_path):
        result = load_repo_config(tmp_path)
        assert result is None


class TestAdoptedRepos:
    def test_roundtrip(self, config_dir):
        repos = [
            AdoptedRepo(path=Path("/some/repo"), adopted_at="2026-06-04T00:00:00Z"),
        ]
        save_adopted_repos(repos)

        from agent_codespaces.config import load_adopted_repos
        loaded = load_adopted_repos()
        assert len(loaded) == 1
        assert loaded[0].path == Path("/some/repo")


class TestMergedConfig:
    def test_empty_when_no_adopted(self, config_dir):
        config = load_merged_config()
        assert config.source_paths == []
        assert config.default_machine_type == "largePremiumLinux"

    def test_single_repo(self, config_dir):
        repo = config_dir / "my-repo"
        _write_codespaces_yaml(repo, {
            "defaults": {
                "machine_type": "customMachine",
                "location": "WestUs2",
            },
            "credentials": {
                "relay_port": 9999,
                "sources": {
                    "git-credential": {
                        "enabled": True,
                        "allowed_hosts": ["dev.azure.com"],
                    },
                },
            },
            "repos": {
                "org/my-repo": {
                    "machine_type": "bigLinux",
                },
            },
        })
        save_adopted_repos([AdoptedRepo(path=repo)])
        config = load_merged_config()

        assert config.default_machine_type == "customMachine"
        assert config.default_location == "WestUs2"
        assert config.credentials.relay_port == 9999
        assert "git-credential" in config.credentials.sources
        assert config.credentials.sources["git-credential"].enabled is True
        assert "org/my-repo" in config.repos

    def test_multi_repo_merge(self, config_dir):
        repo1 = config_dir / "repo1"
        repo2 = config_dir / "repo2"
        _write_codespaces_yaml(repo1, {
            "defaults": {"machine_type": "first"},
            "credentials": {
                "sources": {
                    "git-credential": {
                        "enabled": True,
                        "allowed_hosts": ["host-a"],
                    },
                },
            },
            "repos": {"org/shared": {"machine_type": "from-repo1"}},
        })
        _write_codespaces_yaml(repo2, {
            "defaults": {"machine_type": "second"},
            "credentials": {
                "sources": {
                    "git-credential": {
                        "enabled": True,
                        "allowed_hosts": ["host-b"],
                    },
                },
            },
            "repos": {
                "org/shared": {"machine_type": "from-repo2"},
                "org/unique": {"machine_type": "unique"},
            },
        })
        save_adopted_repos([
            AdoptedRepo(path=repo1),
            AdoptedRepo(path=repo2),
        ])
        config = load_merged_config()

        # First wins for defaults
        assert config.default_machine_type == "first"
        # Credential hosts are unioned
        hosts = config.credentials.sources["git-credential"].allowed_hosts
        assert "host-a" in hosts
        assert "host-b" in hosts
        # First wins for repos
        assert config.repos["org/shared"].machine_type == "from-repo1"
        # Unique repos added
        assert "org/unique" in config.repos


class TestValidation:
    def test_valid_config(self):
        config = CodespacesConfig(source_paths=[Path("/repo")])
        issues = validate_config(config)
        assert len(issues) == 0

    def test_no_sources_warning(self):
        config = CodespacesConfig()
        issues = validate_config(config)
        assert any("No adopted repos" in i for i in issues)

    def test_enabled_source_no_hosts(self):
        config = CodespacesConfig(
            source_paths=[Path("/repo")],
            credentials=CredentialsConfig(
                sources={
                    "git-credential": CredentialSourceConfig(
                        enabled=True, allowed_hosts=[]
                    ),
                },
            ),
        )
        issues = validate_config(config)
        assert any("no allowed_hosts" in i for i in issues)


class TestEffectiveAcpCommand:
    def test_bare_default_resolves_workspace_on_remote(self):
        """No workspace_folder / acp_command → cd into the remote-resolved
        workspace (so the session lands in the checkout, not /home/vscode; #33)
        then launch copilot with auto-approve."""
        config = CodespacesConfig()
        assert config.effective_acp_command == (
            'cd "${CODESPACE_VSCODE_FOLDER:-${VM_REPO_PATH:-.}}" '
            "&& copilot --acp --stdio --allow-all-tools"
        )

    def test_workspace_folder_produces_cd_prefix(self):
        config = CodespacesConfig(workspace_folder="/workspaces/my-repo")
        assert config.effective_acp_command == (
            "cd /workspaces/my-repo && copilot --acp --stdio --allow-all-tools"
        )

    def test_explicit_acp_command_wins(self):
        config = CodespacesConfig(
            workspace_folder="/workspaces/my-repo",
            acp_command="custom-launch --acp",
        )
        assert config.effective_acp_command == "custom-launch --acp"

    def test_acp_command_without_workspace_folder(self):
        config = CodespacesConfig(acp_command="copilot -C /tmp --acp --stdio")
        assert config.effective_acp_command == "copilot -C /tmp --acp --stdio"

    def test_workspace_folder_merged_from_yaml(self, config_dir):
        repo = config_dir / "repo"
        _write_codespaces_yaml(repo, {
            "defaults": {"workspace_folder": "/workspaces/my-repo"},
        })
        save_adopted_repos([AdoptedRepo(path=repo)])
        config = load_merged_config()
        assert config.workspace_folder == "/workspaces/my-repo"
        assert "cd /workspaces/my-repo" in config.effective_acp_command
