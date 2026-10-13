"""Host-Copilot is the default; repository inference requires explicit legacy opt-in."""

from types import SimpleNamespace

import pytest

from agent_containers import copilot_process_auth as auth
from agent_containers.config import ContainersConfig, FleetConfig, load_config
from agent_containers.copilot_auth_policy import trusted_auth_mode


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
