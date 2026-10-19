"""Shared machine registry, identity, and transport resolution.

One place to resolve a machine name/alias/hostname against ``machines.yaml``,
decide whether it is this machine or a remote one, and pick the right SSH
target -- vendored (shared, not duplicated) across every Copilot CLI plugin
that dispatches a command to a named machine. See each submodule's own
docstring for the full rationale; ``README.md`` covers the vendoring
mechanism.
"""

from __future__ import annotations

from .control_plane import (
    ControlPlaneError,
    ControlPlaneRepositories,
    load_control_plane,
    parse_control_plane,
)
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
from .identity import is_local_machine, local_execution_platform, is_observed_local_name
from .machine_environment import MachineEnvironment, legacy_alias_forms, normalize_environment, split_machine_environment
from .registry import (
    AmbiguousMachineError,
    MachineEntry,
    SSHEnvironment,
    find_machine_entry,
    machine_name,
    machine_identity_names,
    normalize_machine_entries,
    merge_machines_yaml,
    parse_machines_yaml,
    parse_machines_yaml_file,
    MachineSelection,
    entry_physical_host,
    resolve_machine_selection,
    find_machine_metadata,
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
    "ControlPlaneError",
    "ControlPlaneRepositories",
    "IdentityError",
    "MachineEntry",
    "MachineEnvironment",
    "MachineSelection",
    "entry_physical_host",
    "normalize_environment",
    "legacy_alias_forms",
    "resolve_machine_selection",
    "find_machine_metadata",
    "Resolution",
    "SSHEnvironment",
    "TopologyEntry",
    "TransportPlan",
    "default_shell_for_env_name",
    "detect_platform",
    "find_machine_entry",
    "get_machine_transport",
    "is_local_machine",
    "is_observed_local_name",
    "local_execution_platform",
    "load_control_plane",
    "machine_entries_to_topology",
    "machine_name",
    "machine_identity_names",
    "normalize_machine_entries",
    "merge_machines_yaml",
    "parse_machines_yaml",
    "parse_machines_yaml_file",
    "parse_control_plane",
    "qualify_guest",
    "resolve_identity",
    "resolve_machine_identity",
    "resolve_ssh_target",
    "split_machine_environment",
    "wrap_remote_command",
]
