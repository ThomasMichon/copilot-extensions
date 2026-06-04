"""Topology -- parse machines.yaml into typed machine configs.

Reads the facility's machine topology from machines.yaml and provides
typed access to machine metadata, SSH environments, and readiness state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("agent-bridge")


@dataclass
class SshEnvironment:
    """A single SSH environment on a machine (e.g. windows, wsl, linux)."""

    name: str
    alias: str
    port: int = 22
    user: str | None = None
    shell: str = "bash"


@dataclass
class MachineConfig:
    """Parsed machine entry from machines.yaml."""

    key: str
    display_name: str
    environment: str = ""
    role: str = ""
    field_terminal: bool = False
    ssh_environments: list[SshEnvironment] = field(default_factory=list)
    ssh_ip: str | None = None
    ssh_ready: bool = False

    def get_ssh_env(self, env_name: str | None = None) -> SshEnvironment | None:
        """Get an SSH environment by name, or the first available one."""
        if not self.ssh_environments:
            return None
        if env_name:
            return next(
                (e for e in self.ssh_environments if e.name == env_name), None
            )
        # Default: prefer wsl > linux > first available
        for preferred in ("wsl", "linux"):
            env = next(
                (e for e in self.ssh_environments if e.name == preferred), None
            )
            if env:
                return env
        return self.ssh_environments[0]

    def get_spawnable_ssh_env(self, env_name: str | None = None) -> SshEnvironment | None:
        """Get an SSH environment suitable for agent spawning.

        Phase 2 restricts to POSIX shells (bash, sh, zsh). PowerShell
        targets require different remote command construction.
        """
        POSIX_SHELLS = {"bash", "sh", "zsh", "dash", "fish"}
        if env_name:
            env = self.get_ssh_env(env_name)
            if env and env.shell in POSIX_SHELLS:
                return env
            return None
        # Auto-select: first POSIX-shell environment
        for preferred in ("wsl", "linux"):
            env = next(
                (e for e in self.ssh_environments
                 if e.name == preferred and e.shell in POSIX_SHELLS),
                None,
            )
            if env:
                return env
        return next(
            (e for e in self.ssh_environments if e.shell in POSIX_SHELLS), None
        )


def parse_machines_yaml(data: dict[str, Any]) -> dict[str, MachineConfig]:
    """Parse raw machines.yaml data into typed MachineConfig objects."""
    machines: dict[str, MachineConfig] = {}
    raw_machines = data.get("machines", {})

    for key, mdata in raw_machines.items():
        ssh_envs: list[SshEnvironment] = []
        ssh_block = mdata.get("ssh", {})

        for env_data in ssh_block.get("environments", []):
            ssh_envs.append(SshEnvironment(
                name=env_data.get("name", ""),
                alias=env_data.get("alias", key),
                port=env_data.get("port", 22),
                user=env_data.get("user"),
                shell=env_data.get("shell", "bash"),
            ))

        machines[key] = MachineConfig(
            key=key,
            display_name=mdata.get("display_name", key),
            environment=mdata.get("environment", ""),
            role=mdata.get("role", ""),
            field_terminal=bool(mdata.get("field_terminal", False)),
            ssh_environments=ssh_envs,
            ssh_ip=ssh_block.get("ip"),
            ssh_ready=bool(ssh_block.get("ready", False)),
        )

    return machines


def load_machines_yaml(path: str | Path) -> dict[str, MachineConfig]:
    """Load and parse a machines.yaml file."""
    p = Path(path).expanduser()
    if not p.exists():
        log.warning("machines.yaml not found at %s", p)
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        machines = parse_machines_yaml(data)
        log.info("Loaded %d machines from %s", len(machines), p)
        return machines
    except Exception as exc:
        log.error("Failed to parse machines.yaml at %s: %s", p, exc)
        return {}
