"""Tests for CodeSpace config loading and validation (.agent-codespaces/config.yaml)."""

from __future__ import annotations

import json
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
    repo_copilot_settings,
    save_adopted_repos,
    validate_config,
)


def test_repo_copilot_settings_merges_marketplaces_and_enablement(tmp_path):
    repo = tmp_path / "control-plane"
    settings_dir = repo / ".github" / "copilot"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example-marketplace": {"source": {"source": "git", "url": "u"}},
        },
        "enabledPlugins": {"example-web-harness@example-marketplace": True, "off@m": False},
    }), encoding="utf-8")
    # settings.local.json overrides enabledPlugins (last-wins within a repo).
    (settings_dir / "settings.local.json").write_text(json.dumps({
        "enabledPlugins": {"off@m": True},
    }), encoding="utf-8")

    merged = repo_copilot_settings([repo])
    assert "example-marketplace" in merged["extraKnownMarketplaces"]
    assert merged["enabledPlugins"]["example-web-harness@example-marketplace"] is True
    assert merged["enabledPlugins"]["off@m"] is True  # local override wins


def test_repo_copilot_settings_missing_is_empty(tmp_path):
    merged = repo_copilot_settings([tmp_path / "does-not-exist"])
    assert merged == {"extraKnownMarketplaces": {}, "enabledPlugins": {}}


def test_repo_copilot_settings_reads_claude_convention(tmp_path):
    # A repo that declares its plugins in .claude/settings.json (Claude
    # convention) instead of .github/copilot/settings.json.
    repo = tmp_path / "claude-repo"
    claude = repo / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "spo-core-plugins": {"source": {"source": "directory", "path": "./.ai"}},
        },
        "enabledPlugins": {"cps@spo-core-plugins": True},
    }), encoding="utf-8")

    merged = repo_copilot_settings([repo])
    assert "spo-core-plugins" in merged["extraKnownMarketplaces"]
    assert merged["enabledPlugins"]["cps@spo-core-plugins"] is True


def test_repo_copilot_settings_native_wins_over_claude(tmp_path):
    # Same key declared in both conventions -> Copilot-native wins.
    repo = tmp_path / "both"
    native = repo / ".github" / "copilot"
    native.mkdir(parents=True)
    claude = repo / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {"mp": {"source": {"source": "git", "url": "claude"}}},
        "enabledPlugins": {"cap@mp": False},
    }), encoding="utf-8")
    (native / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {"mp": {"source": {"source": "git", "url": "native"}}},
        "enabledPlugins": {"cap@mp": True},
    }), encoding="utf-8")

    merged = repo_copilot_settings([repo])
    assert merged["extraKnownMarketplaces"]["mp"]["source"]["url"] == "native"
    assert merged["enabledPlugins"]["cap@mp"] is True  # native wins


def test_repo_copilot_settings_claude_local_overrides_claude_base(tmp_path):
    repo = tmp_path / "claude-local"
    claude = repo / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"cap@mp": False},
    }), encoding="utf-8")
    (claude / "settings.local.json").write_text(json.dumps({
        "enabledPlugins": {"cap@mp": True},
    }), encoding="utf-8")

    merged = repo_copilot_settings([repo])
    assert merged["enabledPlugins"]["cap@mp"] is True


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

    def test_loads_canonical_in_repo(self, tmp_path):
        cfg_dir = tmp_path / ".agent-codespaces"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(
            yaml.safe_dump({"defaults": {"machine_type": "canon"}})
        )
        result = load_repo_config(tmp_path)
        assert result is not None
        assert result["defaults"]["machine_type"] == "canon"

    def test_canonical_wins_over_legacy(self, tmp_path):
        _write_codespaces_yaml(tmp_path, {"defaults": {"machine_type": "legacy"}})
        cfg_dir = tmp_path / ".agent-codespaces"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(
            yaml.safe_dump({"defaults": {"machine_type": "canon"}})
        )
        from agent_codespaces.config import repo_config_path
        assert repo_config_path(tmp_path) == cfg_dir / "config.yaml"
        assert load_repo_config(tmp_path)["defaults"]["machine_type"] == "canon"


class TestCwdAutoDiscovery:
    def test_cwd_repo_config_merged_when_unadopted(
        self, config_dir, monkeypatch
    ):
        # A repo carrying a canonical config, NOT adopted, is picked up from cwd.
        repo = config_dir / "product"
        cfg_dir = repo / ".agent-codespaces"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.safe_dump({"defaults": {"machine_type": "from-cwd"}})
        )
        monkeypatch.setattr(
            "agent_codespaces.config.cwd_repo_root", lambda: repo
        )
        cfg = load_merged_config()
        assert cfg.default_machine_type == "from-cwd"
        assert repo in cfg.source_paths

    def test_include_cwd_false_ignores_cwd(self, config_dir, monkeypatch):
        repo = config_dir / "product"
        cfg_dir = repo / ".agent-codespaces"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.safe_dump({"defaults": {"machine_type": "from-cwd"}})
        )
        monkeypatch.setattr(
            "agent_codespaces.config.cwd_repo_root", lambda: repo
        )
        cfg = load_merged_config(include_cwd=False)
        assert cfg.source_paths == []

    def test_convention_repo_zero_config(self, config_dir, monkeypatch):
        # A repo with NO config yields an empty merged config (pure convention).
        monkeypatch.setattr(
            "agent_codespaces.config.cwd_repo_root",
            lambda: config_dir / "no-config-repo",
        )
        cfg = load_merged_config()
        assert cfg.source_paths == []
        # Convention defaults still apply.
        assert cfg.default_machine_type == "largePremiumLinux"


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

    def test_harness_repo_from_defaults(self, config_dir):
        repo = config_dir / "harness-repo"
        _write_codespaces_yaml(repo, {
            "defaults": {
                "harness_repo": "acme/harness",
            },
        })
        save_adopted_repos([AdoptedRepo(path=repo)])
        config = load_merged_config()
        assert config.harness_repo == "acme/harness"
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

    def test_multi_repo_merge_unions_allowed_resources(self, config_dir):
        repo1 = config_dir / "repo1"
        repo2 = config_dir / "repo2"
        _write_codespaces_yaml(repo1, {
            "credentials": {
                "sources": {
                    "az-login": {
                        "enabled": True,
                        "allowed_resources": ["499b84ac-1321-427f-aa17-267ca6975798"],
                    },
                },
            },
        })
        _write_codespaces_yaml(repo2, {
            "credentials": {
                "sources": {
                    "az-login": {
                        "enabled": True,
                        "allowed_resources": ["https://storage.azure.com/"],
                    },
                },
            },
        })
        save_adopted_repos([
            AdoptedRepo(path=repo1),
            AdoptedRepo(path=repo2),
        ])

        config = load_merged_config()
        resources = config.credentials.sources["az-login"].allowed_resources
        assert "499b84ac-1321-427f-aa17-267ca6975798" in resources
        assert "https://storage.azure.com/" in resources


class TestKnowledgeOverlay:
    """codespaces.yaml knowledge-overlay config-graft (citadel E1e, #947)."""

    def test_stateless_harness_grafts_knowledge_codespaces_yaml(
        self, config_dir, monkeypatch
    ):
        # An adopted harness with NO codespaces.yaml of its own reads the bound
        # knowledge repo's codespaces.yaml via the knowledge overlay.
        harness = config_dir / "citadel-harness"
        harness.mkdir()
        knowledge = config_dir / "citadel-knowledge"
        _write_codespaces_yaml(knowledge, {
            "defaults": {"machine_type": "knowledgeMachine"},
            "repos": {"org/kn-web-codespaces": {"workspace_repo": "kn-web"}},
        })
        monkeypatch.setattr(
            "agent_codespaces.config._state_root_config_dir",
            lambda repo: knowledge if Path(repo) == harness else None)
        save_adopted_repos([AdoptedRepo(path=harness)])
        config = load_merged_config()
        assert config.default_machine_type == "knowledgeMachine"
        assert "org/kn-web-codespaces" in config.repos
        # source_paths stays the HARNESS (generic plugin settings sourced there),
        # NOT the knowledge repo.
        assert config.source_paths == [harness]

    def test_repo_with_own_codespaces_yaml_not_grafted(self, config_dir, monkeypatch):
        repo = config_dir / "self-hosted"
        _write_codespaces_yaml(repo, {"defaults": {"machine_type": "ownMachine"}})
        # A repo carrying its own codespaces.yaml must NOT consult the overlay.
        called = {"n": 0}
        monkeypatch.setattr(
            "agent_codespaces.config._state_root_config_dir",
            lambda repo: called.__setitem__("n", called["n"] + 1) or None)
        save_adopted_repos([AdoptedRepo(path=repo)])
        config = load_merged_config()
        assert config.default_machine_type == "ownMachine"
        assert called["n"] == 0  # overlay never consulted

    def test_provision_src_resolves_under_knowledge(self, config_dir, monkeypatch):
        harness = config_dir / "harness"
        harness.mkdir()
        knowledge = config_dir / "knowledge"
        _write_codespaces_yaml(knowledge, {
            "provision": {"files": [{"src": "env/snippet.sh", "dest": "~/x.sh"}]},
        })
        monkeypatch.setattr(
            "agent_codespaces.config._state_root_config_dir",
            lambda repo: knowledge)
        save_adopted_repos([AdoptedRepo(path=harness)])
        config = load_merged_config()
        assert config.provision.files
        # repo_dir is the KNOWLEDGE dir so src resolves where the file lives.
        assert config.provision.files[0].repo_dir == knowledge


class TestStateRootConfigDir:
    """_state_root_config_dir -- the resolver seam (mocked subprocess)."""

    def _mock(self, monkeypatch, payload, *, rc=0):
        import types
        monkeypatch.setattr("shutil.which", lambda name: "agent-worktrees")
        proc = types.SimpleNamespace(returncode=rc, stdout=json.dumps(payload),
                                     stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)

    def test_resolves_when_knowledge_has_codespaces_yaml(self, tmp_path, monkeypatch):
        from agent_codespaces.config import _state_root_config_dir
        knowledge = tmp_path / "knowledge"
        _write_codespaces_yaml(knowledge, {"defaults": {}})
        self._mock(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True})
        assert _state_root_config_dir(tmp_path / "harness") == knowledge

    def test_none_when_knowledge_lacks_codespaces_yaml(self, tmp_path, monkeypatch):
        from agent_codespaces.config import _state_root_config_dir
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        self._mock(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True})
        assert _state_root_config_dir(tmp_path / "harness") is None

    def test_none_when_self_hosted(self, tmp_path, monkeypatch):
        from agent_codespaces.config import _state_root_config_dir
        self._mock(monkeypatch, {
            "state_root": str(tmp_path), "requires_external": False, "bound": True})
        assert _state_root_config_dir(tmp_path) is None

    def test_none_when_no_binstub(self, tmp_path, monkeypatch):
        from agent_codespaces.config import _state_root_config_dir
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert _state_root_config_dir(tmp_path) is None


class TestValidation:
    def test_valid_config(self):
        config = CodespacesConfig(source_paths=[Path("/repo")])
        issues = validate_config(config)
        assert len(issues) == 0

    def test_no_sources_warning(self):
        config = CodespacesConfig()
        issues = validate_config(config)
        assert any("No CodeSpace config found" in i for i in issues)

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

    def test_enabled_az_login_requires_resources_not_hosts(self):
        config = CodespacesConfig(
            source_paths=[Path("/repo")],
            credentials=CredentialsConfig(
                sources={
                    "az-login": CredentialSourceConfig(
                        enabled=True,
                        allowed_resources=["499b84ac-1321-427f-aa17-267ca6975798"],
                    ),
                },
            ),
        )
        assert validate_config(config) == []

    def test_enabled_az_login_no_resources(self):
        config = CodespacesConfig(
            source_paths=[Path("/repo")],
            credentials=CredentialsConfig(
                sources={"az-login": CredentialSourceConfig(enabled=True)},
            ),
        )
        issues = validate_config(config)
        assert any("no allowed_resources" in i for i in issues)


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

    A CodeSpaces repo (e.g. ``org/example-web-codespaces``) often differs from the
    product checkout it hosts (``/workspaces/example-web``). These verify that the
    folder resolves per repo rather than from a single global default.
    """

    def _config(self, **repo_kwargs) -> CodespacesConfig:
        from agent_codespaces.config import RepoConfig

        return CodespacesConfig(
            repos={"org/example-web-codespaces": RepoConfig(**repo_kwargs)}
        )

    def test_workspace_repo_derives_folder(self):
        """``workspace_repo`` derives ``/workspaces/<basename>``."""
        config = self._config(workspace_repo="example-web")
        assert config.workspace_folder_for("org/example-web-codespaces") == (
            "/workspaces/example-web"
        )
        assert config.effective_acp_command_for("org/example-web-codespaces") == (
            "cd /workspaces/example-web && copilot --acp --stdio --allow-all-tools"
        )

    def test_workspace_repo_with_owner_is_basenamed(self):
        config = self._config(workspace_repo="example-org/example-web")
        assert config.workspace_folder_for("org/example-web-codespaces") == (
            "/workspaces/example-web"
        )

    def test_explicit_workspace_folder_overrides_workspace_repo(self):
        config = self._config(
            workspace_repo="example-web", workspace_folder="/custom/checkout"
        )
        assert config.workspace_folder_for("org/example-web-codespaces") == (
            "/custom/checkout"
        )

    def test_per_repo_overrides_global_default(self):
        config = CodespacesConfig(workspace_folder="/workspaces/global")
        from agent_codespaces.config import RepoConfig

        config.repos["org/example-web-codespaces"] = RepoConfig(
            workspace_repo="example-web"
        )
        # The mapped repo gets its own folder...
        assert config.workspace_folder_for("org/example-web-codespaces") == (
            "/workspaces/example-web"
        )
        # ...while an unmapped repo falls back to the global default.
        assert config.workspace_folder_for("org/other") == "/workspaces/global"

    def test_unknown_repo_falls_back_to_global(self):
        config = CodespacesConfig(workspace_folder="/workspaces/global")
        assert config.workspace_folder_for(None) == "/workspaces/global"
        assert config.workspace_folder_for("org/unknown") == "/workspaces/global"

    def test_no_mapping_resolves_remote_workspace(self):
        config = self._config(workspace_repo="example-web")
        # A repo with no per-repo entry and no global default → remote-resolved.
        cmd = config.effective_acp_command_for("org/unmapped")
        assert (
            "CODESPACE_VSCODE_FOLDER" in cmd
            and "WORKING_DIRECTORY" in cmd
            and "VM_REPO_PATH" in cmd
        )

    def test_global_acp_command_still_overrides(self):
        config = self._config(workspace_repo="example-web")
        config.acp_command = "custom --acp"
        assert config.effective_acp_command_for("org/example-web-codespaces") == (
            "custom --acp"
        )

    def test_merged_from_yaml(self, config_dir):
        repo = config_dir / "repo"
        _write_codespaces_yaml(repo, {
            "repos": {
                "org/example-web-codespaces": {
                    "machine_type": "largePremiumLinux256gb",
                    "workspace_repo": "example-web",
                },
            },
        })
        save_adopted_repos([AdoptedRepo(path=repo)])
        config = load_merged_config()
        rc = config.repos["org/example-web-codespaces"]
        assert rc.workspace_repo == "example-web"
        assert config.effective_acp_command_for("org/example-web-codespaces") == (
            "cd /workspaces/example-web && copilot --acp --stdio --allow-all-tools"
        )


class TestCrossRepoRequestFolder:
    """#174: <repo>@<codespace> repo-layout convention.

    A requested repo lands at ``/workspaces/<basename>`` (clone-if-missing),
    except the CodeSpace's own product (already checked out) and the account
    dotfiles repo (owned by the universal bootstrap).
    """

    _COPILOT = "copilot --acp --stdio --allow-all-tools"
    _CS = "example-org/example-web-codespaces"

    def test_own_product_is_prepopulated_no_clone(self):
        config = CodespacesConfig()
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "example-web"
        )
        assert folder == "/workspaces/example-web"
        assert prepopulated is True

    def test_own_product_command_has_no_clone(self):
        config = CodespacesConfig()
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="example-web",
            repo_remote="https://github.com/example-org/example-web",
        )
        assert cmd == f"cd /workspaces/example-web && {self._COPILOT}"
        assert "git clone" not in cmd

    def test_dotfiles_maps_to_persisted_dir(self):
        config = CodespacesConfig(dotfiles_repo="example-user/dotfiles")
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "dotfiles"
        )
        assert folder == "/workspaces/.codespaces/.persistedshare/dotfiles"
        assert prepopulated is True

    def test_dotfiles_command_has_no_clone(self):
        config = CodespacesConfig(dotfiles_repo="example-user/dotfiles")
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="example-user/dotfiles",
            repo_remote="https://github.com/example-user/dotfiles",
        )
        assert cmd == (
            "cd /workspaces/.codespaces/.persistedshare/dotfiles "
            f"&& {self._COPILOT}"
        )
        assert "git clone" not in cmd

    def test_other_repo_clone_if_missing(self):
        config = CodespacesConfig()
        remote = "https://your-org.visualstudio.com/your-org/_git/example-marketplace"
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "example-marketplace"
        )
        assert folder == "/workspaces/example-marketplace"
        assert prepopulated is False
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="example-marketplace", repo_remote=remote,
        )
        assert cmd == (
            f"[ -d /workspaces/example-marketplace/.git ] || "
            f"git clone {remote} /workspaces/example-marketplace; "
            f"cd /workspaces/example-marketplace && {self._COPILOT}"
        )

    def test_other_repo_owner_prefix_basenamed(self):
        config = CodespacesConfig()
        folder, prepopulated = config.workspace_folder_for_request(
            self._CS, "your-org/example-marketplace"
        )
        assert folder == "/workspaces/example-marketplace"
        assert prepopulated is False

    def test_other_repo_no_remote_falls_to_plain_cd(self):
        """No known remote: cd only (fails loudly on the CodeSpace if absent)."""
        config = CodespacesConfig()
        cmd = config.effective_acp_command_for(
            self._CS, requested_repo="example-marketplace", repo_remote=None,
        )
        assert cmd == f"cd /workspaces/example-marketplace && {self._COPILOT}"
        assert "git clone" not in cmd

    def test_bare_request_unchanged(self):
        """requested_repo=None behaves exactly as the legacy bare path."""
        config = CodespacesConfig()
        from agent_codespaces.config import RepoConfig
        config.repos[self._CS] = RepoConfig(workspace_repo="example-web")
        assert config.effective_acp_command_for(self._CS) == (
            f"cd /workspaces/example-web && {self._COPILOT}"
        )
