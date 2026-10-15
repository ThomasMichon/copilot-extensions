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

from machine_transport import MachineEntry, find_machine_entry, machine_entries_to_topology, machine_name, resolve_identity
from machine_transport import is_local_machine as _mt_is_local_machine

from . import config as cfg


def resolve_execution_machine(
    name: str, *, guest: bool, configured: bool,
    load_entries: Callable[[], dict[str, MachineEntry]] | None,
) -> str:
    """Resolve local identity without inferring registered-space authority."""
    if configured and not guest:
        return resolve_identity(name).canonical
    entries: dict[str, MachineEntry] = {}
    if load_entries is not None:
        try:
            entries = load_entries()
        except FileNotFoundError:
            pass
    explicit = any(entry.execution_platform and not entry.legacy_key for entry in entries.values())
    selected = find_machine_entry(
        entries, name, reject_ambiguous=True, execution_platform=cfg.detect_platform(),
    ) if not explicit else None
    if explicit or (selected is not None and selected.execution_platform):
        if configured:
            return resolve_identity(name).canonical
        from .execution_spaces import detect_legacy_machine

        return detect_legacy_machine(entries, name)
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
