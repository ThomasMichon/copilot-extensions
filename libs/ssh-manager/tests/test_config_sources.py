"""Tests for SSH config sources."""

from __future__ import annotations

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
        assert "alice" in identity
        assert "server" in identity
        assert "22" in identity

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

    def test_config_file_passed_through(self):
        source = SSHProfileSource(
            host_alias="server",
            config_file="/path/to/config",
        )
        config = source.get_ssh_config()
        assert config.config_file == "/path/to/config"
