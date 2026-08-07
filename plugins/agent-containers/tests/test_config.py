"""Tests for config loading and ACP command resolution."""

from __future__ import annotations

import textwrap

from agent_containers.config import DEFAULT_ACP_COMMAND, ContainersConfig, load_config


def test_defaults():
    c = ContainersConfig()
    assert c.exec_user == "vscode"
    assert c.workspace_folder == "/workspace"
    assert c.forward_gh_token is True
    assert any(p == "vsc-" for p in c.image_prefixes)


def test_effective_acp_command_default_prefixes_cd():
    c = ContainersConfig()
    cmd = c.effective_acp_command()
    assert cmd == f"cd /workspace && {DEFAULT_ACP_COMMAND}"


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
              myrepo:
                repo: your-org/your-repo
                devcontainer_path: /src/myrepo-devcontainer
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
    assert "myrepo" in c.fleets
    fleet = c.fleets["myrepo"]
    assert fleet.size == 3
    assert fleet.prefix("myrepo") == "myrepo"
    assert fleet.devcontainer_path == "/src/myrepo-devcontainer"


def test_devcontainer_config_resolved_relative_to_path():
    from agent_containers.config import FleetConfig

    fleet = FleetConfig(
        devcontainer_path="/src/myrepo-devcontainer",
        devcontainer_config=".devcontainer/docker/devcontainer.json",
    )
    resolved = fleet.resolved_config()
    assert resolved is not None
    assert resolved.replace("\\", "/") == (
        "/src/myrepo-devcontainer/.devcontainer/docker/devcontainer.json"
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
              myrepo:
                devcontainer_path: /src/myrepo-devcontainer
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
    fleet = c.fleets["myrepo"]
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


def test_harness_defaults_off():
    # harness is opt-in and decoupled from dotfiles: None unless configured.
    assert ContainersConfig().harness is None


def test_load_config_harness(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            harness:
              repo: /host/harness
            fleets:
              myrepo:
                devcontainer_path: /src/myrepo-devcontainer
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.harness is not None
    assert c.harness.repo == "/host/harness"
    # target derived from the repo basename by the standard convention, no install
    assert c.harness.target == "/workspaces/harness"
    assert c.harness.install_command is None
    # dotfiles and harness are independent
    assert c.dotfiles is None


def test_harness_target_derives_from_repo_basename():
    from agent_containers.config import HarnessConfig

    assert HarnessConfig(repo="/host/control-plane").target == "/workspaces/control-plane"
    assert HarnessConfig(repo="D:/Src/myharness").target == "/workspaces/myharness"


def test_load_config_no_harness_when_repo_missing(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text("harness:\n  install_command: bash x\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    assert load_config().harness is None


# --- E1e knowledge overlay (config-graft, #947) -------------------------------

class TestKnowledgeOverlay:
    """containers.yaml resolves from the bound knowledge repo for a stateless harness."""

    def _isolate(self, tmp_path, monkeypatch):
        # No env, cwd, or machine-local containers.yaml -> only the overlay remains.
        monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("agent_containers.config.RUNTIME_DIR",
                            tmp_path / "empty-runtime")

    def test_overlay_fallback_used_when_nothing_local(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        (knowledge / "containers.yaml").write_text("exec_user: knuser\n", encoding="utf-8")
        monkeypatch.setattr(
            "agent_containers.config._knowledge_overlay_config",
            lambda: knowledge / "containers.yaml")
        assert load_config().exec_user == "knuser"

    def test_machine_local_wins_over_overlay(self, tmp_path, monkeypatch):
        # A deliberate machine-local containers.yaml still takes precedence.
        self._isolate(tmp_path, monkeypatch)
        runtime = tmp_path / "rt"
        runtime.mkdir()
        (runtime / "containers.yaml").write_text("exec_user: localuser\n", encoding="utf-8")
        monkeypatch.setattr("agent_containers.config.RUNTIME_DIR", runtime)
        called = {"n": 0}
        monkeypatch.setattr(
            "agent_containers.config._knowledge_overlay_config",
            lambda: called.__setitem__("n", called["n"] + 1) or None)
        assert load_config().exec_user == "localuser"
        assert called["n"] == 0  # overlay never consulted

    def test_no_overlay_falls_back_to_defaults(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "agent_containers.config._knowledge_overlay_config", lambda: None)
        assert load_config().exec_user == "vscode"  # built-in default


class TestKnowledgeOverlayResolver:
    """_knowledge_overlay_config -- the resolver seam (mocked subprocess)."""

    def _mock(self, monkeypatch, payload, *, rc=0):
        import types
        monkeypatch.setattr("shutil.which", lambda name: "agent-worktrees")
        proc = types.SimpleNamespace(returncode=rc, stdout=__import__("json").dumps(payload),
                                     stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)

    def test_resolves_when_knowledge_has_containers_yaml(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        (knowledge / "containers.yaml").write_text("exec_user: x\n", encoding="utf-8")
        self._mock(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True})
        assert _knowledge_overlay_config() == knowledge / "containers.yaml"

    def test_none_when_self_hosted(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        self._mock(monkeypatch, {
            "state_root": str(tmp_path), "requires_external": False, "bound": True})
        assert _knowledge_overlay_config() is None

    def test_none_when_knowledge_lacks_file(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        self._mock(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True})
        assert _knowledge_overlay_config() is None

    def test_none_when_no_binstub(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert _knowledge_overlay_config() is None
