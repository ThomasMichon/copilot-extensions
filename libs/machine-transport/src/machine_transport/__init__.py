"""Shared machine registry, identity, and transport resolution.

One place to resolve a machine name/alias/hostname against ``machines.yaml``,
decide whether it is this machine or a remote one, and pick the right SSH
target -- vendored (shared, not duplicated) across every Copilot CLI plugin
that dispatches a command to a named machine. See each submodule's own
docstring for the full rationale; ``README.md`` covers the vendoring
mechanism.
"""

from __future__ import annotations

from .execution_identity import (
    IdentityError,
    Resolution,
    TopologyEntry,
    detect_platform,
    machine_entries_to_topology,
    qualify_guest,
    resolve_identity,
    resolve_machine_identity,
)
from .identity import is_local_machine
from .registry import (
    AmbiguousMachineError,
    MachineEntry,
    SSHEnvironment,
    find_machine_entry,
    machine_name,
    merge_machines_yaml,
    parse_machines_yaml,
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
    "AmbiguousMachineError",
    "IdentityError",
    "MachineEntry",
    "Resolution",
    "SSHEnvironment",
    "TopologyEntry",
    "TransportPlan",
    "default_shell_for_env_name",
    "detect_platform",
    "find_machine_entry",
    "get_machine_transport",
    "is_local_machine",
    "machine_entries_to_topology",
    "machine_name",
    "merge_machines_yaml",
    "parse_machines_yaml",
    "parse_machines_yaml_file",
    "qualify_guest",
    "resolve_identity",
    "resolve_machine_identity",
    "resolve_ssh_target",
    "wrap_remote_command",
]
