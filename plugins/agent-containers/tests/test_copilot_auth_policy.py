"""Host-Copilot is the default; repository inference requires explicit legacy opt-in."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_containers import copilot_process_auth as auth
from agent_containers.config import ContainersConfig, FleetConfig, load_config
from agent_containers.copilot_auth_policy import trusted_auth_mode


@pytest.mark.guard
def test_shipped_starter_selects_host_copilot_without_legacy_migration(monkeypatch):
    import agent_containers.config as config_module
    import yaml

    path = (
        Path(__file__).resolve().parents[1]
        / "skills" / "containers-fleet" / "references" / "containers.yaml"
    )
    assert yaml.safe_load(path.read_text())["schema_version"] == 2
    monkeypatch.setattr(config_module, "_config_path", lambda: path)
    config = load_config()
    assert config.copilot_cli_account == "your-copilot-login"
    assert trusted_auth_mode(config, None) == "host-copilot"
    for fleet in config.fleets.values():
        if fleet.security_profile == "trusted":
            assert trusted_auth_mode(config, fleet) == "host-copilot"


@pytest.mark.guard
@pytest.mark.parametrize(
    ("mode", "profile", "preserved"),
    [
        ("legacy", "trusted", True),
        ("host-copilot", "trusted", False),
        ("legacy", "restricted", False),
    ],
)
def test_staging_preserves_only_explicit_trusted_legacy_auth(mode, profile, preserved):
    staged = []
    values = {"COPILOT_GITHUB_TOKEN": "fake-container-login", "GH_TOKEN": "fake-repository"}
    auth.stage_env(
        "example-1", "vscode", values,
        lambda name, user, environment: staged.append(environment),
        config=ContainersConfig(copilot_auth_mode=mode),
        fleet=FleetConfig(security_profile=profile),
    )
    assert ("COPILOT_GITHUB_TOKEN" in staged[0]) is preserved
    assert staged[0]["GH_TOKEN"] == "fake-repository"
    assert values["COPILOT_GITHUB_TOKEN"] == "fake-container-login"


@pytest.mark.guard
def test_unset_account_does_not_consult_gh_or_ambient_inference(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "repository-only")
    monkeypatch.setenv("GITHUB_TOKEN", "other-repository")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ambient-inference")
    monkeypatch.setattr(auth, "resolve_copilot_cli_token_or_raise",
                        lambda *a: pytest.fail("no account was selected"))
    monkeypatch.setattr(auth.transport, "_run", lambda *a, **k: pytest.fail("no launch/probe"))
    with pytest.raises(RuntimeError, match="refusing GH_TOKEN/GITHUB_TOKEN"):
        auth.prepare_launch(ContainersConfig(), FleetConfig(), "example-1", "vscode")
    assert auth.without_inference_token({
        "GH_TOKEN": "repository-only", "GITHUB_TOKEN": "other-repository",
        "COPILOT_GITHUB_TOKEN": "ambient-inference",
    }) == {"GH_TOKEN": "repository-only", "GITHUB_TOKEN": "other-repository"}


@pytest.mark.guard
def test_legacy_is_only_explicit_and_restricted_never_reads_host_source(monkeypatch):
    monkeypatch.setattr(auth, "resolve_copilot_cli_token_or_raise",
                        lambda *a: pytest.fail("host source not authorized for this mode"))
    config = ContainersConfig()
    assert trusted_auth_mode(config, FleetConfig()) == "host-copilot"
    assert auth.prepare_launch(ContainersConfig(copilot_auth_mode="legacy"),
                               FleetConfig(), "example-1", "vscode") is None
    assert auth.prepare_launch(config, FleetConfig(copilot_auth_mode="legacy"),
                               "example-1", "vscode") is None
    assert auth.prepare_launch(config, FleetConfig(security_profile="restricted"),
                               "example-1", "vscode") is None
    assert trusted_auth_mode(ContainersConfig(copilot_auth_mode="legacy"),
                             FleetConfig(copilot_auth_mode="host-copilot")) == "host-copilot"
    with pytest.raises(RuntimeError, match="copilot_auth_mode must"):
        trusted_auth_mode(SimpleNamespace(copilot_auth_mode="guess-account"), None)


@pytest.mark.guard
def test_loader_preserves_explicit_policy_and_rejects_invalid_modes(tmp_path, monkeypatch):
    import agent_containers.config as config_module

    path = tmp_path / "containers.yaml"
    monkeypatch.setattr(config_module, "_config_path", lambda: path)
    path.write_text(
        "copilot_auth_mode: legacy\nfleets:\n  example:\n    repo: example/repo\n"
        "    copilot_auth_mode: host-copilot\n",
    )
    config = load_config()
    assert trusted_auth_mode(config, None) == "legacy"
    assert trusted_auth_mode(config, config.fleets["example"]) == "host-copilot"
    path.write_text("copilot_auth_mode: automatic-gh-fallback\n")
    with pytest.raises(RuntimeError, match="copilot_auth_mode must"):
        load_config()


@pytest.mark.guard
def test_existing_posture_migrates_explicitly_but_new_host_mode_never_falls_back(tmp_path, monkeypatch):
    import agent_containers.config as config_module

    path = tmp_path / "containers.yaml"
    monkeypatch.setattr(config_module, "_config_path", lambda: path)
    path.write_text("schema_version: 1\nforward_gh_token: true\n")
    assert trusted_auth_mode(load_config(), None) == "legacy"
    assert "copilot_auth_mode" not in path.read_text()
    path.write_text("schema_version: 1\ncopilot_cli_account: inference-user\n")
    assert trusted_auth_mode(load_config(), None) == "host-copilot"
    path.write_text("schema_version: 2\nforward_gh_token: true\n")
    current = load_config()
    assert trusted_auth_mode(current, None) == "host-copilot"
    with pytest.raises(RuntimeError, match="refusing GH_TOKEN/GITHUB_TOKEN"):
        auth.prepare_launch(current, FleetConfig(), "example-1", "vscode")
    path.write_text(
        "schema_version: 1\nfleets:\n  selected:\n    copilot_cli_account: inference-user\n"
        "  existing:\n    repo: example/repo\n",
    )
    current = load_config()
    assert trusted_auth_mode(current, current.fleets["selected"]) == "host-copilot"
    assert trusted_auth_mode(current, current.fleets["existing"]) == "legacy"
