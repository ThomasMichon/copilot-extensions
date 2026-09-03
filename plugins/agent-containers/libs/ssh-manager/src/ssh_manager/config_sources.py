"""Pluggable SSH configuration sources.

Each ConfigSource provides SSH connection parameters for a specific type
of target (static machine, CodeSpace, etc.). The ConnectionManager uses
these to establish and refresh ControlMaster connections.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
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
    effective_config: tuple[tuple[str, str], ...] = field(
        default_factory=tuple,
        repr=False,
    )

    @property
    def ssh_target(self) -> str:
        """The SSH target string (user@host or just host)."""
        if self.user:
            return f"{self.user}@{self.host_alias}"
        return self.host_alias

    @property
    def connection_identity(self) -> str:
        """Stable normalized key for the complete connection configuration.

        Used by ConnectionManager to determine if an existing master
        connection matches the requested configuration. The digest includes
        every SSH-routing input but does not expose paths, proxy commands, or
        option values when used as an internal registry key.
        """
        def _path(value: str | None) -> str:
            if not value:
                return ""
            return os.path.normcase(os.path.normpath(os.path.expanduser(value)))

        normalized: object
        if self.effective_config:
            normalized = self.effective_config
        else:
            normalized = {
                "config_file": _path(self.config_file),
                "extra_options": sorted(
                    (str(key).strip().casefold(), str(value).strip())
                    for key, value in self.extra_options.items()
                ),
                "host": (self.hostname or self.host_alias).strip().casefold(),
                "identity_file": _path(self.identity_file),
                "port": int(self.port or 22),
                "proxy_command": (self.proxy_command or "").strip(),
                "user": (self.user or "").strip(),
            }
        encoded = json.dumps(
            normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


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
            effective_config=self._resolve_effective_config(),
        )

    def refresh(self) -> SSHConfig:
        # Static profiles don't change -- just return current config
        return self.get_ssh_config()

    def _resolve_effective_config(self) -> tuple[tuple[str, str], ...]:
        """Resolve the routing identity OpenSSH will actually use."""
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("OpenSSH client executable `ssh` is not available")
        args = [ssh, "-G"]
        if self._config_file:
            args.extend(["-F", self._config_file])
        if self._port:
            args.extend(["-p", str(self._port)])
        if self._user:
            args.extend(["-l", self._user])
        args.append(self._host_alias)
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()
            raise RuntimeError(
                detail or f"ssh -G exited {proc.returncode}"
            )

        resolved: list[tuple[str, str]] = []
        for line in proc.stdout.splitlines():
            key, separator, value = line.partition(" ")
            if not separator or key.casefold() == "host":
                continue
            resolved.append((key.casefold(), value.strip()))
        if not resolved:
            raise RuntimeError("ssh -G returned no effective configuration")
        return tuple(resolved)
