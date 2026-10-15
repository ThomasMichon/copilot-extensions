"""Canonicalized "is this the local machine?" check.

The same physical machine can be spelled differently in different contexts:
a claim/codename recorded under a raw COMPUTERNAME before the
``hostname:``-field decoupling landed vs. the current canonical alias, or a
caller passing a ``machines.yaml`` registry key where another caller passes
its alias. A bare string comparison (``name == config.machine``) then
silently misses that the target IS this machine, and dispatches an
unnecessary (and sometimes self-looping, over a devtunnel/SSH mesh) network
call. Every machine-targeted SSH dispatch path should resolve "is this
local?" through :func:`is_local_machine` instead.

This is a thin, agent-worktrees-flavored wrapper over the shared
``machine_transport.is_local_machine`` (vendored, not duplicated -- see its
own module docstring for the full canonicalization algorithm): it supplies
this plugin's own ``Config``/``load_machines_yaml`` shapes so every existing
call site keeps its original ``(name, config)`` signature, and passes
``guest=True`` on a WSL guest so a guest execution identity can never
silently resolve local against its own native-host registry entry.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from machine_transport import MachineEntry, machine_entries_to_topology, machine_name, resolve_identity
from machine_transport import resolve_machine_selection, split_machine_environment
from machine_transport import is_local_machine as _mt_is_local_machine

from . import config as cfg


def resolve_execution_machine(
    name: str, *, guest: bool, configured: bool,
    load_entries: Callable[[], dict[str, MachineEntry]] | None,
) -> str:
    """Resolve local identity without inferring registered-space authority."""
    entries: dict[str, MachineEntry] = {}
    if load_entries is not None:
        try:
            entries = load_entries()
        except FileNotFoundError:
            pass
    explicit = any(entry.execution_platform and not entry.legacy_key for entry in entries.values())
    selection = resolve_machine_selection(entries, name, "wsl" if guest else cfg.detect_platform())
    selected = selection.entry
    if configured:
        if selected is not None and not selected.execution_platform and not guest:
            return name if not split_machine_environment(name).environment else selected.key
        return selected.key if selected is not None else selection.target.logical_machine
    if explicit or (selected is not None and selected.execution_platform):
        from .execution_spaces import detect_legacy_machine

        return detect_legacy_machine(entries, name)
    if selected is not None:
        if not guest:
            return machine_name(selected)
        if split_machine_environment(selected.key).environment == "wsl":
            return selected.key
    resolved = resolve_identity(name, machine_entries_to_topology(entries), guest=guest)
    for warning in resolved.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not guest and resolved.topology_key is not None:
        return machine_name(entries[resolved.topology_key])
    return resolved.canonical


def is_local_machine(name: str, config: cfg.Config) -> bool:
    """True when ``name`` (a machine key, alias, ``hostname`` field, or
    display_name) refers to the CURRENT machine.

    Canonicalizes both sides through the registry (the same key/alias/
    hostname/display_name matching ``config.detect_machine`` itself uses)
    and, as a final ground-truth check, the real local OS hostname, before
    deciding.
    """
    from .execution_spaces import require_owner_identity

    require_owner_identity(name, config, canonical_ref=False)
    return _mt_is_local_machine(
        name,
        config_machine=config.machine,
        load_entries=lambda: cfg.load_machines_yaml(config.default_repo.anchor),
        guest=cfg.detect_platform() == "wsl",
    )
