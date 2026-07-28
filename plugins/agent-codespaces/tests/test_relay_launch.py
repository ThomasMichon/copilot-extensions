"""Tests for the relay launch prelude seam (Session-Host path)."""

from __future__ import annotations

from agent_codespaces.relay_launch import (
    SCRUB_ENV_VARS,
    build_relay_env,
    build_relay_launch_env,
)


def test_build_relay_env_scrubs_and_exports():
    env = build_relay_env(9857, "tok123", use_relay=True)
    # PAT scrub always prepended
    for v in SCRUB_ENV_VARS:
        assert f"unset {v};" in env
    assert "export LC_GIT_CREDENTIAL_RELAY=9857;" in env
    assert "export LC_GIT_CREDENTIAL_RELAY_TOKEN=tok123;" in env
    assert "GIT_TERMINAL_PROMPT=0" in env
    # scrub comes before the relay exports (never clobbered)
    assert env.index("unset") < env.index("LC_GIT_CREDENTIAL_RELAY")


def test_build_relay_env_no_relay_still_scrubs():
    env = build_relay_env(9857, "tok", use_relay=False)
    assert "unset MS_ADO_PAT;" in env
    assert "LC_GIT_CREDENTIAL_RELAY" not in env


def test_build_relay_launch_env(monkeypatch):
    import agent_codespaces.relay_launch as rl

    class _Creds:
        relay_port = 9999

    class _Cfg:
        credentials = _Creds()

    monkeypatch.setattr("agent_codespaces.config.load_merged_config",
                        lambda: _Cfg())
    monkeypatch.setattr("agent_codespaces.relay_token.token_for",
                        lambda name: "minted-tok")
    env, port = rl.build_relay_launch_env("cs-foo")
    assert port == 9999
    assert "export LC_GIT_CREDENTIAL_RELAY=9999;" in env
    assert "minted-tok" in env


def test_build_relay_launch_env_live_port_override(monkeypatch):
    """An injected (live) relay port wins over the static config port."""
    import agent_codespaces.relay_launch as rl

    class _Creds:
        relay_port = 9999

    class _Cfg:
        credentials = _Creds()

    monkeypatch.setattr("agent_codespaces.config.load_merged_config",
                        lambda: _Cfg())
    monkeypatch.setattr("agent_codespaces.relay_token.token_for",
                        lambda name: "minted-tok")
    env, port = rl.build_relay_launch_env("cs-foo", relay_port=51234)
    assert port == 51234
    assert "export LC_GIT_CREDENTIAL_RELAY=51234;" in env
    # config port is not consulted / not present
    assert "9999" not in env


def test_build_relay_launch_env_none_falls_back_to_config(monkeypatch):
    """``relay_port=None`` falls back to the configured relay port."""
    import agent_codespaces.relay_launch as rl

    class _Creds:
        relay_port = 9999

    class _Cfg:
        credentials = _Creds()

    monkeypatch.setattr("agent_codespaces.config.load_merged_config",
                        lambda: _Cfg())
    monkeypatch.setattr("agent_codespaces.relay_token.token_for",
                        lambda name: "minted-tok")
    env, port = rl.build_relay_launch_env("cs-foo", relay_port=None)
    assert port == 9999
    assert "export LC_GIT_CREDENTIAL_RELAY=9999;" in env
