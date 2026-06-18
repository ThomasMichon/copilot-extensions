"""Tests for config loading and ACP command resolution."""

from __future__ import annotations

import textwrap

from agent_containers.config import DEFAULT_ACP_COMMAND, ContainersConfig, load_config


def test_defaults():
    c = ContainersConfig()
    assert c.exec_user == "vscode"
    assert c.workspace_folder == "/workspaces/odsp-web"
    assert c.forward_gh_token is True
    assert any("odsp-web-codespaces" in p for p in c.image_prefixes)


def test_effective_acp_command_default_prefixes_cd():
    c = ContainersConfig()
    cmd = c.effective_acp_command()
    assert cmd == f"cd /workspaces/odsp-web && {DEFAULT_ACP_COMMAND}"


def test_effective_acp_command_explicit_override_wins():
    c = ContainersConfig()
    assert c.effective_acp_command(acp_command="custom") == "custom"


def test_effective_acp_command_custom_workspace():
    c = ContainersConfig()
    cmd = c.effective_acp_command(workspace_folder="/work/x")
    assert cmd == f"cd /work/x && {DEFAULT_ACP_COMMAND}"


def test_load_config_from_file(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            exec_user: dev
            workspace_folder: /workspaces/foo
            forward_gh_token: false
            image_prefixes:
              - vsc-foo-
            fleets:
              odsp-web:
                repo: odsp-microsoft/odsp-web
                devcontainer_path: /src/odsp-web-codespaces
                size: 3
                code_model: clone
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.exec_user == "dev"
    assert c.workspace_folder == "/workspaces/foo"
    assert c.forward_gh_token is False
    assert c.image_prefixes == ["vsc-foo-"]
    assert "odsp-web" in c.fleets
    fleet = c.fleets["odsp-web"]
    assert fleet.size == 3
    assert fleet.prefix("odsp-web") == "odsp-web"
    assert fleet.devcontainer_path == "/src/odsp-web-codespaces"


def test_devcontainer_config_resolved_relative_to_path():
    from agent_containers.config import FleetConfig

    fleet = FleetConfig(
        devcontainer_path="/src/odsp-web-codespaces",
        devcontainer_config=".devcontainer/docker/devcontainer.json",
    )
    resolved = fleet.resolved_config()
    assert resolved is not None
    assert resolved.replace("\\", "/") == (
        "/src/odsp-web-codespaces/.devcontainer/docker/devcontainer.json"
    )


def test_devcontainer_config_absolute_kept():
    from agent_containers.config import FleetConfig

    fleet = FleetConfig(
        devcontainer_path="/src/x",
        devcontainer_config="/abs/devcontainer.json",
    )
    assert fleet.resolved_config().replace("\\", "/") == "/abs/devcontainer.json"


def test_devcontainer_config_none_when_unset():
    from agent_containers.config import FleetConfig

    assert FleetConfig(devcontainer_path="/src/x").resolved_config() is None


def test_load_config_dotfiles(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            dotfiles:
              repo: /home/me/dotfiles
              install_command: bash install.sh
            fleets:
              odsp-web:
                devcontainer_path: /src/odsp-web-codespaces
                devcontainer_config: .devcontainer/docker/devcontainer.json
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.dotfiles is not None
    assert c.dotfiles.repo == "/home/me/dotfiles"
    assert c.dotfiles.target == "/workspaces/.codespaces/.persistedshare/dotfiles"
    assert c.dotfiles.install_command == "bash install.sh"
    fleet = c.fleets["odsp-web"]
    assert fleet.devcontainer_config == ".devcontainer/docker/devcontainer.json"


def test_load_config_dotfiles_install_disabled(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            dotfiles:
              repo: /home/me/dotfiles
              target: /custom/dotfiles
              install_command: ""
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.dotfiles is not None
    assert c.dotfiles.target == "/custom/dotfiles"
    assert c.dotfiles.install_command is None


def test_load_config_no_dotfiles_when_repo_missing(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text("dotfiles:\n  target: /x\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    assert load_config().dotfiles is None
