"""Shared machine registry, identity, and transport resolution.

One place to resolve a machine name/alias/hostname against ``machines.yaml``,
decide whether it is this machine or a remote one, and pick the right SSH
target -- vendored (shared, not duplicated) across every Copilot CLI plugin
that dispatches a command to a named machine. See each submodule's own
docstring for the full rationale; ``README.md`` covers the vendoring
mechanism.
"""

from __future__ import annotations

from .identity import is_local_machine
from .registry import (
    MachineEntry,
    SSHEnvironment,
    find_machine_entry,
    machine_name,
    merge_machines_yaml,
    parse_machines_yaml_file,
)
from .transport import (
    TransportPlan,
    default_shell_for_env_name,
    get_machine_transport,
    resolve_ssh_target,
    wrap_remote_command,
)

__all__ = [
    "MachineEntry",
    "SSHEnvironment",
    "TransportPlan",
    "default_shell_for_env_name",
    "find_machine_entry",
    "get_machine_transport",
    "is_local_machine",
    "machine_name",
    "merge_machines_yaml",
    "parse_machines_yaml_file",
    "resolve_ssh_target",
    "wrap_remote_command",
]
