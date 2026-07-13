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

    def test_harness_defaults_off(self, config_dir):
        # harness is opt-in: unset unless a defaults.harness_repo is declared,
        # so by default no harness is placed on a venue.
        config = load_merged_config()
        assert config.harness_repo is None
        assert config.harness_dir == "/workspaces/harness"

    def test_harness_repo_and_dir_from_defaults(self, config_dir):
        repo = config_dir / "harness-repo"
        _write_codespaces_yaml(repo, {
            "defaults": {
                "harness_repo": "acme/harness",
                "harness_dir": "/workspaces/harness",
            },
        })
        save_adopted_repos([AdoptedRepo(path=repo)])
        config = load_merged_config()
        assert config.harness_repo == "acme/harness"
        assert config.harness_dir == "/workspaces/harness"
        # decoupled from the dotfiles shim, which stays unset here
        assert config.dotfiles_repo is None

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
            'cd "${CODESPACE_VSCODE_FOLDER:-${WORKING_DIRECTORY:-${VM_REPO_PATH:-.}}}" '
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


class TestPerRepoWorkspaceFolder:
    """Per-CodeSpace-repo workspace folder resolution (the related-repo link).

    A CodeSpaces repo (e.g. ``org/odsp-web-codespaces``) often differs from the
    product checkout it hosts (``/workspaces/odsp-web``). These verify that the
    folder resolves per repo rather than from a single global default.
    """

    def _config(self, **repo_kwargs) -> CodespacesConfig:
        from agent_codespaces.config import RepoConfig

        return CodespacesConfig(
            repos={"org/odsp-web-codespaces": RepoConfig(**repo_kwargs)}
        )

    def test_workspace_repo_derives_folder(self):
        """``workspace_repo`` derives ``/workspaces/<basename>``."""
        config = self._config(workspace_repo="odsp-web")
        assert config.workspace_folder_for("org/odsp-web-codespaces") == (
            "/workspaces/odsp-web"
        )
        assert config.effective_acp_command_for("org/odsp-web-codespaces") == (
            "cd /workspaces/odsp-web && copilot --acp --stdio --allow-all-tools"
        )

    def test_workspace_repo_with_owner_is_basenamed(self):
        config = self._config(workspace_repo="odsp-microsoft/odsp-web")
        assert config.workspace_folder_for("org/odsp-web-codespaces") == (
            "/workspaces/odsp-web"
        )

    def test_explicit_workspace_folder_overrides_workspace_repo(self):
        config = self._config(
            workspace_repo="odsp-web", workspace_folder="/custom/checkout"
        )
        assert config.workspace_folder_for("org/odsp-web-codespaces") == (
            "/custom/checkout"
        )

    def test_per_repo_overrides_global_default(self):
        config = CodespacesConfig(workspace_folder="/workspaces/global")
        from agent_codespaces.config import RepoConfig

        config.repos["org/odsp-web-codespaces"] = RepoConfig(
            workspace_repo="odsp-web"
        )
        # The mapped repo gets its own folder...
        assert config.workspace_folder_for("org/odsp-web-codespaces") == (
            "/workspaces/odsp-web"
        )
        # ...while an unmapped repo falls back to the global default.
        assert config.workspace_folder_for("org/other") == "/workspaces/global"

    def test_unknown_repo_falls_back_to_global(self):
        config = CodespacesConfig(workspace_folder="/workspaces/global")
        assert config.workspace_folder_for(None) == "/workspaces/global"
        assert config.workspace_folder_for("org/unknown") == "/workspaces/global"

    def test_no_mapping_resolves_remote_workspace(self):
        config = self._config(workspace_repo="odsp-web")
        # A repo with no per-repo entry and no global default → remote-resolved.
        cmd = config.effective_acp_command_for("org/unmapped")
        assert (
            "CODESPACE_VSCODE_FOLDER" in cmd
            and "WORKING_DIRECTORY" in cmd
            and "VM_REPO_PATH" in cmd
        )

    def test_global_acp_command_still_overrides(self):
        config = self._config(workspace_repo="odsp-web")
        config.acp_command = "custom --acp"
        assert config.effective_acp_command_for("org/odsp-web-codespaces") == (
            "custom --acp"
        )

    def test_merged_from_yaml(self, config_dir):
        repo = config_dir / "repo"
        _write_codespaces_yaml(repo, {
            "repos": {
                "org/odsp-web-codespaces": {
                    "machine_type": "largePremiumLinux256gb",
                    "workspace_repo": "odsp-web",
                },
            },
        })
        save_adopted_repos([AdoptedRepo(path=repo)])
        config = load_merged_config()
        rc = config.repos["org/odsp-web-codespaces"]
        assert rc.workspace_repo == "odsp-web"
        assert config.effective_acp_command_for("org/odsp-web-codespaces") == (
            "cd /workspaces/odsp-web && copilot --acp --stdio --allow-all-tools"
        )


class TestCrossRepoRequestFolder:
    """#174: <repo>@<codespace> repo-layout convention.

    A requested repo lands at ``/workspaces/<basename>`` (clone-if-missing),
    except the CodeSpace's own product (already checked out) and the account
    dotfiles repo (owned by the universal bootstrap).
    """

    _COPILOT = "copilot --acp --stdio --allow-all-tools"
    _CS = "odsp-microsoft/odsp-web-codespaces"

    def test_own_product_is_prepopulated_no_clone(self):
        config = CodespacesConfig()
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "odsp-web"
        )
        assert folder == "/workspaces/odsp-web"
        assert prepopulated is True

    def test_own_product_command_has_no_clone(self):
        config = CodespacesConfig()
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="odsp-web",
            repo_remote="https://github.com/odsp-microsoft/odsp-web",
        )
        assert cmd == f"cd /workspaces/odsp-web && {self._COPILOT}"
        assert "git clone" not in cmd

    def test_dotfiles_maps_to_persisted_dir(self):
        config = CodespacesConfig(dotfiles_repo="tmichon_microsoft/dotfiles")
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "dotfiles"
        )
        assert folder == "/workspaces/.codespaces/.persistedshare/dotfiles"
        assert prepopulated is True

    def test_dotfiles_command_has_no_clone(self):
        config = CodespacesConfig(dotfiles_repo="tmichon_microsoft/dotfiles")
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="tmichon_microsoft/dotfiles",
            repo_remote="https://github.com/tmichon_microsoft/dotfiles",
        )
        assert cmd == (
            "cd /workspaces/.codespaces/.persistedshare/dotfiles "
            f"&& {self._COPILOT}"
        )
        assert "git clone" not in cmd

    def test_other_repo_clone_if_missing(self):
        config = CodespacesConfig()
        remote = "https://onedrive.visualstudio.com/onedrive/_git/dev.tmichon"
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "dev.tmichon"
        )
        assert folder == "/workspaces/dev.tmichon"
        assert prepopulated is False
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="dev.tmichon", repo_remote=remote,
        )
        assert cmd == (
            f"[ -d /workspaces/dev.tmichon/.git ] || "
            f"git clone {remote} /workspaces/dev.tmichon; "
            f"cd /workspaces/dev.tmichon && {self._COPILOT}"
        )

    def test_other_repo_owner_prefix_basenamed(self):
        config = CodespacesConfig()
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "onedrive/dev.tmichon"
        )
        assert folder == "/workspaces/dev.tmichon"
        assert prepopulated is False

    def test_other_repo_no_remote_falls_to_plain_cd(self):
        """No known remote: cd only (fails loudly on the CodeSpace if absent)."""
        config = CodespacesConfig()
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="dev.tmichon", repo_remote=None,
        )
        assert cmd == f"cd /workspaces/dev.tmichon && {self._COPILOT}"
        assert "git clone" not in cmd

    def test_bare_request_unchanged(self):
        """requested_repo=None behaves exactly as the legacy bare path."""
        config = CodespacesConfig()
        from agent_codespaces.config import RepoConfig
        config.repos[self._CS] = RepoConfig(workspace_repo="odsp-web")
        assert config.effective_acp_command_for(self._CS) == (
            f"cd /workspaces/odsp-web && {self._COPILOT}"
        )
