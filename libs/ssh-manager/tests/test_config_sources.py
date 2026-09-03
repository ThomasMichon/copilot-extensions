"""Tests for SSH config sources."""

from __future__ import annotations

from types import SimpleNamespace

import ssh_manager.config_sources as config_sources
from ssh_manager.config_sources import ConfigSource, SSHConfig, SSHProfileSource


class TestSSHConfig:
    """SSHConfig dataclass tests."""

    def test_ssh_target_with_user(self):
        config = SSHConfig(host_alias="server", user="alice")
        assert config.ssh_target == "alice@server"

    def test_ssh_target_without_user(self):
        config = SSHConfig(host_alias="server")
        assert config.ssh_target == "server"

    def test_connection_identity_basic(self):
        config = SSHConfig(host_alias="server", user="alice", port=22)
        identity = config.connection_identity
        assert len(identity) == 64
        assert identity.isascii()

    def test_connection_identity_with_proxy(self):
        c1 = SSHConfig(host_alias="server", proxy_command="proxy1")
        c2 = SSHConfig(host_alias="server", proxy_command="proxy2")
        assert c1.connection_identity != c2.connection_identity

    def test_connection_identity_hostname_overrides_alias(self):
        c1 = SSHConfig(host_alias="alias", hostname="real.host")
        c2 = SSHConfig(host_alias="alias", hostname="other.host")
        assert c1.connection_identity != c2.connection_identity

    def test_connection_identity_stable(self):
        config = SSHConfig(host_alias="server", user="alice", port=22)
        assert config.connection_identity == config.connection_identity

    def test_connection_identity_normalizes_host_and_option_order(self):
        c1 = SSHConfig(
            host_alias="SERVER",
            extra_options={"ProxyJump": "jump", "Compression": "yes"},
        )
        c2 = SSHConfig(
            host_alias="server",
            extra_options={"compression": "yes", "proxyjump": "jump"},
        )
        assert c1.connection_identity == c2.connection_identity

    def test_connection_identity_includes_all_routing_inputs(self):
        base = SSHConfig(host_alias="server")
        variants = [
            SSHConfig(host_alias="server", identity_file="key"),
            SSHConfig(host_alias="server", config_file="config"),
            SSHConfig(host_alias="server", extra_options={"ProxyJump": "jump"}),
        ]
        assert all(
            variant.connection_identity != base.connection_identity
            for variant in variants
        )


class TestSSHProfileSource:
    """SSHProfileSource tests."""

    def test_implements_config_source_protocol(self):
        source = SSHProfileSource(host_alias="borealis")
        assert isinstance(source, ConfigSource)

    def test_get_ssh_config(self):
        source = SSHProfileSource(host_alias="borealis", user="cjohnson", port=2222)
        config = source.get_ssh_config()
        assert config.host_alias == "borealis"
        assert config.user == "cjohnson"
        assert config.port == 2222

    def test_refresh_returns_same_config(self):
        source = SSHProfileSource(host_alias="borealis")
        c1 = source.get_ssh_config()
        c2 = source.refresh()
        assert c1.host_alias == c2.host_alias

    def test_config_file_passed_through(self, monkeypatch):
        monkeypatch.setattr(
            SSHProfileSource,
            "_resolve_effective_config",
            lambda _self: (("hostname", "server"),),
        )
        source = SSHProfileSource(
            host_alias="server",
            config_file="/path/to/config",
        )
        config = source.get_ssh_config()
        assert config.config_file == "/path/to/config"

    def test_aliases_share_effective_connection_identity(self, monkeypatch):
        monkeypatch.setattr(config_sources.shutil, "which", lambda _name: "ssh")

        def run(args, **_kwargs):
            alias = args[-1]
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    f"host {alias}\n"
                    "hostname shared.example\n"
                    "user alice\n"
                    "port 22\n"
                    "proxycommand none\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(config_sources.subprocess, "run", run)
        alias_a = SSHProfileSource("alias-a").get_ssh_config()
        alias_b = SSHProfileSource("alias-b").get_ssh_config()
        assert alias_a.connection_identity == alias_b.connection_identity

    def test_effective_profile_is_cached(self, monkeypatch):
        calls = 0

        def resolve(_self):
            nonlocal calls
            calls += 1
            return (("hostname", "shared.example"),)

        monkeypatch.setattr(SSHProfileSource, "_resolve_effective_config", resolve)
        source = SSHProfileSource("alias")
        first = source.get_ssh_config()
        second = source.get_ssh_config()
        refreshed = source.refresh()
        assert first is second
        assert second is refreshed
        assert calls == 1
