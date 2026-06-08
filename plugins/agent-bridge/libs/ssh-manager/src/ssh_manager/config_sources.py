"""Pluggable SSH configuration sources.

Each ConfigSource provides SSH connection parameters for a specific type
of target (static machine, CodeSpace, etc.). The ConnectionManager uses
these to establish and refresh ControlMaster connections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SSHConfig:
    """SSH connection parameters produced by a ConfigSource."""

    host_alias: str  # SSH target name (e.g., "borealis", "cs.fluffy-parakeet.org/repo")
    hostname: str | None = None  # resolved hostname (if different from alias)
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None
    proxy_command: str | None = None
    config_file: str | None = None  # path to SSH config file (for -F flag)
    extra_options: dict[str, str] = field(default_factory=dict)

    @property
    def ssh_target(self) -> str:
        """The SSH target string (user@host or just host)."""
        if self.user:
            return f"{self.user}@{self.host_alias}"
        return self.host_alias

    @property
    def connection_identity(self) -> str:
        """Unique key for this connection configuration.

        Used by ConnectionManager to determine if an existing master
        connection matches the requested configuration.
        """
        parts = [
            self.user or "",
            self.hostname or self.host_alias,
            str(self.port or 22),
        ]
        if self.proxy_command:
            parts.append(self.proxy_command)
        return "|".join(parts)


@runtime_checkable
class ConfigSource(Protocol):
    """Protocol for pluggable SSH config providers.

    Implementations provide SSH configuration for a specific target type.
    The ConnectionManager calls get_ssh_config() to obtain connection
    parameters and refresh() when reconnection is needed (e.g., after
    a CodeSpace restart refreshes its SSH config).
    """

    def get_ssh_config(self) -> SSHConfig:
        """Return SSH config for this source's target."""
        ...

    def refresh(self) -> SSHConfig:
        """Re-generate config (e.g., after target restart).

        May perform expensive operations like shelling out to `gh`.
        Called on reconnect, not on every command.
        """
        ...


class SSHProfileSource:
    """ConfigSource that reads from the local SSH config.

    For static machines defined in ~/.ssh/config. The host_alias is
    the SSH config Host entry (e.g., "borealis", "lambda-core-wsl").
    All connection details (hostname, user, port, key, proxy) are
    resolved by OpenSSH from the config file.
    """

    def __init__(
        self,
        host_alias: str,
        user: str | None = None,
        port: int | None = None,
        config_file: str | None = None,
    ) -> None:
        self._host_alias = host_alias
        self._user = user
        self._port = port
        self._config_file = config_file

    def get_ssh_config(self) -> SSHConfig:
        return SSHConfig(
            host_alias=self._host_alias,
            user=self._user,
            port=self._port,
            config_file=self._config_file,
        )

    def refresh(self) -> SSHConfig:
        # Static profiles don't change -- just return current config
        return self.get_ssh_config()
