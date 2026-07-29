"""Tests for the relay launch prelude seam (Session-Host path)."""

from __future__ import annotations

from agent_codespaces.relay_launch import (
    SCRUB_ENV_VARS,
    build_relay_env,
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


def test_build_relay_launch_env(monkeypatch, tmp_path):
    import agent_codespaces.relay_launch as rl

    class _Creds:
        relay_port = 9999

    class _Cfg:
        credentials = _Creds()

    # Empty config dir -> no published live port, so the config port is used.
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
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


def test_build_relay_launch_env_none_falls_back_to_config(monkeypatch, tmp_path):
    """``relay_port=None`` falls back to the configured relay port when no live
    port has been published."""
    import agent_codespaces.relay_launch as rl

    class _Creds:
        relay_port = 9999

    class _Cfg:
        credentials = _Creds()

    # Empty config dir -> no published live port -> config fallback.
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("agent_codespaces.config.load_merged_config",
                        lambda: _Cfg())
    monkeypatch.setattr("agent_codespaces.relay_token.token_for",
                        lambda name: "minted-tok")
    env, port = rl.build_relay_launch_env("cs-foo", relay_port=None)
    assert port == 9999
    assert "export LC_GIT_CREDENTIAL_RELAY=9999;" in env


def test_build_relay_launch_env_none_uses_published_live_port(monkeypatch, tmp_path):
    """``relay_port=None`` prefers the daemon's published live port (#540 pt3)
    over the static config port, so an ephemeral relay bind is honored even on
    the standalone agent-codespaces path."""
    import agent_codespaces.relay_launch as rl

    class _Creds:
        relay_port = 9999

    class _Cfg:
        credentials = _Creds()

    # A daemon published an ephemeral port to its config dir.
    (tmp_path / "relay-port").write_text("52001", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("agent_codespaces.config.load_merged_config",
                        lambda: _Cfg())
    monkeypatch.setattr("agent_codespaces.relay_token.token_for",
                        lambda name: "minted-tok")
    env, port = rl.build_relay_launch_env("cs-foo", relay_port=None)
    assert port == 52001
    assert "export LC_GIT_CREDENTIAL_RELAY=52001;" in env
    # the static config port must not be consulted
    assert "9999" not in env


def test_build_relay_launch_env_resolves_elevated_config_dir(monkeypatch, tmp_path):
    """An elevated sub-daemon's config dir (``<primary>/elevated``) resolves to
    the primary parent when reading the published live port (#540 pt3)."""
    import agent_codespaces.relay_launch as rl

    (tmp_path / "relay-port").write_text("52002", encoding="utf-8")
    elevated = tmp_path / "elevated"
    elevated.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(elevated))
    assert rl._published_live_relay_port() == 52002


def test_prelude_publishes_port_mapping_file():
    """When the relay is in use, the prelude publishes a discoverable
    port-mapping file keyed by port (Stage 2 port-discovery)."""
    from agent_codespaces.relay_launch import RELAY_PORTMAP_DIR, build_relay_env

    env = build_relay_env(51234, "tok", use_relay=True)
    assert RELAY_PORTMAP_DIR in env
    assert "relay-ports/51234.json" in env
    assert "|| true" in env  # best-effort; never aborts the prelude
    # Not published when the relay is disabled.
    assert "relay-ports" not in build_relay_env(51234, "tok", use_relay=False)


def test_build_relay_portmap_write_shape():
    from agent_codespaces.relay_launch import build_relay_portmap_write

    snip = build_relay_portmap_write(51234)
    assert '"$HOME/.agent-bridge/relay-ports/51234.json"' in snip
    assert "umask 177" in snip                       # 600 perms on the CS
    assert '"port":%s' in snip
    assert "$LC_GIT_CREDENTIAL_RELAY_TOKEN" in snip   # token not re-interpolated
    assert snip.rstrip().endswith("|| true;") or "|| true" in snip
